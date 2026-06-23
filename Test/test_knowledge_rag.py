#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Knowledge-base RAG indexing and hybrid retrieval tests."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Function.Knowledge.knowledge_base import KnowledgeBase
from agent.tools.business.data import DataToolsMixin


class TestKnowledgeRag(unittest.TestCase):

    def _kb(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        kb = KnowledgeBase(Path(tmp.name) / "knowledge.db")
        self.addCleanup(kb.close)
        return kb

    def test_document_chunks_are_retrieved_and_deleted(self):
        kb = self._kb()
        text = (
            "客户流失预警模型用于识别高风险用户。\n\n"
            "当最近 30 天活跃下降、投诉增加、复购间隔拉长时，应进入召回名单。"
        )
        indexed = kb.index_document("retention_playbook.docx", text)

        self.assertGreaterEqual(indexed["chunks"], 1)
        self.assertIn("retention_playbook.docx", kb.get_enabled_summary())

        result = kb.search("用户流失风险", limit=3)
        docs = result["documents"]

        self.assertTrue(docs)
        self.assertEqual(docs[0]["source_name"], "retention_playbook.docx")
        self.assertIn("客户流失预警", docs[0]["content"])

        deleted = kb.delete_document_index("retention_playbook.docx")
        self.assertGreaterEqual(deleted, 1)
        self.assertEqual(kb.search("用户流失风险", limit=3)["documents"], [])

    def test_structured_records_use_vector_fallback(self):
        kb = self._kb()
        kb.add_metric(
            name="DAU",
            alias="日活跃用户",
            definition="统计当日启动产品一次及以上的独立用户数。",
        )

        result = kb.search("日活用户口径", limit=3)

        self.assertTrue(result["metrics"])
        self.assertEqual(result["metrics"][0]["name"], "DAU")

    def test_low_score_structured_matches_are_filtered(self):
        kb = self._kb()
        kb.add_metric(
            name="IPO",
            alias="每单骑手成本",
            definition="Income per order，平均每单骑手成本，可按城市/模式/距离段细分。",
        )

        result = kb.search("各城市盈利状况", limit=5)

        self.assertEqual(result["metrics"], [])

    def test_relevant_structured_match_survives_threshold(self):
        kb = self._kb()
        kb.add_metric(
            name="City Profitability Status",
            alias="城市盈利状况",
            definition="各城市当前盈利状况，按城市汇总收入、成本、补贴和利润。",
        )

        result = kb.search("各城市盈利状况", limit=5)

        self.assertTrue(result["metrics"])
        self.assertEqual(result["metrics"][0]["alias"], "城市盈利状况")

    def test_chinese_phrase_retrieval_for_document_chunks(self):
        kb = self._kb()
        kb.index_document(
            "cost_policy.docx",
            "IPO 成本口径包含骑手基础费、履约奖励、恶劣天气溢价和夜间补贴。",
        )

        result = kb.search("骑手成本 基础费 奖励 溢价", limit=3)

        self.assertTrue(result["documents"])
        self.assertEqual(result["documents"][0]["source_name"], "cost_policy.docx")

    def test_knowledge_refs_are_ui_safe(self):
        mixin = DataToolsMixin()
        refs = mixin._knowledge_refs_from_results({
            "metrics": [{
                "name": "IPO",
                "alias": "单均成本",
                "definition": "每单配送成本口径。",
                "vector_score": 0.8,
            }],
            "rules": [],
            "notes": [],
            "documents": [{
                "source_name": "cost_policy.docx",
                "chunk_index": 2,
                "content": "IPO 成本口径包含骑手基础费、履约奖励和溢价。",
                "score": 1.23,
            }],
        })

        self.assertEqual(refs[0]["type"], "指标")
        self.assertEqual(refs[1]["type"], "文档")
        self.assertIn("cost_policy.docx", refs[1]["title"])


if __name__ == "__main__":
    unittest.main()
