"""Allocate engineers at their exact levels, excluding engineers in training."""


def _validate_counts(name, counts):
    """Validate every level and count, including unused levels."""
    if not isinstance(counts, dict):
        raise ValueError(f"{name} must be a dict")
    for level, count in counts.items():
        if isinstance(level, bool) or not isinstance(level, int) or level <= 0:
            raise ValueError(f"{name} levels must be positive integers")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError(f"{name} counts must be nonnegative integers")


def allocate_staff(requirements, available, training):
    """Return an all-or-nothing allocation using only exact engineer levels.

    All inputs map positive integer levels to nonnegative integer counts.
    Available engineers include those in training, so training reduces capacity
    at the same level. Invalid inputs or excessive training raise ValueError.
    The input dictionaries are never modified.
    """
    for name, counts in (
        ("requirements", requirements),
        ("available", available),
        ("training", training),
    ):
        _validate_counts(name, counts)

    for level, count in training.items():
        if count > available.get(level, 0):
            raise ValueError(f"training exceeds available engineers at level {level}")

    shortages = {}
    for level, required in requirements.items():
        capacity = available.get(level, 0) - training.get(level, 0)
        missing = required - capacity
        if missing > 0:
            shortages[level] = missing

    if shortages:
        return {"can_run": False, "assigned": {}, "shortages": shortages}
    return {
        "can_run": True,
        "assigned": {level: count for level, count in requirements.items() if count},
        "shortages": {},
    }
