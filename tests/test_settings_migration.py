"""migrate_superseded_defaults() -- one-time correction of never-customized
shipped defaults in an existing user's settings.json.

No test covered this before; the captions_font_size entry recently needed a
second hop (26 -> 32) on top of its first ("" -> 26), which only works if an
old_default can be more than one prior value -- exactly the kind of thing
that's easy to silently break while editing SUPERSEDED_DEFAULTS.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web_app"))
import app as app_module  # noqa: E402


class SettingsMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings_path = os.path.join(self.tmp.name, "settings.json")
        patchers = [
            patch.object(app_module, "SETTINGS_FILE", self.settings_path),
            patch.object(app_module, "PRIVATE_SETTINGS_FILE", Path(self.settings_path)),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def _write(self, data):
        with open(self.settings_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)

    def _read(self):
        with open(self.settings_path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def test_single_old_default_still_migrates(self):
        self._write({"outro_title_bg_color": "#e20390"})
        app_module.migrate_superseded_defaults()
        self.assertEqual(self._read()["outro_title_bg_color"], "#f97316")

    def test_captions_font_size_migrates_from_either_prior_default(self):
        for old_value in ("", "26"):
            with self.subTest(old_value=old_value):
                self._write({"captions_font_size": old_value})
                app_module.migrate_superseded_defaults()
                self.assertEqual(self._read()["captions_font_size"], "32")

    def test_a_value_the_user_actually_chose_is_never_touched(self):
        self._write({"captions_font_size": "40"})
        app_module.migrate_superseded_defaults()
        self.assertEqual(self._read()["captions_font_size"], "40")

    def test_idempotent_rerun_makes_no_further_changes(self):
        self._write({"captions_font_size": "26"})
        app_module.migrate_superseded_defaults()
        first_pass = self._read()
        app_module.migrate_superseded_defaults()
        self.assertEqual(self._read(), first_pass)

    def test_missing_settings_file_is_a_noop(self):
        # No file written -- must not raise or create one.
        result = app_module.migrate_superseded_defaults()
        self.assertEqual(result, {})
        self.assertFalse(os.path.exists(self.settings_path))


if __name__ == "__main__":
    unittest.main()
