"""Session identity and database-path identity of a trusted write.

L4 (trusted-write line): the host hook passes the host ``sessionID`` to the
child process as ``DOM_SESSION``; a session resolved that way is labelled
``source='hook'``.  An MCP process gets no ``DOM_SESSION`` (the host does not
launch it with one), so it stays ``mcp-unverified`` until a verified channel
exists.  Environment is a launch-time fact of the process -- a model-supplied
session or origin is never accepted from a tool argument.

L5: every writer records the real path of the database it wrote; health
compares the paths recorded by the MCP and hook channels.  A junction, case or
unicode difference that names the same database must not look like a different
one: paths are compared through ``realpath`` -> NFC -> ``normcase``
(D-019).  A recorded path that is still not the same database is reported as
``DB_PATH_MISMATCH``.
"""
from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass

# A session source is a launch-time fact.  ``mcp-unverified`` is the honest
# label of the MCP channel; it is never accepted as a claimed source.
SESSION_SOURCES = ('hook', 'mcp-plugin', 'mcp-unverified', 'director', 'unknown')


@dataclass(frozen=True)
class SessionIdentity:
    session: str
    source: str
    origin: str


def resolve_session(environ=None):
    """Identity of this process, from its environment only (L4).

    ``DOM_SESSION`` present -> that session, source ``DOM_SESSION_SOURCE``
    (default ``hook``, an unknown or ``mcp-unverified`` source degrades to
    ``unknown``), origin ``DOM_ORIGIN`` (default ``hook``).  No
    ``DOM_SESSION`` -> the MCP channel, which stays unverified.
    """
    env = os.environ if environ is None else environ
    session = env.get('DOM_SESSION')
    if session:
        source = env.get('DOM_SESSION_SOURCE') or 'hook'
        if source not in SESSION_SOURCES or source == 'mcp-unverified':
            source = 'unknown'
        return SessionIdentity(session, source, env.get('DOM_ORIGIN') or 'hook')
    return SessionIdentity('mcp-unverified', 'mcp-unverified', env.get('DOM_ORIGIN') or 'mcp')


def normalize_db_path(p):
    """Comparable form of a database path: realpath -> NFC -> normcase (L5/D-019)."""
    return os.path.normcase(unicodedata.normalize('NFC', os.path.realpath(str(p))))


def same_db_path(a, b):
    """True when both paths name the same database on this host."""
    return normalize_db_path(a) == normalize_db_path(b)
