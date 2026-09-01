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


def test_common_and_individual_notices_are_separated_conservatively():
    common = build_train_schedule_view_model(service([point(name, notice="Hinweis ABC") for name in "ABCD"]))
    assert common.common_notices == ("Hinweis ABC",)
    assert all(not row.notice for row in common.rows)
    individual = build_train_schedule_view_model(service([
        point("A"), point("B", notice="Lokwechsel"), point("C")]))
    assert individual.common_notices == ()
    assert [row.notice for row in individual.rows] == ["", "Lokwechsel", ""]


def test_flags_have_german_labels_and_unknown_data_survives():
    entry = point("A", flags="DARKFELP[r]X(9)")
    labels = format_schedule_flags(entry)
    assert labels[:7] == ("Durchfahrt", "Frühere Abfahrt möglich", "Wendet", "Kuppelt",
                          "Flügelt", "E", "Setzt Lok um")
    assert "P[r]" in labels
    assert "X(9)" in labels


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
