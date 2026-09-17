"""Validate job eligibility and advance progress without consuming resources."""

from collections.abc import Mapping
from numbers import Integral, Real


def _nonnegative_real(value, name):
    if (
        isinstance(value, bool)
        or not isinstance(value, Real)
        or value != value
        or value == float("inf")
        or value < 0
    ):
        raise ValueError(f"{name} must be a finite nonnegative real number")


def _staffing_map(value, name):
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    for level, count in value.items():
        if isinstance(level, bool) or not isinstance(level, Integral) or level <= 0:
            raise ValueError(f"{name} levels must be positive integers")
        if isinstance(count, bool) or not isinstance(count, Integral) or count < 0:
            raise ValueError(f"{name} counts must be nonnegative integers")


def _material_map(value, name):
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    for material, quantity in value.items():
        if not isinstance(material, str) or not material:
            raise ValueError(f"{name} keys must be nonempty strings")
        _nonnegative_real(quantity, f"{name}[{material!r}]")


def advance_job(
    progress,
    duration,
    elapsed,
    required_staff,
    available_staff,
    required_materials,
    available_materials,
    required_energy,
    available_energy,
) -> float:
    """Return retained or advanced progress after validating every input.

    Staffing matches exact levels. Missing resource entries count as zero.
    Inputs are read only, and eligibility does not consume any resources.
    Numeric quantities use Python's numbers.Real protocol (excluding bool).
    """
    for name, value in (
        ("progress", progress),
        ("duration", duration),
        ("elapsed", elapsed),
        ("required_energy", required_energy),
        ("available_energy", available_energy),
    ):
        _nonnegative_real(value, name)
    if duration == 0:
        raise ValueError("duration must be positive")
    if progress > duration:
        raise ValueError("progress must not exceed duration")

    _staffing_map(required_staff, "required_staff")
    _staffing_map(available_staff, "available_staff")
    _material_map(required_materials, "required_materials")
    _material_map(available_materials, "available_materials")

    eligible = (
        all(available_staff.get(level, 0) >= count for level, count in required_staff.items())
        and all(
            available_materials.get(material, 0) >= quantity
            for material, quantity in required_materials.items()
        )
        and available_energy >= required_energy
    )
    if not eligible:
        return float(progress)
    if progress == duration or elapsed >= duration:
        return float(duration)
    return float(min(duration, progress + elapsed))
