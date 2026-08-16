"""synth_vertex_gemini() region fallback on 429 RESOURCE_EXHAUSTED.

Vertex quota is per-region, not per-project -- a user hit a hard 429 in
us-central1 with no other TTS provider configured (empty tts_chain), so the
whole render failed. The fix retries the identical request against other
locations (starting with the pooled "global" endpoint) before giving up.
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web_app"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_files"))
import tts_engine  # noqa: E402


def _resp(status_code, json_body=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text or str(json_body)
    resp.json.return_value = json_body or {}
    return resp


class VertexTtsRegionFallbackTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "vertex_service_account_private_key": '{"type": "service_account"}',
            "vertex_project_id": "proj",
            "vertex_location": "us-central1",
            "vertex_tts_model": "gemini-2.5-flash-preview-tts",
        }
        patcher = patch("vertex_auth.get_access_token", return_value="tok")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_429_in_primary_region_falls_back_to_global_and_succeeds(self):
        ok = _resp(200, {
            "candidates": [{"content": {"parts": [{"inlineData": {"data": "AAAA"}}]}}]
        })
        rate_limited = _resp(429, text='{"error": {"status": "RESOURCE_EXHAUSTED"}}')

        calls = []

        def fake_post(url, **kwargs):
            calls.append(url)
            return rate_limited if "us-central1" in url else ok

        with patch("requests.post", side_effect=fake_post), \
                patch.object(tts_engine, "_run_ffmpeg"):
            tts_engine.synth_vertex_gemini("hello world", "/tmp/out.mp3", self.config)

        self.assertEqual(len(calls), 2)
        self.assertIn("us-central1-aiplatform.googleapis.com", calls[0])
        self.assertIn("aiplatform.googleapis.com", calls[1])
        self.assertNotIn("us-central1", calls[1])

    def test_all_regions_exhausted_raises_with_last_error_visible(self):
        rate_limited = _resp(429, text='{"error": {"status": "RESOURCE_EXHAUSTED"}}')
        with patch("requests.post", return_value=rate_limited):
            with self.assertRaises(tts_engine.TTSError) as ctx:
                tts_engine.synth_vertex_gemini("hello world", "/tmp/out.mp3", self.config)
        self.assertIn("RESOURCE_EXHAUSTED", str(ctx.exception))

    def test_non_retryable_error_does_not_try_other_regions(self):
        bad_auth = _resp(401, text='{"error": "unauthorized"}')
        calls = []

        def fake_post(url, **kwargs):
            calls.append(url)
            return bad_auth

        with patch("requests.post", side_effect=fake_post):
            with self.assertRaises(tts_engine.TTSError):
                tts_engine.synth_vertex_gemini("hello world", "/tmp/out.mp3", self.config)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
