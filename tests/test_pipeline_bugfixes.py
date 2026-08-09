"""Regression tests for the remaining v6 bug-audit fixes that don't need a
real ffmpeg render: the music-library skip-not-abort fix, the QC
fail-closed-on-probe-failure fix, and metadata_generator.get_related_videos
actually using its parameters."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app_files import music_manager
from app_files import media_qc


class MusicManagerSkipTests(unittest.TestCase):
    def _write_library(self, temp_dir, tracks):
        manifest = Path(temp_dir) / "music_library.json"
        manifest.write_text(json.dumps({"tracks": tracks}), encoding="utf-8")
        return manifest

    def test_path_traversal_entry_is_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as temp:
            good = Path(temp) / "ok.mp3"
            good.write_bytes(b"fake")
            manifest = self._write_library(
                temp,
                [
                    {"file": "../escape.mp3", "license": "CC0", "id": "bad", "moods": ["general"]},
                    {"file": "ok.mp3", "license": "CC0", "id": "good", "moods": ["general"]},
                ],
            )
            # Previously: ValueError("Music track escapes its library") raised
            # out of select_track entirely, silencing music for the whole run.
            chosen = music_manager.select_track("test keyword", manifest)
            self.assertEqual(chosen["id"], "good")

    def test_raises_only_when_no_track_is_usable(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest = self._write_library(
                temp, [{"file": "../escape.mp3", "license": "CC0", "id": "bad", "moods": ["general"]}]
            )
            with self.assertRaises(ValueError):
                music_manager.select_track("test keyword", manifest)


class MediaQcFailClosedTests(unittest.TestCase):
    def test_detect_silences_raises_on_ffmpeg_failure(self):
        # A failing ffmpeg invocation (bad binary, corrupt input) must not be
        # swallowed into an empty silence list -- that's the one QC signal
        # meant to catch missing/broken narration.
        with self.assertRaises(Exception):
            media_qc.detect_silences("/nonexistent/file.mp4", ffmpeg_bin="ffmpeg")

    def test_run_media_qc_reports_probe_failure_instead_of_passing(self):
        report = media_qc.run_media_qc(
            final_path="/nonexistent/file.mp4",
            planned_segment_ids=["intro-0"],
            rendered_segments={"intro-0": "seg.mp4"},
            expected_duration=10.0,
        )
        self.assertEqual(report["status"], "FAILED")


class RelatedVideosParamTests(unittest.TestCase):
    def test_uses_the_passed_channel_url_not_the_global(self):
        from app_files import metadata_generator as mg

        captured = {}

        def fake_get(url, timeout=10):
            captured["url"] = url
            response = Mock()
            response.json.return_value = {"items": []}
            return response

        with patch.object(mg, "YOUTUBE_API_KEY", "fake-key"), patch.object(
            mg.requests, "get", side_effect=fake_get
        ):
            mg.get_related_videos("https://youtube.com/@SomeOtherChannel", "cold air intake")

        self.assertIn("SomeOtherChannel", captured["url"])


if __name__ == "__main__":
    unittest.main()
