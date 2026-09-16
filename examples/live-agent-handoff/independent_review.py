"""Reproduce the independent staffing review against a supplied snapshot path."""

import sys
from copy import deepcopy
from itertools import product
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
from staffing import allocate_staff


maps = [
    {level: count for level, count in zip((1, 2), counts) if count is not None}
    for counts in product((None, 0, 1, 2), repeat=2)
]
valid = rejected = 0
for args in product(maps, repeat=3):
    required, available, training = args
    before = deepcopy(args)
    pool = [level for level, count in available.items() for _ in range(count)]
    excessive = False
    for level, count in training.items():
        for _ in range(count):
            if level not in pool:
                excessive = True
                break
            pool.remove(level)
    if excessive:
        try:
            allocate_staff(*args)
        except ValueError:
            rejected += 1
        else:
            raise AssertionError(("excess training accepted", args))
    else:
        missing = {}
        for level, count in required.items():
            for _ in range(count):
                if level in pool:
                    pool.remove(level)
                else:
                    missing[level] = missing.get(level, 0) + 1
        expected = {
            "can_run": not missing,
            "assigned": {} if missing else {k: v for k, v in required.items() if v},
            "shortages": missing,
        }
        actual = allocate_staff(*args)
        assert actual == expected, (args, expected, actual)
        assert type(actual["can_run"]) is bool
        valid += 1
    assert args == before, ("mutated input", before, args)
print(
    f"Exhaustive two-level oracle: {valid} valid allocations and {rejected} "
    "excessive-training cases passed; all 4096 cases preserved inputs."
)

invalids = [None, [], (), "", True, 1]
invalids += [{key: 0} for key in (True, False, 1.0, "1", None, (1,), 0, -1)]
invalids += [{7: count} for count in (True, False, 1.0, "1", None, [], {}, -1)]
checks = 0
for position in range(3):
    for invalid in invalids:
        args = [{}, {7: 20}, {}]
        args[position] = invalid
        before = deepcopy(args)
        try:
            allocate_staff(*args)
        except ValueError:
            pass
        else:
            raise AssertionError(("invalid accepted", position, invalid))
        assert args == before
        checks += 1
assert allocate_staff({10**100: 10**100}, {10**100: 10**100 + 1}, {10**100: 1}) == {
    "can_run": True,
    "assigned": {10**100: 10**100},
    "shortages": {},
}
shared = {1: 2}
assert allocate_staff(shared, shared, shared) == {
    "can_run": False,
    "assigned": {},
    "shortages": {1: 2},
}
assert shared == {1: 2}
print(
    f"Additional independent checks: {checks} invalid argument cases, "
    "arbitrary-precision integers, and shared-input aliasing passed."
)
