import unittest

from harness.context.manager import ContextItem, ContextManager, Level, ModelCapabilities, Priority


class TestContextManager(unittest.TestCase):
    def setUp(self):
        # Small capabilities for testing compaction
        self.caps = ModelCapabilities(max_context_tokens=100, max_output_tokens=50)
        # 100 * 0.8 = 80 tokens safe capacity
        self.manager = ContextManager(self.caps, safe_ratio=0.8)

    def test_item_token_estimation_and_reason(self):
        # 16 chars -> 4 tokens
        item = ContextItem("test_id", "A" * 16, Priority.P2_TARGET, Level.L2_EXACT, "Target file")
        self.assertEqual(item.tokens, 4)
        self.assertEqual(item.reason, "Target file")

    def test_p0_not_evicted_and_budget_respected(self):
        # P0 critical items (40 tokens each -> total 80 tokens, fits in budget)
        i1 = ContextItem("obj", "O" * 160, Priority.P0_CRITICAL, Level.L0_METADATA, "Objective")
        i2 = ContextItem("step", "S" * 160, Priority.P0_CRITICAL, Level.L0_METADATA, "Current step")

        # P7 background item (20 tokens)
        i3 = ContextItem(
            "bg", "B" * 80, Priority.P7_BACKGROUND, Level.L2_EXACT, "Background search"
        )

        self.manager.add_item(i1)
        self.manager.add_item(i2)
        self.manager.add_item(i3)

        self.assertEqual(self.manager.metrics.total_tokens, 100) # 40 + 40 + 20
        self.assertEqual(self.manager.metrics.tokens_by_category["L0"], 80)
        self.assertEqual(self.manager.metrics.tokens_by_category["L2"], 20)

        # Compact should drop P7 because total tokens (100) > safe_capacity (80)
        compacted = self.manager.compact()
        self.assertEqual(len(compacted), 2)
        self.assertIn(i1, compacted)
        self.assertIn(i2, compacted)
        self.assertNotIn(i3, compacted)

        self.assertEqual(self.manager.metrics.compaction_events, 1)
        self.assertEqual(self.manager.metrics.total_tokens, 80)

    def test_p0_not_evicted_even_if_over_budget(self):
        # P0 over budget (100 tokens, safe is 80)
        i1 = ContextItem("obj", "O" * 400, Priority.P0_CRITICAL, Level.L0_METADATA, "Objective")
        self.manager.add_item(i1)

        compacted = self.manager.compact()
        # P0 should not be evicted, even if over capacity
        self.assertEqual(len(compacted), 1)
        self.assertIn(i1, compacted)

    def test_invalidation(self):
        i1 = ContextItem("sum1", "S" * 40, Priority.P5_SUMMARIES, Level.L1_SUMMARY, "Old summary")
        self.manager.add_item(i1)
        self.assertEqual(len(self.manager.items), 1)

        self.manager.invalidate_by_id("sum1")
        self.assertEqual(len(self.manager.items), 0)
        self.assertEqual(self.manager.metrics.total_tokens, 0)

    def test_large_repo_retrieval(self):
        # Prove that adding many items correctly evicts low priority ones
        for i in range(10):
            self.manager.add_item(
                ContextItem(
                    f"file_{i}", "F" * 40, Priority.P7_BACKGROUND, Level.L2_EXACT,
                    "Search candidate",
                )
            )  # 10 tokens each, total 100

        self.manager.add_item(
            ContextItem("new_target", "T" * 40, Priority.P2_TARGET, Level.L2_EXACT, "New target")
        ) # 10 tokens

        self.assertEqual(self.manager.metrics.total_tokens, 110)

        # Safe budget now reserves output headroom: (100 - 50) * 0.8 = 40.
        # The P2 target is non-evictable; P7 background files fill the rest.
        compacted = self.manager.compact()
        self.assertEqual(len(compacted), 4)
        self.assertEqual(compacted[0].id, "new_target")
        assert self.manager.metrics.total_tokens <= self.manager.budget()
        self.assertTrue(
            all(c.priority in (Priority.P2_TARGET, Priority.P7_BACKGROUND) for c in compacted)
        )

if __name__ == '__main__':
    unittest.main()
