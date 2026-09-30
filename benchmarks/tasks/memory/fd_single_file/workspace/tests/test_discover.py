"""发现工具的行为测试。"""

from __future__ import annotations

import unittest

from src.discover import DiscoveryError, discover


class DiscoverTests(unittest.TestCase):
    def test_directory_is_traversed_recursively(self) -> None:
        found = discover("src")
        self.assertIn("src/discover.py", found)
        self.assertTrue(all(path.endswith(".py") for path in found))

    def test_explicit_file_is_reported(self) -> None:
        self.assertEqual(discover("src/discover.py"), ["src/discover.py"])

    def test_missing_path_is_rejected(self) -> None:
        with self.assertRaises(DiscoveryError):
            discover("src/missing.py")


if __name__ == "__main__":
    unittest.main()
