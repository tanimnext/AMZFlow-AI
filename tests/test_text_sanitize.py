"""Regression tests for the sanitize_text / drawtext escaping bugs found in
the v6 audit: an apostrophe in a channel name broke every render's
filtergraph (bug 1), product titles were sanitized twice and double-escaped
`%` (bug 7), and sanitizing *after* line-wrapping destroyed the `\\<newline>`
escape wrap_lines_for_overlay's join relies on (bug 8)."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app_files.amazon_video_maker import (
    escape_path,
    sanitize_text,
    setup_font,
    wrap_lines_for_overlay,
)


class SanitizeTextTests(unittest.TestCase):
    def test_strips_apostrophes_and_quotes(self):
        self.assertEqual(sanitize_text("Dad's Garage"), "Dads Garage")
        self.assertEqual(sanitize_text('Say "hello"'), "Say hello")

    def test_escapes_percent_exactly_once(self):
        # Bug 7: create_product_segment_ffmpeg used to call sanitize_text a
        # second time on already-sanitized text, turning %% into %%%%.
        once = sanitize_text("100% Cotton")
        self.assertEqual(once, "100%% Cotton")
        twice = sanitize_text(once)
        self.assertNotEqual(twice, once, "sanitizing twice must not be a no-op (proves the bug exists)")
        self.assertEqual(twice, "100%%%% Cotton")

    def test_backslash_becomes_space(self):
        self.assertEqual(sanitize_text("A\\B"), "A B")

    def test_empty_and_none_input(self):
        self.assertEqual(sanitize_text(""), "")
        self.assertEqual(sanitize_text(None), "")

    def test_exotic_dash_variants_become_ascii_hyphen(self):
        # Titles pulled from a URL slug or a site's own metadata sometimes
        # carry an en-dash/em-dash/non-breaking-hyphen instead of a plain
        # "-". Roboto has no glyph for those, so they used to render as a
        # tofu box on screen instead of a dash.
        self.assertEqual(sanitize_text("Top‑Rated Cam–Review"), "Top-Rated Cam-Review")
        self.assertEqual(sanitize_text("A—B"), "A-B")


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
class DrawtextRenderTests(unittest.TestCase):
    """Proves bug 1's fix end-to-end: sanitized text -- including text that
    originally contained an apostrophe -- renders successfully through the
    same filter_complex + chained output label shape amazon_video_maker.py
    actually uses for branding overlays (a bare `-vf` alone doesn't reproduce
    the original failure; ffmpeg's text-value parser is more lenient there
    than inside a `[label]`-chained filter_complex)."""

    def _render(self, text):
        font = escape_path(setup_font("bold"))
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "out.mp4"
            cmd = [
                shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=black:s=320x180:d=0.2",
                "-filter_complex",
                f"[0:v]drawtext=fontfile='{font}':text='{text}':fontsize=20:fontcolor=white[o]",
                "-map", "[o]", "-frames:v", "1", str(output),
            ]
            return subprocess.run(cmd, capture_output=True, text=True)

    def test_unsanitized_apostrophe_breaks_the_filtergraph(self):
        result = self._render("Dad's Garage".upper())
        self.assertNotEqual(result.returncode, 0, "an unescaped apostrophe should break drawtext")

    def test_sanitized_channel_name_renders_successfully(self):
        result = self._render(sanitize_text("Dad's Garage".upper()))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_sanitized_wrapped_title_renders_successfully(self):
        # Bug 8: sanitize_text() must run BEFORE wrapping, not after, or its
        # backslash-to-space replacement destroys the `\<newline>` escape.
        title = sanitize_text("Rough Country 4\" Lift Kit w/ Shocks")
        lines = wrap_lines_for_overlay(title, width=20, max_lines=3)
        wrapped = "\\\n".join(lines)
        result = self._render(wrapped)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_intro_outro_multiline_text_never_embeds_a_raw_newline(self):
        # Regression: create_text_slide_ffmpeg used to join wrapped lines
        # with "\<newline>" into one drawtext text= value. This ffmpeg build
        # (libharfbuzz-enabled) shapes that control character into a visible
        # tofu box glyph before applying the line break -- every wrapped
        # intro/outro slide showed a box at each line break. The fix renders
        # one drawtext filter per line instead, so no text= value should
        # ever contain a newline byte.
        import re
        from unittest.mock import patch
        from app_files import amazon_video_maker as avm

        captured = {}

        def fake_run_ffmpeg(cmd_args):
            idx = cmd_args.index("-filter_complex")
            captured["filter_complex"] = cmd_args[idx + 1]

        long_text = (
            "Thanks for watching Best Dash Cam Review! Check the links in "
            "description for more details and best prices."
        )
        with patch.object(avm, "run_ffmpeg", side_effect=fake_run_ffmpeg), patch.object(
            avm, "get_audio_duration", return_value=3.0
        ):
            avm.create_text_slide_ffmpeg(long_text, None, "/tmp/unused_output.mp4", is_intro=False)

        self.assertIn("filter_complex", captured)
        text_values = re.findall(r"text='([^']*)'", captured["filter_complex"])
        self.assertGreater(len(text_values), 1, "expected one drawtext per wrapped line")
        for value in text_values:
            self.assertNotIn("\n", value)

    def test_draw_text_false_skips_the_title_overlay(self):
        # The intro slide can reuse the styled Thumbnail.jpg as its
        # background -- that image already has its own baked-in title text,
        # so draw_text=False must suppress the slide's own drawtext entirely
        # instead of drawing a second, duplicate title on top of it.
        from unittest.mock import patch
        from app_files import amazon_video_maker as avm

        captured = {}

        def fake_run_ffmpeg(cmd_args):
            idx = cmd_args.index("-filter_complex")
            captured["filter_complex"] = cmd_args[idx + 1]

        with patch.object(avm, "run_ffmpeg", side_effect=fake_run_ffmpeg), patch.object(
            avm, "get_audio_duration", return_value=3.0
        ):
            avm.create_text_slide_ffmpeg(
                "Top 7 Best Dash Cam Review",
                None,
                "/tmp/unused_output.mp4",
                bg_path="/tmp/unused_thumb.jpg",
                is_intro=True,
                draw_text=False,
            )

        self.assertNotIn("drawtext=", captured["filter_complex"])


if __name__ == "__main__":
    unittest.main()
