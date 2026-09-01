from pathlib import Path
from types import SimpleNamespace

from bildfahrplan.train_hits import line_hit_candidates, prefer_label
from bildfahrplan.train_schedule import (
    build_train_schedule_view_model, format_schedule_flags, remaining_indices,
    sequential_group_indices,
)


def point(name, arrival=None, departure=None, flags="", notice=None, operating_point=None):
    return SimpleNamespace(
        raw_name=name, planned_name=name, planned_arrival=arrival,
        planned_departure=departure, flags_raw=flags, hint_text=notice,
        operating_point=operating_point,
    )


def service(original, current=None):
    return SimpleNamespace(
        zid=7, name="RS 26550", origin="Aalen", destination="Ulm Hbf", current_delay=11,
        original_schedule=original, current_schedule=original if current is None else current,
    )


def test_sequential_operating_point_groups():
    assert sequential_group_indices(("TBL", "TSK", "TSK", "TSK", "TALL", "TEH")) == (0, 1, 1, 1, 2, 3)
    assert sequential_group_indices(("A", "B", "B", "A")) == (0, 1, 1, 2)


def test_completed_rows_follow_remaining_schedule_not_clock():
    original = [point(name) for name in "ABCD"]
    model = build_train_schedule_view_model(service(original, original[2:]))
    assert [row.completed for row in model.rows] == [True, True, False, False]


def test_repeated_names_match_latest_ordered_occurrence():
    original = [point("A"), point("X"), point("A"), point("B")]
    assert remaining_indices(original, [point("A"), point("B")]) == frozenset({2, 3})


def test_schedule_view_model_has_no_route_or_notice_presentation():
    model = build_train_schedule_view_model(service([point("A", notice="nicht vollständig geliefert")]))
    assert not hasattr(model, "route_points")
    assert not hasattr(model, "common_notices")
    assert not hasattr(model.rows[0], "notice")


def test_schedule_flag_user_texts_hide_parameters_and_p_flags():
    assert format_schedule_flags(point("A", flags="P[l]")) == ()
    assert format_schedule_flags(point("A", flags="P[r]")) == ()
    assert format_schedule_flags(point("A", flags="D")) == ("Durchfahrt",)
    assert format_schedule_flags(point("A", flags="E(12345)")) == ("Neuer Fahrplan",)
    assert format_schedule_flags(point("A", flags="E(RE 123)")) == ("Neuer Fahrplan",)
    assert format_schedule_flags(point("A", flags="F(12345)")) == ("Flügelt",)
    assert format_schedule_flags(point("A", flags="K(RE 123)")) == ("Kuppelt",)
    assert format_schedule_flags(point("A", flags="P[l] D")) == ("Durchfahrt",)
    assert format_schedule_flags(point("A", flags="P[l] E(123) F(456)")) == (
        "Neuer Fahrplan", "Flügelt")
    assert format_schedule_flags(point("A", flags="E(1)E(2)F(3)F(4)K(5)K(6)")) == (
        "Neuer Fahrplan", "Flügelt", "Kuppelt")


def test_unknown_schedule_flags_remain_visible():
    assert format_schedule_flags(point("A", flags="X(9)")) == ("X(9)",)


def test_line_hits_are_unique_per_zid_and_ambiguity_is_preserved():
    hits = line_hit_candidates((5, 2), [
        (1, "A", ((0, 0), (10, 0))), (1, "A", ((0, 1), (10, 1))),
        (2, "B", ((0, 4), (10, 4))), (3, "C", ((20, 20), (30, 30))),
    ])
    assert [(hit.zid, hit.distance_px) for hit in hits] == [(1, 1.0), (2, 2.0)]
    assert prefer_label(hits, 2)[0].source == "label"
    assert [hit.zid for hit in prefer_label(hits, 2)] == [2]


def test_plan_and_projection_segments_resolve_to_one_train_candidate():
    hits = line_hit_candidates((5, 0), [
        (7, "RS 26550", ((0, 0), (10, 0))),
        (7, "RS 26550", ((0, 2), (10, 2))),
    ])
    assert [hit.zid for hit in hits] == [7]


def test_completed_row_palette_uses_disabled_color_group():
    source = (Path(__file__).parents[1] / "src/app/train_schedule_window.py").read_text(encoding="utf-8")
    assert "QtGui.QPalette.ColorGroup.Disabled" in source
    assert "QtGui.QPalette.ColorRole.Disabled" not in source


def test_schedule_window_uses_six_columns_without_route_or_notices():
    source = (Path(__file__).parents[1] / "src/app/train_schedule_window.py").read_text(encoding="utf-8")
    assert "QTableWidget(0, 6)" in source
    assert '("Betriebsstelle", "Gleis / Fahrplanpunkt", "Ankunft", "Abfahrt", "Flags", "Optionen")' in source
    assert "self.route" not in source
    assert "self.notices" not in source
