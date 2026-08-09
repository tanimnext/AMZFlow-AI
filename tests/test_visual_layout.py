import unittest
import shutil
import subprocess
import tempfile
from pathlib import Path

from app_files.amazon_video_maker import (
    build_music_mix_filter,
    create_product_segment_ffmpeg,
    wrap_lines_for_overlay,
)
from app_files.media_qc import detect_silences, probe_media


class VisualLayoutTests(unittest.TestCase):
    def test_music_mix_uses_one_48khz_clock_and_non_pumping_limiter(self):
        audio_filter = build_music_mix_filter(120.0)
        self.assertIn("aresample=48000", audio_filter)
        self.assertIn("apad=whole_dur=120.0", audio_filter)
        self.assertIn("atrim=duration=120.0", audio_filter)
        self.assertIn("alimiter=", audio_filter)
        self.assertNotIn("loudnorm", audio_filter)

    def test_overlay_wrapping_caps_long_titles(self):
        title = (
            "Rough Country Cold Air Intake Kit for Ford Super Duty Powerstroke "
            "Diesel Engine with Washable Filter and Installation Hardware"
        )
        lines = wrap_lines_for_overlay(title, width=32, max_lines=3)
        self.assertLessEqual(len(lines), 3)
        self.assertTrue(lines[-1].endswith("..."))
        self.assertTrue(all(len(line) <= 32 for line in lines))

    def test_overlay_wrapping_preserves_short_titles(self):
        lines = wrap_lines_for_overlay(
            "Sinister Diesel Cold Air Intake",
            width=32,
            max_lines=3,
        )
        self.assertEqual(lines, ["Sinister Diesel Cold Air Intake"])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_product_segment_renders_visuals_with_narration_audio(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            image = temp_path / "product.jpg"
            audio = temp_path / "voice.mp3"
            audio_two = temp_path / "voice_two.mp3"
            output = temp_path / "segment.mp4"

            subprocess.run(
                [
                    shutil.which("ffmpeg"),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=white:s=1280x720:d=1",
                    "-frames:v",
                    "1",
                    str(image),
                ],
                check=True,
            )
            subprocess.run(
                [
                    shutil.which("ffmpeg"),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=3:sample_rate=44100",
                    "-c:a",
                    "libmp3lame",
                    str(audio),
                ],
                check=True,
            )
            subprocess.run(
                [
                    shutil.which("ffmpeg"),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=520:duration=2:sample_rate=24000",
                    "-c:a",
                    "libmp3lame",
                    str(audio_two),
                ],
                check=True,
            )

            result = create_product_segment_ffmpeg(
                None,
                [str(image)],
                [str(audio), str(audio_two)],
                "Rough Country Cold Air Intake Kit for Ford Super Duty Powerstroke Diesel Engine",
                str(output),
            )

            self.assertEqual(result, str(output))
            probe = probe_media(output, shutil.which("ffprobe"))
            self.assertTrue(probe["hasVideo"])
            self.assertTrue(probe["hasAudio"])
            self.assertGreater(probe["duration"], 4.5)
            self.assertEqual(probe["audioSampleRate"], 48000)
            self.assertLess(
                abs(probe["videoDuration"] - probe["audioDuration"]),
                0.08,
            )
            self.assertEqual(detect_silences(output, shutil.which("ffmpeg")), [])

            # "Check Price" CTA badge (bottom-right corner) is composited into
            # every segment; its scratch PNG must not survive the render.
            self.assertFalse((temp_path / "segment.mp4_ctabadge.png").exists())

    def test_different_titles_get_different_badge_color_variants(self):
        # The badge's color scheme is picked deterministically from the
        # product title so consecutive products in one video don't all show
        # an identical badge -- this only holds if distinct titles actually
        # hash to different variant indices often enough to matter.
        from cta_badge import _variant_for

        variants = {
            _variant_for(title)["bg"]
            for title in [
                "Rough Country Cold Air Intake Kit",
                "Wireless Bluetooth Headphones Noise Cancelling",
                "Stainless Steel Kitchen Knife Set",
                "Portable Camping Tent 4 Person",
                "Electric Standing Desk Adjustable Height",
            ]
        }
        self.assertGreater(len(variants), 1)


if __name__ == "__main__":
    unittest.main()
