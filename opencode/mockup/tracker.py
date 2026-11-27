"""Fire-and-forget tool error tracker. Writes to the same JSONL as observability.js.

Architecture: single exit boundary in ``server.call_tool`` (Option C in
``C:/Temp/opencode/python-tracker-spec.md``).  No handler edits, no contextvars,
no singleton.

Invariants (each one is a test in ``tests/check_tracker.py``):

* stdlib only at module import time -- ``mockup.paths`` is imported lazily, never
  the ledger, storage, admission or public DTO modules;
* never raises: a failed write increments ``dropped`` and the tool result is
  unchanged (fail-closed);
* append-only, one JSON object per line, no ``fsync``, no compaction;
* observation only: this module never reads receipts, policy or claim bodies.

The output directory is the one ``observability.js`` uses
(``<project>/.dom-memory/observability``), so ``monitor/server.py:_read_hooks``
reads both channels with no monitor change.  ``DOM_TRACKER_DIR`` is a
launch-time escape hatch (never a tool argument).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Circuit breaker: 10k lines per file generation, then a fresh numeric name that
# still matches the retention regex in observability.js
# (``^hooks-\d{4}-\d{2}-\d{2}-\d+\.jsonl$``) and the monitor glob
# ``hooks-*.jsonl``.
MAX_LINES = 10_000
MAX_LINE_BYTES = 64 * 1024

TOOL_MAX = 64
STATUS_MAX = 64
HINT_MAX = 1000
HELP_MAX = 500
SESSION_MAX = 64
TYPE_MAX = 64
FIELD_MAX = 80

_SECRET = (
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.I),
    re.compile(r"(password|api[_-]?key|token)\s*[=:]\s*\S+", re.I),
    re.compile(r"AKIA[0-9A-Z]{16}"),
)
_TOOL_SAFE = re.compile(r"[^A-Za-z0-9_]")
_SESSION_SAFE = re.compile(r"[^A-Za-z0-9._-]")

_lock = threading.Lock()
_override_dir = None
_enabled_override = None
_written = 0
_dropped = 0
_failures = 0
_disabled_until = 0.0
_last_path = None
# Per-process line counter: the filename contains the pid, so only this process
# writes it.  ``key`` is (directory, utc day); a new day or a new configured
# directory starts a fresh count.
_state = {"key": None, "lines": 0, "rotation": 0}


# ------------------------------------------------------------------ helpers --

def _clip(value, limit):
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def scrub_text(value):
    """Mirror ``SECRET_TEXT`` in observability.js (``[REDACTED]`` markers)."""
    text = "" if value is None else str(value)
    for pattern in _SECRET:
        text = pattern.sub("[REDACTED]", text)
    return text


def _now_iso():
    # Same shape as JS ``new Date().toISOString()``: milliseconds + ``Z``.
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _utc_day():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _project_dir():
    """Canonical project root (lazy import: no ``mockup.*`` at module scope)."""
    try:
        from .paths import resolve_paths
        return Path(resolve_paths().project_dir)
    except Exception:  # noqa: BLE001 - resolution must never break telemetry
        return None


def _directory():
    if _override_dir is not None:
        return _override_dir
    base = os.environ.get("DOM_TRACKER_DIR")
    if base:
        return Path(base).expanduser()
    project = _project_dir()
    if project is not None:
        return project / ".dom-memory" / "observability"
    # Last resort: keeps the tracker usable from a bare interpreter.  The normal
    # server path always resolves the project dir above.
    return Path.home() / ".dom-memory" / "observability"


def _file_for(directory, day, rotation=0):
    """``hooks-<date>-<pid>.jsonl``; rotations keep a numeric (retention-safe) name."""
    pid = os.getpid()
    suffix = pid if rotation == 0 else pid * 10_000 + rotation
    return Path(directory) / ("hooks-%s-%d.jsonl" % (day, suffix))


def _count_lines(path):
    try:
        with open(path, "rb") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return 0


def _target(directory, day):
    key = (str(directory), day)
    if _state["key"] != key:
        _state["key"] = key
        _state["lines"] = None
        _state["rotation"] = 0
    limit = MAX_LINES if MAX_LINES > 0 else 1
    while True:
        path = _file_for(directory, day, _state["rotation"])
        if _state["lines"] is None:
            # One stat+read per file generation, not per record.
            _state["lines"] = _count_lines(path)
        if _state["lines"] < limit:
            return path
        _state["rotation"] += 1
        _state["lines"] = 0


# --------------------------------------------------------------- configuration --

def configure(*, directory=None, environ=None, force=None):
    """Test/boot seam: pin the output directory and/or the enabled flag."""
    global _override_dir, _enabled_override
    if force is not None:
        _enabled_override = bool(force)
    if directory is None and environ is not None:
        directory = environ.get("DOM_TRACKER_DIR")
    if directory is not None:
        _override_dir = Path(directory)
        _state["key"] = None


def enabled():
    if _enabled_override is not None:
        return bool(_enabled_override)
    return str(os.environ.get("DOM_TRACKER", "1")).lower() not in ("0", "off", "false", "no")


def statistics():
    return {
        "written": _written,
        "dropped": _dropped,
        "disabled": not enabled(),
        "path": str(_last_path) if _last_path else None,
    }


def _reset():
    """Test-only: clear counters and the configured directory override."""
    global _override_dir, _enabled_override, _written, _dropped, _failures, _disabled_until, _last_path
    with _lock:
        _override_dir = None
        _enabled_override = None
        _written = 0
        _dropped = 0
        _failures = 0
        _disabled_until = 0.0
        _last_path = None
        _state.update(key=None, lines=0, rotation=0)


# ------------------------------------------------------------------- record --

@dataclass(frozen=True, slots=True)
class ToolError:
    """Value object, not an exception: nothing in this module is allowed to throw."""

    tool: str = ""
    status: str = ""
    hint: str = ""
    help: str = ""
    session_id: str = ""
    duration_ms: int = 0
    field: str = ""
    next_action: str = ""
    error_type: str = ""

    def __post_init__(self):
        object.__setattr__(self, "tool", _TOOL_SAFE.sub("_", _clip(self.tool, TOOL_MAX)) or "unknown")
        object.__setattr__(self, "status", _clip(self.status, STATUS_MAX) or "UNKNOWN")
        object.__setattr__(self, "hint", scrub_text(_clip(self.hint, HINT_MAX)))
        object.__setattr__(self, "help", scrub_text(_clip(self.help, HELP_MAX)))
        object.__setattr__(self, "session_id",
                           _SESSION_SAFE.sub("_", _clip(self.session_id, SESSION_MAX)))
        object.__setattr__(self, "error_type", _TOOL_SAFE.sub("_", _clip(self.error_type, TYPE_MAX)))
        object.__setattr__(self, "field", _clip(self.field, FIELD_MAX))
        object.__setattr__(self, "next_action", _clip(self.next_action, HELP_MAX))
        try:
            duration = int(self.duration_ms or 0)
        except (TypeError, ValueError):
            duration = 0
        object.__setattr__(self, "duration_ms", duration)

    def record(self):
        """The JSONL object; ``ts``/``pid`` are stamped at write time."""
        body = {
            "ts": _now_iso(),
            "pid": os.getpid(),
            "hook": "tool",
            "phase": "after",
            "source": "dom.mcp",
            "tool": self.tool,
            "status": self.status,
            "hint": self.hint,
            "help": self.help,
            "session_id": self.session_id,
            "duration_ms": int(self.duration_ms or 0),
        }
        if self.field:
            body["field"] = self.field
        if self.next_action:
            body["next_action"] = self.next_action
        if self.error_type:
            body["error_type"] = self.error_type
        if self.help:
            # Makes an empty-help defect visible in the monitor's "chars" column.
            body["injected"] = {"chars": len(self.help), "source": "error_payload"}
        return body


def record(error: ToolError) -> bool:
    """Append one line. Fail-closed: never raises; returns True if written."""
    global _written, _dropped, _failures, _disabled_until, _last_path
    try:
        if not enabled() or time.monotonic() < _disabled_until:
            return False
        line = json.dumps(error.record(), ensure_ascii=False, sort_keys=True)
        data = (line + "\n").encode("utf-8")
        if len(data) > MAX_LINE_BYTES:
            return False
        directory = _directory()
        with _lock:
            if time.monotonic() < _disabled_until:
                return False
            directory.mkdir(parents=True, exist_ok=True)
            path = _target(directory, _utc_day())
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, data)
            finally:
                os.close(fd)
            _state["lines"] = (_state["lines"] or 0) + 1
            _last_path = path
        _written += 1
        _failures = 0
        return True
    except Exception:  # noqa: BLE001 - fail-closed by contract
        _dropped += 1
        _failures += 1
        if _failures >= 5:  # circuit breaker: 60 s of silence after repeated faults
            _disabled_until = time.monotonic() + 60
        return False


def error(tool, status, hint="", help_text="", *, session=None, duration_ms=None,
          field="", next_action="", error_type=""):
    """Positional-friendly wrapper used by call sites and tests."""
    return record(ToolError(tool=tool, status=status, hint=hint, help=help_text,
                            session_id=session or "", duration_ms=duration_ms or 0,
                            field=field, next_action=next_action, error_type=error_type))
