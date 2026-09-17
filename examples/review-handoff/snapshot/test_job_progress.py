"""Behavioral tests for the independent job-progress gate."""

import copy
from fractions import Fraction
from types import MappingProxyType
import unittest

from job_progress import advance_job


class AdvanceJobTests(unittest.TestCase):
    def setUp(self):
        self.inputs = dict(
            progress=2,
            duration=10,
            elapsed=3,
            required_staff={1: 2, 3: 1},
            available_staff={1: 2, 3: 1},
            required_materials={"wood": 2.5, "iron": 1},
            available_materials={"wood": 2.5, "iron": 1},
            required_energy=4,
            available_energy=4,
        )

    def run_job(self, **changes):
        return advance_job(**(self.inputs | changes))

    def test_advances_when_every_requirement_is_met(self):
        result = self.run_job()
        self.assertEqual(result, 5.0)
        self.assertIsInstance(result, float)

    def test_caps_at_duration_and_allows_zero_elapsed(self):
        self.assertEqual(self.run_job(elapsed=100), 10.0)
        self.assertEqual(self.run_job(progress=2.0, elapsed=10**1000), 10.0)
        self.assertEqual(self.run_job(elapsed=0), 2.0)
        self.assertEqual(self.run_job(progress=0), 3.0)

    def test_fractional_progress_time_and_energy(self):
        self.assertEqual(
            self.run_job(progress=Fraction(1, 2), elapsed=Fraction(1, 4),
                         required_energy=0.5, available_energy=0.5),
            0.75,
        )

    def test_each_independent_shortfall_retains_progress(self):
        for changes in (
            {"available_staff": {1: 1, 3: 1}},
            {"available_staff": {1: 2}},
            {"available_materials": {"wood": 2.4, "iron": 1}},
            {"available_materials": {"wood": 2.5}},
            {"available_energy": 3.9},
        ):
            with self.subTest(changes=changes):
                self.assertEqual(self.run_job(**changes), 2.0)

    def test_higher_staff_levels_cannot_substitute(self):
        self.assertEqual(self.run_job(available_staff={2: 100, 3: 100}), 2.0)

    def test_surplus_and_unneeded_resources_are_allowed(self):
        self.assertEqual(self.run_job(
            available_staff={1: 8, 3: 5, 9: 100},
            available_materials={"wood": 100, "iron": 8, "gold": 9},
            available_energy=100,
        ), 5.0)

    def test_empty_and_zero_requirements_need_no_resources(self):
        for staff, materials in (({}, {}), ({1: 0}, {"wood": 0})):
            with self.subTest(staff=staff):
                self.assertEqual(self.run_job(
                    required_staff=staff, available_staff={},
                    required_materials=materials, available_materials={},
                    required_energy=0, available_energy=0,
                ), 5.0)

    def test_recovery_resumes_from_retained_progress(self):
        progress = self.run_job(available_energy=0)
        progress = self.run_job(progress=progress, available_staff={})
        self.assertEqual(progress, 2.0)
        self.assertEqual(self.run_job(progress=progress), 5.0)

    def test_completed_job_remains_complete_even_with_shortfalls(self):
        self.assertEqual(self.run_job(progress=10), 10.0)
        self.assertEqual(self.run_job(progress=10, available_staff={}), 10.0)

    def test_invalid_scalar_inputs_raise_value_error(self):
        for name in ("progress", "duration", "elapsed", "required_energy", "available_energy"):
            for value in (-1, float("nan"), float("inf"), -float("inf"),
                          True, False, "1", None, 1j):
                with self.subTest(name=name, value=value):
                    with self.assertRaises(ValueError):
                        self.run_job(**{name: value})
        for changes in ({"duration": 0}, {"progress": 11}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    self.run_job(**changes)

    def test_invalid_map_containers_raise_value_error(self):
        for name in ("required_staff", "available_staff", "required_materials", "available_materials"):
            for value in (None, [], [(1, 2)], "wood", 5):
                with self.subTest(name=name, value=value):
                    with self.assertRaises(ValueError):
                        self.run_job(**{name: value})

    def test_invalid_staff_levels_and_counts_in_both_maps(self):
        invalid_maps = [{key: 1} for key in (0, -1, 1.5, 1.0, True, False, "1", None)]
        invalid_maps += [{1: value} for value in (-1, 1.5, 1.0, True, False, "1", None)]
        for name in ("required_staff", "available_staff"):
            for value in invalid_maps:
                with self.subTest(name=name, value=value):
                    with self.assertRaises(ValueError):
                        self.run_job(**{name: value})

    def test_invalid_material_keys_and_quantities_in_both_maps(self):
        invalid_maps = [{key: 1} for key in ("", 1, True, None)]
        invalid_maps += [{"wood": value} for value in (
            -1, float("nan"), float("inf"), -float("inf"), True, False, "1", None, 1j,
        )]
        for name in ("required_materials", "available_materials"):
            for value in invalid_maps:
                with self.subTest(name=name, value=value):
                    with self.assertRaises(ValueError):
                        self.run_job(**{name: value})

    def test_validation_is_not_skipped_for_completion_or_shortfall(self):
        for changes in (
            {"progress": 10, "elapsed": float("nan")},
            {"progress": 10, "available_staff": {True: 1}},
            {"progress": 10, "available_materials": {"wood": -1}},
            {"available_staff": {}, "available_materials": {"unused": float("nan")}},
            {"available_energy": 0, "available_staff": {99: -1}},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    self.run_job(**changes)

    def test_input_maps_are_not_mutated_or_consumed(self):
        before = copy.deepcopy(self.inputs)
        self.assertEqual(self.run_job(), 5.0)
        self.assertEqual(self.run_job(), 5.0)
        self.assertEqual(self.inputs, before)
        self.assertEqual(self.run_job(available_energy=0), 2.0)
        self.assertEqual(self.inputs, before)

    def test_read_only_mappings_are_supported(self):
        inputs = {
            name: MappingProxyType(value) if isinstance(value, dict) else value
            for name, value in self.inputs.items()
        }
        self.assertEqual(advance_job(**inputs), 5.0)


if __name__ == "__main__":
    unittest.main()
