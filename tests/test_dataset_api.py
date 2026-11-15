"""Real small-file Dataset API lifecycle with controlled Job scheduling."""
from contextlib import ExitStack
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from flask import Flask

import api.datasets as subject


class Runner:
    def __init__(self):
        self.workers = {}
        self.jobs = {}

    def create(self, worker, *, job_type, label):
        job_id = "job-" + str(len(self.jobs) + 1)
        self.workers[job_id] = worker
        self.jobs[job_id] = {"id": job_id, "status": "queued", "type": job_type, "label": label}
        return job_id

    def get_status(self, job_id):
        return self.jobs.get(job_id)

    def run(self, job_id):
        context = SimpleNamespace(check_canceled=lambda: None, is_canceled=lambda: False,
                                  set_progress=lambda *_: None)
        self.jobs[job_id].update(status="running")
        result = self.workers[job_id](context)
        self.jobs[job_id].update(status="succeeded", result=result)


class Session:
    def __init__(self):
        self.workspace_id = ""
        self.owner_user_id = ""
        self._sources = []
        self.job_runner = Runner()

    def add_source(self, source):
        identity = "source-" + str(len(self._sources) + 1)
        self._sources.append({"id": identity, "source": source})
        return identity


class DatasetApiTests(unittest.TestCase):
    def setUp(self):
        from data.dataset_catalog import DatasetCatalog
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.session = Session()
        self.catalog = DatasetCatalog(Path(self.directory.name) / "catalog", owner_id="local-default", session_id="sid", workspace_id="")
        closer = getattr(self.catalog, "close", None)
        if callable(closer):
            self.addCleanup(closer)
        self.stack.enter_context(patch.object(subject, "check_session_ownership", return_value=(True, "")))
        self.stack.enter_context(patch.object(subject, "session_manager", SimpleNamespace(get_or_create=lambda _sid: self.session)))
        self.stack.enter_context(patch.object(subject, "workspace_manager", SimpleNamespace(get=lambda _sid: None)))
        def scoped_catalog(sid):
            subject._scope(sid)
            return self.catalog
        self.stack.enter_context(patch.object(subject, "catalog_for_session", side_effect=scoped_catalog))
        app = Flask(__name__)
        app.config.update(TESTING=True)
        app.register_blueprint(subject.bp)
        self.client = app.test_client()
        self.root = "/api/session/sid/datasets"

    def plan(self):
        response = self.client.post(self.root + "/plans", data={
            "file": (io.BytesIO(b"id,amount\n1,10\n2,20\n3,30\n"), "synthetic.csv")
        }, content_type="multipart/form-data")
        self.assertEqual(200, response.status_code, response.get_json())
        self.assertNotIn(str(self.directory.name), response.get_data(as_text=True))
        return response.get_json()["plan_id"]

    def test_import_replay_preview_and_activation_use_one_complete_snapshot(self):
        plan_id = self.plan()
        body = {"plan_id": plan_id, "idempotency_key": "same-submit"}
        first = self.client.post(self.root + "/imports", json=body)
        repeated = self.client.post(self.root + "/imports", json=body)
        self.assertEqual(202, first.status_code, first.get_json())
        self.assertEqual(first.get_json()["job_id"], repeated.get_json()["job_id"])
        self.assertEqual(1, len(self.session.job_runner.workers))
        self.session.job_runner.run(first.get_json()["job_id"])
        status = self.client.get(self.root + "/imports/" + first.get_json()["operation_id"])
        self.assertEqual("succeeded", status.get_json()["status"])
        dataset_id = status.get_json()["result"]["dataset_id"]
        preview = self.client.get(self.root + f"/{dataset_id}/preview?limit=2")
        self.assertEqual(200, preview.status_code, preview.get_json())
        self.assertEqual(2, len(preview.get_json()["preview"]["rows"]))
        self.assertEqual(1, len(self.client.get(self.root + f"/{dataset_id}/versions").get_json()["versions"]))
        activated = self.client.post(self.root + f"/{dataset_id}/activate", json={})
        self.assertEqual(200, activated.status_code, activated.get_json())
        self.assertTrue(activated.get_json()["reused"])
        self.assertEqual(1, len(self.session._sources))
        for entry in self.session._sources:
            entry["source"].close()

    def test_same_idempotency_key_cannot_select_another_plan(self):
        first, second = self.plan(), self.plan()
        self.assertEqual(202, self.client.post(self.root + "/imports", json={"plan_id": first, "idempotency_key": "unique"}).status_code)
        response = self.client.post(self.root + "/imports", json={"plan_id": second, "idempotency_key": "unique"})
        self.assertEqual(409, response.status_code, response.get_json())

    def test_arbitrary_path_and_missing_workspace_are_rejected(self):
        response = self.client.post(self.root + "/plans", json={"input_path": "/private/data.csv"})
        self.assertEqual(400, response.status_code)
        response = self.client.post(self.root + "/plans", json={"workspace_file": "data.csv"})
        self.assertEqual(403, response.status_code)

    def test_ownership_rejection_precedes_uploaded_file_access(self):
        with patch.object(subject, "check_session_ownership", return_value=(False, "other")):
            response = self.client.get(self.root)
        self.assertEqual(403, response.status_code)


if __name__ == "__main__":
    unittest.main()
