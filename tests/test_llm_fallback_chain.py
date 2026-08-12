"""_build_llm_chain() -- a render used to hard-fail every ASIN the instant
the one configured LLM provider had a bad key, hit a quota, or (Vertex AI)
wasn't fully set up, even when a second provider's key was sitting right
there in Settings, unused. The chain now falls back to any OTHER provider
that already has a key/credential saved, automatically."""
import unittest
from unittest.mock import patch

from app_files import amazon_video_maker as avm


class LlmFallbackChainTests(unittest.TestCase):
    def setUp(self):
        # Every provider's globals start empty/default for each test.
        patches = {
            "LLM_SERVICE": "gemini",
            "GEMINI_API_KEYS": [],
            "OPENROUTER_API_KEYS": [],
            "OPENAI_API_KEYS": [],
            "DEEPSEEK_API_KEYS": [],
            "LONGCAT_API_KEYS": [],
            "LLM_FALLBACK_ENABLED": False,
            "LLM_CHAIN_RAW": "",
            "VERTEX_PROJECT_ID": "",
            "VERTEX_SERVICE_ACCOUNT_JSON": "",
        }
        self._patchers = [patch.object(avm, name, value) for name, value in patches.items()]
        for p in self._patchers:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self._patchers])

    def test_no_other_provider_configured_is_a_single_entry_chain(self):
        avm.GEMINI_API_KEYS.append("gemini-key-1")
        chain = avm._build_llm_chain()
        self.assertEqual([entry["provider"] for entry in chain], ["gemini"])

    def test_falls_back_to_any_other_provider_with_a_saved_key_automatically(self):
        # Primary (gemini) has no usable key -- e.g. Vertex AI not fully
        # configured yet, matching the real-world log this was written for.
        avm.OPENAI_API_KEYS.append("sk-openai-key")
        chain = avm._build_llm_chain()
        providers = [entry["provider"] for entry in chain]
        self.assertIn("gemini", providers)
        self.assertIn("openai", providers)
        # Primary keeps its slot even with no key -- call_chain() reports
        # "no key configured" for it and moves on, which is what surfaces
        # the real problem to the user instead of silently hiding it.
        self.assertEqual(providers[0], "gemini")

    def test_providers_with_no_key_are_never_added_as_fallback(self):
        # Nothing configured anywhere -- the chain must not fabricate
        # unusable entries for providers with empty api_keys.
        chain = avm._build_llm_chain()
        self.assertEqual([entry["provider"] for entry in chain], ["gemini"])
        self.assertEqual(chain[0]["api_keys"], [])

    def test_manual_fallback_chain_is_tried_before_the_automatic_one(self):
        avm.LLM_FALLBACK_ENABLED = True
        avm.LLM_CHAIN_RAW = "deepseek|deepseek-chat"
        avm.DEEPSEEK_API_KEYS.append("deepseek-key")
        avm.OPENAI_API_KEYS.append("sk-openai-key")
        chain = avm._build_llm_chain()
        providers = [entry["provider"] for entry in chain]
        self.assertEqual(providers, ["gemini", "deepseek", "openai"])

    def test_vertex_gemini_without_project_id_is_skipped_as_a_fallback(self):
        # _provider_config("vertex_gemini") returns empty api_keys when auth
        # fails (no project ID / service account configured) -- must not be
        # added as a fallback entry that will just fail again.
        avm.LLM_SERVICE = "openai"
        avm.OPENAI_API_KEYS.append("sk-openai-key")
        chain = avm._build_llm_chain()
        self.assertNotIn("vertex_gemini", [entry["provider"] for entry in chain])


if __name__ == "__main__":
    unittest.main()
