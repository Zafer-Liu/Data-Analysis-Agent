"""Schema coverage for school_score ontologies; no store, API, or UI dependencies."""
from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from unittest import mock

import yaml


_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "ontology_schema_parser", _ROOT / "Function" / "Knowledge" / "ontology_parser.py",
)
_parser = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_parser)
OntologyValidationError = _parser.OntologyValidationError
parse_ontology = _parser.parse_ontology
validate_ontology = _parser.validate_ontology
_SCHOOL = _ROOT / "tests" / "fixtures" / "ontology_school_score" / "school_score.yaml"


def _document(**sections):
    return {
        "version": "1.0.0",
        "domain": {"name": "School"},
        "entities": {
            "Score": {
                "source": {"table": "score", "primary_key": "id", "business_key": "student_id+course_id"},
                "properties": {"total_score": {"type": "decimal", "role": "measure"}},
            },
        },
        **sections,
    }


def _dimension(buckets):
    return {"entity": "Score", "property": "total_score", "type": "bucket", "buckets": buckets}


class OntologySchemaTests(unittest.TestCase):
    def assert_invalid(self, document, path):
        with self.assertRaises(OntologyValidationError) as caught:
            validate_ontology(document)
        errors = caught.exception.errors
        self.assertTrue(errors)
        self.assertTrue(any(error["path"].startswith(path) for error in errors), errors)
        self.assertTrue(all(set(error) == {"path", "message"} and error["message"] for error in errors))

    def test_domain_policies_and_limits(self):
        for policies in ([], {"visible": False}, {"visible": None}, {"visible": []}, {"visible": {}}, {"": "rule"}, {1: "rule"}):
            with self.subTest(policies=policies):
                document = _document()
                document["domain"]["policies"] = policies
                self.assert_invalid(document, "$.domain.policies")
        for limit in (True, False, 0, -1, 1.5, "50", None, 10_001):
            with self.subTest(limit=limit):
                document = _document()
                document["domain"]["default_limit"] = limit
                self.assert_invalid(document, "$.domain.default_limit")
        for limit in (1, 50, 10_000):
            document = _document()
            document["domain"].update(default_limit=limit, policies={"visible": "score_status IN ('PUBLISHED','LOCKED')"})
            self.assertEqual(validate_ontology(document)["ontology"], document)

    def test_timezone_validation_and_missing_windows_database(self):
        for timezone in (1, [], {}, "", "../Asia/Shanghai", "/etc/passwd", "Asia//Shanghai", "Mars/Olympus"):
            with self.subTest(timezone=timezone):
                document = _document()
                document["domain"]["timezone"] = timezone
                self.assert_invalid(document, "$.domain.timezone")
        document = _document()
        document["domain"]["timezone"] = "Asia/Shanghai"
        with mock.patch.object(_parser, "ZoneInfo", side_effect=_parser.ZoneInfoNotFoundError("no tzdata")):
            preview = validate_ontology(document)
        self.assertEqual(preview["ontology"]["domain"]["timezone"], "Asia/Shanghai")
        self.assertTrue(any(w["path"] == "$.domain.timezone" for w in preview["warnings"]))

        def existing_database(name):
            if name == "UTC":
                return object()
            raise _parser.ZoneInfoNotFoundError(name)

        document["domain"]["timezone"] = "Asia/Unknown_City"
        with mock.patch.object(_parser, "ZoneInfo", side_effect=existing_database):
            self.assert_invalid(document, "$.domain.timezone")

    def test_source_keys_references_and_concepts_remain_compatible(self):
        document = _document()
        document["entities"]["Score"]["properties"]["student_id"] = {"type": "reference", "reference": "Student"}
        document["entities"]["Student"] = {"source": {"table": "student", "primary_key": "id"}}
        document["entities"]["文科"] = {"label": "文科", "source": {"table": "", "primary_key": "id"}, "properties": {}}
        preview = parse_ontology(json.dumps(document, ensure_ascii=False))
        self.assertEqual(preview["ontology"], document)
        self.assertEqual(preview["summary"]["properties"], 2)
        self.assertEqual(preview["summary"]["concepts"], 1)
        self.assertTrue(any(w["path"] == "$.entities.文科.source.table" for w in preview["warnings"]))

    def test_property_aggregation_consistency(self):
        for values, path in (
            ({"default_aggregation": "sum", "allowed_aggregations": ["avg", "count"]}, "default_aggregation"),
            ({"allowed_aggregations": []}, "allowed_aggregations"),
            ({"allowed_aggregations": ["avg", "avg"]}, "allowed_aggregations[1]"),
            ({"allowed_aggregations": [{}]}, "allowed_aggregations[0]"),
            ({"allowed_aggregations": {}}, "allowed_aggregations"),
            ({"default_aggregation": []}, "default_aggregation"),
        ):
            with self.subTest(values=values):
                document = _document()
                document["entities"]["Score"]["properties"]["total_score"].update(values)
                self.assert_invalid(document, f"$.entities.Score.properties.total_score.{path}")
        document = _document()
        document["entities"]["Score"]["properties"]["total_score"].update(
            default_aggregation="weighted_avg", allowed_aggregations=["weighted_avg", "distribution", "count"],
        )
        self.assertEqual(validate_ontology(document)["ontology"], document)

    def test_bucket_shape_bounds_and_labels(self):
        for buckets, suffix in (
            ([], ""), ({}, ""), ([[]], "[0]"),
            ([{"label": "low", "max": 60}], "[0].min"),
            ([{"label": "low", "min": 0}], "[0].max"),
            ([{"label": "low", "min": True, "max": 60}], "[0].min"),
            ([{"label": "low", "min": "0", "max": 60}], "[0].min"),
            ([{"label": "low", "min": 60, "max": 0}], "[0]"),
            ([{"label": [], "min": 0, "max": 60}], "[0].label"),
            ([{"label": "same", "min": 0, "max": 59}, {"label": "same", "min": 60, "max": 100}], "[1].label"),
        ):
            with self.subTest(buckets=buckets):
                self.assert_invalid(_document(dimensions={"band": _dimension(buckets)}), f"$.dimensions.band.buckets{suffix}")
        dimension = _dimension([])
        del dimension["buckets"]
        self.assert_invalid(_document(dimensions={"band": dimension}), "$.dimensions.band.buckets")

    def test_bucket_diagnostics_preserve_order_and_fractional_bounds(self):
        document = _document(dimensions={"band": _dimension([
            {"label": "90-100", "min": 90, "max": 100},
            {"label": "80-89", "min": 80, "max": 89.999},
        ])})
        preview = validate_ontology(document)
        self.assertEqual(preview["ontology"], document)
        self.assertTrue(any("间隙" in warning["message"] for warning in preview["warnings"]))
        document["dimensions"]["band"]["buckets"] = [
            {"label": "outer", "min": 0, "max": 100},
            {"label": "inner", "min": 10, "max": 20},
            {"label": "also_inside", "min": 30, "max": 40},
        ]
        warnings = validate_ontology(document)["warnings"]
        self.assertEqual(sum("重叠" in warning["message"] for warning in warnings), 2)
        self.assertFalse(any("间隙" in warning["message"] for warning in warnings))

    def test_cardinality_fanout_conflicts_are_advisory(self):
        for cardinality, expected in (("one_to_one", False), ("many_to_one", False), ("one_to_many", True), ("many_to_many", True)):
            for fanout in (False, True):
                with self.subTest(cardinality=cardinality, fanout=fanout):
                    document = _document(relations=[{
                        "name": "ScoreLink", "from": "Score", "to": "Score",
                        "join": {"from_table": "score", "from_column": "id", "to_table": "score", "to_column": "id"},
                        "cardinality": cardinality, "fanout": fanout,
                    }])
                    warnings = validate_ontology(document)["warnings"]
                    self.assertEqual(any(w["path"] == "$.relations[0].fanout" for w in warnings), fanout != expected)

    def test_query_surface_columns_and_source_aliases(self):
        document = _document(query_surface={"authorized": {"source_view": "v_score", "view": "v_other", "columns": ["total_score"]}})
        preview = validate_ontology(document)
        self.assertTrue(any(w["path"] == "$.query_surface.authorized" and "别名" in w["message"] for w in preview["warnings"]))
        self.assertEqual(preview["ontology"], document)
        document["query_surface"]["authorized"]["view"] = "v_score"
        self.assertFalse(any(w["path"] == "$.query_surface.authorized" for w in validate_ontology(document)["warnings"]))
        for columns in (["total_score", "total_score"], [{}], "total_score"):
            document["query_surface"]["authorized"]["columns"] = columns
            self.assert_invalid(document, "$.query_surface.authorized.columns")

    def test_security_references_identifiers_and_default_role(self):
        def secured():
            return _document(
                query_surface={"authorized": {"columns": ["total_score"]}},
                security={"fail_closed": True, "default_role": "student", "roles": {"student": {
                    "required_context": ["student_no"], "allowed_tables": ["authorized"], "row_scope": "student_no = :student_no",
                }}},
            )

        document = secured()
        self.assertEqual(validate_ontology(document)["ontology"]["security"], document["security"])
        for value in ("true", 1, None, []):
            document = secured()
            document["security"]["fail_closed"] = value
            self.assert_invalid(document, "$.security.fail_closed")
        for value in ("unknown", [], None):
            document = secured()
            document["security"]["default_role"] = value
            self.assert_invalid(document, "$.security.default_role")
        for context in (["student no"], ["student.no"], [":student_no"], ["student-no"], ["1student"], [{}], ["student_no", "student_no"], "student_no"):
            document = secured()
            document["security"]["roles"]["student"]["required_context"] = context
            self.assert_invalid(document, "$.security.roles.student.required_context")
        for tables in (["undeclared"], [{}], ["authorized", "authorized"], "authorized"):
            document = secured()
            document["security"]["roles"]["student"]["allowed_tables"] = tables
            self.assert_invalid(document, "$.security.roles.student.allowed_tables")
        document = secured()
        document["query_surface"] = {}
        self.assert_invalid(document, "$.security.roles.student.allowed_tables")
        del document["query_surface"]
        self.assertEqual(validate_ontology(document)["ontology"], document)
        del document["security"]["roles"]
        self.assert_invalid(document, "$.security.default_role")

    def test_intent_defaults_accept_json_values_and_runtime_slots(self):
        for value in (None, False, 42, 1.25, "NORMAL", [], ["PUBLISHED", "LOCKED"], [1, True, None], [[1, 2], 3]):
            with self.subTest(value=value):
                document = _document(intents={"ranking": {
                    "required_slots": ["custom_value"], "optional_slots": ["course"],
                    "defaults": {"custom_value": value, "score_status": ["PUBLISHED", "LOCKED"], "attempt_scope": "NORMAL"},
                    "clarification": [{"when": "ranking_scope is null or custom_condition()", "question": "Which scope?"}],
                }})
                self.assertEqual(validate_ontology(document)["ontology"], document)

    def test_intent_slot_scope_and_malformed_defaults(self):
        for intent, suffix in (
            ({"defaults": {"total_score": 90}}, "defaults.total_score"),
            ({"required_slots": ["own"], "defaults": {"own": {"nested": 1}}}, "defaults.own"),
            ({"required_slots": ["own"], "defaults": {"own": [{"nested": 1}]}}, "defaults.own[0]"),
            ({"required_slots": ["same"], "optional_slots": ["same"]}, "optional_slots"),
            ({"required_slots": ["same", "same"]}, "required_slots[1]"),
            ({"required_slots": [{}]}, "required_slots[0]"),
            ({"required_slots": "metric"}, "required_slots"),
            ({"clarification": [{"when": [], "question": "Choose"}]}, "clarification[0].when"),
        ):
            with self.subTest(intent=intent):
                self.assert_invalid(_document(intents={"ranking": intent}), f"$.intents.ranking.{suffix}")
        self.assert_invalid(_document(intents={
            "one": {"defaults": {"other_intent_slot": "x"}},
            "two": {"required_slots": ["other_intent_slot"]},
        }), "$.intents.one.defaults.other_intent_slot")

    def test_malformed_containers_raise_public_validation_error(self):
        for section in ("entities", "dimensions", "metrics", "query_surface", "synonyms", "intents", "security", "domain"):
            with self.subTest(section=section):
                document = _document()
                document[section] = []
                self.assert_invalid(document, f"$.{section}")
        for section in ("relations", "quality_rules"):
            self.assert_invalid(_document(**{section: [[]]}), f"$.{section}[0]")
        for kind in ({}, []):
            document = _document()
            document["entities"]["Score"]["properties"]["total_score"]["type"] = kind
            self.assert_invalid(document, "$.entities.Score.properties.total_score.type")
        with self.assertRaises(OntologyValidationError):
            parse_ontology('{"version":"1.0.0","domain":[],"metrics":{"broken":{"expression":[]}}}')
        for scalar in ("!!timestamp invalid", "!!bool perhaps", "!!int invalid"):
            for source in (f"extension: {scalar}\n", f"{scalar}: extension\n"):
                with self.subTest(source=source), self.assertRaises(OntologyValidationError):
                    parse_ontology(source)

    @unittest.skipUnless(_SCHOOL.is_file(), "portable school_score ontology fixture is not present")
    def test_original_school_ontology_counts_and_warning_semantics(self):
        source = _SCHOOL.read_text(encoding="utf-8")
        preview = parse_ontology(source)
        expected = {
            "entities": 12, "relations": 15, "dimensions": 14, "metrics": 13, "intents": 8,
            "query_surfaces": 3, "synonyms": 31, "quality_rules": 7, "properties": 52, "concepts": 2,
        }
        for section, count in expected.items():
            self.assertEqual(preview["summary"][section], count, section)
        self.assertTrue(preview["summary"]["security_declared"])
        self.assertEqual(preview["ontology"], yaml.safe_load(source))
        original_warning_paths = {
            "$.domain.database.default_path", "$.entities.文科.source.table", "$.entities.理科.source.table",
            "$.query_surface", "$.query_surface.authorized_transcript", "$.security",
            "$.quality_rules[1].expression", "$.metrics.pass_rate", "$.metrics.class_rank",
        }
        self.assertTrue(original_warning_paths <= {w["path"] for w in preview["warnings"]})
        self.assertEqual(sum("间隙" in w["message"] for w in preview["warnings"]), 4)
        security_warning = next(w["message"] for w in preview["warnings"] if w["path"] == "$.security")
        self.assertIn("不会执行授权", security_warning)
        self.assertIn("fail_closed", security_warning)


if __name__ == "__main__":
    unittest.main()
