### Task 2: Config hashing and the backtest contract

**Files:**
- Create: `capitalscan/research/backtest.py`
- Test: `capitalscan/tests/unit/test_backtest_config.py`

**Interfaces:**
- Consumes: `core.config` dataclasses (`IndicatorParams`, `SignalParams`, `ExitParams`, `CostParams`, `SplitParams`, `UniverseParams`)
- Produces:
  - `@dataclass(frozen=True) BacktestConfig` with fields `indicators: IndicatorParams`, `signals: SignalParams`, `exits: ExitParams`, `costs: CostParams`, `splits: SplitParams`, `universe: UniverseParams`
  - `config_hash(config: BacktestConfig) -> str` — 16-char hex, deterministic
  - `split_key_for(signal_date: date, sp: SplitParams) -> str` — returns `"train" | "validate" | "holdout"`

- [ ] **Step 1: Write failing tests.** Four behaviours: identical configs hash identically across processes (serialize via `dataclasses.asdict` and hash the sorted JSON, so dict ordering cannot leak in); changing any single field changes the hash; `split_key_for` returns `train` for 2021-12-31, `validate` for 2022-01-01 and 2023-12-31, `holdout` for 2024-01-01; a date before `SplitParams.event_start` raises rather than silently landing in `train`.

- [ ] **Step 2: Run.** Expect FAIL, `ModuleNotFoundError: capitalscan.research.backtest`.

- [ ] **Step 3: Implement.** `config_hash` uses `hashlib.sha256(json.dumps(asdict(config), sort_keys=True, default=str).encode()).hexdigest()[:16]`. `split_key_for` compares against `sp.train_end` / `sp.validate_end` as `date` objects, raising `ValueError` below `sp.event_start`.

- [ ] **Step 4: Verify.** Tests pass, plus full unit + property suite.

- [ ] **Step 5: Commit.**

---

