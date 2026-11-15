"""Knowledge blueprint integration with isolated storage and no model calls."""
from contextlib import contextmanager
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from flask import Flask
from Function.Knowledge.knowledge_base import KnowledgeBase
from Function.Knowledge.ontology_parser import parse_ontology
from Function.Knowledge.ontology_store import physical_tables

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "ontology_school_score" / "school_score.yaml"


class OntologyApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.raw = FIXTURE.read_bytes()
        self.document = parse_ontology(self.raw.decode("utf-8-sig"))["ontology"]
        tables = {name: sorted(columns) for name, columns in physical_tables(self.document).items()}
        source = types.SimpleNamespace(
            name="School", _db_path=self.directory / "school.db",
            get_preview=lambda: [{"name": name, "columns": columns} for name, columns in tables.items()],
            list_tables=lambda: list(tables),
        )
        session = types.SimpleNamespace(_sources=[{"id": "school", "source": source}])
        session._active_entries = lambda: session._sources
        self.state = types.ModuleType("api.state")
        self.state.session_manager = types.SimpleNamespace(get=lambda sid: session if sid == "owned" else None)
        self.state.config_manager = types.SimpleNamespace()
        self.state.require_session_ownership = lambda fn: fn
        self.state.check_session_ownership = Mock(return_value=(True, "test-user"))
        prompts = types.ModuleType("agent.prompts")
        prompts.TEMP_PROMPT_MAX_CHARS = 4000
        prompts.strip_temp_prompt_thinking = lambda value: value
        self.modules = patch.dict(sys.modules, {"api.state": self.state, "agent.prompts": prompts})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        spec = importlib.util.spec_from_file_location("api._ontology_test_subject", ROOT / "api" / "knowledge.py")
        self.api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.api)
        self.api._kb_dir = lambda: self.directory

        @contextmanager
        def scoped_kb():
            with KnowledgeBase(db_path=self.directory / "knowledge.db") as kb:
                yield kb

        self.api._kb = scoped_kb
        self.api._get_client = Mock(side_effect=AssertionError("YAML import must not call an LLM"))
        self.batch = patch("Function.Knowledge.knowledge_base._embed_batch", side_effect=lambda texts: [[] for _ in texts])
        self.batch.start()
        self.addCleanup(self.batch.stop)
        self.signature = patch("Function.Knowledge.knowledge_base._embedding_signature", return_value="api-test")
        self.signature.start()
        self.addCleanup(self.signature.stop)
        self.query = patch("Function.Knowledge.knowledge_base._embed_query", return_value=[])
        self.query.start()
        self.addCleanup(self.query.stop)
        app = Flask(__name__)
        app.config.update(TESTING=True)
        app.register_blueprint(self.api.bp)
        self.client = app.test_client()

    def url(self, suffix):
        return "/api/knowledge/" + suffix + ("&" if "?" in suffix else "?") + "session_id=owned"

    def upload(self, raw=None):
        response = self.client.post(self.url("parse"), data={
            "session_id": "owned", "file": (io.BytesIO(self.raw if raw is None else raw), "school_score.yaml"),
        }, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def confirm(self, upload, **fields):
        response = self.client.post(self.url("confirm"), json={
            "filename": upload["filename"], "source_name": "school_score.yaml",
            "source_id": "school", "category_id": 1, **fields,
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()["ontology"]

    def test_upload_preview_confirm_download_and_definition_without_model(self):
        uploaded = self.upload()
        self.assertEqual(uploaded["summary"]["properties"], 52)
        self.assertIn("authorized_transcript", uploaded["expected_tables"])
        self.assertTrue(any(row["name"] == "authorized_transcript" and row["kind"] == "query_surface"
                            for row in uploaded["table_requirements"]))
        saved = self.confirm(uploaded)
        response = self.client.get(self.url(f"ontologies/{saved['id']}/source"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(parse_ontology(response.data.decode("utf-8"))["ontology"], self.document)
        definition = self.client.get(self.url(f"ontologies/{saved['id']}/definition?path=metrics.gpa"))
        self.assertEqual(definition.status_code, 200)
        self.assertIn("gpa_point * credit", definition.get_json()["definition"]["expression"])
        self.api._get_client.assert_not_called()

    def test_confirm_retry_is_idempotent_and_replacement_removes_old_source(self):
        uploaded = self.upload()
        saved = self.confirm(uploaded)
        self.assertEqual(self.confirm(uploaded)["id"], saved["id"])
        new = self.upload(self.raw.replace(b"version: 1.0.0", b"version: 1.1.0", 1))
        replacement = self.confirm(new, ontology_id=saved["id"])
        self.assertEqual(replacement["id"], saved["id"])
        self.assertEqual(replacement["version"], "1.1.0")
        self.assertFalse((self.directory / uploaded["filename"]).exists())

    def test_preview_retrieval_disable_and_delete(self):
        saved = self.confirm(self.upload())
        path = f"ontologies/{saved['id']}"
        preview = self.client.post(self.url(path + "/preview"), json={"question": "我的期末成绩"})
        self.assertEqual(preview.status_code, 200)
        self.assertTrue(preview.get_json()["bound_to_active_source"])
        paths = {entry["path"] for entry in preview.get_json()["matches"][0]["entries"]}
        self.assertIn("entities.Score.properties.final_exam_score", paths)
        self.assertEqual(self.client.put(self.url(path), json={"enabled": False}).status_code, 200)
        disabled = self.client.post(self.url(path + "/preview"), json={"question": "数学平均分"})
        self.assertEqual(disabled.get_json()["matches"], [])
        self.assertEqual(self.client.delete(self.url(path)).status_code, 200)
        self.assertEqual(self.client.get(self.url(path)).status_code, 404)
        self.assertFalse((self.directory / saved["filename"]).exists())

    def test_snapshot_recovery_keeps_canonical_source_when_raw_file_disappears(self):
        saved = self.confirm(self.upload())
        exported = self.client.get(self.url("ontologies?export=1")).get_json()
        response = self.client.post(self.url("restore"), json={"snapshot": {
            "version": 2, "categories": [{"name": "默认业务", "enabled": True}],
            "metrics": [], "rules": [], "notes": [], "ontologies": exported,
        }})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertFalse((self.directory / saved["filename"]).exists())
        restored = self.client.get(self.url(f"ontologies/{saved['id']}")).get_json()
        self.assertEqual(restored["document"], self.document)
        self.assertEqual(restored["binding"], saved["binding"])
        self.assertEqual(self.client.get(self.url(f"ontologies/{saved['id']}/source")).status_code, 200)
        self.api._get_client.assert_not_called()

    def test_malformed_snapshot_preserves_existing_source_and_index(self):
        saved = self.confirm(self.upload())
        exported = self.client.get(self.url("ontologies?export=1")).get_json()
        exported[0]["binding"]["column_mapping"] = {"student": {"not_declared": "id"}}
        with self.assertLogs(self.api.log.name, level="ERROR"):
            response = self.client.post(self.url("restore"), json={"snapshot": {"ontologies": exported}})
        self.assertEqual(response.status_code, 400)
        self.assertTrue((self.directory / saved["filename"]).exists())
        self.assertEqual(self.client.get(self.url(f"ontologies/{saved['id']}")).status_code, 200)

    def test_bad_requests_are_field_errors_not_server_errors(self):
        saved = self.confirm(self.upload())
        path = f"ontologies/{saved['id']}"
        self.assertEqual(self.client.put(self.url(path), json=[]).status_code, 400)
        self.assertEqual(self.client.post(self.url("confirm"), json=[]).status_code, 400)
        self.assertEqual(self.client.post(self.url(path + "/preview"), json={"question": []}).status_code, 400)
        bad = self.client.put(self.url(path), json={"column_mapping": {"student": {"name": "absent"}}})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("absent", bad.get_json()["error"])
        self.assertEqual(self.client.get(self.url(path + "/definition?path=__class__")).status_code, 404)

    def test_source_access_denial_never_leaves_staged_yaml(self):
        self.state.check_session_ownership.return_value = (False, "someone-else")
        response = self.client.post(self.url("parse"), data={
            "session_id": "owned", "file": (io.BytesIO(self.raw), "school_score.yaml"),
        }, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(list(self.directory.glob("*.yaml")), [])
        self.api._get_client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
