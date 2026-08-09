import json
import tempfile
import unittest
from pathlib import Path


class MusicManagerTests(unittest.TestCase):
    def setUp(self):
        from app_files import music_manager

        self.music = music_manager
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.manifest = self.root / "music_library.json"
        self.manifest.write_text(
            json.dumps(
                {
                    "tracks": [
                        {"id": "tech", "file": "tech.mp3", "moods": ["tech"], "license": "generated-original"},
                        {"id": "warm", "file": "warm.mp3", "moods": ["home"], "license": "generated-original"},
                        {"id": "neutral", "file": "neutral.mp3", "moods": ["general"], "license": "generated-original"},
                    ]
                }
            )
        )
        for name in ("tech.mp3", "warm.mp3", "neutral.mp3"):
            (self.root / name).write_bytes(b"audio")

    def tearDown(self):
        self.temp.cleanup()

    def test_routes_category_and_avoids_recent_tracks(self):
        chosen = self.music.select_track(
            "best laptop docking station",
            self.manifest,
            recent_track_ids=["tech"],
        )
        self.assertEqual(chosen["id"], "neutral")

    def test_home_keyword_routes_to_warm_track(self):
        chosen = self.music.select_track("quiet kitchen appliance", self.manifest)
        self.assertEqual(chosen["id"], "warm")

    def test_rejects_track_path_outside_library(self):
        self.manifest.write_text(
            json.dumps(
                {"tracks": [{"id": "bad", "file": "../secret.mp3", "moods": ["general"], "license": "unknown"}]}
            )
        )
        with self.assertRaises(ValueError):
            self.music.select_track("anything", self.manifest)


if __name__ == "__main__":
    unittest.main()
