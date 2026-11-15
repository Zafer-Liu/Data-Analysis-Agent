"""Cluster activation and configuration revocation at the application boundary."""
from contextlib import ExitStack
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from flask import Flask

import api.cluster as subject
from data.cluster_connections import ClusterConnectionStore, ClusterError


class ClusterAppIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, CLUSTER_STORE_ROOT=str(Path(self.directory.name) / "cluster"))
        self.app.register_blueprint(subject.bp)
        self.session = SimpleNamespace(workspace_id="", _sources=[], owner_user_id="")
        def add_source(source):
            identity = "source-" + str(len(self.session._sources) + 1)
            self.session._sources.append({"id": identity, "source": source})
            return identity
        self.session.add_source = add_source
        self.session.list_sources = lambda: [{"id": entry["id"], "name": "cluster"} for entry in self.session._sources]
        self.stack.enter_context(patch.object(subject, "is_cloud_managed_runtime", return_value=False))
        self.stack.enter_context(patch("api.state.check_session_ownership", return_value=(True, "")))
        self.stack.enter_context(patch("api.state.session_manager", SimpleNamespace(get_or_create=lambda _sid: self.session)))
        self.connections = ClusterConnectionStore(self.app.config["CLUSTER_STORE_ROOT"], owner_id="local-default")
        self.record = self.connections.save({"kind": "trino", "host": "warehouse.example", "port": 443,
                                             "tls": True, "auth": "none", "username": "reader",
                                             "catalog": "hive", "schema": "default", "allowed_tables": ["hive.default.orders"]})
        self.identity = self.record["connection_id"]
        self.scope = {"owner_id": "local-default", "session_id": "sid", "workspace_id": ""}
        self.client = self.app.test_client()

    def test_activation_is_idempotent_and_does_not_reprobe_a_live_source(self):
        source = SimpleNamespace(connection_id=self.identity, config=self.record["config"], _closed=False,
                                 test_connection=Mock(return_value={"connected": True}), close=Mock())
        with patch.object(subject, "create_session_source", return_value=source):
            first = self.client.post(f"/api/datasource/cluster/connections/{self.identity}/activate", json={"session_id": "sid"})
            second = self.client.post(f"/api/datasource/cluster/connections/{self.identity}/activate", json={"session_id": "sid"})
        self.assertEqual(200, first.status_code, first.get_json())
        self.assertEqual(first.get_json()["source_id"], second.get_json()["source_id"])
        self.assertTrue(second.get_json()["reused"])
        source.test_connection.assert_called_once()
        self.assertEqual(1, len(self.session._sources))

    def test_active_agent_source_rejects_new_queries_after_permission_configuration_changes(self):
        with self.app.app_context():
            source = subject.create_session_source(self.identity, self.scope)
            try:
                self.assertIn("hive.default.orders", source.get_schema())
                self.assertEqual("SELECT amount FROM hive.default.orders", source.validate_query("SELECT amount FROM hive.default.orders"))
                changed = dict(self.record["config"], allowed_tables=["hive.default.other"])
                self.connections.save(changed, connection_id=self.identity)
                with self.assertRaises(ClusterError) as caught:
                    source.validate_query("SELECT amount FROM hive.default.orders")
                self.assertEqual("cluster_permission_denied", caught.exception.code)
            finally:
                source.close()

    def test_hosted_cluster_targets_require_trusted_origin_policy(self):
        with self.app.app_context(), patch.object(subject, "is_cloud_managed_runtime", return_value=True):
            with self.assertRaises(ClusterError):
                subject._assert_network_target(self.record["config"])
            self.app.config["CLUSTER_ALLOWED_ORIGINS"] = ["https://warehouse.example:443"]
            subject._assert_network_target(self.record["config"])
            with self.assertRaises(ClusterError):
                subject._assert_network_target(dict(self.record["config"], host="internal.example"))
            with self.assertRaises(ClusterError):
                subject._assert_network_target(dict(self.record["config"], approved_endpoints=["https://unapproved.example:443"]))


if __name__ == "__main__":
    unittest.main()
