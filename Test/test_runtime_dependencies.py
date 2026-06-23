"""Regression checks for dependencies required during application startup."""

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RuntimeDependencyTests(unittest.TestCase):
    def test_pyyaml_is_declared_and_mapped_to_yaml(self):
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        self.assertRegex(requirements, r"(?m)^pyyaml(?:[<>=!~]|\s|$)")

        app_tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
        mappings = {}
        for node in ast.walk(app_tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and isinstance(value, ast.Constant):
                    mappings[key.value] = value.value
        self.assertEqual(mappings.get("pyyaml"), "yaml")


if __name__ == "__main__":
    unittest.main()
