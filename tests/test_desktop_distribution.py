import hashlib
import io
import tempfile
import unittest
import zipfile
from pathlib import Path


class DesktopDistributionTests(unittest.TestCase):
    def test_artifact_name_is_stable_and_has_no_spaces(self):
        from scripts.build_dist import artifact_base

        self.assertEqual(
            artifact_base("7.2.0", "windows-x64"),
            "AmzFlow-AI-7.2.0-windows-x64",
        )

    def test_model_snapshot_copy_dereferences_huggingface_symlinks(self):
        from scripts.build_dist import copy_model_snapshot

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            blob = root / "blobs" / "model-data"
            blob.parent.mkdir()
            blob.write_bytes(b"weights")
            snapshot = root / "snapshots" / "revision"
            snapshot.mkdir(parents=True)
            (snapshot / "kokoro-v1_0.pth").symlink_to(Path("../../blobs/model-data"))
            copied = copy_model_snapshot(snapshot, root / "staged")
            self.assertFalse((copied / "kokoro-v1_0.pth").is_symlink())
            self.assertEqual((copied / "kokoro-v1_0.pth").read_bytes(), b"weights")

    def test_semantic_version_comparison_ignores_v_prefix(self):
        from web_app.update_manager import is_newer_version

        self.assertTrue(is_newer_version("v7.1.0", "7.0.9"))
        self.assertFalse(is_newer_version("7.0.0", "v7.0.0"))
        self.assertFalse(is_newer_version("7.0.0-beta.1", "7.0.0"))

    def test_release_asset_is_selected_for_the_current_platform(self):
        from web_app.update_manager import select_release_assets

        release = {
            "tag_name": "v7.2.0",
            "assets": [
                {"name": "AmzFlow-AI-7.2.0-macos-arm64.zip", "browser_download_url": "mac"},
                {"name": "AmzFlow-AI-7.2.0-windows-x64.zip", "browser_download_url": "win"},
                {"name": "AmzFlow-AI-7.2.0-windows-x64.zip.sha256", "browser_download_url": "sum"},
            ],
        }
        archive, checksum = select_release_assets(release, "windows-x64")
        self.assertEqual(archive["browser_download_url"], "win")
        self.assertEqual(checksum["browser_download_url"], "sum")

    def test_checksum_verification_accepts_only_matching_digest(self):
        from web_app.update_manager import verify_checksum

        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "release.zip"
            archive.write_bytes(b"portable release")
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            self.assertTrue(verify_checksum(archive, f"{digest}  release.zip\n"))
            self.assertFalse(verify_checksum(archive, f"{'0' * 64}  release.zip\n"))

    def test_bundled_binary_wins_over_path_lookup(self):
        from web_app.runtime_support import resolve_binary

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / "bin" / "ffmpeg"
            binary.parent.mkdir()
            binary.write_bytes(b"binary")
            self.assertEqual(resolve_binary("ffmpeg", resource_root=root), str(binary))

    def test_bundled_kokoro_files_are_resolved_without_network(self):
        from web_app.runtime_support import kokoro_files

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_root = root / "models" / "kokoro"
            (model_root / "voices").mkdir(parents=True)
            (model_root / "config.json").write_text("{}", encoding="utf-8")
            (model_root / "kokoro-v1_0.pth").write_bytes(b"model")
            (model_root / "voices" / "af_heart.pt").write_bytes(b"voice")
            files = kokoro_files("af_heart", resource_root=root)
            self.assertEqual(files["voice"], model_root / "voices" / "af_heart.pt")
            self.assertTrue(files["complete"])

    def test_flask_uses_explicit_packaged_template_and_static_paths(self):
        from web_app import app as application

        self.assertEqual(
            Path(application.app.template_folder), Path(application.BASE_DIR) / "templates"
        )
        self.assertEqual(
            Path(application.app.static_folder), Path(application.BASE_DIR) / "static"
        )

    def test_updater_rejects_archive_path_traversal(self):
        from desktop_updater import safe_extract

        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as bundle:
            bundle.writestr("../outside.txt", "bad")
        payload.seek(0)
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "bad.zip"
            archive.write_bytes(payload.read())
            with self.assertRaises(ValueError):
                safe_extract(archive, Path(tmp) / "extract")


if __name__ == "__main__":
    unittest.main()
