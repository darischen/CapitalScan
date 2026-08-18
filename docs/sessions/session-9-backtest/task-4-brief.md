### Task 4: Cluster tagging

DESIGN §5.3. Overlapping events are **tagged, not suppressed** (ADR 056).

**Files:**
- Modify: `capitalscan/research/candidates.py`
- Test: `capitalscan/tests/unit/test_backtest_clusters.py`

**Interfaces:**
- Produces: `tag_clusters(candidates: pd.DataFrame, max_hold_days: int) -> pd.DataFrame`, adding `cluster_id: int`, `seq_in_cluster: int`, `is_cluster_head: bool`, `days_since_head: int`

- [ ] **Step 1: Write failing tests.** Two events on one ticker within `max_hold_days` share a `cluster_id`, with `seq_in_cluster` 1 and 2 and `is_cluster_head` true only for seq 1. Events further apart than `max_hold_days` get distinct clusters, both heads. Two tickers never share a cluster even on identical dates. `days_since_head` counts **trading** bars from the head, not calendar days.

- [ ] **Step 2: Run.** Expect FAIL.

- [ ] **Step 3: Implement.** Group by ticker, sort by `signal_date`, walk forward opening a new cluster whenever the gap from the current head exceeds `max_hold_days` bars.

- [ ] **Step 4: Verify.**

- [ ] **Step 5: Commit.**

---

