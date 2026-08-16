"""create_text_slide_ffmpeg()'s typed reveal for intro/outro text.

A long AI-generated conclusion used to draw every wrapped line always-on
for the whole slide -- readable as a wall of text, and (per a user report
with a real rendered frame) the multi-line block could show up looking
stacked/smeared. This now types one line at a time, same mechanic as the
product caption bar: each reveal step owns a half-open time window so at
most one prefix is ever visible per line, and only one line is "active"
(mid-type or freshly finished) at a time.
"""
import re
import unittest
from unittest.mock import patch

from app_files import amazon_video_maker as avm


def _render_and_capture(text, duration=14.0):
    captured = {}

    def fake_run_ffmpeg(cmd_args):
        captured["cmd"] = cmd_args
        idx = cmd_args.index("-filter_complex")
        captured["filter_complex"] = cmd_args[idx + 1]

    with patch.object(avm, "run_ffmpeg", side_effect=fake_run_ffmpeg), \
            patch.object(avm, "get_audio_duration", return_value=duration):
        avm.create_text_slide_ffmpeg(text, None, "/tmp/unused_output.mp4", is_intro=False)
    return captured


class OutroTypedRevealTests(unittest.TestCase):
    LONG_CONCLUSION = (
        "So, which cream blush is right for you? For the absolute best "
        "overall, grab the Milk Makeup Lip and Cheek Multi-Use Stick. If you "
        "want a great value pick, try the Milani Cheek Kiss Cream Blush in "
        "Merlot Moment. And for vibrant color, the Makeup By Mario Raspberry "
        "Soft Pop Blush Stick is fantastic. Don't forget to like this video "
        "and subscribe for more reviews!"
    )

    def test_each_wrapped_line_reveals_in_its_own_non_overlapping_windows(self):
        captured = _render_and_capture(self.LONG_CONCLUSION)
        # Only the video/drawtext portion -- the audio keystroke-gate filter
        # appended after it reuses the same between(...) windows at the
        # per-line (not per-character-step) granularity, which legitimately
        # doesn't line up with the drawtext steps checked here.
        video_part = captured["filter_complex"].split("; [", 1)[0]
        windows_by_step = re.findall(
            r"between\(t,([\d.]+),([\d.]+)\)", video_part
        )
        self.assertGreater(len(windows_by_step), 1, "expected multiple typed-reveal steps")
        spans = [(float(a), float(b)) for a, b in windows_by_step]
        # Steps within a single line are sequential and half-open, so no two
        # windows should overlap -- overlapping windows are exactly what
        # produced simultaneously-visible stacked text before this fix.
        spans.sort()
        for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
            self.assertLessEqual(e1, s2 + 1e-6, f"overlapping reveal windows: ({s1},{e1}) vs ({s2},{e2})")

    def test_typing_keystroke_sfx_is_mixed_in_when_text_is_drawn(self):
        captured = _render_and_capture(self.LONG_CONCLUSION)
        self.assertIn("keytype_loop.wav", " ".join(captured["cmd"]))
        self.assertIn("amix=inputs=2", captured["filter_complex"])

    def test_no_typing_sfx_when_draw_text_is_false(self):
        captured = {}

        def fake_run_ffmpeg(cmd_args):
            captured["cmd"] = cmd_args
            idx = cmd_args.index("-filter_complex")
            captured["filter_complex"] = cmd_args[idx + 1]

        with patch.object(avm, "run_ffmpeg", side_effect=fake_run_ffmpeg), \
                patch.object(avm, "get_audio_duration", return_value=5.0):
            avm.create_text_slide_ffmpeg(
                "Top 7 Best Dash Cam Review", None, "/tmp/unused_output.mp4",
                bg_path="/tmp/unused_thumb.jpg", is_intro=True, draw_text=False,
            )
        self.assertNotIn("keytype_loop.wav", " ".join(captured["cmd"]))


if __name__ == "__main__":
    unittest.main()
