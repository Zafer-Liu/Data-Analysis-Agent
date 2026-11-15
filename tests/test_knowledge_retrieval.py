"""Knowledge evidence admission with synthetic data and mocked embeddings."""
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from Function.Knowledge import knowledge_base as knowledge


class KnowledgeRetrievalTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "knowledge.db"
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.query = stack.enter_context(patch.object(knowledge, "_embed_query", return_value=[1.0, 0.0]))
        self.vectors = stack.enter_context(patch.object(knowledge, "_embed_batch", side_effect=lambda texts: [[0.0, 1.0] for _ in texts]))
        stack.enter_context(patch.object(knowledge, "_embedding_signature", return_value="test-vector-v1"))
        self.kb = knowledge.KnowledgeBase(self.path)
        self.addCleanup(self.kb.close)

    def document(self, name="inventory.txt", text="Inventory stock counts and warehouse locations."):
        self.kb.index_document(name, text)

    def test_unrelated_documents_never_gain_evidence_from_rank_alone(self):
        for index in range(8):
            self.document(f"inventory-{index}.txt")
        results = self.kb.search("astronomy")
        self.assertFalse(any(results.values()), results)
        self.kb._conn.execute("DELETE FROM rag_chunks WHERE id % 2 = 0")
        self.kb._rebuild_fts("rag_chunks")
        self.assertFalse(any(self.kb.search("astronomy").values()))

    def test_real_keyword_only_match_survives_without_a_vector(self):
        self.document("policy.txt", "Refund accounting requires a confirmed return.")
        self.query.return_value = []
        with patch.object(knowledge, "_text_match_score", return_value=0.0):
            results = self.kb.search("refund")
        self.assertEqual(1, len(results["documents"]))
        self.assertEqual(["keyword"], results["documents"][0]["matched_channels"])

    def test_query_embedding_failure_keeps_document_and_metric_text_matches(self):
        self.document("policy.txt", "Refund accounting requires a confirmed return.")
        self.kb.add_metric(name="refund", definition="Confirmed return count", cache_embedding=False)
        self.query.side_effect = RuntimeError("synthetic embedding outage")
        results = self.kb.search("refund")
        self.assertEqual(1, len(results["documents"]))
        self.assertEqual(1, len(results["metrics"]))
        self.assertEqual(1, self.query.call_count)

    def test_signed_vector_only_match_is_admitted(self):
        self.vectors.side_effect = lambda texts: [[1.0, 0.0] for _ in texts]
        self.document()
        rows = self.kb.search("astronomy")["documents"]
        self.assertEqual(1, len(rows))
        self.assertEqual(["vector"], rows[0]["matched_channels"])

    def test_incompatible_or_malformed_vectors_do_not_create_matches(self):
        self.document()
        for embedding, signature in (("[1.0, 0.0]", "old-model"), ("invalid-json", "test-vector-v1"), ("[1.0]", "test-vector-v1")):
            with self.subTest(embedding=embedding, signature=signature):
                self.kb._conn.execute("UPDATE rag_chunks SET embedding=?, embedding_signature=?", (embedding, signature))
                self.assertFalse(any(self.kb.search("astronomy").values()))

    def test_documents_do_not_get_an_extra_rrf_vote_for_the_same_text_match(self):
        self.kb.add_metric(name="refund", definition="Confirmed return count", cache_embedding=False)
        self.document("policy.txt", "Refund accounting requires a confirmed return.")
        results = self.kb.search("refund", limit=1)
        self.assertEqual(1, len(results["metrics"]))
        self.assertEqual([], results["documents"])
        self.assertEqual(1, sum(len(rows) for rows in results.values()))

    def test_chinese_keyword_index_and_query_use_the_same_segmentation(self):
        self.document("经营口径.txt", "净销售额等于销售金额减去已确认退款金额。")
        self.query.return_value = []
        with patch.object(knowledge, "_text_match_score", return_value=0.0):
            rows = self.kb.search("净销售额")["documents"]
        self.assertEqual(1, len(rows))
        self.assertIn("keyword", rows[0]["matched_channels"])

    def test_disabled_categories_and_deleted_documents_cannot_match(self):
        self.document("policy.txt", "Refund accounting requires a confirmed return.")
        self.kb._conn.execute("UPDATE knowledge_categories SET enabled=0")
        self.assertFalse(any(self.kb.search("refund").values()))
        self.kb._conn.execute("UPDATE knowledge_categories SET enabled=1")
        self.kb.delete_document_index("policy.txt")
        self.assertFalse(any(self.kb.search("refund").values()))

    def test_legacy_external_content_index_migrates_without_changing_chunks(self):
        self.document("policy.txt", "净销售额等于销售金额减去退款金额。")
        before = [tuple(row) for row in self.kb._conn.execute("SELECT * FROM rag_chunks")]
        self.kb._conn.execute("DROP TABLE rag_chunks_fts")
        self.kb._conn.execute("CREATE VIRTUAL TABLE rag_chunks_fts USING fts5(source_name, content, content=rag_chunks, content_rowid=id)")
        self.kb._conn.execute("INSERT INTO rag_chunks_fts(rag_chunks_fts) VALUES('rebuild')")
        self.kb._conn.commit()
        self.kb.close()
        migrated = knowledge.KnowledgeBase(self.path)
        self.addCleanup(migrated.close)
        self.assertEqual(before, [tuple(row) for row in migrated._conn.execute("SELECT * FROM rag_chunks")])
        self.query.return_value = []
        with patch.object(knowledge, "_text_match_score", return_value=0.0):
            self.assertEqual(1, len(migrated.search("净销售额")["documents"]))
        schema = migrated._conn.execute("SELECT sql FROM sqlite_master WHERE name='rag_chunks_fts'").fetchone()[0]
        self.assertNotIn("content=rag_chunks", schema)

    def test_deleting_a_category_removes_its_derived_keyword_rows(self):
        category = self.kb.add_category("Synthetic policies")
        self.kb.index_document("policy.txt", "Refund accounting requires a return.", category_id=category["id"])
        self.kb.delete_category(category["id"])
        self.assertEqual(0, self.kb._conn.execute("SELECT COUNT(*) FROM rag_chunks_fts").fetchone()[0])
        self.assertFalse(any(self.kb.search("refund").values()))

    def test_browser_snapshot_restore_clears_document_keyword_rows(self):
        self.document("policy.txt", "Refund accounting requires a return.")
        self.kb.replace_structured_snapshot({"categories": [{"name": "Restored"}]})
        self.assertEqual(0, self.kb._conn.execute("SELECT COUNT(*) FROM rag_chunks_fts").fetchone()[0])
        self.assertFalse(any(self.kb.search("refund").values()))

    def test_failed_legacy_index_migration_keeps_the_previous_index_and_chunks(self):
        self.document("policy.txt", "Refund accounting requires a return.")
        self.kb._conn.execute("DROP TABLE rag_chunks_fts")
        self.kb._conn.execute("CREATE VIRTUAL TABLE rag_chunks_fts USING fts5(source_name, content, content=rag_chunks, content_rowid=id)")
        self.kb._conn.execute("INSERT INTO rag_chunks_fts(rag_chunks_fts) VALUES('rebuild')")
        self.kb._conn.commit()
        with patch.object(knowledge, "_rebuild_chunk_fts", side_effect=RuntimeError("synthetic indexing failure")):
            with self.assertRaisesRegex(RuntimeError, "synthetic indexing failure"):
                knowledge._init_db(self.kb._conn)
        schema = self.kb._conn.execute("SELECT sql FROM sqlite_master WHERE name='rag_chunks_fts'").fetchone()[0]
        self.assertIn("content=rag_chunks", schema)
        self.assertEqual(1, self.kb._conn.execute("SELECT COUNT(*) FROM rag_chunks").fetchone()[0])

    def test_empty_query_does_not_request_an_embedding(self):
        self.assertFalse(any(self.kb.search("  ").values()))
        self.query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
