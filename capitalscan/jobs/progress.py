"""Progress reporting standard (ADR 052).

Anything over 30 seconds uses `rich.progress`: current item,
completed/total, elapsed, ETA, and a running error count. `--quiet`
switches to one JSON line per 100 items, for cron / Task Scheduler
contexts where nothing reads a TTY.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Iterator
from typing import TypeVar

from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

T = TypeVar("T")

_QUIET_INTERVAL = 100


def track(
    items: Iterable[T],
    *,
    description: str,
    total: int | None = None,
    quiet: bool = False,
    label: str = "item",
    errors: "ErrorCounter | None" = None,
) -> Iterator[T]:
    """Yield from `items`, reporting progress and a running error count.

    Pass an `ErrorCounter` and call `.record(...)` on it inside the loop
    body when an item fails; the bar reads its `.count` on every advance,
    so a failure on one item does not stop the fetch of the rest
    (DESIGN §4.9) and still shows up live.
    """
    items = list(items) if total is None else items
    if total is None:
        total = len(items)  # type: ignore[arg-type]
    errors = errors if errors is not None else ErrorCounter()

    if quiet:
        yield from _track_quiet(items, total=total, label=label, errors=errors)
        return

    columns = (
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        TextColumn("[red]{task.fields[errors]} errors[/red]"),
    )
    with Progress(*columns) as progress:
        task_id = progress.add_task(description, total=total, errors=0)
        for item in items:
            yield item
            progress.update(task_id, advance=1, errors=errors.count)


def _track_quiet(
    items: Iterable[T], *, total: int, label: str, errors: "ErrorCounter"
) -> Iterator[T]:
    for i, item in enumerate(items, start=1):
        yield item
        if i % _QUIET_INTERVAL == 0 or i == total:
            sys.stdout.write(
                json.dumps({"completed": i, "total": total, "unit": label, "errors": errors.count})
                + "\n"
            )
            sys.stdout.flush()


class ErrorCounter:
    """Running error count, printed alongside the progress bar."""

    def __init__(self) -> None:
        self.count = 0
        self.failures: list[str] = []

    def record(self, item: str) -> None:
        self.count += 1
        self.failures.append(item)
