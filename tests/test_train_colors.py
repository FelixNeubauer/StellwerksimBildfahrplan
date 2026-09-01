import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bildfahrplan.train_colors import (
    TrainSegmentExtent, VisibleTrainGeometry, assign_colorful_train_colors,
    color_distance, trains_conflict,
)


def train(zid, instance="route", x=(0.1, 0.8), time=(1000, 1600)):
    return VisibleTrainGeometry(zid, (TrainSegmentExtent(instance, *x, *time),))


class TrainColorTests(unittest.TestCase):
    def test_near_trains_conflict_and_receive_distinct_colors(self):
        trains = [train(1), train(2, time=(1100, 1700)), train(3, time=(1200, 1800))]
        self.assertTrue(trains_conflict(trains[0], trains[1]))
        colors = assign_colorful_train_colors(trains)
        self.assertEqual(len(set(colors.values())), 3)
        self.assertEqual(colors, assign_colorful_train_colors(trains))

    def test_distant_trains_do_not_conflict_and_may_share_stable_fallback(self):
        left = train(1, time=(1000, 1100))
        right = train(21, time=(5000, 5100))
        self.assertFalse(trains_conflict(left, right))
        colors = assign_colorful_train_colors((left, right))
        self.assertEqual(colors[1], colors[21])

    def test_existing_colors_remain_when_new_train_appears(self):
        existing = assign_colorful_train_colors((train(1), train(2)))
        expanded = assign_colorful_train_colors((train(1), train(2), train(3)), existing)
        self.assertEqual(expanded[1], existing[1])
        self.assertEqual(expanded[2], existing[2])
        self.assertNotIn(expanded[3], {expanded[1], expanded[2]})

    def test_oklab_distance_prefers_visually_more_distant_candidate(self):
        self.assertGreater(color_distance("#FF5252", "#00BCD4"),
                           color_distance("#FF5252", "#FF6E40"))


if __name__ == "__main__":
    unittest.main()
