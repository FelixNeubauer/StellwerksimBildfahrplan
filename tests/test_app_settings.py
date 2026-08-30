import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from app.settings import (
    COLORFUL_TRAIN_COLORS, DEFAULT_TRAIN_COLOR, ApplicationSettings, ApplicationSettingsStore,
    extract_train_type, normalize_hex_color, train_type_category,
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

    def test_train_types_are_classified_and_unknown_is_other(self):
        self.assertEqual(extract_train_type("RE 1234"), "RE")
        self.assertEqual(extract_train_type("Rf 7"), "RF")
        self.assertEqual(train_type_category("RE"), "local")
        self.assertEqual(train_type_category("ICE"), "long_distance")
        self.assertEqual(train_type_category("DGS"), "freight")
        self.assertEqual(train_type_category("RF"), "shunting")
        self.assertEqual(train_type_category("unbekannt"), "other")

    def test_train_type_mode_category_override_and_bulk_apply(self):
        settings = ApplicationSettings(train_color_mode="train_type").validated()
        local = settings.category_train_colors["local"]
        self.assertEqual(settings.train_color(0, "RE 1"), local)
        overridden = settings.with_train_type_color("RE", "#00FF00")
        self.assertEqual(overridden.train_color(0, "RE 1"), "#00FF00")
        self.assertEqual(overridden.train_color(0, "RB 2"), local)
        bulk = overridden.apply_category_color("local", "#0000FF")
        self.assertEqual(bulk.train_color(0, "RE 1"), "#0000FF")
        changed = bulk.with_train_type_color("RE", "#00FF00")
        single = ApplicationSettings("single", "#ABCDEF", 50, changed.category_train_colors,
                                     changed.train_type_colors).validated()
        restored = ApplicationSettings("train_type", single.single_train_color, 50,
                                       single.category_train_colors, single.train_type_colors).validated()
        self.assertEqual(restored.train_color(0, "RE 1"), "#00FF00")

    def test_unknown_train_type_uses_other_category_color(self):
        settings = ApplicationSettings(train_color_mode="train_type").validated()
        self.assertEqual(settings.train_color(0, "XYZ 9"), settings.category_train_colors["other"])


if __name__ == "__main__":
    unittest.main()
