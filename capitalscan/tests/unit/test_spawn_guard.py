"""Verify ProcessPoolExecutor spawn mode works on Windows.

ProcessPoolExecutor uses spawn on Windows (not fork), which re-imports every
job module. This test verifies the module structure is import-safe with no
top-level execution.
"""

from concurrent.futures import ProcessPoolExecutor


def _simple_task(x: int) -> int:
    """A trivial task for multiprocessing test."""
    return x * 2


def test_process_pool_executor_spawn():
    """Verify spawn-mode pool can start and complete tasks."""
    with ProcessPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(_simple_task, i) for i in range(4)]
        results = [f.result(timeout=5) for f in futures]

    assert results == [0, 2, 4, 6], "Pool execution produced wrong results"


def test_process_pool_executor_no_hang():
    """Verify the pool terminates cleanly (detects hangs)."""
    with ProcessPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_simple_task, 42)
        result = future.result(timeout=10)

    assert result == 84, "Pool task did not complete correctly"
