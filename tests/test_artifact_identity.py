import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from infrastructure.artifact_identity import (
    SavedStellwerkIdentity, artifact_metadata, atomic_write_json, find_identity_candidate,
    validate_saved_stellwerk_identity,
)


class ArtifactIdentityTests(unittest.TestCase):
    def test_match_name_change_and_legacy(self):
        current = SavedStellwerkIdentity(77, "Blaubeuren 2024")
        self.assertEqual(validate_saved_stellwerk_identity(
            artifact_metadata(current, "operating_points", 2), current).status, "match")
        old = artifact_metadata(SavedStellwerkIdentity(77, "Blaubeuren (2024)"), "operating_points", 2)
        result = validate_saved_stellwerk_identity(old, current)
        self.assertEqual((result.status, result.saved.name, result.current.name),
                         ("name_changed", "Blaubeuren (2024)", "Blaubeuren 2024"))
        self.assertEqual(validate_saved_stellwerk_identity({"aid": 77}, current).status,
                         "legacy_identity_confirmation")

    def test_unique_aid_change_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "77.json"
            atomic_write_json(path, artifact_metadata(
                SavedStellwerkIdentity(77, "Blaubeuren 2024"), "operating_points", 2))
            result = find_identity_candidate(directory, SavedStellwerkIdentity(177, "Blaubeuren 2024"),
                                             "operating_points")
            self.assertEqual((result.status, result.saved.aid, result.current.aid), ("aid_changed", 77, 177))

    def test_metadata_has_required_identity(self):
        data = artifact_metadata(SavedStellwerkIdentity(7, "Test"), "generated_graph", 14)
        self.assertEqual((data["aid"], data["stellwerk_name"]), (7, "Test"))
        self.assertIn("saved_at", data); self.assertEqual(data["stellwerk"]["name"], "Test")
