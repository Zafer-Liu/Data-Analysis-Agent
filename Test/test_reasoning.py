import unittest

from agent.reasoning import ThinkTagStreamParser, split_reasoning_tags
from data.session import ChatSession


class TestThinkTagStreamParser(unittest.TestCase):
    def test_splits_complete_response(self):
        visible, reasoning = split_reasoning_tags(
            "<think>先检查数据</think>\n\n这是最终结论。"
        )
        self.assertEqual(reasoning, "先检查数据")
        self.assertEqual(visible, "这是最终结论。")

    def test_handles_tags_split_across_chunks(self):
        parser = ThinkTagStreamParser()
        parts = ["<thi", "nk>内部", "推理</th", "ink>最终", "回答"]
        visible = []
        reasoning = []
        for part in parts:
            v, r = parser.feed(part)
            visible.append(v)
            reasoning.append(r)
        v, r = parser.finish()
        visible.append(v)
        reasoning.append(r)
        self.assertEqual("".join(reasoning), "内部推理")
        self.assertEqual("".join(visible), "最终回答")

    def test_session_persists_reasoning(self):
        session = ChatSession()
        session.add_assistant("正文", reasoning="推理")
        self.assertEqual(session.history[-1]["reasoning"], "推理")

    def test_session_splits_embedded_think_before_persisting(self):
        session = ChatSession()
        session.add_assistant("<think>内部过程</think>\n最终答案")
        self.assertEqual(session.history[-1]["content"], "最终答案")
        self.assertEqual(session.history[-1]["reasoning"], "内部过程")


if __name__ == "__main__":
    unittest.main()
