import tempfile
import unittest
from pathlib import Path


class MediaQcTests(unittest.TestCase):
    def setUp(self):
        from app_files import media_qc

        self.qc = media_qc
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.video = self.root / "video.mp4"
        self.video.write_bytes(b"placeholder")

    def tearDown(self):
        self.temp.cleanup()

    def test_missing_rendered_segment_fails_closed(self):
        report = self.qc.evaluate_qc(
            final_path=self.video,
            planned_segment_ids=["intro", "rank-2", "product-2", "outro"],
            rendered_segments={
                "intro": "intro.mp4",
                "product-2": "product.mp4",
                "outro": "outro.mp4",
            },
            probe={"hasVideo": True, "hasAudio": True, "duration": 20.0},
            silences=[],
            expected_duration=20.0,
        )
        self.assertEqual(report["status"], "FAILED")
        self.assertIn("rank-2", report["missingSegments"])

    def test_missing_audio_stream_fails(self):
        report = self.qc.evaluate_qc(
            final_path=self.video,
            planned_segment_ids=["intro"],
            rendered_segments={"intro": "intro.mp4"},
            probe={"hasVideo": True, "hasAudio": False, "duration": 3.0},
            silences=[],
            expected_duration=3.0,
        )
        self.assertEqual(report["status"], "FAILED")
        self.assertIn("Final output has no audio stream", report["errors"])

    def test_nonstandard_or_short_audio_stream_fails(self):
        report = self.qc.evaluate_qc(
            final_path=self.video,
            planned_segment_ids=["intro"],
            rendered_segments={"intro": "intro.mp4"},
            probe={
                "hasVideo": True,
                "hasAudio": True,
                "duration": 20.0,
                "videoDuration": 20.0,
                "audioDuration": 18.8,
                "audioSampleRate": 96000,
            },
            silences=[],
            expected_duration=20.0,
        )
        self.assertEqual(report["status"], "FAILED")
        self.assertTrue(
            any("sample rate" in error.lower() for error in report["errors"]),
            report,
        )
        self.assertTrue(
            any("ends" in error.lower() for error in report["errors"]),
            report,
        )

    def test_unexpected_long_silence_fails(self):
        report = self.qc.evaluate_qc(
            final_path=self.video,
            planned_segment_ids=["intro"],
            rendered_segments={"intro": "intro.mp4"},
            probe={"hasVideo": True, "hasAudio": True, "duration": 15.0},
            silences=[{"start": 5.0, "end": 10.5, "duration": 5.5}],
            expected_duration=15.0,
            max_silence_seconds=3.0,
        )
        self.assertEqual(report["status"], "FAILED")
        self.assertEqual(len(report["unexpectedSilences"]), 1)

    def test_complete_healthy_render_passes(self):
        report = self.qc.evaluate_qc(
            final_path=self.video,
            planned_segment_ids=["intro", "product-1", "outro"],
            rendered_segments={
                "intro": "intro.mp4",
                "product-1": "product.mp4",
                "outro": "outro.mp4",
            },
            probe={"hasVideo": True, "hasAudio": True, "duration": 29.6},
            silences=[{"start": 10.0, "end": 10.8, "duration": 0.8}],
            expected_duration=30.0,
        )
        self.assertEqual(report["status"], "PASSED")
        self.assertEqual(report["errors"], [])


if __name__ == "__main__":
    unittest.main()
