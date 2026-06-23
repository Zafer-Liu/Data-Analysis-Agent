# -*- coding: utf-8 -*-
"""Data preview policy: only remote SQL sources require table selection."""
import unittest
import uuid

from api import create_app
from api.chat import _resolve_data_context
from api.state import session_manager
from data.sources.sql import SQLDataSource


class _LocalSource:
    name = "uploaded.xlsx"

    def list_tables(self):
        return ["Sheet1"]

    def get_preview(self):
        return [{"name": "Sheet1", "columns": ["value"], "total_rows": 2}]


class _RemoteSQLSource(SQLDataSource):
    """Metadata-only SQL double; deliberately avoids a real external DB."""
    name = "remote-db"

    def __init__(self):
        pass

    def list_tables(self):
        return ["orders", "customers"]

    def list_catalog_tables(self):
        return ["orders", "customers"]

    def get_preview(self):
        return [
            {"name": "orders", "columns": ["id"], "total_rows": None},
            {"name": "customers", "columns": ["id"], "total_rows": None},
        ]


class TestPreviewSelectionPolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config["TESTING"] = True
        cls.client = cls.app.test_client()

    def setUp(self):
        self.sid = f"preview-policy-{uuid.uuid4().hex}"
        self.session = session_manager.get_or_create(self.sid)

    def tearDown(self):
        session_manager.remove(self.sid)

    def test_uploaded_file_preview_has_no_selection_requirement(self):
        self.session.add_source(_LocalSource())
        response = self.client.get(f"/api/session/{self.sid}/preview")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload["requires_table_selection"])
        self.assertFalse(payload["tables"][0]["selectable_for_analysis"])

    def test_only_remote_sql_tables_are_selectable_in_mixed_preview(self):
        self.session.add_source(_LocalSource())
        self.session.add_source(_RemoteSQLSource())
        response = self.client.get(f"/api/session/{self.sid}/preview")
        payload = response.get_json()
        self.assertTrue(payload["requires_table_selection"])
        selectable = {item["name"]: item["selectable_for_analysis"] for item in payload["tables"]}
        self.assertFalse(selectable["Sheet1"])
        self.assertTrue(selectable["orders"])
        self.assertTrue(selectable["customers"])

    def test_chat_context_rejects_local_tables_and_accepts_remote_sql(self):
        local_id = self.session.add_source(_LocalSource())
        sql_id = self.session.add_source(_RemoteSQLSource())
        context = _resolve_data_context(self.session, {
            "tables": [
                {"source_id": local_id, "table": "Sheet1"},
                {"source_id": sql_id, "table": "orders"},
            ]
        })
        self.assertEqual(len(context["tables"]), 1)
        self.assertEqual(context["tables"][0]["source_id"], sql_id)
        self.assertEqual(context["tables"][0]["table"], "orders")


if __name__ == "__main__":
    unittest.main()
