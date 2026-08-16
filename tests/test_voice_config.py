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

        self.assertIn("natural, confident and conversational product reviewer", prompt)
        self.assertIn('say "LiDAR" as "lie-dar"', prompt)
        self.assertIn("Pause briefly before the verdict", prompt)
        self.assertTrue(prompt.endswith("The LiDAR sensor maps the room."))

    def test_direction_is_one_line_so_tts_cannot_narrate_it_as_prose(self):
        # Gemini TTS narrated the old multi-line "Director's notes:" block
        # aloud, so videos opened by speaking the style descriptor. The
        # direction must stay a single instruction clause ending in a colon.
        prompt = build_gemini_tts_prompt(
            "Real narration text goes here.",
            {"gemini_voice_style": "FRIENDLY_BUYER_GUIDE"},
        )
        direction, _, script = prompt.partition(": ")
        self.assertNotIn("\n", direction, "multi-line direction reads as prose to the model")
        self.assertNotIn("Director's notes", prompt)
        self.assertNotIn("Script:", prompt)
        self.assertEqual(script, "Real narration text goes here.")

    def test_prompt_pins_a_single_narrator(self):
        # Without an explicit single-narrator instruction the model sometimes
        # performed one script as a two-person read, alternating male and
        # female voices partway through.
        prompt = build_gemini_tts_prompt("Some narration.", {})
        self.assertIn("one single narrator", prompt)

    def test_a_huge_pronunciation_dictionary_does_not_bloat_the_direction(self):
        entries = "\n".join(f"Term{i}=tee {i}" for i in range(40))
        prompt = build_gemini_tts_prompt(
            "Narration.", {"gemini_pronunciations": entries}
        )
        direction = prompt.split(": ")[0]
        self.assertLessEqual(direction.count('say "'), 6)


if __name__ == "__main__":
    unittest.main()
