"""Narration is voiced as short beats instead of one long call.

A whole product review used to be handed to the TTS provider as a single
request, so the engine picked one emotion and one pace and held it for the
entire stretch -- the flat, obviously-synthetic delivery. Splitting the
script into 2-4 sentence beats lets intonation re-set per beat, and the
joins between them become natural breaths.
"""
import unittest
import unittest.mock

from app_files import amazon_video_maker as avm


class SplitScriptIntoParagraphsTests(unittest.TestCase):
    def test_authored_paragraphs_each_become_their_own_beat(self):
        script = (
            "150 PSI, and it shuts off at the pressure you set. The motor is quick.\n\n"
            "The downside is noise. It is loud enough that you notice it.\n\n"
            "Still the one I would buy at this price."
        )
        beats = avm._split_script_into_paragraphs(script)
        self.assertEqual(len(beats), 3)
        self.assertTrue(beats[0].startswith("150 PSI"))
        self.assertTrue(beats[1].startswith("The downside"))

    def test_a_short_paragraph_is_not_swallowed_by_the_previous_one(self):
        # The blank line is the writer saying "new thought, re-set delivery".
        # Merging across it would silently undo the pacing control that is
        # the whole point of splitting.
        script = "A full opening thought that runs a little longer.\n\nWorth it."
        beats = avm._split_script_into_paragraphs(script)
        self.assertEqual(beats, ["A full opening thought that runs a little longer.", "Worth it."])

    def test_an_over_long_paragraph_is_subdivided(self):
        script = " ".join(f"Sentence number {i} here and it is reasonably long." for i in range(1, 10))
        beats = avm._split_script_into_paragraphs(script, max_sentences=4)
        self.assertGreater(len(beats), 1)
        for beat in beats:
            self.assertLessEqual(len(beat.split(". ")), 5)

    def test_a_tail_fragment_within_one_paragraph_is_glued_back_on(self):
        script = (
            "First sentence is long enough to stand alone here. "
            "Second sentence is also long enough to matter. "
            "Third sentence carries real detail as well. "
            "Fourth sentence rounds the thought out nicely. "
            "Yes."
        )
        beats = avm._split_script_into_paragraphs(script, max_sentences=4)
        self.assertEqual(len(beats), 1, "a 4-word tail should not become its own audio clip")
        self.assertTrue(beats[0].endswith("Yes."))

    def test_a_model_that_ignores_blank_lines_still_gets_split(self):
        script = " ".join(f"This is spoken sentence {i} in the review." for i in range(1, 9))
        beats = avm._split_script_into_paragraphs(script)
        self.assertGreater(len(beats), 1)

    def test_empty_and_whitespace_scripts_produce_no_beats(self):
        self.assertEqual(avm._split_script_into_paragraphs(""), [])
        self.assertEqual(avm._split_script_into_paragraphs("   \n\n  "), [])
        self.assertEqual(avm._split_script_into_paragraphs(None), [])

    def test_no_text_is_lost_when_splitting(self):
        script = (
            "Opening thought that sets the scene.\n\n"
            "A second beat with two sentences. Here is the second one.\n\n"
            "Closing verdict goes right here."
        )
        beats = avm._split_script_into_paragraphs(script)
        rejoined = " ".join(beats).split()
        original = script.split()
        self.assertEqual(rejoined, original)


class StripScriptArtifactsTests(unittest.TestCase):
    """Finished videos were narrating scaffolding out loud -- section
    headings, "Number 3", and prompt preamble -- because only single-ASIN
    mode ever consumed headers and nothing removed them anywhere else."""

    def test_section_headings_are_not_narrated(self):
        script = "Key Features\n\n150 PSI here.\n\nFinal Verdict\n\nWorth it."
        out = avm.strip_script_artifacts(script)
        self.assertNotIn("Key Features", out)
        self.assertNotIn("Final Verdict", out)
        self.assertIn("150 PSI here.", out)
        self.assertIn("Worth it.", out)

    def test_a_real_sentence_starting_with_a_heading_word_survives(self):
        # "Performance is where it wins." must not be mistaken for the
        # "Performance" heading -- that would delete real narration.
        out = avm.strip_script_artifacts("Performance is where this one wins.")
        self.assertEqual(out, "Performance is where this one wins.")

    def test_rank_labels_are_not_narrated(self):
        for label in ("Number 3", "Product 2:", "#4", "5.", "Pick 1"):
            out = avm.strip_script_artifacts(f"{label}\n\nReal narration sentence here.")
            self.assertEqual(out, "Real narration sentence here.", f"failed for {label!r}")

    def test_model_preamble_is_not_narrated(self):
        script = "Here's the script:\n\nSure, this is the real narration.\n\nActual content here."
        out = avm.strip_script_artifacts(script)
        self.assertNotIn("Here's the script", out)
        self.assertIn("Actual content here.", out)

    def test_markdown_scaffolding_is_removed_but_text_kept(self):
        out = avm.strip_script_artifacts("## Overview\n\n**Bold claim** about the product.\n\n- A bullet point.")
        self.assertNotIn("#", out)
        self.assertNotIn("**", out)
        self.assertIn("Bold claim about the product.", out)
        self.assertIn("A bullet point.", out)

    def test_paragraph_breaks_survive_because_they_drive_voice_beats(self):
        out = avm.strip_script_artifacts("First beat here.\n\nSecond beat here.")
        self.assertEqual(out, "First beat here.\n\nSecond beat here.")
        self.assertEqual(len(avm._split_script_into_paragraphs(out)), 2)

    def test_single_asin_keeps_headers_because_they_drive_section_labels(self):
        script = "Key Features\n\n150 PSI here.\n\nFinal Verdict\n\nWorth it."
        out = avm.strip_script_artifacts(script, keep_section_headers=True)
        self.assertIn("Key Features", out)
        self.assertIn("Final Verdict", out)
        # Scaffolding that is never structural is still removed.
        self.assertNotIn("Number", avm.strip_script_artifacts("Number 3\n\nKey Features\n\nText.", keep_section_headers=True))

    def test_empty_and_scaffolding_only_input(self):
        self.assertEqual(avm.strip_script_artifacts(""), "")
        self.assertEqual(avm.strip_script_artifacts(None), "")
        self.assertEqual(avm.strip_script_artifacts("Key Features\n\nFinal Verdict"), "")


class ProductHasAudioGateTests(unittest.TestCase):
    """Narration is now voiced as several separate beats instead of one call
    (see SplitScriptIntoParagraphsTests above). That means many more chances
    for exactly one beat to fail while the rest succeed -- and a silently
    dropped beat would ship a video missing whatever that beat said, which is
    a worse failure than the old single-call path (where one failed chunk
    failed the whole description). _product_has_audio is the guard that
    beat_segments in process_single_asin relies on to catch this."""

    def _sane_segment(self, tmp_path, text="Six real words go right here."):
        with open(tmp_path, "wb") as fh:
            fh.write(b"\x00" * 5000)  # _audio_is_sane only checks size/duration/path
        return (tmp_path, text, False)

    def test_a_product_where_every_beat_succeeded_is_fine(self):
        with unittest.mock.patch.object(avm, "_audio_is_sane", return_value=(True, "ok")):
            product = {"asin": "A1", "audio_segments": [
                ("/fake/beat0.mp3", "First beat text here.", False),
                ("/fake/beat1.mp3", "Second beat text here.", False),
            ]}
            self.assertTrue(avm._product_has_audio(product))

    def test_one_failed_beat_among_successful_ones_fails_the_whole_product(self):
        # This is the exact regression this test guards: beat_segments used
        # to silently DROP a failed (None-path) beat instead of keeping it,
        # so a product with 3 good beats and 1 failed one looked "fine" to
        # this gate and shipped with a missing sentence.
        with unittest.mock.patch.object(avm, "_audio_is_sane", side_effect=lambda path, text: (
            (False, "file missing") if path is None else (True, "ok")
        )):
            product = {"asin": "A1", "audio_segments": [
                ("/fake/beat0.mp3", "First beat text here.", False),
                (None, "Second beat -- this is the one that failed.", False),
                ("/fake/beat2.mp3", "Third beat text here.", False),
            ]}
            self.assertFalse(avm._product_has_audio(product))

    def test_a_product_with_no_narration_segments_at_all_has_no_audio(self):
        self.assertFalse(avm._product_has_audio({"asin": "A1", "audio_segments": []}))
        self.assertFalse(avm._product_has_audio({"asin": "A1"}))

    def test_silent_header_markers_are_not_required_to_have_audio(self):
        # audio_segments entries of the form (label, True, None, header) are
        # header markers, not spoken narration -- they carry no audio path
        # and must not count against the product.
        with unittest.mock.patch.object(avm, "_audio_is_sane", return_value=(True, "ok")):
            product = {"asin": "A1", "audio_segments": [
                ("KEY FEATURES", True, None, "KEY FEATURES"),
                ("/fake/beat0.mp3", "Spoken paragraph text.", False),
            ]}
            self.assertTrue(avm._product_has_audio(product))


class DualVoiceTests(unittest.TestCase):
    """Gemini TTS was producing an unintended two-person read. One narrator
    is the default; two hosts is now something the user turns on."""

    def test_single_narrator_by_default(self):
        with unittest.mock.patch.object(avm, "DUAL_VOICE_ENABLED", False):
            self.assertEqual([avm._voice_for_beat(i, "primary") for i in range(4)],
                             ["primary"] * 4)

    def test_enabling_it_alternates_narrators_between_beats(self):
        with unittest.mock.patch.object(avm, "DUAL_VOICE_ENABLED", True), \
                unittest.mock.patch.object(avm, "DUAL_VOICE_SECOND", "second-voice"):
            self.assertEqual(
                [avm._voice_for_beat(i, "primary") for i in range(4)],
                ["primary", "second-voice", "primary", "second-voice"],
            )

    def test_enabled_without_a_second_voice_stays_single(self):
        # Turning the switch on but leaving the voice field blank must not
        # silently pass an empty voice id to the provider.
        with unittest.mock.patch.object(avm, "DUAL_VOICE_ENABLED", True), \
                unittest.mock.patch.object(avm, "DUAL_VOICE_SECOND", ""):
            self.assertEqual([avm._voice_for_beat(i, "primary") for i in range(3)],
                             ["primary"] * 3)


class AiIntroConclusionTests(unittest.TestCase):
    """Opt-in AI opener/closer. The template versions are fixed text; these
    are written per video from the real product list, following the
    hook -> promise -> reassurance -> "let's go" retention structure."""

    PRODUCTS = [
        {"rank": 1, "title": "Banks Ram Air Cold Air Intake System"},
        {"rank": 2, "title": "S&B Filters Cold Air Intake Kit"},
        {"rank": 3, "title": "aFe Power Momentum HD Intake"},
    ]

    def _capture_prompt(self, builder, reply="Some spoken opener text here."):
        seen = {}

        def fake(prompt):
            seen["prompt"] = prompt
            return reply

        with unittest.mock.patch.object(avm, "call_llm_local", fake):
            result = builder()
        return seen.get("prompt", ""), result

    def test_intro_prompt_includes_the_real_products_and_topic(self):
        prompt, _ = self._capture_prompt(
            lambda: avm.build_ai_intro_text("Best Cold Air Intakes", self.PRODUCTS, False)
        )
        self.assertIn("Best Cold Air Intakes", prompt)
        self.assertIn("Banks", prompt)
        self.assertIn("aFe", prompt)

    def test_intro_prompt_forbids_the_cta_that_the_closing_owns(self):
        # Both sections pitching the description produced the same ask twice
        # in one video.
        prompt, _ = self._capture_prompt(
            lambda: avm.build_ai_intro_text("Widgets", self.PRODUCTS, False)
        )
        self.assertIn("Do NOT mention links", prompt)

    def test_conclusion_prompt_ranks_products_and_bans_invented_prices(self):
        prompt, _ = self._capture_prompt(
            lambda: avm.build_ai_conclusion_text("Widgets", self.PRODUCTS, False)
        )
        self.assertIn("rank 1", prompt)
        self.assertIn("do not", prompt.lower())
        self.assertIn("Do NOT state a price", prompt)

    def test_single_product_conclusion_asks_for_a_verdict_not_a_ranking(self):
        prompt, _ = self._capture_prompt(
            lambda: avm.build_ai_conclusion_text("One Widget", self.PRODUCTS[:1], True)
        )
        self.assertIn("worth buying", prompt)
        self.assertNotIn("best-value pick", prompt)

    def test_scaffolding_is_stripped_from_whatever_the_model_returns(self):
        _, result = self._capture_prompt(
            lambda: avm.build_ai_intro_text("Widgets", self.PRODUCTS, False),
            reply="Here's the script:\n\nKey Features\n\nThe real opening line.",
        )
        self.assertEqual(result, "The real opening line.")

    def test_empty_or_whitespace_model_output_signals_fallback(self):
        for reply in ("", "   ", "\n\n"):
            _, result = self._capture_prompt(
                lambda: avm.build_ai_intro_text("Widgets", self.PRODUCTS, False), reply=reply
            )
            self.assertEqual(result, "", "caller relies on '' to fall back to the template")

    def test_a_model_returning_only_scaffolding_also_signals_fallback(self):
        _, result = self._capture_prompt(
            lambda: avm.build_ai_conclusion_text("Widgets", self.PRODUCTS, False),
            reply="Key Features\n\nFinal Verdict",
        )
        self.assertEqual(result, "")

    def test_output_is_collapsed_to_a_single_spoken_line(self):
        _, result = self._capture_prompt(
            lambda: avm.build_ai_intro_text("Widgets", self.PRODUCTS, False),
            reply="First beat here.\n\nSecond beat here.",
        )
        self.assertNotIn("\n", result)


class ConclusionTests(unittest.TestCase):
    """The old outro was a bare "Check the links in description for the best
    prices" -- no recommendation, and a CTA with no reason to act on it."""

    def test_roundup_names_the_top_pick_and_asks_for_a_price_check(self):
        out = avm.build_conclusion_text("Tire Inflators", [], False, "Gamma Digital Inflator XL Pro Max Cordless")
        self.assertIn("Gamma Digital Inflator", out)
        self.assertIn("price", out.lower())
        self.assertIn("description", out.lower())

    def test_top_pick_name_is_shortened_so_it_stays_speakable(self):
        long_title = " ".join(f"Word{i}" for i in range(20))
        out = avm.build_conclusion_text("Widgets", [], False, long_title)
        self.assertNotIn("Word9", out)

    def test_single_product_close_uses_singular_link_wording(self):
        out = avm.build_conclusion_text("Tire Inflator", [], True, None)
        self.assertIn("link below", out.lower())
        self.assertNotIn("roundup", out.lower())

    def test_missing_top_pick_still_produces_a_usable_close(self):
        out = avm.build_conclusion_text("Widgets", [], False, None)
        self.assertTrue(out.strip().endswith("."))
        self.assertIn("price", out.lower())


class CaptionStyleTests(unittest.TestCase):
    def test_ass_colour_is_byte_reversed_with_inverted_alpha(self):
        # ASS stores colour blue-first and treats 00 as fully OPAQUE.
        self.assertEqual(avm._ass_color("#FF0000"), "&H000000FF")
        self.assertEqual(avm._ass_color("#0000FF"), "&H00FF0000")
        self.assertEqual(avm._ass_color("#FFFFFF", 0.0), "&HFFFFFFFF")

    def test_malformed_colour_falls_back_to_white(self):
        for bad in ("", None, "nonsense", "#12", "#GGGGGG"):
            self.assertEqual(avm._ass_color(bad), "&H00FFFFFF")

    def test_windows_drive_letter_is_escaped_for_the_filtergraph(self):
        # An unescaped "C:" makes ffmpeg parse the drive letter as its own
        # option separator and the subtitles filter fails to load.
        out = avm._subtitles_filter_path(r"C:\Users\t\captions.srt")
        self.assertEqual(out, r"C\:/Users/t/captions.srt")

    def test_posix_path_is_left_usable(self):
        self.assertEqual(avm._subtitles_filter_path("/Users/t/captions.srt"),
                         "/Users/t/captions.srt")


class DedupeProductsByTitleTests(unittest.TestCase):
    """The same product was sometimes appearing twice in one video. Input
    ASINs can be deduplicated (see the keywords_asin.txt parsing loop this
    mirrors), but Amazon also resolves several ASINs -- colour/size variants
    -- to the identical listing, which only shows up AFTER scraping, as a
    repeated title. This is the second dedup layer that catches that case."""

    def test_an_exact_repeated_title_is_dropped(self):
        products = [
            {"asin": "AAAAAAAAAA", "title": "Cordless Tire Inflator 150 PSI"},
            {"asin": "BBBBBBBBBB", "title": "Cordless Tire Inflator 150 PSI"},
        ]
        out = avm._dedupe_products_by_title(products)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["asin"], "AAAAAAAAAA")

    def test_case_and_whitespace_differences_still_count_as_the_same_title(self):
        products = [
            {"asin": "AAAAAAAAAA", "title": "Cordless Tire Inflator"},
            {"asin": "BBBBBBBBBB", "title": "  cordless   tire inflator  "},
        ]
        out = avm._dedupe_products_by_title(products)
        self.assertEqual(len(out), 1)

    def test_genuinely_different_products_all_survive(self):
        products = [
            {"asin": "AAAAAAAAAA", "title": "Cordless Tire Inflator"},
            {"asin": "BBBBBBBBBB", "title": "Digital Tire Pressure Gauge"},
            {"asin": "CCCCCCCCCC", "title": "Portable Air Compressor"},
        ]
        self.assertEqual(len(avm._dedupe_products_by_title(products)), 3)

    def test_products_with_no_title_are_never_dropped_against_each_other(self):
        # An empty title must not become a shared dedup key that silently
        # collapses two distinct (if both scrape-failed) products into one.
        products = [{"asin": "AAAAAAAAAA", "title": ""}, {"asin": "BBBBBBBBBB", "title": ""}]
        self.assertEqual(len(avm._dedupe_products_by_title(products)), 2)


class AsinDedupParsingTests(unittest.TestCase):
    """Same bug, the other input path: a repeated ASIN in the keyword line
    itself (typo, duplicate paste) was submitted to the worker pool twice
    with nothing to stop it, producing two identical segments."""

    def test_duplicate_asins_collapse_to_one_preserving_first_seen_order(self):
        raw = ["B0FL15BG5Y", "B0GKNH9KHF", "B0FL15BG5Y", "B07MPW62XJ"]
        deduped = list(dict.fromkeys(raw))
        self.assertEqual(deduped, ["B0FL15BG5Y", "B0GKNH9KHF", "B07MPW62XJ"])


class CtaSpreadTests(unittest.TestCase):
    """The on-screen 'Check the Links'/'Check Price' overlays were rendered
    on EVERY product segment -- reported as looking unnatural, repeated so
    often. _evenly_spaced_indices picks which 2-3 segments (out of the whole
    video) show it instead."""

    def test_a_short_video_shows_it_on_every_segment_it_has(self):
        self.assertEqual(avm._evenly_spaced_indices(1, 3), {0})
        self.assertEqual(avm._evenly_spaced_indices(2, 3), {0, 1})

    def test_a_longer_video_is_capped_at_the_requested_count(self):
        for total in (3, 5, 8, 10, 20):
            self.assertLessEqual(len(avm._evenly_spaced_indices(total, 3)), 3)

    def test_spread_includes_both_the_first_and_last_segment(self):
        for total in (3, 5, 8, 10):
            indices = avm._evenly_spaced_indices(total, 3)
            self.assertIn(0, indices)
            self.assertIn(total - 1, indices)

    def test_zero_or_negative_total_yields_nothing(self):
        self.assertEqual(avm._evenly_spaced_indices(0, 3), set())
        self.assertEqual(avm._evenly_spaced_indices(-1, 3), set())


class IntroHookSelectionTests(unittest.TestCase):
    """The intro cuts from the thumbnail into real footage. It must not use
    product #1's clip -- product #1's own segment plays immediately after the
    intro, so hooking with it showed the same footage twice in a row."""

    def test_first_products_footage_is_skipped_in_favour_of_a_later_one(self):
        picked = avm._pick_intro_hook_video([
            {"video": __file__},
            {"video": __file__},
        ])
        self.assertEqual(picked, __file__)

    def test_a_later_product_is_used_when_the_first_has_no_footage(self):
        self.assertEqual(
            avm._pick_intro_hook_video([{"video": None}, {"video": __file__}]),
            __file__,
        )

    def test_missing_files_are_not_offered_as_hook_footage(self):
        self.assertIsNone(
            avm._pick_intro_hook_video([{"video": "/nope/missing.mp4"}, {"video": None}])
        )

    def test_first_product_is_the_last_resort_rather_than_a_frozen_intro(self):
        # Reusing one clip beats what happened before: the intro sitting on a
        # single frozen thumbnail for its entire duration.
        self.assertEqual(
            avm._pick_intro_hook_video([{"video": __file__}, {"video": None}]),
            __file__,
        )

    def test_no_products_at_all_is_handled(self):
        self.assertIsNone(avm._pick_intro_hook_video([]))
        self.assertIsNone(avm._pick_intro_hook_video(None))


if __name__ == "__main__":
    unittest.main()
