# Handoff exercise: job progress gate

Implement a pure Python function in job_progress.py:

advance_job(progress, duration, elapsed, required_staff, available_staff, required_materials, available_materials, required_energy, available_energy) -> float

- progress, duration, elapsed, energy and material quantities are finite nonnegative real numbers. duration must be positive; progress must not exceed duration. Invalid numeric inputs raise ValueError. Boolean values are not valid numeric inputs.
- Staffing maps use positive integer levels and nonnegative integer counts; booleans are invalid keys/counts. Invalid maps raise ValueError.
- Material maps use nonempty string keys. Invalid maps/quantities raise ValueError.
- Work advances by elapsed, capped at duration, only when ALL requirements are met.
- Each staffing requirement uses the EXACT level; higher levels cannot substitute. Surplus counts at a required level are allowed. Missing entries count as zero.
- Any shortfall (staff, material, energy) retains progress unchanged. Restoring requirements lets work continue from retained progress.
- Validate input even for a completed job. A valid completed job remains complete.
- This exercise does not consume resources or mutate input maps. It models eligibility/progress only, not the complete game's resource accounting.

Provide implementation, meaningful unittest tests, and a brief handoff (what changed, checks run, limitations). No external packages, shelling out, networking, or unrelated edits in the implementation.
