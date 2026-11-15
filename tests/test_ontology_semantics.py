"""Real-format retrieval and mapping regressions; run Python with -I for fixture reads."""
import json
from pathlib import Path
import sqlite3
import unittest

from Function.Knowledge.ontology_parser import parse_ontology
from Function.Knowledge.ontology_store import OntologyStore, init_ontology_tables, physical_tables, validate_binding

FIXTURE = Path(__file__).parent / "fixtures" / "ontology_school_score" / "school_score.yaml"


class SchoolOntologySemanticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parsed = parse_ontology(FIXTURE.read_text(encoding="utf-8-sig"))

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("CREATE TABLE knowledge_categories(id INTEGER PRIMARY KEY, name TEXT, enabled INTEGER)")
        self.conn.execute("INSERT INTO knowledge_categories VALUES(1,'school',1)")
        init_ontology_tables(self.conn)
        self.store = OntologyStore(self.conn)
        self.doc = self.parsed["ontology"]
        tables = {name: sorted(columns) for name, columns in physical_tables(self.doc).items()
                  if name not in self.doc["query_surface"]}
        tables["authorized_transcript"] = self.doc["query_surface"]["authorized_transcript"]["columns"]
        self.source = {"source_id": "school", "session_id": "session", "source_key": "school-physical",
                       "source_name": "School", "index": 1, "tables": tables}
        self.binding, _ = validate_binding(self.doc, self.source)
        self.record = self.store.save(self.parsed, filename="school.yaml", source_name="school_score.yaml",
                                      category_id=1, binding=self.binding, embed=False)

    def tearDown(self):
        self.conn.close()

    def context(self, question, **kwargs):
        matches = self.store.search(question, [self.source], **kwargs)
        self.assertTrue(matches, question)
        result = matches[0]
        definitions = {row["path"]: json.loads(row["content"]) for row in result["entries"]}
        return result, definitions

    def test_property_alias_scope_and_detail_intent_survive_retrieval(self):
        context, definitions = self.context("我的期末成绩是多少")
        self.assertIn("entities.Score.properties.final_exam_score", definitions)
        self.assertEqual(definitions["synonyms.期末成绩"]["target"], "Score.final_exam_score")
        self.assertEqual(definitions["synonyms.我的成绩"]["scope"], "current_user")
        self.assertIn("intents.detail_query", definitions)
        self.assertNotIn("metrics.avg_total_score", definitions)
        self.assertTrue(context["semantic_context"]["scoped_aliases"])

    def test_gpa_uses_weighted_metric_and_effective_surface_candidate(self):
        context, definitions = self.context("计算数学 GPA")
        self.assertIn("metrics.gpa", definitions)
        self.assertIn("gpa_point * credit", definitions["metrics.gpa"]["expression"])
        self.assertIn("query_surface.authorized_effective_score", definitions)
        self.assertNotIn("entities.Transcript.properties.gpa", definitions)
        surfaces = context["semantic_context"]["query_surface_candidates"]
        effective = next(item for item in surfaces if item["name"] == "authorized_effective_score")
        self.assertTrue(effective["preferred_for_entity"])
        self.assertEqual(effective["mapped_table"], "v_effective_score")
        self.assertFalse(effective["authorization_verified"])

    def test_bucket_bounds_defaults_and_clarification_remain_available(self):
        context, definitions = self.context("高二班数学分数段分布", max_chars=32000)
        self.assertIn("dimensions.score_band", definitions)
        self.assertEqual(definitions["dimensions.score_band"]["buckets"][1]["max"], 89.999)
        self.assertIn("intents.distribution", definitions)
        self.assertEqual(definitions["intents.distribution"]["defaults"]["attempt_scope"], "NORMAL")
        self.assertEqual(context["semantic_context"]["domain_defaults"]["timezone"], "Asia/Shanghai")
        self.assertEqual(context["semantic_context"]["domain_defaults"]["default_limit"], 50)
        _, ranking = self.context("班级数学成绩排名前10", max_chars=32000)
        self.assertIn("intents.ranking", ranking)
        self.assertTrue(ranking["intents.ranking"]["clarification"])

    def test_logical_surface_without_declared_source_can_be_bound(self):
        self.assertEqual(self.binding["mapping"]["authorized_transcript"], "authorized_transcript")
        context, definitions = self.context("生成我的成绩单", max_chars=32000)
        self.assertIn("query_surface.authorized_transcript", definitions)
        self.assertIn("intents.transcript_report", definitions)
        self.assertEqual(context["security_status"], "advisory_only")
        self.assertTrue(context["semantic_context"]["authorization"]["fail_closed_requested"])

    def test_nonphysical_concept_and_many_to_many_join_are_preserved(self):
        _, definitions = self.context("文科平均分", max_chars=32000)
        self.assertIn("entities.文科", definitions)
        self.assertEqual(definitions["entities.文科"]["source"]["table"], "")
        relation = self.store.definition(self.record["id"], "relations.8")
        self.assertEqual(relation["definition"]["cardinality"], "many_to_many")
        self.assertEqual(relation["definition"]["join"]["via_table"], "teaching_class_class")

    def test_column_mapping_roundtrip_and_invalid_destination(self):
        changed = {**self.source, "tables": {**self.source["tables"],
                                           "student": ["id", "student_no", "full_name", "gender", "class_id", "status"]}}
        binding, warnings = validate_binding(self.doc, changed, column_mapping={"student": {"name": "full_name"}})
        self.assertEqual(binding["column_mapping"]["student"]["name"], "full_name")
        self.assertFalse(any(item["path"] == "binding.tables.student" for item in warnings))
        self.store.update(self.record["id"], binding=binding)
        restored = self.store.prepare_snapshot({"ontologies": self.store.export()})
        self.store.restore(restored, {"school": 1})
        self.assertEqual(self.store.get(self.record["id"])["binding"]["column_mapping"]["student"]["name"], "full_name")
        with self.assertRaises(ValueError):
            validate_binding(self.doc, changed, column_mapping={"student": {"name": "does_not_exist"}})

    def test_explicit_unmapping_survives_and_does_not_auto_remap(self):
        binding, _ = validate_binding(self.doc, self.source, {"authorized_score": ""})
        self.store.update(self.record["id"], binding=binding)
        context, _ = self.context("数学平均分", max_chars=32000)
        self.assertNotIn("authorized_score", context["mapping"])
        self.assertIn("authorized_score", context["unavailable_tables"])

    def test_unresolved_column_is_preserved_in_model_context(self):
        binding, _ = validate_binding(self.doc, self.source, column_mapping={"student": {"name": ""}})
        self.store.update(self.record["id"], binding=binding)
        context, _ = self.context("学生平均分", max_chars=32000)
        self.assertEqual(context["column_mapping"]["student"]["name"], "")

    def test_existing_projection_upgrades_without_changing_version_or_identity(self):
        self.conn.execute("DELETE FROM ontology_nodes WHERE kind IN ('properties','synonyms')")
        self.conn.execute("UPDATE ontologies SET projection_version=0")
        self.conn.commit()
        _, definitions = self.context("我的期末成绩是多少")
        self.assertIn("entities.Score.properties.final_exam_score", definitions)
        record = self.store.get(self.record["id"])
        self.assertEqual(record["id"], self.record["id"])
        self.assertEqual(record["version"], "1.0.0")
        self.assertGreaterEqual(record["projection_version"], 2)

    def test_budget_is_real_json_size_and_field_lookup_has_provenance(self):
        matches = self.store.search("数学平均分", [self.source], max_chars=4000)
        self.assertLessEqual(sum(len(json.dumps(item, ensure_ascii=False, separators=(",", ":"))) for item in matches), 4000)
        self.assertTrue(matches)
        definition = self.store.definition(self.record["id"], "metrics.gpa")
        self.assertEqual(definition["path"], "metrics.gpa")
        self.assertEqual(definition["source_name"], "school_score.yaml")
        self.assertEqual(definition["version"], "1.0.0")
        self.assertIsNone(self.store.definition(self.record["id"], "__class__"))


if __name__ == "__main__":
    unittest.main()
