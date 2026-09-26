import unittest
from pathlib import Path

from harness.repository.discovery import DiscoveryEngine


class TestDiscoveryEngine(unittest.TestCase):
    def setUp(self):
        self.repo_path = Path(__file__).parent.parent
        self.engine = DiscoveryEngine(str(self.repo_path))

    def test_text_search(self):
        results = self.engine.text_search("DiscoveryEngine", is_regex=False)
        self.assertTrue(len(results) > 0)
        # Should find its own declaration
        found_decl = any(
            r.path == "harness/repository/discovery.py" and "class DiscoveryEngine" in r.context
            for r in results
        )
        self.assertTrue(found_decl)

    def test_symbol_search(self):
        results = self.engine.symbol_search("DiscoveryEngine")
        self.assertTrue(len(results) > 0)
        self.assertTrue(any("Symbol declaration" in r.match_reason for r in results))

    def test_find_tests_for_source(self):
        results = self.engine.find_tests_for_source("harness/repository/discovery.py")
        self.assertTrue(len(results) > 0)
        self.assertTrue(any(r.path == "tests/test_discovery.py" for r in results))

    def test_extract_dependencies(self):
        # We might not have a pyproject.toml or package.json yet, so just check it returns a dict
        deps = self.engine.extract_dependencies()
        self.assertIsInstance(deps, dict)

if __name__ == '__main__':
    unittest.main()
