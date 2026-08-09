import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


class ProductCoreTests(unittest.TestCase):
    def setUp(self):
        from web_app import product_core

        self.core = product_core
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_remote_url_rejects_local_and_non_https_destinations(self):
        self.assertFalse(self.core.is_safe_https_url("http://example.com/audio.mp3"))
        self.assertFalse(self.core.is_safe_https_url("https://127.0.0.1/audio.mp3"))
        self.assertFalse(
            self.core.is_safe_https_url("https://169.254.169.254/metadata")
        )
        self.assertFalse(
            self.core.is_safe_https_url("https://user:pass@example.com/audio.mp3")
        )

    def test_resolve_project_dir_rejects_traversal_and_absolute_paths(self):
        for unsafe in ("../outside", "..", "/tmp/outside", "a/../../outside", ""):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(ValueError):
                    self.core.resolve_project_dir(self.root, unsafe)

    def test_resolve_project_dir_accepts_nested_project_id_within_root(self):
        path = self.core.resolve_project_dir(self.root, "coffee-maker/20260727-ab12cd34")
        self.assertEqual(
            path, self.root.resolve() / "coffee-maker" / "20260727-ab12cd34"
        )

    def test_order_products_has_one_canonical_direction(self):
        products = [{"title": "One"}, {"title": "Two"}, {"title": "Three"}]
        listed = self.core.order_products(products, "list")
        countdown = self.core.order_products(products, "countdown")
        self.assertEqual([p["rank"] for p in listed], [1, 2, 3])
        self.assertEqual([p["title"] for p in listed], ["One", "Two", "Three"])
        self.assertEqual([p["rank"] for p in countdown], [3, 2, 1])
        self.assertEqual([p["title"] for p in countdown], ["Three", "Two", "One"])

    def test_settings_response_redacts_secrets_recursively(self):
        settings = {
            "edge_voice": "en-US-AndrewNeural",
            "elevenlabs_api_key": "secret",
            "creators_api_client_id": "client-id",
            "nested": {"access_token": "token", "safe": True},
        }
        public = self.core.public_settings(settings)
        self.assertEqual(public["edge_voice"], "en-US-AndrewNeural")
        self.assertNotIn("elevenlabs_api_key", public)
        self.assertNotIn("creators_api_client_id", public)
        self.assertNotIn("access_token", public["nested"])
        self.assertTrue(public["nested"]["safe"])

    def test_output_root_must_be_a_safe_user_folder(self):
        home = self.root / "home"
        home.mkdir()
        selected = home / "Videos" / "Reviews"
        validated = self.core.validate_output_root(selected, home=home)
        self.assertEqual(validated, selected.resolve())
        for unsafe in (Path("/"), home.parent / "outside", home):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(ValueError):
                    self.core.validate_output_root(unsafe, home=home)

    def test_create_project_package_writes_required_files(self):
        rendered = self.root / "rendered.mp4"
        thumb = self.root / "rendered.jpg"
        rendered.write_bytes(b"video")
        thumb.write_bytes(b"image")
        metadata = {
            "title": "Best Coffee Makers",
            "description": "Evidence-based buying guide.",
            "tags": ["coffee maker", "buying guide"],
            "hashtags": ["Coffee"],
            "chapters": ["00:00 Intro", "00:10 Product 1"],
        }
        project = self.core.create_project_package(
            output_root=self.root / "library",
            keyword="Best Coffee Makers",
            video_path=rendered,
            thumbnail_path=thumb,
            metadata=metadata,
            products=[{"rank": 1, "title": "Product One"}],
            sources=[{"url": "https://example.com/product"}],
            qc_report={"status": "PASSED"},
            now=datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc),
        )
        for name in (
            "best-coffee-makers.mp4",
            "Thumbnail.jpg",
            "youtube.txt",
        ):
            self.assertTrue((project / name).exists(), name)
        self.assertEqual(sorted(p.name for p in project.iterdir()), [
            "Thumbnail.jpg",
            "best-coffee-makers.mp4",
            "youtube.txt",
        ])
        text = (project / "youtube.txt").read_text(encoding="utf-8")
        self.assertIn("[TITLE]\nBest Coffee Makers", text)
        self.assertIn("[TAGS]\ncoffee maker, buying guide", text)
        parsed = self.core.parse_youtube_text(text)
        self.assertEqual(parsed["title"], "Best Coffee Makers")

    def test_publish_time_must_be_future_and_private(self):
        now = datetime.now(timezone.utc)
        with self.assertRaises(ValueError):
            self.core.validate_publish_options(
                "unlisted", (now + timedelta(hours=1)).isoformat(), now=now
            )
        with self.assertRaises(ValueError):
            self.core.validate_publish_options(
                "private", (now - timedelta(minutes=1)).isoformat(), now=now
            )
        privacy, publish_at = self.core.validate_publish_options(
            "private", (now + timedelta(hours=1)).isoformat(), now=now
        )
        self.assertEqual(privacy, "private")
        self.assertIsNotNone(publish_at)


if __name__ == "__main__":
    unittest.main()
