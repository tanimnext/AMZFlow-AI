import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app_files.media_qc import run_media_qc


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class MediaQcIntegrationTests(unittest.TestCase):
    def test_ffmpeg_generated_av_file_passes_real_probe_and_silence_scan(self):
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / "healthy.mp4"
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
                    "color=c=blue:s=320x180:d=3:r=25",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=3:sample_rate=44100",
                    "-shortest",
                    "-c:v",
                    "libx264",
                    "-c:a",
                    "aac",
                    str(video),
                ],
                check=True,
            )
            report = run_media_qc(
                final_path=video,
                planned_segment_ids=["intro-0"],
                rendered_segments={"intro-0": str(video)},
                expected_duration=3.0,
                ffmpeg_bin=shutil.which("ffmpeg"),
                ffprobe_bin=shutil.which("ffprobe"),
            )
            self.assertEqual(report["status"], "PASSED", report)


if __name__ == "__main__":
    unittest.main()
