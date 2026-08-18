"""The response validator (session 15.3). Invariant 8, enforced.

> Every response carrying a probability carries `n_eff` and a confidence
> interval.

That is a project invariant, and until this module it was a convention
observed by whoever wrote the query. Three consumers are about to call this
layer and a convention does not survive three implementations.

**It refuses, it does not repair.** A response missing an interval raises
`ResponseInvalid`. Filling one in would be worse than the omission, because
a response that silently acquires a plausible interval is a response that
ships. A raise is a defect report; a repair is a fabricated number.

**Every handler passes its result through `validated()` before returning.**
`test_handlers_contract.py` asserts that structurally by reading each
handler's source for the call, rather than by inspection - the point of a
guard is that it cannot be forgotten.

**What it does not do.** It does not suppress. Suppression is a property of
the *cell*, decided by `research/cell_stats.py` at write time and stored, and
a serving layer that re-decided it would be a second implementation of the
rule. The validator only checks that what the handler built is internally
coherent.

**What it flags rather than refuses.** A `q_value` above `StatsParams.
fdr_alpha` is a real, reportable measurement - on the live config it is
*every* cell that returns (ADR 112). Refusing those would empty the product.
So the result carries `survives_fdr`, and the validator's job is to confirm
that flag agrees with the q-value it sits next to, rather than to hide the
row.
"""

from __future__ import annotations

import math
from dataclasses import fields, is_dataclass
from typing import Any, TypeVar

from capitalscan.core.config import StatsParams
from capitalscan.handlers.errors import ResponseInvalid
from capitalscan.handlers.types import (
    COMPANION_FIELDS,
    CellStats,
    Suppressed,
    is_probability_field,
)

# The debugging escape hatch session 15.3 permits, and the test that keeps it
# shut. Module-level and not a per-call argument on purpose: a `validate=
# False` keyword would end up in a call site, then in a copied call site, and
# the guarantee would be gone with nothing to point at. Flipping this is a
# source edit that shows up in a diff and fails
# `test_handlers_validate.py::test_the_escape_hatch_is_off`.
_DISABLED = False

# `ci_low <= p_hit <= ci_high` is exact in theory - a Wilson interval always
# contains its point estimate. It is not exact after a round trip through
# `numeric(12,6)` and back, so containment is checked to the stored
# precision rather than to float equality. Tighter than this and the check
# fires on rounding; looser and it stops meaning anything.
_PRECISION_TOLERANCE = 1e-6

T = TypeVar("T")


def _isnan(value: Any) -> bool:
    return isinstance(value, float) and math.isnan(value)


def _present(value: Any) -> bool:
    """A field carries a value. NaN counts as absent.

    Postgres `numeric` nulls arrive as `None`, but a value routed through
    pandas arrives as `nan`, and `nan is not None`. Treating the two
    differently would let a probability escape with `n_eff = nan` alongside
    it, which reads on screen as a blank and passes a `is not None` check.
    """
    return value is not None and not _isnan(value)


def _num(value: Any) -> float:
    """`float(value)` past a `_present` check, for mypy's benefit.

    Every call site has already established the value is neither None nor
    NaN. `float()` on `Any | None` is still an error to the checker, and a
    `# type: ignore` would suppress the next, real one.
    """
    return float(value)


def _fail(obj: Any, message: str) -> None:
    raise ResponseInvalid(f"{type(obj).__name__}: {message}")


def _check_probability_companions(obj: Any) -> None:
    """Invariant 8 on one object.

    Runs only when a probability field actually carries a value. A
    `CellStats` built for a cell with no events has `p_hit=None` and no
    interval, and that is coherent: nothing is being claimed, so nothing
    needs backing.
    """
    names = {f.name for f in fields(obj)}
    stated = [name for name in names if is_probability_field(name) and _present(getattr(obj, name))]
    if not stated:
        return

    missing_fields = [c for c in COMPANION_FIELDS if c not in names]
    if missing_fields:
        _fail(
            obj,
            f"states {sorted(stated)} but the type has no "
            f"{sorted(missing_fields)} field. Invariant 8 requires the "
            "companions to live in the same object as the probability.",
        )

    if not _present(getattr(obj, "n_eff")):
        _fail(
            obj,
            f"states {sorted(stated)} with no n_eff. A rate with no sample "
            "size is not a measurement.",
        )
    if not (_present(getattr(obj, "ci_low")) and _present(getattr(obj, "ci_high"))):
        _fail(
            obj,
            f"states {sorted(stated)} with an incomplete interval "
            f"(ci_low={getattr(obj, 'ci_low')!r}, ci_high={getattr(obj, 'ci_high')!r}). "
            "Half an interval is not an interval.",
        )


def _check_interval_coherence(obj: Any) -> None:
    low = getattr(obj, "ci_low", None)
    high = getattr(obj, "ci_high", None)
    if not (_present(low) and _present(high)):
        return
    if _num(low) > _num(high):
        _fail(obj, f"interval is inverted: ci_low={low} > ci_high={high}")

    point = getattr(obj, "p_hit", None)
    if _present(point):
        # A Wilson interval contains its point estimate by construction, so
        # a violation here means the two came from different samples - the
        # classic symptom of a join that matched the wrong cell.
        if not (
            _num(low) - _PRECISION_TOLERANCE <= _num(point) <= _num(high) + _PRECISION_TOLERANCE
        ):
            _fail(
                obj,
                f"p_hit={point} sits outside its own interval [{low}, {high}]. "
                "A Wilson interval contains its point estimate, so these two "
                "did not come from the same sample.",
            )


def _check_fdr_flag(obj: Any, sp: StatsParams) -> None:
    """`survives_fdr` must agree with the q-value beside it.

    ADR 103: era breakdowns are descriptive and enter no test family, so
    they carry no q-value. A null q-value therefore means "not tested", and
    "not tested" is not "survived" - the flag must be False either way, and
    a True flag with no q-value is a claim with nothing behind it.
    """
    if not hasattr(obj, "survives_fdr"):
        return
    q = getattr(obj, "q_value", None)
    expected = _present(q) and _num(q) <= sp.fdr_alpha
    if bool(getattr(obj, "survives_fdr")) is not expected:
        _fail(
            obj,
            f"survives_fdr={getattr(obj, 'survives_fdr')!r} disagrees with "
            f"q_value={q!r} at fdr_alpha={sp.fdr_alpha}. The flag exists so "
            "three consumers do not each write this comparison; it has to be "
            "the comparison.",
        )


def _check_suppressed_states_nothing(obj: Any) -> None:
    """A `Suppressed` may not carry a rate under any name.

    The type has no probability field, so this can only fire if someone adds
    one. That is precisely when it should fire: a greyed-out `p_hit` on a
    suppressed cell gets read off the screen by someone in a hurry, and
    suppression exists to make that impossible rather than discouraged.
    """
    stated = [
        f.name
        for f in fields(obj)
        if is_probability_field(f.name) and _present(getattr(obj, f.name))
    ]
    if stated:
        _fail(obj, f"is suppressed and still states {sorted(stated)}")


def _check_one(obj: Any, sp: StatsParams) -> None:
    if isinstance(obj, Suppressed):
        _check_suppressed_states_nothing(obj)
        return
    _check_probability_companions(obj)
    _check_interval_coherence(obj)
    _check_fdr_flag(obj, sp)


def _walk(obj: Any, sp: StatsParams, seen: set[int]) -> None:
    """Depth-first over dataclasses, tuples, lists, and dict values.

    Nesting is the normal case, not an edge case: a `ScreenResult` holds
    rows, each of which may hold a `CellStats`. Validating only the outer
    object would check the one shape that never carries a probability.

    `seen` guards against a cycle. Frozen dataclasses make one hard to build
    and not impossible, and an unbounded recursion inside a guard is a poor
    way to learn that.
    """
    if id(obj) in seen:
        return
    if is_dataclass(obj) and not isinstance(obj, type):
        seen.add(id(obj))
        _check_one(obj, sp)
        for f in fields(obj):
            _walk(getattr(obj, f.name), sp, seen)
        return
    if isinstance(obj, (tuple, list)):
        for item in obj:
            _walk(item, sp, seen)
        return
    if isinstance(obj, dict):
        for item in obj.values():
            _walk(item, sp, seen)


def validate(result: Any, sp: StatsParams | None = None) -> None:
    """Raise `ResponseInvalid` if `result` violates invariant 8. Else return.

    Separate from `validated()` so session 16 can call it on a payload it
    assembled, and so a test can assert a failure without discarding the
    object it was checking.
    """
    if _DISABLED:  # pragma: no cover - the test asserts this branch is dead
        return
    _walk(result, sp or StatsParams(), set())


def validated(result: T, sp: StatsParams | None = None) -> T:
    """`validate`, returning the result, so a handler ends `return validated(x)`.

    The return-through shape is what makes the call unforgettable at a
    glance: a handler whose last line is `return result` is visibly missing
    its guard, where a handler that called `validate(result)` on the line
    above and then returned would look identical to one that did not.
    """
    validate(result, sp)
    return result


def flagged_cells(result: Any, sp: StatsParams | None = None) -> tuple[str, ...]:
    """Cell ids in `result` whose q-value did not survive correction.

    Not part of validation - nothing here refuses a flagged cell. It exists
    so a surface can say "none of these survived FDR correction" once at the
    top rather than repeating it on every row, which on the live config is
    every row (ADR 112).
    """
    sp = sp or StatsParams()
    found: list[str] = []

    def _collect(obj: Any, seen: set[int]) -> None:
        if id(obj) in seen:
            return
        if isinstance(obj, CellStats):
            seen.add(id(obj))
            if not obj.survives_fdr and _present(obj.p_hit):
                found.append(obj.cell_id)
        if is_dataclass(obj) and not isinstance(obj, type):
            seen.add(id(obj))
            for f in fields(obj):
                _collect(getattr(obj, f.name), seen)
        elif isinstance(obj, (tuple, list)):
            for item in obj:
                _collect(item, seen)
        elif isinstance(obj, dict):
            for item in obj.values():
                _collect(item, seen)

    _collect(result, set())
    return tuple(found)
