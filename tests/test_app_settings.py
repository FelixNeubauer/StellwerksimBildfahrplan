import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from app.settings import (
    COLORFUL_TRAIN_COLORS, DEFAULT_TRAIN_COLOR, ApplicationSettings, ApplicationSettingsStore,
    normalize_hex_color,
)


class ApplicationSettingsTests(unittest.TestCase):
    def test_defaults_are_single_light_grey(self):
        settings = ApplicationSettings()
        self.assertEqual(settings.train_color_mode, "single")
        self.assertEqual(settings.single_train_color, DEFAULT_TRAIN_COLOR)
        self.assertEqual(settings.live_follow_position_percent, 50)

    def test_hex_validation_accepts_and_normalizes_only_rrggbb(self):
        self.assertEqual(normalize_hex_color("#a0b1c2"), "#A0B1C2")
        for invalid in ("A0B1C2", "#FFF", "#GG0000", "#1234567", ""):
            self.assertIsNone(normalize_hex_color(invalid))

    def test_color_modes_select_palette_or_one_color(self):
        colorful = ApplicationSettings("colorful")
        self.assertEqual(colorful.train_color(0), COLORFUL_TRAIN_COLORS[0])
        self.assertNotEqual(colorful.train_color(0), colorful.train_color(1))
        single = ApplicationSettings("single", "#ABCDEF")
        self.assertEqual({single.train_color(index) for index in range(5)}, {"#ABCDEF"})

    def test_settings_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ApplicationSettingsStore(directory)
            expected = ApplicationSettings("colorful", "#A1B2C3", 25)
            store.save(expected)
            self.assertEqual(store.load(), expected)

    def test_invalid_persisted_values_fall_back(self):
        settings = ApplicationSettings("unknown", "red", 200).validated()
        self.assertEqual(settings.train_color_mode, "single")
        self.assertEqual(settings.single_train_color, DEFAULT_TRAIN_COLOR)
        self.assertEqual(settings.live_follow_position_percent, 100)


if __name__ == "__main__":
    unittest.main()
