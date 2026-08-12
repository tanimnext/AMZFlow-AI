"""_build_tts_chain() -- mirrors test_llm_fallback_chain.py for voice. A
provider failure used to always fall straight to Edge with no way to prefer
a second configured paid provider (ElevenLabs, Cartesia, ...) first; the
chain now tries the user's drag-ordered list, then any other provider with
a saved credential, and only then Edge as the final safety net."""
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


if __name__ == "__main__":
    unittest.main()
