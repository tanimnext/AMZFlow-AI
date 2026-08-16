"""generate_ai_music() -- opt-in Vertex AI (Lyria-002) background music.

Every call is a real, billed Vertex request, so caching by (project,
keyword) matters: a retried render or a re-generated keyword must not pay
for a second generation when the first is still on disk.
"""
import base64
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_files"))
import amazon_video_maker as avm  # noqa: E402


def _predict_response(wav_bytes=b"RIFF....WAVEfake"):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "predictions": [{"bytesBase64Encoded": base64.b64encode(wav_bytes).decode()}]
    }
    return resp


class AiMusicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(avm, "DATA_DIR", __import__("pathlib").Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        token_patcher = patch("vertex_auth.get_access_token", return_value="tok")
        token_patcher.start()
        self.addCleanup(token_patcher.stop)
        self.settings = {
            "vertex_service_account_private_key": '{"type": "service_account"}',
            "vertex_project_id": "proj",
            "vertex_location": "us-central1",
        }

    def test_missing_credentials_raises_clear_error(self):
        with self.assertRaises(RuntimeError) as ctx:
            avm.generate_ai_music("Best Widgets", {})
        self.assertIn("credentials", str(ctx.exception))

    def test_generates_and_caches_by_project_and_keyword(self):
        with patch("requests.post", return_value=_predict_response()) as mock_post:
            path1 = avm.generate_ai_music("Best Scalp Massagers", self.settings)
            self.assertTrue(os.path.isfile(path1))
            path2 = avm.generate_ai_music("Best Scalp Massagers", self.settings)
            self.assertEqual(path1, path2)
        # Only ONE real API call for two generate_ai_music() calls with the
        # same keyword -- the second must be served entirely from cache.
        self.assertEqual(mock_post.call_count, 1)

    def test_different_keyword_is_not_served_stale_cache(self):
        with patch("requests.post", return_value=_predict_response()) as mock_post:
            path1 = avm.generate_ai_music("Best Scalp Massagers", self.settings)
            path2 = avm.generate_ai_music("Best Tire Inflators", self.settings)
        self.assertNotEqual(path1, path2)
        self.assertEqual(mock_post.call_count, 2)

    def test_api_failure_raises_instead_of_silently_returning_no_music(self):
        failed = MagicMock(status_code=429, text="RESOURCE_EXHAUSTED")
        with patch("requests.post", return_value=failed):
            with self.assertRaises(RuntimeError):
                avm.generate_ai_music("Best Widgets", self.settings)


if __name__ == "__main__":
    unittest.main()
