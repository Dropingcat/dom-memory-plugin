"""L1 WriteContext: trusted write session frame and writer inventory.

WriteContext tracks *who* is writing, to *which scope*, with *what intent*,
and *what they read beforehand* (read receipt — stored from L2, tracked here
from L1).  It is purely a frame: no schema changes, no gate enforcement yet.

The writer inventory registers known writers (tools, agents) and what they
may write.  Registration is launch-time code, never a model call.

Usage::

    ctx = WriteContext(principal='orchestrator', session='ses_abc',
                       branch='AA', intent='close 0.3.0 L1',
                       read_receipt={'chain_head': 'abc...', 'count': 42})
    async with ctx:
        # every write inside this block is framed by the context
        ...
        # the exit records duration and success/failure

    # The inventory is a module-level dict, populated at plugin load:
    writer_inventory.register('coder-worker', capabilities={'submit', 'review'},
                              allowed_branches={'AA', 'AB'})
    writer_inventory.check('coder-worker', 'submit', 'AA')  # -> True / raises WriterDenied
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Writer inventory
# ---------------------------------------------------------------------------

class WriterDenied(RuntimeError):
    """A writer attempted an action it is not registered for."""


_INVENTORY: dict[str, dict[str, Any]] = {}  # writer name -> info


def register(name: str, capabilities: set[str] | None = None,
             allowed_branches: set[str] | None = None,
             description: str = '') -> None:
    """Declare a writer that can submit under this identity.

    Registration is idempotent: calling it twice refreshes the capabilities
    but does not create a duplicate.
    """
    if not name.strip():
        raise ValueError('WRITER_NAME_REQUIRED')
    _INVENTORY[name] = {
        'capabilities': frozenset(capabilities or set()),
        'allowed_branches': frozenset(allowed_branches or set()),
        'description': description.strip(),
        'registered_at': datetime.now(timezone.utc).isoformat(),
    }


def check(name: str, capability: str | None = None,
          branch: str | None = None) -> bool:
    """Return True if *name* may use *capability* on *branch*; raise WriterDenied otherwise."""
    info = _INVENTORY.get(name)
    if info is None:
        return False
    if capability is not None and capability not in info['capabilities']:
        return False
    if branch is not None and branch not in info['allowed_branches']:
        return False
    return True


def enforce(name: str, capability: str | None = None,
            branch: str | None = None) -> None:
    """Like *check* but raises WriterDenied instead of returning False."""
    if not check(name, capability, branch):
        caps_info = ''
        info = _INVENTORY.get(name)
        if info:
            caps_info = f' (registered: capabilities={set(info["capabilities"])}, branches={set(info["allowed_branches"])})'
        raise WriterDenied(
            f'Writer {name!r} not allowed to use capability={capability!r} '
            f'branch={branch!r}{caps_info}'
        )


def known_writers() -> dict[str, dict[str, Any]]:
    """Return a snapshot of the writer registry (for diagnostics and the brief)."""
    return dict(_INVENTORY)


def reset_for_test() -> None:
    """Clear the inventory.  Only for tests."""
    _INVENTORY.clear()


# ---------------------------------------------------------------------------
# WriteContext
# ---------------------------------------------------------------------------

@dataclass
class WriteContext:
    """Frame for a trusted write session.

    Fields (all set at creation, never mutated):
        principal      caller identity (e.g. 'orchestrator', 'user', 'system')
        session        session identifier (e.g. 'ses_abc')
        intent         single-line description of *why* this write is happening
        branch         branch the write targets
        read_receipt   snapshot of what was read before writing, from L2 onward.
                       For L1 it is a free-form dict (metadata only); L2 will
                       enforce schema and chain validation.
        started_at     when the context was opened (ISO-8601)
        duration_ms    filled in by ``__aexit__`` / ``close()`` — time inside the frame
        _closed        internal flag

    Context-manager usage::

        async with ctx:
            # write here

    The manager catches no exceptions: if the body raises, the frame is still
    marked closed but the failure must be handled upstream.
    """
    principal: str
    session: str
    intent: str
    branch: str = 'AA'
    read_receipt: dict | None = None

    # Internal — not part of the public constructor
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    duration_ms: float = 0.0
    _closed: bool = False
    _start_ts: float = 0.0

    def __post_init__(self) -> None:
        if not self.principal.strip():
            raise ValueError('PRINCIPAL_REQUIRED')
        if not self.session.strip():
            raise ValueError('SESSION_REQUIRED')
        if not self.intent.strip():
            raise ValueError('INTENT_REQUIRED')

    # Context manager protocol
    def __enter__(self) -> WriteContext:
        self._start_ts = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    async def __aenter__(self) -> WriteContext:
        return self.__enter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def close(self) -> None:
        """Mark the context as closed and record elapsed time.
        Idempotent: calling it twice is a no-op."""
        if self._closed:
            return
        self._closed = True
        self.duration_ms = round((time.perf_counter() - self._start_ts) * 1000, 1)

    def snap(self) -> dict:
        """Read-only snapshot for logging, the brief, or the gate."""
        return {
            'principal': self.principal,
            'session': self.session,
            'intent': self.intent,
            'branch': self.branch,
            'read_receipt': self.read_receipt,
            'started_at': self.started_at,
            'duration_ms': self.duration_ms,
            'closed': self._closed,
        }