"""Whole-video speed control (apply_video_speed) -- Phase 4 of the AmzFlow AI
rebuild. v6 had no speed handling anywhere: every `setpts` in the codebase was
a timestamp reset, never a rate change."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app_files.amazon_video_maker import apply_video_speed
from app_files.media_qc import probe_media


def _make_clip(path: Path, duration: float):
    subprocess.run(
        [
            shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"color=c=blue:s=640x360:d={duration}:r=25",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}:sample_rate=48000",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(path),
        ],
        check=True,
    )


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class VideoSpeedTests(unittest.TestCase):
    def test_no_op_at_1x_returns_the_same_file_and_duration(self):
        with tempfile.TemporaryDirectory() as temp:
            clip = Path(temp) / "in.mp4"
            _make_clip(clip, 2.0)
            out_path, out_duration = apply_video_speed(str(clip), 2.0, temp, speed=1.0)
            self.assertEqual(out_path, str(clip))
            self.assertEqual(out_duration, 2.0)

    def test_speeding_up_shortens_output_duration_proportionally(self):
        with tempfile.TemporaryDirectory() as temp:
            clip = Path(temp) / "in.mp4"
            base_duration = 4.0
            _make_clip(clip, base_duration)
            speed = 1.25
            out_path, reported_duration = apply_video_speed(
                str(clip), base_duration, temp, speed=speed
            )
            self.assertNotEqual(out_path, str(clip))
            self.assertAlmostEqual(reported_duration, base_duration / speed, places=2)

            probe = probe_media(out_path, shutil.which("ffprobe"))
            self.assertTrue(probe["hasVideo"])
            self.assertTrue(probe["hasAudio"])
            self.assertAlmostEqual(probe["duration"], base_duration / speed, delta=0.15)
            # Speed changes pitch-preserving tempo (atempo), not sample rate.
            self.assertEqual(probe["audioSampleRate"], 48000)

    def test_slowing_down_lengthens_output_duration_proportionally(self):
        with tempfile.TemporaryDirectory() as temp:
            clip = Path(temp) / "in.mp4"
            base_duration = 3.0
            _make_clip(clip, base_duration)
            speed = 0.8
            out_path, reported_duration = apply_video_speed(
                str(clip), base_duration, temp, speed=speed
            )
            self.assertAlmostEqual(reported_duration, base_duration / speed, places=2)
            probe = probe_media(out_path, shutil.which("ffprobe"))
            self.assertAlmostEqual(probe["duration"], base_duration / speed, delta=0.15)

    def test_original_file_is_removed_after_a_successful_speed_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            clip = Path(temp) / "in.mp4"
            _make_clip(clip, 2.0)
            apply_video_speed(str(clip), 2.0, temp, speed=1.1)
            self.assertFalse(clip.exists())


if __name__ == "__main__":
    unittest.main()
