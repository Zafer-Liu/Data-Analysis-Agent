"""Ontology import/lifecycle regressions. Run only with explicit validation approval."""
import copy
import sqlite3
import unittest

from Function.Knowledge.ontology_parser import (
    OntologyValidationError, parse_ontology, validate_ontology,
)
from Function.Knowledge.ontology_store import (
    OntologyStore, init_ontology_tables, validate_binding,
)


def document():
    return {
        "version": "1.0.0", "domain": {"name": "成绩分析", "policies": {"nulls": "排除空成绩"}},
        "entities": {
            "Score": {"label": "成绩", "source": {"table": "score"},
                      "properties": {"value": {"type": "decimal"}}},
            "Student": {"label": "学生", "source": {"table": "student"},
                        "properties": {"name": {"type": "string"}}},
        },
        "relations": [{"name": "StudentHasScore", "from": "Student", "to": "Score",
                       "join": {"from_table": "student", "from_column": "id",
                                "to_table": "score", "to_column": "student_id"},
                       "cardinality": "one_to_many", "fanout": True}],
        "metrics": {"average_score": {"label": "平均分", "synonyms": ["均分"],
                                      "entity": "Score", "expression": "AVG(value)"}},
        "security": {"fail_closed": True},
    }


class OntologyParserTests(unittest.TestCase):
    def test_parses_yaml_and_normalizes_numeric_enum_keys(self):
        parsed = parse_ontology("""version: 1.0.0
domain:
  name: 学生状态
entities:
  Student:
    source:
      table: student
    properties:
      status:
        type: enum
        value_map:
          1: 在读
""")
        self.assertEqual(parsed["format"], "ontology")
        self.assertEqual(parsed["ontology"]["entities"]["Student"]["properties"]["status"]["value_map"], {"1": "在读"})

    def test_rejects_duplicate_keys_and_aliases(self):
        for text in ("version: 1.0.0\nversion: 1.1.0", "domain: &domain {name: x}\ncopy: *domain"):
            with self.subTest(text=text), self.assertRaises(OntologyValidationError):
                parse_ontology(text)

    def test_reference_errors_have_field_paths(self):
        data = document()
        data["relations"][0]["to"] = "Missing"
        with self.assertRaises(OntologyValidationError) as raised:
            validate_ontology(data)
        self.assertTrue(any("relations[0].to" in item["path"] for item in raised.exception.errors))

    def test_security_is_advisory_and_payload_is_detached(self):
        data = document()
        parsed = validate_ontology(data)
        self.assertTrue(any(item["path"] == "$.security" for item in parsed["warnings"]))
        parsed["ontology"]["domain"]["name"] = "changed"
        self.assertEqual(data["domain"]["name"], "成绩分析")

    def test_enforces_size_and_finite_scalars(self):
        with self.assertRaises(OntologyValidationError):
            parse_ontology("#" * (256 * 1024 + 1))
        data = document()
        data["extension"] = float("nan")
        with self.assertRaises(OntologyValidationError):
            validate_ontology(data)


class OntologyLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("CREATE TABLE knowledge_categories(id INTEGER PRIMARY KEY, name TEXT, enabled INTEGER)")
        self.conn.execute("INSERT INTO knowledge_categories VALUES(1, '默认业务', 1)")
        init_ontology_tables(self.conn)
        self.store = OntologyStore(self.conn)
        self.source = {"source_id": "source-a", "session_id": "session-a", "source_key": "physical-a",
                       "source_name": "School", "index": 1,
                       "tables": {"score": ["value", "student_id"], "student": ["id", "name"]}}
        self.binding, _ = validate_binding(document(), self.source)

    def tearDown(self):
        self.conn.close()

    def save(self, data=None, **kwargs):
        return self.store.save(validate_ontology(data or document()), filename=kwargs.pop("filename", "school.yaml"),
                               source_name="school.yaml", category_id=1, binding=self.binding,
                               embed=False, **kwargs)

    def test_replacement_and_retry_keep_identity_and_remove_old_nodes(self):
        old = self.save()
        changed = document()
        changed["version"] = "1.1.0"
        changed["metrics"] = {"new_average": changed["metrics"]["average_score"]}
        replacement = self.save(changed, filename="new.yaml", ontology_id=old["id"])
        self.assertEqual(replacement["id"], old["id"])
        paths = {row[0] for row in self.conn.execute("SELECT path FROM ontology_nodes")}
        self.assertIn("metrics.new_average", paths)
        self.assertNotIn("metrics.average_score", paths)
        self.assertEqual(self.save(changed, filename="new.yaml")["id"], old["id"])

    def test_search_expands_relations_and_respects_binding_and_enable_state(self):
        saved = self.save()
        matches = self.store.search("学生均分", [self.source])
        self.assertEqual(len(matches), 1)
        paths = {item["path"] for item in matches[0]["entries"]}
        self.assertIn("metrics.average_score", paths)
        self.assertIn("relations.0", paths)
        self.assertIn("domain", paths)
        other = {**self.source, "source_id": "other", "source_key": "physical-b"}
        self.assertEqual(self.store.search("学生均分", [other]), [])
        self.store.update(saved["id"], enabled=False)
        self.assertEqual(self.store.search("学生均分", [self.source]), [])
        self.store.update(saved["id"], enabled=True)
        self.conn.execute("UPDATE knowledge_categories SET enabled=0")
        self.assertEqual(self.store.search("学生均分", [self.source]), [])

    def test_delete_removes_structure_and_search_projection(self):
        saved = self.save()
        self.assertTrue(self.store.delete(saved["id"]))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM ontology_nodes").fetchone()[0], 0)
        self.assertEqual(self.store.search("学生均分", [self.source]), [])

    def test_snapshot_restores_stable_identity_and_binding(self):
        saved = self.save()
        snapshot = {"ontologies": self.store.export()}
        prepared = self.store.prepare_snapshot(snapshot)
        self.store.restore(prepared, {"默认业务": 1})
        restored = self.store.get(saved["id"])
        self.assertEqual(restored["document"], saved["document"])
        self.assertEqual(restored["binding"], saved["binding"])
        self.assertEqual(restored["security_status"], "advisory_only")
        invalid = copy.deepcopy(snapshot)
        invalid["ontologies"][0]["document"]["relations"][0]["to"] = "Missing"
        with self.assertRaises(OntologyValidationError):
            self.store.prepare_snapshot(invalid)
        self.assertIsNotNone(self.store.get(saved["id"]))

    def test_binding_reports_missing_fields_and_rejects_nonexistent_mapping_keys(self):
        source = {**self.source, "tables": {"score": ["student_id"]}}
        _, warnings = validate_binding(document(), source)
        self.assertTrue(any("value" in item["message"] for item in warnings))
        self.assertTrue(any("student" in item["path"] for item in warnings))
        with self.assertRaises(ValueError):
            validate_binding(document(), self.source, {"unknown": "score"})

    def test_exact_binding_wins_and_ambiguous_reconnections_require_rebind(self):
        other = {**self.source, "source_id": "source-b"}
        self.assertEqual(self.store._bound_source(self.binding, [other, self.source])["source_id"], "source-a")
        binding = {**self.binding, "session_id": "old-session"}
        self.assertIsNone(self.store._bound_source(binding, [other, self.source]))


if __name__ == "__main__":
    unittest.main()
