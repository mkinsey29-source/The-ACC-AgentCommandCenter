"""Behavioral tests for exact-level, all-or-nothing staff allocation."""

import copy
import unittest
from collections import UserDict

from staffing import allocate_staff


class AllocateStaffTests(unittest.TestCase):
    def test_success_assigns_only_nonzero_requirements(self):
        self.assertEqual(
            allocate_staff({1: 2, 3: 1, 4: 0}, {1: 5, 3: 2, 8: 10}, {}),
            {"can_run": True, "assigned": {1: 2, 3: 1}, "shortages": {}},
        )

    def test_higher_levels_cannot_substitute(self):
        self.assertEqual(
            allocate_staff({1: 3, 2: 2}, {1: 1, 3: 20}, {}),
            {"can_run": False, "assigned": {}, "shortages": {1: 2, 2: 2}},
        )

    def test_lower_levels_cannot_substitute(self):
        self.assertEqual(
            allocate_staff({3: 1}, {1: 20, 2: 20}, {}),
            {"can_run": False, "assigned": {}, "shortages": {3: 1}},
        )

    def test_training_interrupts_an_otherwise_possible_job(self):
        requirements = {2: 3}
        available = {2: 3}
        self.assertTrue(allocate_staff(requirements, available, {})["can_run"])
        self.assertEqual(
            allocate_staff(requirements, available, {2: 1}),
            {"can_run": False, "assigned": {}, "shortages": {2: 1}},
        )

    def test_training_is_subtracted_only_at_its_own_level(self):
        self.assertEqual(
            allocate_staff({1: 2, 2: 1}, {1: 3, 2: 3, 9: 5}, {1: 1, 2: 2, 9: 5}),
            {"can_run": True, "assigned": {1: 2, 2: 1}, "shortages": {}},
        )

    def test_training_can_consume_all_capacity(self):
        self.assertEqual(
            allocate_staff({2: 3}, {2: 5}, {2: 5}),
            {"can_run": False, "assigned": {}, "shortages": {2: 3}},
        )

    def test_excessive_training_is_rejected_even_for_unrequired_levels(self):
        for requirements in ({}, {1: 0}, {1: 1}):
            for available, training in (({1: 1, 9: 2}, {9: 3}), ({1: 1}, {9: 1})):
                with self.subTest(requirements=requirements, available=available):
                    with self.assertRaises(ValueError):
                        allocate_staff(requirements, available, training)

    def test_assignment_is_all_or_nothing(self):
        self.assertEqual(
            allocate_staff({1: 2, 2: 3, 4: 5, 6: 0}, {1: 2, 2: 3, 4: 1}, {2: 1}),
            {"can_run": False, "assigned": {}, "shortages": {2: 1, 4: 4}},
        )

    def test_empty_and_zero_requirements(self):
        for requirements in ({}, {1: 0, 2: 0}):
            for available, training in (({}, {}), ({1: 2}, {1: 2}), ({}, {9: 0})):
                with self.subTest(requirements=requirements, available=available, training=training):
                    self.assertEqual(
                        allocate_staff(requirements, available, training),
                        {"can_run": True, "assigned": {}, "shortages": {}},
                    )

    def test_non_dict_inputs_in_each_argument(self):
        for position in range(3):
            for invalid in (None, [], [(1, 2)], (), "", 1, True, UserDict({1: 2})):
                args = [{}, {}, {}]
                args[position] = invalid
                with self.subTest(position=position, invalid=invalid):
                    with self.assertRaises(ValueError):
                        allocate_staff(*args)

    def test_invalid_keys_in_each_argument(self):
        for position in range(3):
            for invalid in (True, False, 1.5, "2", None, (2,), 0, -2):
                args = [{}, {}, {}]
                args[position] = {invalid: 0}
                with self.subTest(position=position, invalid=invalid):
                    with self.assertRaises(ValueError):
                        allocate_staff(*args)

    def test_invalid_counts_in_each_argument_including_unused_levels(self):
        for position in range(3):
            for invalid in (True, False, 1.5, "2", None, [], {}, -1):
                args = [{1: 1}, {1: 1, 9: 5}, {}]
                args[position] = {1: 1, 9: invalid}
                with self.subTest(position=position, invalid=invalid):
                    with self.assertRaises(ValueError):
                        allocate_staff(*args)

    def test_empty_requirements_do_not_skip_input_validation(self):
        for args in (({}, {9: -1}, {}), ({}, {}, {9: False}), ({1: 0}, {0: 0}, {})):
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    allocate_staff(*args)

    def test_inputs_remain_unchanged_on_success_shortage_and_error(self):
        cases = (
            ({1: 2, 2: 0}, {1: 3}, {1: 1}),
            ({1: 3, 2: 2}, {1: 3, 2: 1}, {1: 1}),
            ({1: 2}, {1: 3}, {1: 4}),
            ({1: 2}, {1: 3, 9: -1}, {}),
        )
        for args in cases:
            original = copy.deepcopy(args)
            with self.subTest(args=args):
                try:
                    allocate_staff(*args)
                except ValueError:
                    pass
                self.assertEqual(args, original)

    def test_output_dictionaries_do_not_alias_inputs(self):
        requirements, available, training = {1: 2}, {1: 3}, {1: 1}
        result = allocate_staff(requirements, available, training)
        result["assigned"][1] = 99
        result["shortages"][2] = 99
        self.assertEqual(requirements, {1: 2})
        self.assertEqual(available, {1: 3})
        self.assertEqual(training, {1: 1})


if __name__ == "__main__":
    unittest.main()
