import tempfile
import unittest
from pathlib import Path

from flask import Flask

from api.skills import bp
from agent.skills import get_skill, load_skills, parse_skill_file, render_skill_prompt, SkillError


class TestAnalysisSkills(unittest.TestCase):
    def test_load_nested_skill_and_substitute_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "cohort"
            folder.mkdir()
            path = folder / "SKILL.md"
            path.write_text(
                "---\nname: cohort-analysis\ndescription: Cohort retention\nicon: X\n---\n"
                "Analyze: $ARGUMENTS",
                encoding="utf-8",
            )
            skills = load_skills(root)
            self.assertIn("cohort-analysis", skills)
            self.assertEqual(
                render_skill_prompt(skills["cohort-analysis"], "Q2 customers"),
                "Analyze: Q2 customers",
            )
            self.assertEqual(get_skill("cohort-analysis", root).description, "Cohort retention")

    def test_request_is_appended_without_placeholder(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "skill.md"
            path.write_text(
                "---\nname: rfm\ndescription: RFM analysis\n---\nUse RFM.",
                encoding="utf-8",
            )
            rendered = render_skill_prompt(parse_skill_file(path), "segment customers")
            self.assertIn("Use RFM.", rendered)
            self.assertIn("segment customers", rendered)

    def test_rejects_unsafe_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "skill.md"
            path.write_text(
                "---\nname: ../escape\ndescription: bad\n---\nprompt",
                encoding="utf-8",
            )
            with self.assertRaises(SkillError):
                parse_skill_file(path)

    def test_catalog_endpoint_exposes_example_without_prompt_body(self):
        app = Flask(__name__)
        app.register_blueprint(bp)
        with app.test_client() as client:
            response = client.get("/api/skills")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        funnel = next(s for s in payload["skills"] if s["name"] == "funnel-analysis")
        self.assertIn("description", funnel)
        self.assertNotIn("prompt", funnel)


if __name__ == "__main__":
    unittest.main()
