import unittest

from web_app.voice_config import (
    GEMINI_TTS_MODELS,
    GEMINI_TTS_VOICES,
    build_gemini_tts_prompt,
    normalize_gemini_tts_settings,
)


class GeminiVoiceConfigTests(unittest.TestCase):
    def test_exposes_current_models_and_all_official_voices(self):
        self.assertIn("gemini-3.1-flash-tts-preview", GEMINI_TTS_MODELS)
        self.assertIn("gemini-2.5-pro-preview-tts", GEMINI_TTS_MODELS)
        self.assertEqual(len(GEMINI_TTS_VOICES), 30)
        # Voice labels include gender now (Chirp3-HD voice list), not just style.
        self.assertEqual(GEMINI_TTS_VOICES["Sadaltager"], "Knowledgeable (Male)")

    def test_normalizes_untrusted_voice_settings(self):
        settings = normalize_gemini_tts_settings(
            {
                "gemini_tts_model": "not-a-model",
                "gemini_tts_voice": "not-a-voice",
                "gemini_voice_style": "unknown",
                "gemini_voice_pace": "999",
                "gemini_voice_energy": "-2",
                "gemini_voice_warmth": "60",
                "gemini_voice_accent": "<script>",
                "gemini_voice_instruction": "x" * 800,
                "gemini_pronunciations": "LiDAR=lie-dar\nASIN=A sin\nbad-line",
            }
        )

        self.assertEqual(settings["model"], "gemini-3.1-flash-tts-preview")
        self.assertEqual(settings["voice"], "Sadaltager")
        self.assertEqual(settings["pace"], 100)
        self.assertEqual(settings["energy"], 0)
        self.assertEqual(settings["accent"], "US_NEUTRAL")
        self.assertLessEqual(len(settings["instruction"]), 500)
        self.assertEqual(settings["pronunciations"]["LiDAR"], "lie-dar")

    def test_prompt_directs_style_and_exact_script(self):
        prompt = build_gemini_tts_prompt(
            "The LiDAR sensor maps the room.",
            {
                "gemini_voice_style": "TRUSTED_EXPERT",
                "gemini_voice_pace": 48,
                "gemini_voice_energy": 42,
                "gemini_voice_warmth": 68,
                "gemini_voice_accent": "US_NEUTRAL",
                "gemini_voice_instruction": "Pause briefly before the verdict.",
                "gemini_pronunciations": "LiDAR=lie-dar",
            },
        )

        self.assertIn("trusted product-review expert", prompt)
        self.assertIn('Pronounce "LiDAR" as "lie-dar"', prompt)
        self.assertIn("Pause briefly before the verdict.", prompt)
        self.assertTrue(prompt.endswith("The LiDAR sensor maps the room."))


if __name__ == "__main__":
    unittest.main()
