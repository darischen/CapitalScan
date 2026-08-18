# Task report: run_universe O(n²) query fix

## What I implemented

Memoized `_rel_return_756d` and `_sector_median_return` (`capitalscan/jobs/compute.py`)
with an explicit, call-scoped `dict` cache created inside `run_universe` and threaded
through both functions as an optional trailing parameter. No other behavior changed.

- `_rel_return_756d(engine, ticker, as_of, lookback_days, cache=None)` — checks
  `cache` first, computes and stores on miss.
- `_sector_median_return(engine, sector, as_of, lookback_days, cache=None)` — same
  pattern for the `(median, used_fallback)` pair, and passes the *same* cache down
  into its own `_rel_return_756d` calls over peer tickers, so a peer's return
  computed while building one ticker's sector median is reused by every other
  ticker's median call and by `run_universe`'s own per-ticker `_rel_return_756d`
  call — the second, overlapping redundancy the task description called out.
- `run_universe` creates one `rel_return_cache: dict = {}` before the ticker loop
  and passes it to both calls.

`cache=None` (the default) reproduces the exact old code path with zero behavior
change — existing callers/tests that invoke these functions without a cache, or
monkeypatch `_rel_return_756d` with its old 4-positional-argument shape, are
unaffected.

## What I tested and results

Added to `capitalscan/tests/unit/test_universe_membership.py`:

- `TestRelReturnMemoization` (4 tests): cached result equals uncached result;
  repeat lookup with the same key does not requery; a cached `None` (insufficient
  history) is not requeried — proves `None` is treated as a real answer, not an
  absent cache entry; a different `as_of` is a different cache key (proves the
  key can't silently serve a stale quarter's answer).
- `TestSectorMedianMemoization` (2 tests): cached result equals uncached result;
  repeat call with the same key does not re-list the ticker universe.
- `TestRunUniverseQueryCountIsLinear` (1 test, the point of the task): a fake
  engine that counts queries by category, run through `run_universe` end-to-end
  for N=10 and N=40 synthetic tickers (all with `sector=None`, matching
  production reality). Asserts:
  - per-ticker helpers (`indicator`, `ticker_meta`) stay exactly N — unaffected,
    as expected.
  - the active-ticker listing (`_sector_median_return`'s fallback source) is
    issued **exactly once** regardless of N, not once per ticker.
  - `rel_return` queries stay `<= N`.
  - quadrupling N (10 → 40) does not quadruple-squared (16x) the rel-return
    query count; it stays within the linear bound (`<= 4x + 4`).

Result: `698 passed` across `capitalscan/tests/unit capitalscan/tests/property`.

## TDD evidence

**RED** — implementation reverted (`git stash push -- capitalscan/jobs/compute.py`),
tests present:

```
uv run pytest capitalscan/tests/unit/test_universe_membership.py -q
...
7 failed, 9 passed in 1.04s
```

Failures were exactly the expected shape:
- `TypeError: _rel_return_756d() got an unexpected keyword argument 'cache'`
  (4 tests)
- `TypeError: _sector_median_return() got an unexpected keyword argument 'cache'`
  (2 tests)
- `TestRunUniverseQueryCountIsLinear::test_query_counts_scale_linearly_not_quadratically`:
  `assert counts_10["active_list"] == 1` failed with `assert 10 == 1` — i.e. on
  unfixed code, 10 tickers issued 10 separate "list all active tickers" queries
  (one per `_sector_median_return` call), directly demonstrating the bug the
  task describes, live, in a test assertion.

**GREEN** — implementation restored (`git stash pop`):

```
uv run pytest capitalscan/tests/unit/test_universe_membership.py -q
...
16 passed in 0.12s
```

Full safe suite:

```
uv run pytest capitalscan/tests/unit capitalscan/tests/property -q
...
698 passed in 26.90s
```

## Files changed

- `capitalscan/jobs/compute.py` — `_rel_return_756d`, `_sector_median_return`,
  `run_universe` (lines ~282–420, ~537–575 pre-diff numbering).
- `capitalscan/tests/unit/test_universe_membership.py` — added
  `TestRelReturnMemoization`, `TestSectorMedianMemoization`,
  `TestRunUniverseQueryCountIsLinear`, and the small fixture classes
  (`_BarRow`, `_OneRow`) they need; also fixed a pre-existing missing
  `timedelta` import discovered while writing the new tests.

Commit: `5bf5aec` "Memoize run_universe's rel-return / sector-median queries
(call-scoped)".

## Cache scoping approach and why it cannot leak

A plain `dict` (`rel_return_cache`) is created as a local variable inside
`run_universe`'s body, immediately before the ticker loop, and passed down by
parameter to `_rel_return_756d` and `_sector_median_return`. It holds no
reference anywhere outside that stack frame — not on `engine`, not on a module
global, not via `functools.lru_cache` (which would key on the function's own
argument tuple across every call ever made, process-lifetime, including engine
identity quirks). When `run_universe` returns, the last reference to the dict
goes away and it is garbage-collected. A second `run_universe` call — a
different quarter, i.e. a different `as_of` — creates a brand-new empty dict;
it is structurally impossible for one call to see another call's cache. A
comment at the cache's creation site in `run_universe` states this rationale
in place, per the file's comment-density convention.

## Cache keys, confirmed to include `as_of`

- `_rel_return_756d`: `(ticker, as_of, lookback_days)` — 3-tuple.
- `_sector_median_return`: `("sector_median", sector, as_of, lookback_days)` —
  4-tuple, tagged with a literal string so it can never collide with a
  `_rel_return_756d` key even in the pathological case of a ticker symbol
  equal to `"sector_median"` (tuple *arity* differs: 3 vs 4, so no collision
  is possible regardless of tag content — the tag is defense in depth, not
  the only thing preventing collision).

Both keys include `as_of`. Confirmed by `TestRelReturnMemoization::
test_different_as_of_is_a_different_cache_key`, which asserts a second call
with a different `as_of` re-queries rather than reusing the first quarter's
entry.

## Measured query-count reduction

From `TestRunUniverseQueryCountIsLinear` (fixed code, memoized):

| N tickers | active-ticker listing queries | rel_return queries |
|---|---|---|
| 10 | 1 | ≤ 10 |
| 40 | 1 | ≤ 40 |

Before the fix (measured directly by running the *same* test harness against
reverted code, RED phase): for N=10, the active-ticker listing alone was
issued **10 times** (once per `_sector_median_return` call) rather than once —
i.e., exactly the O(n) → O(n²) blowup the task describes for that one query
category. Extrapolating the pre-fix pattern (each of the N `_sector_median_return`
calls redoing an N-ticker `_rel_return_756d` fan-out, plus N more from
`run_universe`'s own per-ticker call) to the real ~620-ticker/quarter case
matches the task's ~385k-query estimate; post-fix that collapses to on the
order of N+1 queries total for this hot path.

## `_evaluate_universe_row` signature

Unchanged — no diff touches it. All pre-existing tests in
`test_universe_membership.py` that call it directly
(`TestEvaluateUniverseRow`, three tests) pass unmodified.

## Note on the discarded `used_fallback` flag

`run_universe` still does `sector_median, _ = _sector_median_return(...)`,
discarding `used_fallback`. Since `tickers.sector` is null for every row
today, `used_fallback` is `True` for every ticker, every quarter — DESIGN
§4.6 describes flagging the fallback, and right now that flag is silently
uniform and dropped on the floor rather than surfaced (e.g. as a
`used_sector_fallback` column on `universe`). I did not implement surfacing
it — out of this task's scope per the brief — but flag it for the
controller: it's a one-line change now that the value is already computed
once per unique `(sector, as_of, lookback_days)` key rather than 620 times,
so if it's wanted, this is a cheap follow-up.

## Self-review findings

- Naming: `rel_return_cache` in `run_universe` is arguably imprecise since it
  also carries `_sector_median_return`'s entries under a differently-shaped
  key. Kept the name because it reflects the *primary* thing being cached
  (rel-return queries) and the docstrings on both callee functions spell out
  exactly what else shares it — renaming to something like
  `_universe_query_cache` was considered but didn't clearly read better.
- YAGNI: did not generalize this into a decorator or a reusable memoization
  utility — the task scope is this one call site, and a bespoke dict with
  explicit keys is more auditable here than a generic cache abstraction would
  be, per invariant 9's spirit (no cleverness that obscures a magic key).
- Verified the `_peer_return` closure inside `_sector_median_return` doesn't
  change call arity when `cache is None`, specifically so the existing
  `TestSectorMedianFallback` tests (which monkeypatch `_rel_return_756d` with
  a 4-argument lambda) keep passing unmodified — confirmed by the green run.
- Did not touch `_adv_20d` — it's O(n) already (one query per ticker, no
  cross-ticker fan-out), out of scope per the task's problem statement.
- Ran only the safe test paths per CONSTRAINTS.md; never touched
  `capitalscan/tests/integration/`, never ran a bare `pytest`, never executed
  `cscan universe` or any DB-writing command. No live database connection was
  made at any point — the test engines are pure Python fakes.

## Issues or concerns

None blocking. The `used_fallback` discard (above) is worth a controller
decision but is explicitly out of scope here, as instructed.
