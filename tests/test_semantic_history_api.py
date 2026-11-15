"""Scoped history, paging and delete-impact APIs; synthetic stores, no servers."""
import importlib.util
import sys
import types
import unittest
from unittest.mock import patch

from flask import Flask
from Function.Knowledge import knowledge_base as knowledge
import test_ontology_api as fixtures


class SemanticHistoryApiTests(unittest.TestCase):
    # Reuse the existing isolated blueprint fixture without inheriting/rerunning
    # its unrelated tests. It mocks all embedding/model boundaries.
    setUp = fixtures.OntologyApiTests.setUp
    url = fixtures.OntologyApiTests.url
    upload = fixtures.OntologyApiTests.upload
    confirm = fixtures.OntologyApiTests.confirm

    def test_same_version_history_is_readable_after_disable_and_delete(self):
        saved = self.confirm(self.upload())
        old_revision = saved["revision_id"]
        raw = self.raw.replace(b"SUM(gpa_point * credit)", b"SUM(gpa_point * credit) * 2", 1)
        self.assertNotEqual(raw, self.raw)
        replacement = self.confirm(self.upload(raw), ontology_id=saved["id"])
        self.assertEqual(replacement["version"], saved["version"])
        self.assertNotEqual(replacement["revision_id"], old_revision)
        endpoint = "ontologies/" + saved["id"]
        self.client.put(self.url(endpoint), json={"enabled": False})
        self.client.delete(self.url(endpoint))
        revisions = self.client.get(self.url(endpoint + "/revisions?limit=1")).get_json()
        self.assertEqual(len(revisions["revisions"]), 1)
        self.assertEqual(revisions["next_offset"], 1)
        response = self.client.get(self.url(endpoint + "/definition?path=metrics.gpa&revision_id=" + old_revision))
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["revision_id"], old_revision)
        self.assertNotIn("* 2", response.get_json()["definition"]["expression"])

    def test_history_pages_validate_bounds_and_use_the_pinned_revision(self):
        saved = self.confirm(self.upload())
        endpoint = f"ontologies/{saved['id']}/definition?path=entities.Score&revision_id={saved['revision_id']}"
        first = self.client.get(self.url(endpoint + "&limit=100")).get_json()
        self.assertEqual(first["returned_chars"], 100)
        self.assertIsNone(first["definition"])
        second = self.client.get(self.url(endpoint + "&offset=100&limit=100")).get_json()
        self.assertEqual(second["offset"], 100)
        self.assertNotEqual(first["content"], second["content"])
        self.assertEqual(first["revision_id"], second["revision_id"])
        bad = self.client.get(self.url(endpoint + "&offset=bad"))
        self.assertEqual(bad.status_code, 400)
        missing = self.client.get(self.url(endpoint.replace(saved["revision_id"], "or_" + "0" * 32)))
        self.assertEqual(missing.status_code, 404)

    def test_confirm_links_ordinary_definitions_and_file_impact_retains_their_history(self):
        filename = "synthetic.xlsx"
        (self.directory / filename).write_bytes(b"synthetic-source")
        records = [{"table": "metrics", "name": "synthetic-value", "definition": "synthetic contract"},
                   {"table": "business_rules", "rule_id": "synthetic-valid", "description": "synthetic rule"}]
        with patch("Function.Knowledge.file_parser.extract_text", return_value=""):
            confirmed = self.client.post(self.url("confirm"), json={"filename": filename, "records": records})
        self.assertEqual(confirmed.status_code, 200, confirmed.get_json())
        impact = self.client.get(self.url(f"files/{filename}/impact")).get_json()
        self.assertEqual(len(impact["linked"]["metrics"]), 1)
        self.assertEqual(len(impact["linked"]["rules"]), 1)
        metric = impact["linked"]["metrics"][0]
        self.assertEqual(self.client.delete(self.url(f"files/{filename}")).status_code, 200)
        response = self.client.get(self.url(f"metrics/{metric['id']}/revisions/{metric['revision_id']}"))
        self.assertEqual(response.status_code, 200)
        definition = response.get_json()["definition"]
        self.assertEqual(definition["source_filename"], filename)
        self.assertEqual(definition["source_status"], "linked")
        self.assertEqual(len(self.client.get(self.url("metrics")).get_json()), 1)
        self.assertEqual(self.client.get(self.url(f"metrics/{metric['id']}/revisions?offset=bad")).status_code, 400)

    def test_history_endpoints_use_owner_storage_and_expired_revisions_return_not_found(self):
        spec = importlib.util.spec_from_file_location("api._semantic_scope_test", fixtures.ROOT / "api" / "knowledge.py")
        api = importlib.util.module_from_spec(spec)
        workspace = types.ModuleType("data.workspace")
        workspace.workspace_manager = types.SimpleNamespace(workspace_id_for_session=lambda sid: "")
        with patch.dict(sys.modules, {"data.workspace": workspace}), patch.object(knowledge, "_KB_DIR", self.directory / "owners"):
            spec.loader.exec_module(api)
            app = Flask(__name__)
            app.config.update(TESTING=True)
            app.register_blueprint(api.bp)
            client = app.test_client()
            with knowledge.KnowledgeBase(user_id="owner-a") as kb:
                old = kb.add_metric("private", definition="first", cache_embedding=False)
                kb.update_metric(old["id"], definition="replacement")
            path = f"/api/knowledge/metrics/{old['id']}/revisions/{old['revision_id']}"
            self.assertEqual(client.get(path, headers={"X-BAA-User-ID": "owner-a"}).status_code, 200)
            denied = client.get(path, headers={"X-BAA-User-ID": "owner-b"})
            self.assertEqual(denied.status_code, 404)
            rows = client.get(f"/api/knowledge/metrics/{old['id']}/revisions", headers={"X-BAA-User-ID": "owner-b"})
            self.assertEqual(rows.get_json()["revisions"], [])
            with knowledge.KnowledgeBase(user_id="owner-a") as kb:
                kb.prune_history(10 ** 12)
            self.assertEqual(client.get(path, headers={"X-BAA-User-ID": "owner-a"}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
