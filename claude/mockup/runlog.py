"""Append-only run log: ``<run>/watcher.log``.

One JSON line per event: plans queued by the hooks, jobs taken in or failed by
the duty cycle, plan ingests.  The log is a trace, never a dependency: a record
that cannot be written is dropped silently, so logging can not break the agent.
"""
import json
from datetime import datetime, timezone
from pathlib import Path


def log_record(directory, record):
    """Append one event to the run log; failures are ignored on purpose."""
    record = dict(record, at=datetime.now(timezone.utc).isoformat())
    try:
        with open(Path(directory) / 'watcher.log', 'a', encoding='utf8') as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')
    except OSError:
        pass
