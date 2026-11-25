"""L1 WriteContext + writer_inventory tests.

    python -B tests/check_write_context.py

Checks:
  1a writer_inventory register + check + enforce
  1b writer_inventory idempotent registration
  1c writer_inventory refuses unknown writer
  2a WriteContext creation + fields
  2b WriteContext context-manager records duration
  2c WriteContext async context manager works
  2d WriteContext snapshot
  2e WriteContext refuses empty principal/session/intent
  3a inventory known_writers snapshot
  4  reset_for_test cleans inventory
"""
from __future__ import annotations

import asyncio
import sys
import time as _time

sys.path.insert(0, 'opencode/mockup')

from write_context import (
    WriteContext, WriterDenied,
    register, check, enforce, known_writers, reset_for_test,
)


def _pass(name: str) -> None:
    print(f'  PASS {name}')


def _fail(name: str, msg: str) -> None:
    print(f'  FAIL {name}: {msg}')
    raise SystemExit(1)


# 1a — writer_inventory register + check + enforce
register('orchestrator', capabilities={'submit', 'review'},
         allowed_branches={'AA', 'AB'})
register('worker', capabilities={'submit'},
         allowed_branches={'AA'})

assert check('orchestrator', 'submit', 'AA'), 'orchestrator submit AA'
assert check('orchestrator', 'review', 'AB'), 'orchestrator review AB'
assert not check('orchestrator', 'test', 'AA'), 'orchestrator test AA'
assert not check('worker', 'review', 'AA'), 'worker review AA'
assert not check('worker', 'submit', 'AB'), 'worker submit AB'
assert not check('unknown', 'submit', 'AA'), 'unknown writer'
_pass('1a register + check + enforce')

# 1b — idempotent registration
register('orchestrator', capabilities={'submit', 'review'},
         allowed_branches={'AA', 'AB', 'AC'})  # add AC
assert check('orchestrator', 'submit', 'AC'), 'idempotent added AC'
_pass('1b idempotent registration')

# 1c — enforce raises
try:
    enforce('worker', 'review', 'AA')
    _fail('1c', 'enforce should have raised')
except WriterDenied:
    pass
_pass('1c enforce raises')

# 2a — WriteContext creation
ctx = WriteContext(principal='orchestrator', session='ses_abc',
                   intent='close L1', branch='AA')
assert ctx.principal == 'orchestrator'
assert ctx.session == 'ses_abc'
assert ctx.intent == 'close L1'
assert ctx.branch == 'AA'
assert not ctx._closed
_pass('2a creation + fields')

# 2b — context manager duration
with WriteContext(principal='test', session='s1', intent='check duration') as wc:
    _time.sleep(0.01)
assert wc._closed
assert wc.duration_ms >= 5.0, f'duration too short: {wc.duration_ms}'
_pass('2b context manager + duration')

# 2c — async context manager
async def _check_async():
    async with WriteContext(principal='test', session='s2',
                            intent='async check') as wc:
        await asyncio.sleep(0.01)
    assert wc._closed
    assert wc.duration_ms >= 5.0
asyncio.run(_check_async())
_pass('2c async context manager')

# 2d — snapshot
snap = ctx.snap()
assert snap['principal'] == 'orchestrator'
assert snap['session'] == 'ses_abc'
assert snap['intent'] == 'close L1'
assert snap['branch'] == 'AA'
assert snap['closed'] is False
assert 'started_at' in snap
assert snap['duration_ms'] == 0.0
_pass('2d snapshot')

# 2e — refuses empty fields
for kwargs in (
    {'principal': '', 'session': 's', 'intent': 'i'},
    {'principal': 'p', 'session': '', 'intent': 'i'},
    {'principal': 'p', 'session': 's', 'intent': ''},
):
    try:
        WriteContext(**kwargs)
        _fail('2e', f'should refuse {kwargs}')
    except ValueError:
        pass
_pass('2e refuses empty fields')

# 3a — known_writers snapshot
all_w = known_writers()
assert 'orchestrator' in all_w
assert 'worker' in all_w
assert 'capabilities' in all_w['orchestrator']
assert 'allowed_branches' in all_w['orchestrator']
_pass('3a known_writers snapshot')

# 4 — reset_for_test
reset_for_test()
assert known_writers() == {}
_pass('4 reset_for_test')

print('PASS write_context: all 8 sub-checks passed')