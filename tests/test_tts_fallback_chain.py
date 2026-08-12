"""_build_tts_chain() -- mirrors test_llm_fallback_chain.py for voice. A
provider failure used to always fall straight to Edge with no way to prefer
a second configured paid provider (ElevenLabs, Cartesia, ...) first; the
chain now tries the user's drag-ordered list, then any other provider with
a saved credential, and only then Edge as the final safety net."""
import asyncio
import unittest
from unittest.mock import patch

from app_files import amazon_video_maker as avm


class TtsFallbackChainTests(unittest.TestCase):
    def setUp(self):
        patches = {
            "TTS_SERVICE": "elevenlabs",
            "ELEVENLABS_API_KEY": "",
            "CARTESIA_API_KEY": "",
            "AI33PRO_API_KEY": "",
            "DEEPGRAM_API_KEY": "",
            "GOOGLE_TTS_VOICE_ID": "en-US-Chirp3-HD-Sulafat",
            "GEMINI_API_KEYS": [],
            "VERTEX_PROJECT_ID": "",
            "VERTEX_SERVICE_ACCOUNT_JSON": "",
            "TTS_FALLBACK_ENABLED": False,
            "TTS_CHAIN_RAW": "",
        }
        self._patchers = [patch.object(avm, name, value) for name, value in patches.items()]
        for p in self._patchers:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self._patchers])

    def test_edge_is_always_the_final_entry_even_with_nothing_else_configured(self):
        chain = avm._build_tts_chain()
        self.assertEqual(chain, ["elevenlabs", "edge"])

    def test_kokoro_and_edge_never_need_a_credential(self):
        self.assertTrue(avm._tts_provider_has_credentials("kokoro"))
        self.assertTrue(avm._tts_provider_has_credentials("edge"))
        self.assertFalse(avm._tts_provider_has_credentials("elevenlabs"))

    def test_falls_back_to_another_configured_provider_before_edge(self):
        avm.CARTESIA_API_KEY = "cartesia-key"
        chain = avm._build_tts_chain()
        self.assertEqual(chain, ["elevenlabs", "cartesia", "edge"])

    def test_manual_drag_ordered_chain_is_tried_before_the_automatic_one(self):
        avm.TTS_FALLBACK_ENABLED = True
        avm.TTS_CHAIN_RAW = "vertex_gemini\ngemini"
        avm.VERTEX_PROJECT_ID = "proj"
        avm.VERTEX_SERVICE_ACCOUNT_JSON = "{}"
        avm.GEMINI_API_KEYS = ["g1"]
        avm.CARTESIA_API_KEY = "cartesia-key"  # only reachable via auto-fallback
        chain = avm._build_tts_chain()
        # google_cloud_tts shares vertex_gemini's credential, so it's also
        # auto-fallback-eligible once VERTEX_PROJECT_ID/JSON are set above.
        self.assertEqual(chain, ["elevenlabs", "vertex_gemini", "gemini", "cartesia", "google_cloud_tts", "edge"])

    def test_kokoro_primary_does_not_duplicate_edge(self):
        avm.TTS_SERVICE = "edge"
        chain = avm._build_tts_chain()
        self.assertEqual(chain, ["edge"])

    def test_manual_chain_entries_are_kept_even_without_a_credential_yet(self):
        # Matches _build_llm_chain(): a manually-listed entry is the user's
        # explicit choice and is not silently dropped at build time even if
        # it isn't configured yet -- it simply fails (and is skipped) when
        # _tts_provider_once() actually tries it.
        avm.TTS_FALLBACK_ENABLED = True
        avm.TTS_CHAIN_RAW = "cartesia"  # no CARTESIA_API_KEY set
        chain = avm._build_tts_chain()
        self.assertIn("cartesia", chain)

    def test_deepgram_is_tried_as_an_automatic_fallback_when_credentialed(self):
        avm.DEEPGRAM_API_KEY = "deepgram-key"
        chain = avm._build_tts_chain()
        self.assertEqual(chain, ["elevenlabs", "deepgram", "edge"])

    def test_google_cloud_tts_shares_the_vertex_service_account_credential(self):
        # Same auth as vertex_gemini (Vertex service-account JSON), different
        # API (texttospeech.googleapis.com) -- both become usable together.
        avm.VERTEX_PROJECT_ID = "proj"
        avm.VERTEX_SERVICE_ACCOUNT_JSON = "{}"
        chain = avm._build_tts_chain()
        self.assertIn("google_cloud_tts", chain)
        self.assertIn("vertex_gemini", chain)

    def test_unknown_provider_ids_in_the_manual_chain_are_rejected(self):
        avm.TTS_FALLBACK_ENABLED = True
        avm.TTS_CHAIN_RAW = "not-a-real-provider"
        chain = avm._build_tts_chain()
        self.assertNotIn("not-a-real-provider", chain)


class PermanentTtsErrorTests(unittest.TestCase):
    """_tts_with_retry makes four attempts and each one walks the whole
    chain. A missing/rejected credential fails identically every time, so
    re-trying it just adds latency to a render that is already failing --
    those providers are dropped for the remaining attempts. Rate limits and
    timeouts must NOT be treated this way; they are why retries exist."""

    def test_credential_and_quota_failures_are_permanent(self):
        for message in (
            "A Deepgram API key is required",
            "No Vertex AI project ID configured (Settings -> AI Provider -> Google Cloud)",
            "Google Cloud TTS monthly character cap reached (1,000,000/1,000,000 this month).",
            "ElevenLabs error: HTTP 401 unauthorized",
            "Cartesia error: 403 forbidden",
        ):
            self.assertTrue(
                avm._tts_error_is_permanent(Exception(message)),
                f"should be permanent: {message}",
            )

    def test_transient_failures_are_still_retried(self):
        for message in (
            "HTTP 429: rate limited, slow down",
            "ffmpeg timed out after 120s",
            "HTTP 503: service unavailable",
            "Connection aborted, ConnectionResetError(54, 'Connection reset by peer')",
        ):
            self.assertFalse(
                avm._tts_error_is_permanent(Exception(message)),
                f"should stay retryable: {message}",
            )


class UnusableProviderSkipTests(unittest.TestCase):
    """The `unusable` set is populated by a permanent failure on one attempt
    and honoured by the next, so a dead credential costs one attempt per
    render chunk instead of four."""

    def setUp(self):
        patches = {
            "TTS_SERVICE": "elevenlabs",
            "ELEVENLABS_API_KEY": "el-key",
            "CARTESIA_API_KEY": "cart-key",
            "AI33PRO_API_KEY": "",
            "DEEPGRAM_API_KEY": "",
            "GEMINI_API_KEYS": [],
            "VERTEX_PROJECT_ID": "",
            "VERTEX_SERVICE_ACCOUNT_JSON": "",
            "TTS_FALLBACK_ENABLED": False,
            "TTS_CHAIN_RAW": "",
        }
        self._patchers = [patch.object(avm, name, value) for name, value in patches.items()]
        for p in self._patchers:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self._patchers])

    def _run_once(self, unusable):
        attempted = []

        def fake_synthesize(text, output_path, config, **kwargs):
            attempted.append(config["service"])
            if config["service"] == "elevenlabs":
                raise RuntimeError("ElevenLabs error: HTTP 401 unauthorized")
            return {"provider": config["service"]}

        with patch.object(avm.tts_engine, "synthesize", side_effect=fake_synthesize):
            asyncio.run(avm._tts_provider_once("hello", "/tmp/out.mp3", unusable=unusable))
        return attempted

    def test_a_permanently_failed_provider_is_recorded(self):
        unusable = set()
        attempted = self._run_once(unusable)
        self.assertEqual(attempted, ["elevenlabs", "cartesia"])
        self.assertIn("elevenlabs", unusable)

    def test_a_recorded_provider_is_skipped_on_the_next_attempt(self):
        attempted = self._run_once({"elevenlabs"})
        self.assertNotIn("elevenlabs", attempted)
        self.assertEqual(attempted, ["cartesia"])


if __name__ == "__main__":
    unittest.main()
