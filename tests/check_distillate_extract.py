"""Stage 3 self-check (П4.1): the deterministic capture primitives.

For each host tree (claude/, opencode/) the pure functions of
``mockup/distillate.py`` must:

1. split sentences with the hardened boundaries of spec §12.3 (decimals and
   single-letter abbreviations do not split; fenced blocks and URLs stay
   inside one unit);
2. score capture per §12.3 (explicit requests capture alone; a lone
   unsupported marker does not; a decision marker plus a second marker or a
   project noun captures; noise-only commands do not; at most 3 sentences,
   none longer than 300 chars, offsets resolve into the raw text);
3. produce a stable marker pack digest, identical in both trees.

    python tests/check_distillate_extract.py

Requires the mockup engine importable (the same interpreter runs the server).
"""
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = sys.executable
TREES = ('claude', 'opencode')

SCRIPT = """
import sys
sys.path.insert(0, sys.argv[1])
from mockup import distillate as d

# 1. decimals and abbreviations stay one sentence
src = "Сделай 3.14 и т.д. Потом решаем."
spans = d.split_spans(src)
assert spans[0][0] == "Сделай 3.14 и т.д.", spans
assert spans[1][0] == "Потом решаем.", spans
assert spans[0][0] == src[spans[0][1]:spans[0][2]], spans  # raw offsets

# 2. a fenced block stays inside one unit
src = "Сделай фикс.\\n```\\nкод. с точками\\n```\\nПотом решаем."
spans = d.split_spans(src)
holders = [t for (t, s, e) in spans if 'код. с точками' in t]
assert len(holders) == 1 and 'решаем' in holders[0], spans

# 3. a URL does not split a sentence
src = "Документация: http://example.com/doc.1. Читай сначала."
spans = d.split_spans(src)
assert spans[-1][0] == "Читай сначала.", spans

# 4. an explicit request captures alone
r = d.capture_decision("Важно запомнить: делаем детерминированный дистиллят.", [])
assert r["capture"] is True and r["explicit"] is True, r

# 5. a lone unsupported marker does not capture
assert d.capture_decision("Важно: привет", [])["capture"] is False, r

# 6. decision marker + project noun
assert d.capture_decision("Решили фиксировать baseline.", ["baseline", "r14"])["capture"] is True

# 7. decision marker + a second marker through stem matching
assert d.capture_decision("Решили фиксировать baseline.", [])["capture"] is True

# 8. a noise-only command does not capture, even with a noun
assert d.capture_decision("сделай дашборд", ["дашборд"])["capture"] is False

# 9. at most 3 sentences are selected, none longer than 300 chars
src = ("Решили фиксировать baseline. "
       "Делаем детерминированный дистиллят, это важно. "
       "Нельзя ломать источник, это наше правило. "
       "Требование: якоря должны быть детерминированы. "
       "Сделай потом отчёт.")
r = d.capture_decision(src, [])
assert len(r["selected"]) == 3, r
assert all(len(t) <= 300 for (t, s, e) in r["selected"]), r
assert all('Сделай потом' not in t for (t, s, e) in r["selected"]), r

# 10. an over-long sentence is not selected
long_src = "Решили " + "очень длинное " * 40 + "правило."
r = d.capture_decision(long_src, [])
assert r["capture"] is False and r["selected"] == [], r

# 11. selected offsets resolve into the raw source
src = "Решили фиксировать baseline. Делаем детерминированный дистиллят."
r = d.capture_decision(src, [])
for (t, s, e) in r["selected"]:
    assert src[s:e] == t, (t, s, e)

# 12. the marker pack digest is stable
assert d.pack_digest() == d.pack_digest()

# 13. Russian stress marks do not break marker matching
r = d.capture_decision("За\u0301помни: правило номер 5", [])
assert r["capture"] is True and r["explicit"] is True, r

# 14. the homonym tongue-twister is not a project statement
assert d.capture_decision("Косил косой косой косой.", [])["capture"] is False

# 15. a homonym false hit is gated by the support rule ("решение" vs "решето")
assert d.capture_decision("Решили мыло через решето.", [])["capture"] is False

print("digest:" + d.pack_digest())
print("ok: extract " + sys.argv[2])
"""


def run_tree(tree):
    env = dict(os.environ)
    env.update({'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'})
    r = subprocess.run([PY, '-B', '-c', SCRIPT, str(REPO / tree), tree],
                       capture_output=True, text=True, encoding='utf8',
                       errors='replace', env=env, timeout=120)
    digest = None
    for line in r.stdout.splitlines():
        if line.startswith('digest:'):
            digest = line[len('digest:'):]
    return r, digest


def main():
    try:
        sys.path.insert(0, str(REPO / 'opencode'))
        import mockup.admission  # noqa: F401
    except ImportError:
        print('SKIP: the mockup engine is not importable; run this check with the venv that runs the server.')
        return 3
    digests, ok = {}, True
    for tree in TREES:
        r, digest = run_tree(tree)
        if r.returncode != 0:
            print('FAIL[%s]: %s' % (tree, (r.stderr or r.stdout).strip()[-800:]))
            ok = False
            continue
        digests[tree] = digest
        print('  %s/: PASS' % tree)
    if ok and len(set(digests.values())) != 1:
        print('FAIL: the marker pack digest differs across trees: %r' % digests)
        ok = False
    print('PASS: extract primitives identical in both trees' if ok else 'FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
