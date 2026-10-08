"""Tests for the desktop pet's physics and size setting: py -3 -m unittest discover -s tests"""

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "overlay"))
import physics  # noqa: E402
from physics import Bounds  # noqa: E402

PET = (100, 110)
SCREEN = Bounds(left=0, top=0, right=1920, floor=1032)


def fall(anchor, velocity=(0.0, 0.0), bounds=SCREEN, unit=1.0, seconds=10.0):
    """Runs the pet at 60 steps a second; returns the path and how many times it bounced."""
    path, bounces = [anchor], 0
    for _ in range(int(seconds * 60)):
        before = velocity[1]
        anchor, velocity, resting = physics.step(anchor, velocity, PET, bounds, 1 / 60, unit)
        path.append(anchor)
        if before > 0 and velocity[1] < 0 and anchor[1] == bounds.floor:
            bounces += 1
        if resting:
            return path, bounces, True
    return path, bounces, False


class Falling(unittest.TestCase):
    def test_a_dropped_pet_bounces_two_or_three_times_and_rests_on_the_floor(self):
        for height in (300, 600, 900):
            path, bounces, rested = fall((900.0, SCREEN.floor - height))
            self.assertTrue(rested, height)
            self.assertIn(bounces, (2, 3), height)
            self.assertEqual(path[-1], (900.0, SCREEN.floor))

    def test_it_never_goes_below_the_floor_or_past_the_walls(self):
        path, _, _ = fall((300.0, 200.0), velocity=(-4000.0, -1500.0))
        for x, y in path:
            self.assertLessEqual(y, SCREEN.floor)
            self.assertGreaterEqual(x - PET[0], SCREEN.left)
            self.assertLessEqual(x, SCREEN.right)

    def test_a_throw_into_a_wall_bounces_back(self):
        path, _, rested = fall((1700.0, 500.0), velocity=(3000.0, 0.0))
        self.assertTrue(rested)
        self.assertEqual(max(x for x, _ in path), SCREEN.right)
        self.assertLess(path[-1][0], SCREEN.right)

    def test_a_sideways_throw_slides_to_a_stop(self):
        _, _, rested = fall((500.0, SCREEN.floor), velocity=(800.0, 0.0))
        self.assertTrue(rested)

    def test_a_side_with_another_monitor_beyond_is_open(self):
        open_right = Bounds(left=0, top=0, right=math.inf, floor=1032)
        path, _, _ = fall((1800.0, 500.0), velocity=(3000.0, 0.0), bounds=open_right, seconds=0.5)
        self.assertGreater(path[-1][0], 1920)

    def test_the_same_drop_looks_the_same_at_any_display_scale(self):
        small, b1, _ = fall((900.0, SCREEN.floor - 400))
        big_floor = Bounds(0, 0, 3840, SCREEN.floor * 2)
        big, b2, _ = fall((1800.0, big_floor.floor - 800), bounds=big_floor, unit=2.0)
        self.assertEqual(b1, b2)
        self.assertEqual(len(small), len(big))

    def test_a_pet_below_the_floor_comes_up_to_it(self):
        anchor, _, resting = physics.step((500.0, SCREEN.floor + 300), (0.0, 0.0), PET, SCREEN, 1 / 60)
        self.assertEqual(anchor[1], SCREEN.floor)
        self.assertTrue(resting)


class Landing(unittest.TestCase):
    def test_with_animations_off_it_lands_at_once_inside_the_walls(self):
        self.assertEqual(physics.land((50.0, 300.0), PET, SCREEN), (PET[0], SCREEN.floor))
        self.assertEqual(physics.land((5000.0, 300.0), PET, SCREEN), (SCREEN.right, SCREEN.floor))
        self.assertTrue(physics.settled((800.0, SCREEN.floor), PET, SCREEN))
        self.assertFalse(physics.settled((800.0, 300.0), PET, SCREEN))


class Throwing(unittest.TestCase):
    def test_the_last_movement_before_letting_go_sets_the_throw(self):
        samples = [(0.00, 0, 0), (0.50, 10, 0), (0.52, 30, -10), (0.56, 70, -30)]
        vx, vy = physics.throw_velocity(samples, released_at=0.57)
        self.assertAlmostEqual(vx, 60 / 0.06)
        self.assertAlmostEqual(vy, -30 / 0.06)

    def test_holding_still_before_letting_go_throws_nothing(self):
        samples = [(0.00, 0, 0), (0.02, 50, 0)]
        self.assertEqual(physics.throw_velocity(samples, released_at=0.3), (0.0, 0.0))

    def test_a_throw_is_capped(self):
        samples = [(0.00, 0, 0), (0.01, 1000, 0)]
        vx, vy = physics.throw_velocity(samples, released_at=0.01, unit=1.5)
        self.assertAlmostEqual(math.hypot(vx, vy), physics.MAX_THROW * 1.5)


class Size(unittest.TestCase):
    def test_any_percentage_in_range(self):
        self.assertEqual(physics.parse_size(150), 150)
        self.assertEqual(physics.parse_size("87"), 87)
        self.assertEqual(physics.parse_size(1000), physics.SIZE_MAX)
        self.assertEqual(physics.parse_size(1), physics.SIZE_MIN)

    def test_old_size_names_keep_their_old_scale(self):
        for name, scale in physics.LEGACY_SIZES.items():
            self.assertAlmostEqual(physics.scale_for(physics.parse_size(name)), scale, places=2)

    def test_nonsense_falls_back_to_the_default(self):
        for value in (None, "huge", float("nan"), [1]):
            self.assertEqual(physics.parse_size(value), physics.SIZE_DEFAULT)


if __name__ == "__main__":
    unittest.main()
