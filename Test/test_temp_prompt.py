import unittest

from agent.prompts import build_temp_prompt_section, strip_temp_prompt_thinking


class TestTempPromptThinkingFilter(unittest.TestCase):
    def test_removes_think_block_and_keeps_instruction(self):
        text = "<think>内部推理\n不应显示</think>\n\n请分析订单趋势。"
        self.assertEqual(strip_temp_prompt_thinking(text), "请分析订单趋势。")

    def test_removes_case_insensitive_and_orphan_closing_tags(self):
        text = "<THINK>reasoning</THINK>\n请对比成本。</think>"
        self.assertEqual(strip_temp_prompt_thinking(text), "请对比成本。")

    def test_unclosed_think_content_is_not_injected(self):
        section = build_temp_prompt_section("请先检查数据。\n<think>unfinished reasoning")
        self.assertIn("请先检查数据。", section)
        self.assertNotIn("unfinished reasoning", section)
        self.assertNotIn("<think>", section)


if __name__ == "__main__":
    unittest.main()
