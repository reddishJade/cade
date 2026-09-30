"""缓存键的稳定性测试。"""

from __future__ import annotations

import unittest

from src.cache import cache_key, get, put


class CacheKeyTests(unittest.TestCase):
    def test_equivalent_parameters_share_one_key(self) -> None:
        self.assertEqual(
            cache_key({"region": "eu", "tier": "gold"}),
            cache_key({"tier": "gold", "region": "eu"}),
        )

    def test_equivalent_parameters_hit_the_same_entry(self) -> None:
        put({"region": "eu", "tier": "gold"}, "rendered-report")
        self.assertEqual(get({"tier": "gold", "region": "eu"}), "rendered-report")

    def test_same_order_still_hits(self) -> None:
        put({"tier": "silver"}, "other-report")
        self.assertEqual(get({"tier": "silver"}), "other-report")


if __name__ == "__main__":
    unittest.main()
