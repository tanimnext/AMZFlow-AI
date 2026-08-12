"""Google Cloud TTS monthly character cap.

Google's free tier (1M characters/month for Chirp3-HD) resets monthly, and a
Cloud Billing budget alert does not stop spending -- it emails after the money
is already gone. The only thing that actually prevents a surprise bill is
refusing to send the request, so the tool keeps its own count and enforces it
locally before calling out.
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web_app"))
import tts_engine  # noqa: E402


class GoogleTtsQuotaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.usage_file = os.path.join(self.tmp.name, "google_tts_usage.json")
        patcher = patch.object(tts_engine, "_google_tts_usage_path", lambda: self.usage_file)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write_usage(self, month, chars):
        with open(self.usage_file, "w", encoding="utf-8") as handle:
            json.dump({"month": month, "chars": chars}, handle)

    def _this_month(self):
        return datetime.now(timezone.utc).strftime("%Y-%m")

    def test_usage_starts_at_zero_when_nothing_recorded_yet(self):
        self.assertEqual(tts_engine.google_tts_usage()["chars"], 0)

    def test_recording_accumulates_within_the_same_month(self):
        tts_engine._google_tts_record(400)
        tts_engine._google_tts_record(600)
        self.assertEqual(tts_engine.google_tts_usage()["chars"], 1000)

    def test_a_previous_months_total_does_not_carry_over(self):
        # The free allowance resets monthly; carrying September's count into
        # October would lock the user out of an allowance they actually have.
        self._write_usage("2000-01", 999_999)
        usage = tts_engine.google_tts_usage()
        self.assertEqual(usage["chars"], 0)
        self.assertEqual(usage["month"], self._this_month())

    def test_a_corrupt_counter_file_does_not_break_synthesis(self):
        with open(self.usage_file, "w", encoding="utf-8") as handle:
            handle.write("{ not json")
        self.assertEqual(tts_engine.google_tts_usage()["chars"], 0)

    def test_request_is_refused_before_it_would_exceed_the_cap(self):
        self._write_usage(self._this_month(), 999_990)
        config = {
            "vertex_project_id": "proj",
            "vertex_service_account_private_key": "{}",
            "google_tts_monthly_char_limit": "1000000",
        }
        with patch.object(tts_engine, "requests") as requests_mock, \
                patch.dict(sys.modules, {"vertex_auth": _FakeVertexAuth()}):
            with self.assertRaises(tts_engine.TTSError) as caught:
                tts_engine.synth_google_cloud_tts("x" * 50, "/tmp/unused.mp3", config)
        self.assertIn("monthly character cap", str(caught.exception))
        # The whole point is that no billable request leaves the machine.
        requests_mock.post.assert_not_called()

    def test_zero_limit_means_the_user_opted_out_of_the_cap(self):
        self.assertEqual(tts_engine._google_tts_char_limit({"google_tts_monthly_char_limit": "0"}), 0)

    def test_blank_or_junk_limit_falls_back_to_the_free_tier_allowance(self):
        for value in ("", "   ", "not-a-number"):
            self.assertEqual(
                tts_engine._google_tts_char_limit({"google_tts_monthly_char_limit": value}),
                tts_engine.GOOGLE_TTS_DEFAULT_CHAR_LIMIT,
            )


class _FakeVertexAuth:
    @staticmethod
    def get_access_token(_json):
        return "fake-token"


if __name__ == "__main__":
    unittest.main()
