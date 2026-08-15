import re
import unittest

from app_files import amazon_video_maker as avm
from app_files.caption_utils import (
    MAX_CHARS_PER_LINE,
    MAX_LINES_PER_CUE,
    build_srt,
    wrap_cue,
)


def _caption_lines(srt):
    """Just the spoken-text lines -- no indices, no timecodes."""
    return [
        line
        for line in srt.splitlines()
        if line.strip() and "-->" not in line and not line.strip().isdigit()
    ]


class CaptionLayoutTests(unittest.TestCase):
    """Burned-in captions covered most of the frame: every cue was a whole
    unwrapped sentence, so the renderer laid it out as one long line and
    overflowed it across the video."""

    def test_no_caption_line_exceeds_the_line_budget(self):
        long_text = (
            "This cordless tire inflator reaches one hundred fifty PSI and shuts "
            "itself off automatically at the pressure you set. The motor is "
            "genuinely quick so you are not standing around waiting for it."
        )
        srt = build_srt([{"start": 0.0, "duration": 12.0, "text": long_text}])
        for line in _caption_lines(srt):
            self.assertLessEqual(len(line), MAX_CHARS_PER_LINE, f"too wide: {line!r}")

    def test_no_cue_exceeds_two_lines(self):
        long_text = " ".join(f"word{i}" for i in range(80))
        srt = build_srt([{"start": 0.0, "duration": 20.0, "text": long_text}])
        for block in srt.strip().split("\n\n"):
            text_lines = _caption_lines(block)
            self.assertLessEqual(len(text_lines), MAX_LINES_PER_CUE)

    def test_a_long_sentence_becomes_several_timed_cues(self):
        long_sentence = "One long run on sentence " + " ".join(f"part{i}" for i in range(40)) + "."
        srt = build_srt([{"start": 0.0, "duration": 10.0, "text": long_sentence}])
        self.assertGreater(len(srt.strip().split("\n\n")), 1)

    def test_cue_timings_stay_ordered_and_inside_the_segment(self):
        srt = build_srt([{"start": 5.0, "duration": 6.0, "text": " ".join(f"w{i}" for i in range(60))}])
        stamps = re.findall(r"(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})", srt)
        self.assertTrue(stamps)
        previous_end = 5.0
        for h1, m1, s1, ms1, h2, m2, s2, ms2 in stamps:
            start = int(h1) * 3600 + int(m1) * 60 + int(s1) + int(ms1) / 1000
            end = int(h2) * 3600 + int(m2) * 60 + int(s2) + int(ms2) / 1000
            self.assertLessEqual(start, end)
            self.assertAlmostEqual(start, previous_end, delta=0.01)
            previous_end = end
        self.assertAlmostEqual(previous_end, 11.0, delta=0.01)

    def test_wrap_cue_breaks_on_word_boundaries(self):
        wrapped = wrap_cue("alpha bravo charlie delta echo foxtrot golf hotel india")
        self.assertNotIn("  ", wrapped)
        for line in wrapped.split("\n"):
            self.assertLessEqual(len(line), MAX_CHARS_PER_LINE)
        # No word may be chopped in half.
        self.assertEqual(wrapped.replace("\n", " ").split()[0], "alpha")


class ProductCaptionPointsTests(unittest.TestCase):
    """Captions duplicated the entire narration the viewer was already
    hearing. Short spec points are what captions are actually useful for."""

    def test_uses_product_feature_bullets_when_available(self):
        product = {"features": ["150 PSI max pressure", "Cordless 20V battery"]}
        out = avm.product_caption_points(product, "the full spoken narration text")
        self.assertIn("150 PSI max pressure", out)
        self.assertIn("Cordless 20V battery", out)
        self.assertNotIn("spoken narration", out)

    def test_falls_back_to_narration_when_there_are_no_features(self):
        out = avm.product_caption_points({"features": []}, "narration fallback text")
        self.assertEqual(out, "narration fallback text")

    def test_overlong_marketing_bullets_are_truncated(self):
        product = {"features": ["x" * 300]}
        out = avm.product_caption_points(product, "")
        self.assertLess(len(out), 120)
        self.assertTrue(out.endswith("..."))

    def test_caps_how_many_points_are_shown(self):
        product = {"features": [f"Feature number {i}" for i in range(20)]}
        out = avm.product_caption_points(product, "", max_points=5)
        self.assertLessEqual(out.count("Feature number"), 5)


class CaptionUtilsTests(unittest.TestCase):
    def test_builds_sentence_level_captions_with_monotonic_timing(self):
        srt = build_srt(
            [
                {
                    "start": 0.0,
                    "duration": 4.0,
                    "text": "This is the first sentence. Here is the second.",
                }
            ]
        )
        self.assertIn("1\n00:00:00,000 -->", srt)
        self.assertIn("This is the first sentence.", srt)
        self.assertIn("2\n", srt)
        self.assertIn("Here is the second.", srt)

    def test_ignores_empty_caption_entries(self):
        self.assertEqual(build_srt([{"start": 0, "duration": 2, "text": ""}]), "")


if __name__ == "__main__":
    unittest.main()
