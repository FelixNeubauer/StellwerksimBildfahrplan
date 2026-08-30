import sys
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from app.simtime import SimTimeInterpolator
from bildfahrplan.navigation import (
    ROUTE_AXIS_POSITION, TIME_MAX, TIME_MIN, X_INTERACTION_ENABLED, Y_INTERACTION_ENABLED,
    centered_time_range, clamp_time_range, time_bounds,
    live_follow_time_range,
)
from infrastructure import entry_points_from_raw_graph


class UiLogicTests(unittest.TestCase):
    def test_infrastructure_tab_imports_entry_point_supplement_builder(self):
        """Startup regression: _topology_supplements must resolve its helper."""
        path = Path(__file__).parents[1] / "src/app/tabs/infrastructure_tab.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module == "infrastructure"
            for alias in node.names
        }
        self.assertIn("entry_points_from_raw_graph", imported)
        self.assertTrue(callable(entry_points_from_raw_graph))

    def test_topology_tools_use_arrow_navigation_and_selection_label(self):
        widgets = (ROOT / "src/app/widgets/topology_graphics.py").read_text(encoding="utf-8")
        tab = (ROOT / "src/app/tabs/infrastructure_tab.py").read_text(encoding="utf-8")
        self.assertIn("QtCore.Qt.CursorShape.ArrowCursor", widgets)
        self.assertIn('(EditorMode.RECTANGLE, "▧ Auswahl")', tab)
        self.assertIn("Mehrere Elemente mit einem Auswahlrechteck markieren", tab)

    def test_kilometrage_read_only_cells_only_remove_supported_editable_flag(self):
        """Regression: QTableWidgetItem besitzt kein Qt ItemIsFocusable-Flag."""
        tab = (ROOT / "src/app/tabs/infrastructure_tab.py").read_text(encoding="utf-8")
        self.assertNotIn("ItemIsFocusable", tab)
        self.assertIn("item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)", tab)

    def test_axis_contract_and_hard_time_limits(self):
        self.assertEqual(ROUTE_AXIS_POSITION, "top")
        self.assertFalse(X_INTERACTION_ENABLED); self.assertTrue(Y_INTERACTION_ENABLED)
        self.assertEqual((TIME_MIN, TIME_MAX), (5 * 3600, 21 * 3600))
        self.assertEqual(clamp_time_range(4 * 3600, 6 * 3600), (TIME_MIN, 7 * 3600))
        self.assertEqual(clamp_time_range(20 * 3600, 22 * 3600), (19 * 3600, TIME_MAX))
        self.assertEqual(time_bounds(86400 + 12 * 3600), (86400 + TIME_MIN, 86400 + TIME_MAX))

    def test_route_axis_has_no_relative_position_heading(self):
        tab = (ROOT / "src/app/tabs/bildfahrplan_tab.py").read_text(encoding="utf-8")
        self.assertNotIn('setLabel("top", "Strecke (relative Position)")', tab)

    def test_station_header_is_not_recomputed_on_time_only_fast_refresh(self):
        tab = (ROOT / "src/app/tabs/bildfahrplan_tab.py").read_text(encoding="utf-8")
        fast_path = tab.split("if trace_signature == self._last_trace_signature:", 1)[1].split(
            "self._last_trace_signature = trace_signature", 1)[0]
        self.assertNotIn("_update_station_header", fast_path)
        self.assertNotIn("_update_train_items", fast_path)
        self.assertNotIn("build_bildfahrplan_x_axis", fast_path)
        self.assertIn("_update_live_items", fast_path)

    def test_center_preserves_zoom_span_and_clamps(self):
        self.assertEqual(centered_time_range(8 * 3600, (8 * 3600, 10 * 3600)),
                         (7 * 3600, 9 * 3600))
        self.assertEqual(centered_time_range(5 * 3600, (8 * 3600, 10 * 3600)),
                         (TIME_MIN, 7 * 3600))

    def test_live_follow_positions_now_without_changing_duration(self):
        self.assertEqual(live_follow_time_range(500, (0, 100), 0), (500, 600))
        self.assertEqual(live_follow_time_range(500, (0, 100), 25), (475, 575))
        self.assertEqual(live_follow_time_range(500, (0, 100), 50), (450, 550))
        self.assertEqual(live_follow_time_range(500, (0, 100), 100), (400, 500))
        self.assertEqual(live_follow_time_range(500, (0, 200), 25), (450, 650))
        with self.assertRaises(ValueError):
            live_follow_time_range(500, (0, 100), -1)

    def test_live_follow_final_ranges_for_two_hour_view(self):
        now = 12 * 3600
        duration = 120 * 60
        self.assertEqual(live_follow_time_range(now, (0, duration), 0),
                         (now, now + duration))
        self.assertEqual(live_follow_time_range(now, (0, duration), 50),
                         (now - 3600, now + 3600))
        self.assertEqual(live_follow_time_range(now, (0, duration), 100),
                         (now - duration, now))

    def test_simtime_interpolates_resynchronizes_and_freezes_disconnected(self):
        now = [100.0]
        clock = SimTimeInterpolator(lambda: now[0], max_extrapolation=10)
        clock.synchronize(8 * 3600 * 1000)
        now[0] += 3
        self.assertEqual(clock.value(True), (8 * 3600 + 3, True))
        clock.synchronize((8 * 3600 + 5.3) * 1000)
        self.assertAlmostEqual(clock.value(True)[0], 8 * 3600 + 5.3)
        now[0] += 1
        self.assertEqual(clock.value(False), (8 * 3600 + 6.3, False))
        now[0] += 20
        self.assertEqual(clock.value(True), (8 * 3600 + 15.3, False))


if __name__ == "__main__":
    unittest.main()
