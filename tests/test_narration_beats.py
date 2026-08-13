"""Narration is voiced as short beats instead of one long call.

A whole product review used to be handed to the TTS provider as a single
request, so the engine picked one emotion and one pace and held it for the
entire stretch -- the flat, obviously-synthetic delivery. Splitting the
script into 2-4 sentence beats lets intonation re-set per beat, and the
joins between them become natural breaths.
"""
import unittest

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
