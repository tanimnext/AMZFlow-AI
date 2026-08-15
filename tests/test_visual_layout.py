import unittest
import unittest.mock
import shutil
import subprocess
import tempfile
from pathlib import Path

from app_files import amazon_video_maker as avm
from app_files.amazon_video_maker import (
    build_music_mix_filter,
    create_product_segment_ffmpeg,
    wrap_lines_for_overlay,
)
from app_files.media_qc import detect_silences, probe_media


class VisualLayoutTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_music_bed_gain_lands_any_track_on_the_same_target_level(self):
        # The old mix applied a blind volume=0.06 to every track. The bundled
        # beds average about -41 dBFS, so that put them near -65 dB -- mixed
        # in, but far below audibility, which is why background music
        # appeared not to work. A loud custom upload had the opposite
        # problem. Gain is now measured per track.
        def make_tone(path, amplitude):
            subprocess.run(
                [
                    shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=3:sample_rate=48000",
                    "-af", f"volume={amplitude}", str(path),
                ],
                check=True,
            )

        with tempfile.TemporaryDirectory() as temp:
            quiet = Path(temp) / "quiet.wav"   # ~-35 dBFS, like a bundled bed
            loud = Path(temp) / "loud.wav"     # ~-22 dBFS, like a mastered upload
            make_tone(quiet, 0.2)
            make_tone(loud, 0.9)

            quiet_level = avm.measure_mean_dbfs(quiet)
            loud_level = avm.measure_mean_dbfs(loud)
            self.assertIsNotNone(quiet_level)
            self.assertIsNotNone(loud_level)
            self.assertLess(quiet_level, loud_level)

            # Both land on the same bed target despite starting 13 dB apart.
            for path, level in ((quiet, quiet_level), (loud, loud_level)):
                corrected = level + avm.music_bed_gain_db(path)
                self.assertAlmostEqual(corrected, avm.MUSIC_BED_TARGET_DBFS, delta=1.0)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_music_bed_gain_refuses_to_massively_boost_a_near_silent_track(self):
        # Reaching the target from near-silence would mean +30 dB of gain,
        # which just amplifies that file's noise floor into the mix. The
        # boost is clamped instead, so such a track stays quiet rather than
        # becoming loud hiss.
        with tempfile.TemporaryDirectory() as temp:
            silent_ish = Path(temp) / "tiny.wav"
            subprocess.run(
                [
                    shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=3:sample_rate=48000",
                    "-af", "volume=0.005", str(silent_ish),
                ],
                check=True,
            )
            self.assertLessEqual(avm.music_bed_gain_db(silent_ish), 12.0)

    def test_music_bed_gain_falls_back_safely_for_an_unreadable_track(self):
        # Must attenuate rather than default to unity, or an unmeasurable
        # track would play at full volume over the narration.
        gain = avm.music_bed_gain_db("/nonexistent/track.mp3")
        self.assertEqual(gain, avm.MUSIC_BED_FALLBACK_GAIN_DB)
        self.assertLess(gain, 0)

    def test_music_mix_applies_the_measured_gain_to_the_bed_only(self):
        audio_filter = avm.build_music_mix_filter(60.0, -12.5)
        self.assertIn("volume=-12.50dB[bgm]", audio_filter)
        # The voice chain must stay untouched by the music gain.
        voice_chain = audio_filter.split("[voice];")[0]
        self.assertNotIn("volume=", voice_chain)

    def test_music_mix_uses_one_48khz_clock_and_non_pumping_limiter(self):
        audio_filter = build_music_mix_filter(120.0)
        self.assertIn("aresample=48000", audio_filter)
        self.assertIn("apad=whole_dur=120.0", audio_filter)
        self.assertIn("atrim=duration=120.0", audio_filter)
        self.assertIn("alimiter=", audio_filter)
        self.assertNotIn("loudnorm", audio_filter)

    def test_overlay_wrapping_caps_long_titles(self):
        title = (
            "Rough Country Cold Air Intake Kit for Ford Super Duty Powerstroke "
            "Diesel Engine with Washable Filter and Installation Hardware"
        )
        lines = wrap_lines_for_overlay(title, width=32, max_lines=3)
        self.assertLessEqual(len(lines), 3)
        self.assertTrue(lines[-1].endswith("..."))
        self.assertTrue(all(len(line) <= 32 for line in lines))

    def test_overlay_wrapping_preserves_short_titles(self):
        lines = wrap_lines_for_overlay(
            "Sinister Diesel Cold Air Intake",
            width=32,
            max_lines=3,
        )
        self.assertEqual(lines, ["Sinister Diesel Cold Air Intake"])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_product_segment_renders_visuals_with_narration_audio(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            image = temp_path / "product.jpg"
            audio = temp_path / "voice.mp3"
            audio_two = temp_path / "voice_two.mp3"
            output = temp_path / "segment.mp4"

            subprocess.run(
                [
                    shutil.which("ffmpeg"),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=white:s=1280x720:d=1",
                    "-frames:v",
                    "1",
                    str(image),
                ],
                check=True,
            )
            subprocess.run(
                [
                    shutil.which("ffmpeg"),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=3:sample_rate=44100",
                    "-c:a",
                    "libmp3lame",
                    str(audio),
                ],
                check=True,
            )
            subprocess.run(
                [
                    shutil.which("ffmpeg"),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=520:duration=2:sample_rate=24000",
                    "-c:a",
                    "libmp3lame",
                    str(audio_two),
                ],
                check=True,
            )

            result = create_product_segment_ffmpeg(
                None,
                [str(image)],
                [str(audio), str(audio_two)],
                "Rough Country Cold Air Intake Kit for Ford Super Duty Powerstroke Diesel Engine",
                str(output),
            )

            self.assertEqual(result, str(output))
            probe = probe_media(output, shutil.which("ffprobe"))
            self.assertTrue(probe["hasVideo"])
            self.assertTrue(probe["hasAudio"])
            self.assertGreater(probe["duration"], 4.5)
            self.assertEqual(probe["audioSampleRate"], 48000)
            self.assertLess(
                abs(probe["videoDuration"] - probe["audioDuration"]),
                0.08,
            )
            self.assertEqual(detect_silences(output, shutil.which("ffmpeg")), [])

            # "Check Price" CTA badge (bottom-right corner) is composited into
            # every segment; its scratch PNG must not survive the render.
            self.assertFalse((temp_path / "segment.mp4_ctabadge.png").exists())

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_product_segment_renders_with_the_title_ticker_suppressed(self):
        # CAPTIONS_ENABLED skips the title/CTA/header ticker so it can't
        # collide with burned-in captions -- routing every titled product
        # through the "no title" filtergraph branch, which had a real,
        # previously-latent bug: it continued straight off filter_base's
        # closed [bg] label with a bare "," instead of "; [bg]", which
        # ffmpeg rejects ("More output link labels specified for filter
        # than it has outputs"). Covers both with and without
        # branding_filters, since each built the chain differently.
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            audio = temp_path / "voice.mp3"
            subprocess.run(
                [
                    shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2:sample_rate=44100",
                    "-c:a", "libmp3lame", str(audio),
                ],
                check=True,
            )

            patcher = unittest.mock.patch.object(avm, "CAPTIONS_ENABLED", True)
            patcher.start()
            self.addCleanup(patcher.stop)

            plain_out = temp_path / "no_branding.mp4"
            result = create_product_segment_ffmpeg(
                None, [], [str(audio)], "Test Product Title", str(plain_out),
            )
            self.assertEqual(result, str(plain_out))
            self.assertTrue(probe_media(plain_out, shutil.which("ffprobe"))["hasVideo"])

            branded_out = temp_path / "with_branding.mp4"
            result = create_product_segment_ffmpeg(
                None, [], [str(audio)], "Test Product Title", str(branded_out),
                branding_filters=["drawtext=text='X':fontcolor=white:x=10:y=10"],
            )
            self.assertEqual(result, str(branded_out))
            self.assertTrue(probe_media(branded_out, shutil.which("ffprobe"))["hasVideo"])

    def test_different_titles_get_different_badge_color_variants(self):
        # The badge's color scheme is picked deterministically from the
        # product title so consecutive products in one video don't all show
        # an identical badge -- this only holds if distinct titles actually
        # hash to different variant indices often enough to matter.
        from cta_badge import _variant_for

        variants = {
            _variant_for(title)["bg"]
            for title in [
                "Rough Country Cold Air Intake Kit",
                "Wireless Bluetooth Headphones Noise Cancelling",
                "Stainless Steel Kitchen Knife Set",
                "Portable Camping Tent 4 Person",
                "Electric Standing Desk Adjustable Height",
            ]
        }
        self.assertGreater(len(variants), 1)


if __name__ == "__main__":
    unittest.main()
