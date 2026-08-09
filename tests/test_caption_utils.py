import unittest

from app_files.caption_utils import build_srt


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
