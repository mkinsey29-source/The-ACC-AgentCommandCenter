# Independent snapshot review

Verdict: **APPROVE**

Reviewed snapshot: `snapshots/v1`

Manifest snapshot ID: `sha256:edcbb60e68b29865a261387e8beac3bdf8ac5cb44917aeccf6c86ca5b79b4765`

I acted as a separate reviewer, not as Claude. I inspected `REQUIREMENTS.md`,
`job_progress.py`, the supplied tests, and the handoff directly. All four payload
SHA-256 hashes match `manifest.json`. This review applies only to that snapshot.

## Findings

No actionable implementation defects were found within the exercise's
representable numeric domain. Validation precedes completion and gate checks;
both sides of every map are checked, including unused entries. Staffing uses
exact keys with no substitution, every resource gate must pass, missing entries
default to zero, and progress is retained through pauses. The implementation
uses the standard library only and has no external effects or map mutation.

One documented specification boundary remains: unrestricted finite real inputs
and a `float` return cannot simultaneously preserve arbitrary progress exactly.
For example, from the snapshot directory:

```python
from job_progress import advance_job
advance_job(10**1000, 10**1000, 0, {}, {}, {}, {}, 0, 0)
```

Observed: `OverflowError: int too large to convert to float`.
The required unchanged completed value has no finite float representation;
returning infinity, truncating it, or rejecting an otherwise finite input would
each sacrifice part of the stated contract. The handoff already discloses this.
This is a specification/domain decision, not a reproducible implementation fix
that could satisfy the existing unrestricted wording. Ordinary rounding of
nonrepresentable fractions is the same type of return-format constraint.
The handoff explicitly defines numeric inputs through `numbers.Real`; Decimal
support was not specified and was not added as an independent acceptance demand.

## Independent checks

Run from any directory:

```sh
python /workspace/scratch/69eae5c87408/acc-handoff-exercise/review/test_independent.py /workspace/scratch/69eae5c87408/acc-handoff-exercise/snapshots/v1
```

Result: **10 test methods passed**, making **428 calls** to the target function.
The test loader imports `job_progress.py` by the supplied explicit snapshot path,
does not depend on the implementer's tests, and suppresses bytecode writes into
the snapshot. Changing the command's snapshot argument reruns the same tests on
a later revision.

| Check | Target calls |
| --- | ---: |
| Exact rational oracle: all 8 gate combinations × 4 progress values × 5 elapsed values | 160 |
| Exact staffing levels, every staff/material entry, and surplus | 8 |
| Empty requirements and absent entries with zero requirements | 2 |
| Repeated pause/resume/completion sequence | 7 |
| Invalid scalar inputs in active/completed jobs, plus duration/progress bounds | 92 |
| Invalid staffing maps on both sides, including completion and shortfall | 80 |
| Invalid material maps on both sides, including unused resources | 72 |
| No consumption/mutation across success, shortfall, and validation failure | 4 |
| Read-only maps and arbitrarily large finite resource quantities | 1 |
| Huge elapsed capping and float-sum overflow capped to finite duration | 2 |
| **Total** | **428** |

Also reran the supplied suite from `snapshots/v1`:

```sh
python -B -m unittest -v test_job_progress.py
```

Result: **16 test methods passed**.

The review did not modify the implementation or snapshot. ACC transport/gating
and a deliberately broken negative-control variant are outside this review's
executed checks; the coordinator is testing those separately.
