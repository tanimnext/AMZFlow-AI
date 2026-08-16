"""_looks_like_junk_feature_text() -- the aggressive feature scraper in
download_assets() grabs any text between '>' and '<' in a chunk of raw
Amazon HTML. Three real garbage classes have shown up in production typed
onto the caption bar as if they were product spec bullets:

1. Raw CSS/JS caught by the tag-boundary regex (e.g. a <style> block)
2. Amazon's own accessibility/nav boilerplate ("use your keyboard...")
3. Amazon's "Shop by Department" menu items (verbatim on every page) --
   three different products in one video all scraped the same three
   department names as their only "features"
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_files"))
import amazon_video_maker as avm  # noqa: E402


class FeatureScrapeFilterTests(unittest.TestCase):
    def test_css_declaration_is_rejected(self):
        self.assertTrue(avm._looks_like_junk_feature_text(
            "root { -nav-desktop-header-tbg #131921;"
        ))

    def test_accessibility_boilerplate_is_rejected(self):
        self.assertTrue(avm._looks_like_junk_feature_text(
            "compare with similar items, to move between items use your keyboard up or down arrow keys"
        ))
        self.assertTrue(avm._looks_like_junk_feature_text(
            "Select the department you want to search in"
        ))

    def test_department_menu_names_are_rejected_exact_match(self):
        for name in ("Beauty & Personal Care", "Beauty & Personal Care.",
                     "Industrial & Scientific.", "Tools & Home Improvement."):
            with self.subTest(name=name):
                self.assertTrue(avm._looks_like_junk_feature_text(name))

    def test_department_name_mentioned_within_a_real_sentence_is_not_rejected(self):
        # Exact match only -- a genuine feature bullet that happens to
        # mention a department in passing must survive.
        self.assertFalse(avm._looks_like_junk_feature_text(
            "Great for beauty and personal care routines every morning"
        ))

    def test_real_feature_bullets_survive(self):
        for text in (
            "Ergonomic design reduces hand fatigue during extended use",
            "Waterproof IPX7 rated for shower use",
            "Made from premium 100% food-grade silicone material",
        ):
            with self.subTest(text=text):
                self.assertFalse(avm._looks_like_junk_feature_text(text))

    def test_product_caption_points_falls_back_to_narration_when_all_features_are_junk(self):
        # product_caption_points() itself doesn't call the junk filter (that
        # happens earlier, at scrape time in download_assets) -- this locks
        # in the fallback behavior that makes the fix actually useful: once
        # a product's scraped "features" are filtered out to nothing at
        # scrape time, the caption bar must fall back to real narration
        # text instead of showing nothing or stale department names.
        product = {"features": []}
        result = avm.product_caption_points(product, narration_text="Real narration for this product.")
        self.assertEqual(result, "Real narration for this product.")


if __name__ == "__main__":
    unittest.main()
