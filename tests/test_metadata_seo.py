import unittest

from app_files.metadata_generator import build_title_variants


class MetadataSeoTests(unittest.TestCase):
    def test_single_product_variants_are_accurate_and_bounded(self):
        variants = build_title_variants(
            "quiet coffee grinder",
            [{"title": "Acme Grinder"}],
            "Quiet Coffee Grinder",
            year="2026",
        )
        self.assertEqual(len(variants), 3)
        self.assertEqual(len(set(variants)), 3)
        self.assertTrue(all(len(title) <= 100 for title in variants))
        self.assertTrue(all("tested" not in title.lower() for title in variants))

    def test_multi_product_variants_include_buying_intent(self):
        variants = build_title_variants(
            "standing desks",
            [{"title": "A"}, {"title": "B"}, {"title": "C"}],
            "Top 3 Standing Desks 2026",
            year="2026",
        )
        self.assertEqual(len(variants), 3)
        self.assertTrue(any("Buying Guide" in title for title in variants))


if __name__ == "__main__":
    unittest.main()
