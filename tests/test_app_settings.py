import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from app.settings import (
    COLORFUL_TRAIN_COLORS, DEFAULT_TRAIN_COLOR, ApplicationSettings, ApplicationSettingsStore,
    extract_train_type, normalize_hex_color, normalize_train_type, train_type_category,
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
        self.assertEqual(len(COLORFUL_TRAIN_COLORS), 20)
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
        self.assertEqual(extract_train_type(" RE 1234 Zusatz"), "RE")
        self.assertEqual(extract_train_type("Tfzf 7"), "Tfzf")
        self.assertEqual(normalize_train_type("Tfzf"), normalize_train_type("TFZF"))
        for name in ("RS", "SAB", "RB", "RE", "IRE"):
            self.assertEqual(train_type_category(name), "local")
        for name in ("ICE", "IC", "EC", "RJ"):
            self.assertEqual(train_type_category(name), "long_distance")
        for name in ("DGN", "DGS", "DGX", "DGZ", "EZ", "EZK", "GAG", "GC", "NG"):
            self.assertEqual(train_type_category(name), "freight")
        for name in ("Rf", "RF", "rf", "RA"):
            self.assertEqual(train_type_category(name), "shunting")
        for name in ("T", "Tfzf", "TFZF", "TFZ"):
            self.assertEqual(train_type_category(name), "other")
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

    def test_category_bulk_apply_includes_expanded_and_custom_types(self):
        settings = ApplicationSettings().add_custom_train_type("SWEG", "local", "#123456")
        settings = settings.apply_category_color("local", "#111111")
        for name in ("RB", "RE", "RS", "SAB", "SWEG"):
            self.assertEqual(settings.train_type_color(f"{name} 1"), "#111111")
        settings = settings.with_train_type_color("re", "#222222")
        self.assertEqual(settings.train_type_color("RE 1"), "#222222")
        settings = settings.apply_category_color("local", "#333333")
        for name in ("RB", "RE", "RS", "SWEG"):
            self.assertEqual(settings.train_type_color(f"{name} 1"), "#333333")

    def test_custom_type_is_case_insensitive_editable_removable_and_persistent(self):
        settings = ApplicationSettings(train_color_mode="train_type").add_custom_train_type(
            "SWEG", "local", "#123456")
        with self.assertRaisesRegex(ValueError, "existiert bereits"):
            settings.add_custom_train_type("sweg", "freight", "#654321")
        self.assertEqual(train_type_category("sweg", settings.custom_train_types), "local")
        self.assertEqual(settings.train_type_color("sWeG 12"), "#123456")
        settings = settings.change_custom_train_type_category("SWEG", "freight")
        self.assertEqual(train_type_category("sweg", settings.custom_train_types), "freight")
        with tempfile.TemporaryDirectory() as directory:
            store = ApplicationSettingsStore(directory); store.save(settings); loaded = store.load()
        self.assertEqual(loaded.custom_train_types, settings.custom_train_types)
        self.assertEqual(loaded.train_type_color("SWEG 1"), "#123456")
        removed = loaded.remove_custom_train_type("sweg")
        self.assertEqual(train_type_category("SWEG", removed.custom_train_types), "other")

    def test_old_uppercase_color_keys_are_migrated_without_loss(self):
        settings = ApplicationSettings(train_color_mode="train_type",
                                       train_type_colors={"RE": "#123456"}).validated()
        self.assertEqual(settings.train_type_color("re 1"), "#123456")

    def test_unknown_train_type_uses_other_category_color(self):
        settings = ApplicationSettings(train_color_mode="train_type").validated()
        self.assertEqual(settings.train_color(0, "XYZ 9"), settings.category_train_colors["other"])


if __name__ == "__main__":
    unittest.main()
