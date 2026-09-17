# Job progress gate handoff

Implemented `advance_job` in `job_progress.py` with full input validation before
eligibility checks, exact-level staffing, missing-entry defaults, independent
material and energy gates, capped advancement, and retained progress during
shortfalls. The implementation uses only the Python standard library and does
not mutate or consume resources.

`test_job_progress.py` contains 16 unittest methods with subcases covering normal
advancement, fractional values, capping, zero time and requirements, independent
shortfalls, staffing substitution rejection, surplus resources, resumption,
completion, malformed scalar/map inputs, validation despite early-exit
conditions, read-only mappings, and repeated calls without resource consumption.

Check run from this directory: `python -m unittest -v test_job_progress.py`.
Result: all 16 tests passed.

Scope and limitations:

- Numeric quantities follow `numbers.Real`, which includes integers, floats,
  and fractions but excludes `decimal.Decimal`; booleans are explicitly rejected.
- The return value is a Python float, so nonrepresentable fractions undergo
  float rounding. Extremely large retained/final values beyond float range may
  raise `OverflowError`. Large elapsed values that simply complete an ordinary
  job are handled without first adding them to progress.
- This function models eligibility only. Resource consumption, persistence,
  concurrency, and a complete game loop are outside this exercise.
- I acted as the implementation worker, not as DeepSeek. No reviewer output was
  consulted, and no review files or requirements were changed.
