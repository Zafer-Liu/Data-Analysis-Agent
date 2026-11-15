"""Synthetic, offline namespace/revision/readiness regressions."""
from contextlib import ExitStack
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from Function.Knowledge import knowledge_base as knowledge
from Function.Knowledge.definition_history import init_definition_history
from Function.Knowledge.ontology_parser import validate_ontology
from Function.Knowledge.ontology_store import validate_binding
from agent.semantic_context import build_semantic_context, validate_semantic_context


def document():
    return {
        "version": "1.0.0", "domain": {"name": "Synthetic shop"},
        "entities": {"Order": {
            "source": {"table": "orders", "primary_key": "id"},
            "properties": {"id": {"type": "integer"}, "amount": {"type": "decimal"},
                           "unused": {"type": "string"}},
        }},
        "metrics": {"revenue": {"entity": "Order", "expression": "SUM(amount)",
                                  "grain": "order", "time_window": "all rows",
                                  "unit": "CNY", "filters": [], "deduplication": "primary key",
                                  "null_policy": "ignore null amount"}},
    }


class SemanticLineageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(knowledge, "_embed_batch", side_effect=lambda texts: [[] for _ in texts]))
        self.stack.enter_context(patch.object(knowledge, "_embed_query", return_value=[]))
        self.stack.enter_context(patch.object(knowledge, "_embedding_signature", return_value="synthetic"))
        self.kb = knowledge.KnowledgeBase(self.root / "kb.db")
        self.addCleanup(self.kb.close)
        self.source = {"source_id": "source-b", "session_id": "session-a", "source_key": "physical-b",
                       "source_name": "Synthetic B", "index": 2,
                       "tables": {"orders": ["id", "amount", "unused"]}}
        self.doc = document()
        self.saved = self.save(self.doc)

    def save(self, doc, *, binding=None, **kwargs):
        parsed = validate_ontology(doc)
        if binding is None:
            binding, _ = validate_binding(parsed["ontology"], self.source)
        return self.kb.ontologies.save(parsed, filename="synthetic.yaml", source_name="synthetic.yaml",
                                       category_id=1, binding=binding, embed=False, **kwargs)

    def definitions(self, *paths, revision=None):
        return [self.kb.ontologies.definition(self.saved["id"], path, revision_id=revision or "") for path in paths]

    def context(self, *, sources=None):
        return build_semantic_context(self.kb, self.definitions("metrics.revenue", "entities.Order", "domain"),
                                      [self.source] if sources is None else sources)

    def test_only_required_fields_are_checked_and_actual_second_source_is_used(self):
        changed = {**self.source, "tables": {"orders": ["id", "amount"]}}
        wrong = {**self.source, "source_id": "source-a", "source_key": "physical-a", "index": 1,
                 "tables": {"orders": ["id", "wrong_amount"]}}
        context = self.context(sources=[wrong, changed])
        self.assertEqual(context["state"], "ready", context["issues"])
        self.assertEqual(context["source_ids"], ["source-b"])
        self.assertEqual({row["column"] for row in context["required_columns"]}, {"id", "amount"})

    def test_schema_drift_and_unknown_schema_are_explicit_and_revalidation_is_detached(self):
        before = self.context()
        drift = {**self.source, "tables": {"orders": ["id", "renamed_amount"]}}
        after = validate_semantic_context(before, [drift])
        self.assertEqual(before["state"], "ready")
        self.assertEqual(after["state"], "incomplete")
        self.assertIn("column_missing", {issue["code"] for issue in after["issues"]})
        self.assertEqual(validate_semantic_context(after, [self.source])["state"], "ready")
        unknown = {**self.source, "tables": {"orders": []}}
        self.assertIn("schema_unavailable", {issue["code"] for issue in self.context(sources=[unknown])["issues"]})
        self.assertEqual(validate_semantic_context(before)["state"], "incomplete")

    def test_precise_read_closes_dependencies_and_remains_on_original_revision(self):
        first = self.kb.ontologies.definition(self.saved["id"], "metrics.revenue")
        partial = build_semantic_context(self.kb, first, [self.source])
        self.assertEqual(partial["state"], "incomplete")
        missing = {issue["path"] for issue in partial["issues"] if issue["code"] == "dependency_unread"}
        self.assertIn("entities.Order", missing)
        replacement = copy.deepcopy(self.doc)
        replacement["metrics"]["revenue"]["expression"] = "SUM(amount) * 2"
        self.save(replacement, ontology_id=self.saved["id"])
        pages = self.definitions(*sorted(missing), revision=first["revision_id"])
        complete = build_semantic_context(self.kb, pages, [self.source], previous_context=partial)
        self.assertEqual(complete["state"], "ready", complete["issues"])
        metric = next(row for row in complete["used_definitions"] if row["kind"] == "metrics")
        self.assertEqual(metric["definition"]["expression"], "SUM(amount)")
        self.assertEqual(metric["revision_id"], first["revision_id"])

    def test_large_definition_is_bounded_and_must_be_read_without_gaps(self):
        doc = copy.deepcopy(self.doc)
        doc["metrics"]["revenue"]["notes"] = "synthetic explanation " * 700
        self.saved = self.save(doc, ontology_id=self.saved["id"])
        pages, offset = [], 0
        while True:
            page = self.kb.ontologies.definition(self.saved["id"], "metrics.revenue", offset=offset, limit=100000)
            self.assertLessEqual(page["returned_chars"], 4000)
            self.assertIsNone(page["definition"])
            pages.append(page)
            if page["next_offset"] is None:
                break
            offset = page["next_offset"]
        self.assertEqual(json.loads("".join(page["content"] for page in pages)), doc["metrics"]["revenue"])
        context = build_semantic_context(self.kb, self.definitions("entities.Order", "domain"), [self.source])
        for page in [pages[-1], *pages[:-2]]:
            context = build_semantic_context(self.kb, page, [self.source], previous_context=context)
        self.assertEqual(context["state"], "incomplete")
        context = build_semantic_context(self.kb, pages[-2], [self.source], previous_context=context)
        self.assertEqual(context["state"], "ready", context["issues"])

    def test_same_version_replacement_and_rebinding_have_immutable_snapshots(self):
        before = self.saved
        doc = copy.deepcopy(self.doc)
        doc["metrics"]["revenue"]["expression"] = "SUM(amount) * 2"
        after = self.save(doc, ontology_id=before["id"])
        self.assertEqual(after["version"], before["version"])
        self.assertNotEqual(after["revision_id"], before["revision_id"])
        self.assertNotEqual(after["content_hash"], before["content_hash"])
        renamed = {**self.source, "tables": {"orders": ["id", "new_amount", "unused"]}}
        binding, _ = validate_binding(doc, renamed, column_mapping={"orders": {"amount": "new_amount"}})
        rebound = self.kb.ontologies.update(before["id"], binding=binding)
        self.assertEqual(after["content_hash"], rebound["content_hash"])
        self.assertNotEqual(after["binding_hash"], rebound["binding_hash"])
        self.kb.ontologies.update(before["id"], enabled=False)
        self.kb.ontologies.delete(before["id"])
        old = self.kb.ontologies.get(before["id"], revision_id=before["revision_id"])
        self.assertEqual(old["document"], self.doc)
        self.assertEqual(old["binding"], before["binding"])
        self.assertEqual(self.kb.ontologies.definition(before["id"], "metrics.revenue", revision_id=before["revision_id"])["definition"]["expression"], "SUM(amount)")

    def test_historical_context_survives_disable_delete_and_hash_checks(self):
        context = self.context()
        self.kb.ontologies.update(self.saved["id"], enabled=False)
        self.kb.ontologies.delete(self.saved["id"])
        restored = build_semantic_context(self.kb, [], [self.source], previous_context=context)
        self.assertEqual(restored["state"], "ready", restored["issues"])
        self.assertEqual(restored["snapshot_hash"], context["snapshot_hash"])
        tampered = copy.deepcopy(context)
        tampered["used_definitions"][0]["definition"]["expression"] = "SUM(fake)"
        self.assertEqual(validate_semantic_context(tampered, [self.source])["state"], "incomplete")

    def test_missing_contract_unsupported_expression_and_unmapped_field_never_report_ready(self):
        for expression in ("custom_synthetic_function(amount)", "SELECT amount FROM orders", "SUM("):
            with self.subTest(expression=expression):
                doc = copy.deepcopy(self.doc)
                doc["metrics"]["revenue"]["expression"] = expression
                self.saved = self.save(doc, ontology_id=self.saved["id"])
                self.assertIn("expression_unsupported", {issue["code"] for issue in self.context()["issues"]})
        doc = copy.deepcopy(self.doc)
        del doc["metrics"]["revenue"]["null_policy"]
        self.saved = self.save(doc, ontology_id=self.saved["id"])
        context = self.context()
        self.assertEqual(context["state"], "needs_clarification")
        binding, _ = validate_binding(doc, self.source, column_mapping={"orders": {"amount": ""}})
        self.kb.ontologies.update(self.saved["id"], binding=binding)
        self.assertIn("column_unmapped", {issue["code"] for issue in self.context()["issues"]})

    def test_unknown_source_is_not_inferred_from_same_table_or_display_name(self):
        other = {**self.source, "source_id": "source-a", "source_key": "physical-a"}
        self.assertIn("source_unavailable", {issue["code"] for issue in self.context(sources=[other])["issues"]})
        moved = {**self.source, "source_id": "new-id", "session_id": "new-session"}
        self.assertEqual(self.context(sources=[moved])["source_ids"], ["new-id"])

    def test_separate_metric_dimension_reads_require_join_and_preserve_fanout_warning(self):
        doc = copy.deepcopy(self.doc)
        doc["entities"]["Refund"] = {"source": {"table": "refunds", "primary_key": "id"},
                                     "properties": {"id": {"type": "integer"}, "order_id": {"type": "integer"},
                                                    "reason": {"type": "string"}}}
        doc["dimensions"] = {"reason": {"entity": "Refund", "property": "reason"}}
        doc["relations"] = [{"name": "order_refunds", "from": "Order", "to": "Refund",
                              "cardinality": "one_to_many", "fanout": True,
                              "join": {"from_table": "orders", "from_column": "id",
                                       "to_table": "refunds", "to_column": "order_id"}}]
        self.source["tables"]["refunds"] = ["id", "order_id", "reason"]
        self.saved = self.save(doc, ontology_id=self.saved["id"])
        reads = self.definitions("metrics.revenue", "dimensions.reason", "entities.Order", "entities.Refund", "domain")
        context = build_semantic_context(self.kb, reads, [self.source])
        self.assertIn("relations.0", {issue["path"] for issue in context["issues"] if issue["code"] == "dependency_unread"})
        context = build_semantic_context(self.kb, self.definitions("relations.0"), [self.source], previous_context=context)
        self.assertEqual(context["state"], "needs_clarification", context["issues"])
        self.assertIn("join_grain_unverified", {issue["code"] for issue in context["issues"]})
        self.assertIn("order_id", {column["column"] for column in context["required_columns"]})
        del doc["relations"]
        self.saved = self.save(doc, ontology_id=self.saved["id"])
        disconnected = build_semantic_context(self.kb, self.definitions("metrics.revenue", "dimensions.reason", "entities.Order", "entities.Refund", "domain"), [self.source])
        self.assertIn("relation_missing", {issue["code"] for issue in disconnected["issues"]})

    def test_repeated_page_reads_are_stable_and_mixed_revisions_need_clarification(self):
        first = self.context()
        same = build_semantic_context(self.kb, self.definitions("metrics.revenue"), [self.source], previous_context=first)
        self.assertEqual(first["snapshot_hash"], same["snapshot_hash"])
        doc = copy.deepcopy(self.doc)
        doc["metrics"]["revenue"]["expression"] = "SUM(amount) * 2"
        self.saved = self.save(doc, ontology_id=self.saved["id"])
        mixed = build_semantic_context(self.kb, self.definitions("metrics.revenue", "entities.Order", "domain"),
                                       [self.source], previous_context=first)
        self.assertEqual(mixed["state"], "needs_clarification", mixed["issues"])
        self.assertIn("revision_conflict", {issue["code"] for issue in mixed["issues"]})

    def test_metric_contract_fields_are_validated_during_import(self):
        for field in ("grain", "time_window", "deduplication", "null_policy"):
            with self.subTest(field=field):
                doc = copy.deepcopy(self.doc)
                doc["metrics"]["revenue"][field] = {"unsupported": True}
                with self.assertRaises(ValueError):
                    validate_ontology(doc)

    def test_namespaces_allow_same_names_and_keep_old_source_unknown(self):
        second = self.kb.add_category("Warehouse")
        first = self.kb.add_metric("revenue", definition="Retail", cache_embedding=False)
        other = self.kb.add_metric("revenue", definition="Wholesale", category_id=second["id"], cache_embedding=False)
        rule = self.kb.add_rule("valid", description="Retail", cache_embedding=False)
        other_rule = self.kb.add_rule("valid", description="Wholesale", category_id=second["id"], cache_embedding=False)
        self.assertNotEqual(first["id"], other["id"])
        self.assertNotEqual(rule["id"], other_rule["id"])
        self.assertEqual(other["business_namespace"], "Warehouse")
        self.assertEqual(other_rule["business_namespace"], "Warehouse")
        names = {row["business_namespace"] for row in self.kb.search("revenue", limit=10)["metrics"]}
        self.assertEqual(names, {first["business_namespace"], "Warehouse"})
        rule_context = build_semantic_context(self.kb, [rule, other_rule], [self.source])
        self.assertEqual(len(rule_context["used_definitions"]), 2)
        revised = self.kb.add_metric("revenue", definition="Updated retail", cache_embedding=False)
        self.assertEqual(first["id"], revised["id"])
        self.assertNotEqual(first["revision_id"], revised["revision_id"])
        before = self.kb.definition_revision("metric", first["id"], first["revision_id"])
        self.assertEqual(before["source_status"], "unknown")
        self.assertEqual(before["definition"], "Retail")
        context = build_semantic_context(self.kb, [revised, other], [self.source])
        self.assertIn("namespace_ambiguous", {issue["code"] for issue in context["issues"]})
        self.kb.update_category(second["id"], name="Wholesale", enabled=0)
        disabled = self.kb.get_metric_by_id(other["id"])
        self.assertNotEqual(other["revision_id"], disabled["revision_id"])
        self.kb.delete_metric(first["id"])
        self.assertEqual(self.kb.definition_revision("metric", first["id"], first["revision_id"]), before)

    def test_import_links_and_delete_impact_do_not_guess_legacy_sources(self):
        manual = self.kb.add_metric("manual", cache_embedding=False)
        self.kb.bulk_insert([{"table": "metrics", "name": "imported", "definition": "synthetic"},
                             {"table": "business_rules", "rule_id": "imported", "description": "synthetic"}],
                            source_filename="synthetic.xlsx")
        impact = self.kb.source_delete_impact("synthetic.xlsx")
        self.assertEqual(len(impact["linked"]["metrics"]), 1)
        self.assertEqual(len(impact["linked"]["rules"]), 1)
        self.assertNotEqual(impact["linked"]["metrics"][0]["id"], manual["id"])
        self.assertTrue(impact["ordinary_definitions_retained"])

    def test_explicit_retention_prunes_only_old_revisions_and_deleted_history(self):
        before = self.saved
        doc = copy.deepcopy(self.doc)
        doc["metrics"]["revenue"]["expression"] = "SUM(amount) * 2"
        after = self.save(doc, ontology_id=before["id"])
        first = self.kb.add_metric("ordinary", definition="old", cache_embedding=False)
        second = self.kb.update_metric(first["id"], definition="new")
        result = self.kb.prune_history(time.time() + 1)
        self.assertGreaterEqual(result["ontologies"], 1)
        self.assertEqual(self.kb.ontologies.get(after["id"])["revision_id"], after["revision_id"])
        self.assertIsNone(self.kb.ontologies.get(before["id"], revision_id=before["revision_id"]))
        self.assertIsNone(self.kb.definition_revision("metric", first["id"], first["revision_id"]))
        self.assertIsNotNone(self.kb.definition_revision("metric", second["id"], second["revision_id"]))

    def test_browser_restore_records_server_revisions_and_retains_linked_source(self):
        self.kb.replace_structured_snapshot({"categories": [{"name": "Restored"}], "metrics": [
            {"name": "same", "source_filename": "source.xlsx"}], "rules": [{"rule_id": "same"}]})
        metric = self.kb.list_metrics()[0]
        self.assertTrue(metric["revision_id"].startswith("dr_"))
        self.assertEqual(self.kb.definition_revision("metric", metric["id"], metric["revision_id"])["source_filename"], "source.xlsx")
        self.assertTrue(self.kb.list_rules()[0]["revision_id"].startswith("dr_"))


class SemanticLegacyMigrationTests(unittest.TestCase):
    def connection(self, declaration='"name" TEXT NOT NULL UNIQUE', explicit=False):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        column, separator, constraint = declaration.partition(", CONSTRAINT")
        table_constraint = ", CONSTRAINT" + constraint if separator else ""
        conn.executescript(f"""
            CREATE TABLE knowledge_categories(id INTEGER PRIMARY KEY,name TEXT,enabled INTEGER);
            INSERT INTO knowledge_categories VALUES(1,'retail',1);
            INSERT INTO knowledge_categories VALUES(2,'wholesale',1);
            CREATE TABLE metrics(id INTEGER PRIMARY KEY AUTOINCREMENT,{column},
                                 definition TEXT DEFAULT '',category_id INTEGER,enabled INTEGER,updated_at REAL{table_constraint});
            CREATE TABLE business_rules(id INTEGER PRIMARY KEY AUTOINCREMENT,rule_id TEXT NOT NULL UNIQUE,
                                        description TEXT,category_id INTEGER,enabled INTEGER,updated_at REAL);
            INSERT INTO metrics VALUES(41,'revenue','old',1,1,1);
            INSERT INTO business_rules VALUES(73,'valid','old',1,1,1);
            INSERT INTO metrics VALUES(99,'deleted','',1,1,1);
            DELETE FROM metrics WHERE id=99;
            CREATE INDEX metric_category_custom ON metrics(category_id);
            CREATE TABLE audit_log(id INTEGER);
            CREATE TRIGGER metric_custom AFTER UPDATE OF definition ON metrics BEGIN INSERT INTO audit_log VALUES(new.id); END;
            CREATE VIRTUAL TABLE metrics_fts USING fts5(name,definition,content=metrics,content_rowid=id);
            INSERT INTO metrics_fts(metrics_fts) VALUES('rebuild');
        """)
        if explicit:
            conn.execute("CREATE UNIQUE INDEX metric_name_custom ON metrics(name)")
        return conn

    def test_inline_table_and_explicit_unique_migration_preserve_ids_fts_triggers_sequence(self):
        for declaration, explicit in (("name TEXT NOT NULL UNIQUE", False),
                                      ('"name" TEXT UNIQUE NOT NULL', False),
                                      ('name TEXT NOT NULL, CONSTRAINT key_unique UNIQUE("name")', False),
                                      ("name TEXT NOT NULL", True)):
            with self.subTest(declaration=declaration):
                conn = self.connection(declaration, explicit)
                init_definition_history(conn)
                self.assertEqual(conn.execute("SELECT id FROM metrics").fetchone()[0], 41)
                self.assertEqual(conn.execute("SELECT id FROM business_rules").fetchone()[0], 73)
                self.assertEqual(conn.execute("SELECT rowid FROM metrics_fts WHERE metrics_fts MATCH 'revenue'").fetchone()[0], 41)
                conn.execute("INSERT INTO metrics(name,definition,category_id,enabled,updated_at) VALUES('revenue','new',2,1,1)")
                self.assertGreater(conn.execute("SELECT MAX(id) FROM metrics").fetchone()[0], 99)
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute("INSERT INTO metrics(name,category_id,updated_at) VALUES('revenue',1,1)")
                conn.execute("UPDATE metrics SET definition='updated' WHERE id=41")
                self.assertEqual(conn.execute("SELECT id FROM audit_log").fetchone()[0], 41)
                count = conn.execute("SELECT COUNT(*) FROM definition_revisions").fetchone()[0]
                init_definition_history(conn)
                # A newly inserted legacy row is backfilled once; existing IDs/revisions stay fixed.
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM definition_revisions").fetchone()[0], count + 1)

    def test_literal_defaults_are_preserved_and_foreign_key_extensions_are_not_dropped(self):
        conn = self.connection("name TEXT NOT NULL UNIQUE DEFAULT 'unique'")
        init_definition_history(conn)
        schema = conn.execute("SELECT sql FROM sqlite_master WHERE name='metrics'").fetchone()[0]
        self.assertIn("DEFAULT 'unique'", schema)
        other = self.connection()
        other.commit()
        other.execute("PRAGMA foreign_keys=ON")
        other.execute("CREATE TABLE extension(metric_id INTEGER REFERENCES metrics(id) ON DELETE CASCADE)")
        other.execute("INSERT INTO extension VALUES(41)")
        with self.assertRaisesRegex(ValueError, "foreign-key"):
            init_definition_history(other)
        self.assertEqual(other.execute("SELECT metric_id FROM extension").fetchone()[0], 41)
        self.assertEqual(other.execute("SELECT id FROM metrics").fetchone()[0], 41)

    def test_migration_failure_rolls_back_original_table_and_indexes(self):
        conn = self.connection()
        conn.execute("CREATE TABLE business_rules_scoped_migration(id INTEGER)")
        with self.assertRaises(sqlite3.OperationalError):
            init_definition_history(conn)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO metrics(name,category_id,updated_at) VALUES('revenue',2,1)")
        self.assertEqual(conn.execute("SELECT definition FROM metrics WHERE id=41").fetchone()[0], "old")
        self.assertIsNotNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name='metric_custom'").fetchone())


if __name__ == "__main__":
    unittest.main()
