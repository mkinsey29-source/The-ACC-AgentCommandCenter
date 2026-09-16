"""Independent requirement tests. Usage: python test_independent.py SNAPSHOT_DIR."""

import copy
from fractions import Fraction
import importlib.util
import itertools
from pathlib import Path
import sys
from types import MappingProxyType
import unittest


sys.dont_write_bytecode = True
if len(sys.argv) != 2:
    raise SystemExit("Usage: python test_independent.py SNAPSHOT_DIR")
snapshot = Path(sys.argv.pop()).resolve()
spec = importlib.util.spec_from_file_location("review_target_job_progress", snapshot / "job_progress.py")
target = importlib.util.module_from_spec(spec)
spec.loader.exec_module(target)
advance_job = target.advance_job


def inputs(**changes):
    result = dict(
        progress=Fraction(3, 2), duration=8, elapsed=Fraction(5, 4),
        required_staff={2: 2, 4: 1}, available_staff={2: 2, 4: 1},
        required_materials={"ore": Fraction(3, 2), "water": 2},
        available_materials={"ore": Fraction(3, 2), "water": 2},
        required_energy=Fraction(7, 4), available_energy=Fraction(7, 4),
    )
    result.update(changes)
    return result


class IndependentRequirements(unittest.TestCase):
    def test_exact_oracle_across_all_gate_combinations(self):
        # Rational inputs make the expected progress independent of floating
        # point intermediate arithmetic in the implementation.
        for staff_ok, material_ok, energy_ok in itertools.product((False, True), repeat=3):
            for progress, elapsed in itertools.product(
                (0, Fraction(1, 4), Fraction(29, 4), 8),
                (0, Fraction(1, 8), Fraction(3, 2), 8, 100),
            ):
                with self.subTest(gates=(staff_ok, material_ok, energy_ok), p=progress, dt=elapsed):
                    args = inputs(progress=progress, elapsed=elapsed)
                    if not staff_ok:
                        args["available_staff"] = {2: 1, 4: 1}
                    if not material_ok:
                        args["available_materials"] = {"ore": 1, "water": 2}
                    if not energy_ok:
                        args["available_energy"] = 1
                    expected = min(Fraction(8), progress + elapsed) if all(
                        (staff_ok, material_ok, energy_ok)
                    ) else progress
                    actual = advance_job(**args)
                    self.assertIs(type(actual), float)
                    self.assertEqual(actual, float(expected))

    def test_exact_levels_and_every_required_entry(self):
        for staff in ({4: 99}, {1: 99, 3: 99, 4: 99}, {2: 99}, {2: 1, 4: 99}):
            with self.subTest(staff=staff):
                self.assertEqual(advance_job(**inputs(available_staff=staff)), 1.5)
        self.assertEqual(advance_job(**inputs(available_staff={2: 7, 4: 8, 9: 9})), 2.75)
        for material in ({"ore": 99}, {"water": 99}, {"ore": 99, "water": 1}):
            with self.subTest(material=material):
                self.assertEqual(advance_job(**inputs(available_materials=material)), 1.5)

    def test_empty_and_missing_zero_requirements(self):
        for staff, material in (({}, {}), ({17: 0}, {"unused": 0})):
            self.assertEqual(advance_job(**inputs(
                required_staff=staff, available_staff={},
                required_materials=material, available_materials={},
                required_energy=0, available_energy=0,
            )), 2.75)

    def test_multiple_pauses_resume_without_lost_progress(self):
        progress = 0.0
        steps = [(True, 2), (False, 2), (False, 2), (True, 4), (True, 6), (True, 8), (False, 8)]
        for eligible, expected in steps:
            progress = advance_job(**inputs(progress=progress, elapsed=2, available_energy=2 if eligible else 0))
            self.assertEqual(progress, expected)

    def test_all_scalar_validation_in_active_and_completed_jobs(self):
        for completed in (False, True):
            for field in ("progress", "duration", "elapsed", "required_energy", "available_energy"):
                for bad in (True, False, -0.5, float("inf"), -float("inf"), float("nan"), "2", None, 2j):
                    with self.subTest(completed=completed, field=field, value=bad):
                        args = inputs(progress=8 if completed else 1)
                        args[field] = bad
                        with self.assertRaises(ValueError):
                            advance_job(**args)
        for args in (inputs(duration=0, progress=0), inputs(progress=9)):
            with self.assertRaises(ValueError):
                advance_job(**args)

    def test_staff_map_validation_on_both_sides(self):
        bad_maps = [None, [], [(2, 1)], "staff"]
        bad_maps += [{key: 1} for key in (0, -2, True, False, 2.0, 2.5, "2", None)]
        bad_maps += [{2: value} for value in (-1, True, False, 1.0, 0.5, "1", None, float("inf"))]
        for field, bad, completed in itertools.product(
            ("required_staff", "available_staff"), bad_maps, (False, True)
        ):
            with self.subTest(field=field, bad=bad, completed=completed):
                args = inputs(progress=8 if completed else 1, available_energy=0)
                args[field] = bad
                with self.assertRaises(ValueError):
                    advance_job(**args)

    def test_material_validation_including_unused_resources(self):
        bad_maps = [None, [], [("ore", 1)], "ore"]
        bad_maps += [{key: 1} for key in ("", True, 1, None, ("ore",))]
        bad_maps += [{"unused": value} for value in (
            -1, True, False, float("nan"), float("inf"), -float("inf"), "1", None, 1j
        )]
        for field, bad, completed in itertools.product(
            ("required_materials", "available_materials"), bad_maps, (False, True)
        ):
            with self.subTest(field=field, bad=bad, completed=completed):
                args = inputs(progress=8 if completed else 1, available_staff={})
                args[field] = bad
                with self.assertRaises(ValueError):
                    advance_job(**args)

    def test_maps_remain_unchanged_across_success_failure_and_errors(self):
        args = inputs()
        before = copy.deepcopy(args)
        self.assertEqual(advance_job(**args), 2.75)
        self.assertEqual(advance_job(**args), 2.75)
        self.assertEqual(args, before)
        blocked = inputs(available_staff={})
        blocked_before = copy.deepcopy(blocked)
        self.assertEqual(advance_job(**blocked), 1.5)
        self.assertEqual(blocked, blocked_before)
        invalid = inputs(available_materials={"ore": -1})
        invalid_before = copy.deepcopy(invalid)
        with self.assertRaises(ValueError):
            advance_job(**invalid)
        self.assertEqual(invalid, invalid_before)

    def test_read_only_mapping_inputs_and_finite_large_requirements(self):
        huge = 10 ** 1000
        args = inputs(required_energy=huge, available_energy=huge,
                      required_materials={"ore": huge}, available_materials={"ore": huge})
        for field in ("required_staff", "available_staff", "required_materials", "available_materials"):
            args[field] = MappingProxyType(args[field])
        self.assertEqual(advance_job(**args), 2.75)

    def test_capping_does_not_require_unbounded_intermediate_conversion(self):
        self.assertEqual(advance_job(**inputs(elapsed=10 ** 1000)), 8.0)
        self.assertEqual(advance_job(**inputs(progress=1e308, duration=1.5e308, elapsed=1e308)), 1.5e308)


if __name__ == "__main__":
    unittest.main(verbosity=2)
