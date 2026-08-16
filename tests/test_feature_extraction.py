"""extract_feature_bullets() -- structural scrape of Amazon's "About this
item" list.

The previous implementation found the literal text "About this item" and
scanned the next 30,000 characters for anything between '>' and '<'. The
real bullet list is a few hundred characters, so that window ran past it
into the recommendation carousels, the department menu and the
keyboard-shortcuts panel, and every prose-shaped string in there became a
"feature": three unrelated products in one video scraped the identical
"Beauty & Personal Care" / "Industrial & Scientific" / "Tools & Home
Improvement", and a caption bar typed "Show/Hide shortcuts" over footage.

These tests pin the structural behaviour: read the container that actually
holds the bullets, and never the site chrome that surrounds it.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_files"))
import amazon_video_maker as avm  # noqa: E402


NAV_JUNK = """
<div id="nav-belt">
  <span>Select the department you want to search in</span>
  <a>Beauty &amp; Personal Care</a><a>Industrial &amp; Scientific</a>
  <a>Tools &amp; Home Improvement</a>
</div>
<style>:root { --nav-desktop-header-tbg: #131921; }</style>
"""

TRAILING_JUNK = """
<div id="similarities_feature_div">
  Compare with similar items. To move between items, use your keyboard up or down arrow keys.
</div>
<div id="nav-flyout-shortcuts"><span>Show/Hide shortcuts</span></div>
<div id="navFooter"><a>Back to top</a></div>
"""

BULLETS = """
<div id="feature-bullets" class="a-section">
  <h1>About this item</h1>
  <ul class="a-unordered-list">
    <li><span class="a-list-item">PAPAYA ENZYMES AND PINK SALT: Exfoliate the scalp gently and effectively</span></li>
    <li><span class="a-list-item">REVITALIZE THE SCALP AND HAIR: Balance scalp oil and nourish strands</span></li>
    <li><span class="a-list-item">CLEANSE AND EXFOLIATE: Gentle yet effective to rid of impurities or buildup</span></li>
  </ul>
</div>
"""


class FeatureExtractionTests(unittest.TestCase):
    def test_reads_the_real_bullets_and_nothing_around_them(self):
        features = avm.extract_feature_bullets(NAV_JUNK + BULLETS + TRAILING_JUNK)
        self.assertEqual(len(features), 3)
        self.assertTrue(features[0].startswith("PAPAYA ENZYMES AND PINK SALT"))
        joined = " ".join(features).lower()
        for leak in ("show/hide", "department", "keyboard", "personal care",
                     "industrial", "home improvement", "nav-desktop"):
            self.assertNotIn(leak, joined, f"site chrome leaked into features: {leak}")

    def test_nested_markup_inside_a_bullet_is_flattened_not_split(self):
        page = """
        <div id="feature-bullets"><ul>
          <li><span class="a-list-item"><b>Waterproof</b> IPX7 rated for <i>shower</i> use daily</span></li>
        </ul></div>
        """
        features = avm.extract_feature_bullets(page)
        self.assertEqual(features, ["Waterproof IPX7 rated for shower use daily"])

    def test_falls_back_to_list_items_when_span_class_is_absent(self):
        page = """
        <div id="feature-bullets"><ul>
          <li>Ergonomic design reduces hand fatigue during extended use</li>
        </ul></div>
        """
        self.assertEqual(
            avm.extract_feature_bullets(page),
            ["Ergonomic design reduces hand fatigue during extended use"],
        )

    def test_falls_back_to_product_description_when_no_bullet_container(self):
        page = NAV_JUNK + """
        <div id="productDescription">
          <p>Made from premium food grade silicone that resists heat and stains.</p>
        </div>
        """ + TRAILING_JUNK
        features = avm.extract_feature_bullets(page)
        self.assertEqual(len(features), 1)
        self.assertIn("silicone", features[0])

    def test_a_page_with_no_feature_markup_yields_nothing_rather_than_junk(self):
        # The important half of the fix: when there is no feature list,
        # returning [] lets product_caption_points() fall back to real
        # narration. The old code returned whatever prose it could find.
        self.assertEqual(avm.extract_feature_bullets(NAV_JUNK + TRAILING_JUNK), [])

    def test_result_is_capped(self):
        many = "".join(
            f'<li><span class="a-list-item">Durable feature number {i} for everyday use</span></li>'
            for i in range(30)
        )
        features = avm.extract_feature_bullets(f'<div id="feature-bullets"><ul>{many}</ul></div>')
        self.assertLessEqual(len(features), 10)


class SlideOutlineContrastTests(unittest.TestCase):
    """A text outline only works when it CONTRASTS with the fill. The intro/
    outro slides used to borrow COLOR_THUMB_GLOW (default white) for the
    outline, so the default white outro text got a 3px white outline and the
    glyphs bloated into an unreadable smear."""

    def test_light_and_dark_colors_are_classified_for_outline_choice(self):
        for light in ("#ffffff", "0xFFFFFF", "white", "#00fbff", "#f97316"):
            self.assertTrue(avm._is_light_color(light), light)
        for dark in ("#000000", "0x000000", "black", "#131921"):
            self.assertFalse(avm._is_light_color(dark), dark)

    def test_unparseable_color_defaults_to_light_so_outline_stays_dark(self):
        # Dark outline is the safe default for this codebase's light text.
        self.assertTrue(avm._is_light_color("not-a-color"))
        self.assertTrue(avm._is_light_color(""))
        self.assertTrue(avm._is_light_color(None))

    def test_outro_outline_contrasts_with_white_title_text(self):
        from unittest.mock import patch
        captured = {}

        def fake_run_ffmpeg(cmd_args):
            captured["fc"] = cmd_args[cmd_args.index("-filter_complex") + 1]

        with patch.object(avm, "run_ffmpeg", side_effect=fake_run_ffmpeg), \
                patch.object(avm, "get_audio_duration", return_value=4.0), \
                patch.object(avm, "COLOR_OUTRO_TITLE", "#ffffff"), \
                patch.object(avm, "COLOR_THUMB_GLOW", "#ffffff"):
            avm.create_text_slide_ffmpeg(
                "Thanks for watching, subscribe for more reviews today.",
                None, "/tmp/unused.mp4", bg_path="/tmp/unused_bg.jpg", is_intro=False,
            )
        self.assertIn("bordercolor=black", captured["fc"])
        self.assertNotIn("bordercolor=0xffffff", captured["fc"])


class CaptionSourceConsistencyTests(unittest.TestCase):
    """The on-screen caption bar and the captions.srt sidecar must describe
    the same segment. The renderer used to hand the overlay the raw
    product["features"] list while the sidecar used product_caption_points()
    (features, falling back to narration), so a product with no usable
    bullets got a sidecar full of narration and an empty on-screen bar."""

    def test_features_are_used_when_present(self):
        points = avm.segment_caption_points(
            {"features": ["Durable steel body", "IPX7 waterproof rating"]},
            "some narration",
        )
        self.assertEqual(points, ["Durable steel body", "IPX7 waterproof rating"])

    def test_falls_back_to_the_same_text_the_srt_shows(self):
        srt_text = "This is point one. And here is point two!"
        points = avm.segment_caption_points({"features": []}, srt_text)
        self.assertEqual(points, ["This is point one.", "And here is point two!"])
        # Every fragment on screen must come from the sidecar text.
        for point in points:
            self.assertIn(point, srt_text)

    def test_blank_everything_yields_no_points_rather_than_a_blank_bar(self):
        self.assertEqual(avm.segment_caption_points({}, ""), [])
        self.assertEqual(avm.segment_caption_points({"features": ["  "]}, None), [])

    def test_single_asin_part_caption_tracks_that_parts_narration(self):
        # product_part passes {} as the product so the bar follows the voice
        # of the CURRENT section instead of repeating the whole feature list.
        self.assertEqual(
            avm.segment_caption_points({}, "The battery lasts twelve hours."),
            ["The battery lasts twelve hours."],
        )


if __name__ == "__main__":
    unittest.main()
