"""Offline index of agent skills: collect, parse, cache, propose categories.

The index is derived data. It never enters the ledger and is rebuilt from files on disk:

    collect(root precedence) -> parse SKILL.md -> SkillRecord -> cache JSON

Three invariants keep the index honest (see docs/concepts/2026-11-27-skill-selector-addendum.md):

* ``mentions`` are extracted from the description text. They are evidence that a skill
  *talks about* a topic, never evidence that it provides a capability.
* ``provides`` are not parsed at all. ``verified_provides`` is reserved for curated cards
  (B5) and stays empty here.
* ``categories`` are a tuple: a skill may belong to several groups, so downstream code must
  not assume a single category string.

Filesystem precedence mirrors the loader: roots are walked in order and a later root
overrides an earlier one when both declare the same ``skill_id``.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Optional

try:  # PyYAML is a declared dependency; the fallback keeps the module importable.
    import yaml
except ImportError:  # pragma: no cover - exercised only on a stripped interpreter
    yaml = None

# Documented roots, in walk order. Project roots are appended by the caller through
# ``collect(roots=skill_roots(project_root))`` so the module has no hidden cwd dependency.
SKILL_ROOTS = [
    Path.home() / '.config' / 'opencode' / 'skills',
    Path.home() / '.claude' / 'skills',
]

CACHE_SCHEMA = 1
DEFAULT_CACHE_DIR = Path('.dom-memory') / 'skill-cache'

# Closed vocabulary for ``mentions``. Character stems, not whole words: ``migrat`` matches
# migration/migrations/миграция; ``миграц`` matches the Russian inflection. Mentions are
# advisory only, so a false stem hit is a display nuisance, not a routing decision.
MENTION_STEMS = (
    'admet', 'article', 'astronom', 'astro', 'audio', 'audit', 'bayesian', 'benchmark',
    'biolog', 'chart', 'citation', 'claim', 'chemistry', 'commit', 'contradiction',
    'dataframe', 'database', 'debug', 'deploy', 'docker', 'doc', 'docking', 'drug',
    'embedding', 'excel', 'fluid', 'formula', 'genom', 'git', 'image', 'inject',
    'javascript', 'latex', 'ledger', 'literature', 'machine learning', 'matlab',
    'microscop', 'migrat', 'model', 'molecul', 'music', 'neural', 'notebook', 'optim',
    'pandas', 'pathway', 'pdf', 'performance', 'physics', 'plan', 'plot', 'polars',
    'prompt', 'protein', 'python', 'queue', 'refactor', 'report', 'research',
    'retrieval', 'review', 'rust', 'schema', 'search', 'secur', 'simulation', 'spectr',
    'spreadsheet', 'sql', 'statistic', 'structure', 'test', 'thermodynam', 'triz',
    'typescript', 'video', 'visuali', 'word', 'writing',
    'безопасн', 'визуализ', 'докум', 'запис', 'миграц', 'модел', 'обзор', 'очеред',
    'план', 'проверк', 'расчёт', 'стать', 'структур', 'тест', 'формул',
)

# A section whose title starts with one of these is the "when to use" gate.
_WHEN_PREFIXES = ('when to use',)
_EXAMPLE_PREFIXES = ('example',)
_HEADER_RE = re.compile(r'^##\s+(.+?)\s*$', re.M)
_FENCE_RE = re.compile(r'```[^\n]*\n(.*?)```', re.S)
_WORD_RE = re.compile(r'\w+')


@dataclass(frozen=True)
class SkillRecord:
    """One parsed SKILL.md. Immutable so a cached record cannot drift under a caller."""

    skill_id: str              # from the name field, else the containing directory
    name: str                  # from frontmatter name
    description: str           # from frontmatter description ('' when absent)
    categories: tuple[str, ...] = ()   # from category/categories; several are allowed
    tags: tuple[str, ...] = ()         # from frontmatter tags
    mentions: tuple[str, ...] = ()     # derived from description; NEVER provides
    usage_hint: str = ''               # first paragraph of "## When to use", else description
    examples: tuple[str, ...] = ()     # commands from "## Example" fenced blocks
    has_when_to_use: bool = False
    has_example: bool = False
    source_path: str = ''
    content_digest: str = ''           # sha256 of the raw file bytes
    verified_provides: tuple[str, ...] = ()  # curated card only (B5); never from text
    card_only: bool = False                  # True when this record was created from a card, not a SKILL.md file
    card_only: bool = False                  # skill exists only in cards, not as SKILL.md

    def __post_init__(self):
        # A hand-built record must obey the same tuple invariants as a parsed one.
        for name in ('categories', 'tags', 'mentions', 'examples', 'verified_provides'):
            value = getattr(self, name)
            if isinstance(value, tuple):
                continue
            if value is None:
                value = ()
            elif isinstance(value, str):
                value = (value,)
            else:
                value = tuple(value)
            object.__setattr__(self, name, value)


def skill_roots(project_root=None) -> list[Path]:
    """Documented roots, optionally followed by a project's own skills directory."""
    roots = [Path(root) for root in SKILL_ROOTS]
    if project_root is not None:
        roots.append(Path(project_root) / 'opencode' / 'skills')
    return roots


def _capability_version() -> int:
    """Version of the curated capability data, read the same way skill_router reads it."""
    try:
        data = json.loads((Path(__file__).with_name('capabilities.json')).read_text(encoding='utf-8'))
        return int(data.get('version') or 1)
    except (OSError, ValueError, TypeError):
        return 1


CAPABILITY_VERSION = _capability_version()


# ---------------------------------------------------------------------------
# frontmatter and sections
# ---------------------------------------------------------------------------

def _split_frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter_text, body). A missing or unterminated block is empty."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != '---':
        return '', text
    for index in range(1, len(lines)):
        if lines[index].strip() == '---':
            return '\n'.join(lines[1:index]), '\n'.join(lines[index + 1:])
    return '', text


def _fallback_frontmatter(fm_text: str) -> dict:
    """Line-based reader for frontmatter that safe_load rejects (unquoted ``: ``)."""
    data = {}
    lines = fm_text.splitlines()
    index = 0
    while index < len(lines):
        match = re.match(r'^([A-Za-z_][\w-]*):\s*(.*)$', lines[index])
        if not match:
            index += 1
            continue
        key, raw = match.group(1), match.group(2).strip()
        if raw in ('>', '|', '>-', '|-', '>+', '|+'):
            block = []
            index += 1
            while index < len(lines) and (not lines[index].strip() or lines[index][:1] in (' ', '\t')):
                block.append(lines[index].strip())
                index += 1
            data[key] = ' '.join(part for part in block if part)
            continue
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ('"', "'"):
            raw = raw[1:-1]
        elif raw.startswith('[') and raw.endswith(']'):
            raw = [item.strip().strip('"\'') for item in raw[1:-1].split(',') if item.strip()]
        data[key] = raw
        index += 1
    return data


def _load_frontmatter(fm_text: str) -> dict:
    if not fm_text.strip():
        return {}
    if yaml is not None:
        try:
            data = yaml.safe_load(fm_text)
        except Exception:  # yaml.YAMLError plus anything a broken file can throw
            return _fallback_frontmatter(fm_text)
        if isinstance(data, dict):
            return data
        return {}
    return _fallback_frontmatter(fm_text)


def _as_tuple(value, lower: bool = False) -> tuple[str, ...]:
    """Normalize a scalar, comma list or YAML list into a de-duplicated tuple."""
    if value is None:
        return ()
    if isinstance(value, (list, tuple, set)):
        items = [str(item) for item in value]
    else:
        items = str(value).split(',')
    seen = []
    for item in items:
        text = item.strip().strip('"\'').strip()
        if lower:
            text = text.lower()
        if text and text not in seen:
            seen.append(text)
    return tuple(seen)


def _collapse(text: str) -> str:
    return ' '.join(str(text or '').split())


def _sections(body: str) -> list[tuple[str, str]]:
    """Level-2 sections as (lowercase title, content), in file order."""
    matches = list(_HEADER_RE.finditer(body))
    result = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        result.append((match.group(1).strip().lower(), body[match.end():end]))
    return result


def _first_paragraph(content: str) -> str:
    block = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            if block:
                break
            continue
        block.append(stripped)
    return _collapse(' '.join(block))


def _section_commands(content: str) -> list[str]:
    commands = []
    for match in _FENCE_RE.finditer(content):
        for line in match.group(1).splitlines():
            command = line.strip()
            if command and not command.startswith('#'):
                commands.append(command)
    if not commands:
        for line in content.splitlines():
            for match in re.finditer(r'`([^`]+)`', line):
                command = match.group(1).strip()
                if command and ' ' in command:
                    commands.append(command)
    seen = []
    for command in commands:
        if command not in seen:
            seen.append(command)
    return seen


def _extract_mentions(description: str) -> tuple[str, ...]:
    """Topic stems present in the description. Deliberately not a provides list."""
    text = description.lower()
    if not text:
        return ()
    return tuple(sorted(stem for stem in MENTION_STEMS if stem in text))


# ---------------------------------------------------------------------------
# file parsing
# ---------------------------------------------------------------------------

def parse_skill_file(path) -> SkillRecord:
    """Parse one SKILL.md. A malformed or missing frontmatter degrades, never raises."""
    path = Path(path)
    raw = path.read_bytes()
    text = raw.decode('utf-8-sig', errors='replace')
    fm_text, body = _split_frontmatter(text)
    frontmatter = _load_frontmatter(fm_text)

    name = _collapse(frontmatter.get('name') or '')
    if not name:
        # "SKILL.md" is a constant, so the directory is the file's identity.
        name = path.parent.name or path.stem
    description = _collapse(frontmatter.get('description') or '')

    categories = _as_tuple(
        frontmatter.get('category') if frontmatter.get('category') is not None
        else frontmatter.get('categories'),
        lower=True,
    )
    tags = _as_tuple(frontmatter.get('tags'))

    usage_hint = ''
    examples: list[str] = []
    has_when = False
    has_example = False
    for title, content in _sections(body):
        if not has_when and title.startswith(_WHEN_PREFIXES):
            has_when = True
            usage_hint = _first_paragraph(content)
        if title.startswith(_EXAMPLE_PREFIXES):
            has_example = True
            examples.extend(_section_commands(content))
    if not usage_hint:
        usage_hint = _first_paragraph(description)

    return SkillRecord(
        skill_id=name,
        name=name,
        description=description,
        categories=categories,
        tags=tags,
        mentions=_extract_mentions(description),
        usage_hint=usage_hint,
        examples=tuple(dict.fromkeys(examples)),
        has_when_to_use=has_when,
        has_example=has_example,
        source_path=str(path),
        content_digest=hashlib.sha256(raw).hexdigest(),
        verified_provides=(),  # curated cards only; parsing never invents provides
    )


# ---------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------

def _record_to_dict(record: SkillRecord) -> dict:
    payload = {}
    for item in fields(SkillRecord):
        value = getattr(record, item.name)
        payload[item.name] = list(value) if isinstance(value, tuple) else value
    return payload


def record_from_dict(data: dict) -> SkillRecord:
    """Rebuild a record, restoring every tuple that JSON turned into a list."""
    payload = dict(data)
    for name in ('categories', 'tags', 'mentions', 'examples', 'verified_provides'):
        payload[name] = tuple(payload.get(name) or ())
    payload.setdefault('name', payload.get('skill_id') or '')
    payload.setdefault('skill_id', payload.get('name') or '')
    return SkillRecord(**payload)


def records_from_cache(cache: Optional[dict]) -> list[SkillRecord]:
    """Records stored in a cache payload, ordered the same way collect() orders them."""
    if not cache or not isinstance(cache.get('skills'), list):
        return []
    return sorted((record_from_dict(row) for row in cache['skills']), key=lambda row: row.skill_id)


def fingerprint_for(skills, capability_version: Optional[int] = None) -> str:
    """sha256 over skill identity + path + mtime + size + capability version."""
    version = CAPABILITY_VERSION if capability_version is None else int(capability_version)
    parts = ['capability_version=%d' % version]
    for record in sorted(skills, key=lambda row: row.skill_id):
        try:
            stat = Path(record.source_path).stat()
            mtime, size = stat.st_mtime_ns, stat.st_size
        except OSError:
            mtime, size = 0, 0
        parts.append('%s|%s|%d|%d' % (record.skill_id, record.source_path, mtime, size))
    return hashlib.sha256('\n'.join(parts).encode('utf-8')).hexdigest()


class SkillIndex:
    """Offline index of all skills. One-time build, cached by digest."""

    def __init__(self, cache_dir=None):
        self.cache_dir = Path(cache_dir) if cache_dir is not None else DEFAULT_CACHE_DIR

    def collect(self, roots: Optional[list[Path]] = None, cards_path: Optional[Path] = None) -> list[SkillRecord]:
        """Walk roots in precedence order; a later root overrides the same skill_id.

        If ``cards_path`` is None (default), merge card-only skills from
        ``<repo>/opencode/mockup/skills.card.json``.  Pass ``cards_path=False`` to skip.
        """
        roots = SKILL_ROOTS if roots is None else roots
        collected: dict[str, SkillRecord] = {}
        for root in roots:
            root = Path(root)
            if not root.is_dir():
                continue
            try:
                paths = sorted(root.rglob('SKILL.md'), key=str)
            except OSError:
                continue
            for path in paths:
                try:
                    record = parse_skill_file(path)
                except OSError:
                    continue
                collected[record.skill_id] = record
        # 2. Merge card-only skills from skills.card.json (virtual skills without SKILL.md)
        cards_source = cards_path if cards_path is not None else Path(__file__).resolve().parents[1] / 'mockup' / 'skills.card.json'
        if cards_source:
            try:
                cards_path_resolved = Path(cards_source) if isinstance(cards_source, str) else cards_source
                if cards_path_resolved.is_file():
                    cards_data = json.loads(cards_path_resolved.read_text(encoding='utf-8'))
                    for card in cards_data.get('cards', []):
                        cid = str(card.get('id', '')).strip()
                        if not cid or cid in collected:
                            continue
                        collected[cid] = SkillRecord(
                            skill_id=cid,
                            name=str(card.get('name', cid)),
                            description='Card-only skill: %s' % card.get('evidence', ''),
                            categories=(str(card.get('category', '')),) if card.get('category') else (),
                            verified_provides=tuple(str(p).strip() for p in card.get('provides', []) if p),
                            card_only=True,
                        )
            except (OSError, ValueError, TypeError):
                pass  # no cards file = no card-only skills; that is fine
        return sorted(collected.values(), key=lambda row: row.skill_id)

    def build_cache(self, skills: list[SkillRecord]) -> dict:
        """Serializable payload keyed by a fingerprint of the inputs, not of wall time."""
        records = sorted(skills, key=lambda row: row.skill_id)
        return {
            'schema': CACHE_SCHEMA,
            'fingerprint': fingerprint_for(records),
            'capability_version': CAPABILITY_VERSION,
            'skills': [_record_to_dict(record) for record in records],
        }

    def save_cache(self, cache: dict, cache_dir=None) -> Path:
        """Write index-<fingerprint>.json under the (gitignored) cache directory."""
        if not isinstance(cache, dict) or not cache.get('fingerprint'):
            raise ValueError('cache payload needs a fingerprint')
        target_dir = Path(cache_dir) if cache_dir is not None else self.cache_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / ('index-%s.json' % cache['fingerprint'])
        path.write_text(json.dumps(cache, ensure_ascii=False, sort_keys=True, indent=2) + '\n',
                        encoding='utf-8')
        return path

    def load_cache(self, fingerprint: str, cache_dir=None) -> Optional[dict]:
        """Load a payload by fingerprint; corrupt or foreign caches are a miss, not an error."""
        target_dir = Path(cache_dir) if cache_dir is not None else self.cache_dir
        path = target_dir / ('index-%s.json' % fingerprint)
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError):
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get('fingerprint') != fingerprint:
            return None
        if payload.get('schema') != CACHE_SCHEMA:
            return None
        return payload


# ---------------------------------------------------------------------------
# BM25 category proposer
# ---------------------------------------------------------------------------

def _normalize_text(text: str) -> str:
    return _collapse(str(text or '').lower())


def char_ngrams(text: str, sizes=(3, 4, 5)) -> list[str]:
    """Per-token character n-grams; Russian inflection survives without stemming."""
    grams = []
    for token in _WORD_RE.findall(_normalize_text(text)):
        if len(token) < min(sizes):
            continue
        for size in sizes:
            if len(token) >= size:
                grams.extend(token[index:index + size] for index in range(len(token) - size + 1))
    return grams


def _idf(term: str, df: int, total: int) -> float:
    return math.log(1.0 + (total - df + 0.5) / (df + 0.5))


def _bm25_scores(query_text: str, corpus_texts, k1: float = 1.2, b: float = 0.75) -> list[float]:
    """BM25 (k1, b) score per document, with IDF taken from the whole corpus."""
    docs = []
    for text in corpus_texts:
        grams = char_ngrams(text)
        docs.append((Counter(grams), len(grams)))
    if not docs:
        return []
    total = len(docs)
    average_length = sum(length for _, length in docs) / total or 1.0
    document_frequency = Counter()
    for counts, _ in docs:
        document_frequency.update(counts.keys())
    terms = sorted(set(char_ngrams(query_text)))
    scores = []
    for counts, length in docs:
        score = 0.0
        for term in terms:
            frequency = counts.get(term, 0)
            if not frequency:
                continue
            weight = _idf(term, document_frequency[term], total)
            score += weight * (frequency * (k1 + 1.0)) / (
                frequency + k1 * (1.0 - b + b * length / average_length))
        scores.append(score)
    return scores


def _mean_doc_bm25(query_text: str, corpus_texts, k1: float = 1.2, b: float = 0.75) -> float:
    """Average of the per-document BM25 scores; 0.0 for an empty corpus."""
    scores = _bm25_scores(query_text, corpus_texts, k1, b)
    return sum(scores) / len(scores) if scores else 0.0


class CategoryProposer:
    """BM25-based category proposal for uncategorized skills.

    Profiles are built from already-categorized skills only. A skill that carries a category
    is never proposed to (it is skipped), and an empty profile set yields no proposals:
    silence is preferable to a decoy category.
    """

    def __init__(self, known_skills: list[SkillRecord], k1: float = 1.2, b: float = 0.75):
        self.k1 = k1
        self.b = b
        profiles: dict[str, dict] = {}
        for skill in known_skills:
            text = self._profile_text(skill)
            if not text:
                continue
            for category in skill.categories:
                profile = profiles.setdefault(category, {'texts': [], 'skills': []})
                profile['texts'].append(text)
                profile['skills'].append(skill.skill_id)
        self._profiles = {}
        for category in sorted(profiles):
            profile = profiles[category]
            text = ' '.join(profile['texts'])
            self._profiles[category] = {
                'text': text,
                'texts': profile['texts'],
                'skills': profile['skills'],
                'grams': set(char_ngrams(text)),
            }
        # One document per category: IDF is computed across categories, so scores compare.
        self._documents = [profile['text'] for profile in self._profiles.values()]
        self._total = len(self._documents)
        self._document_frequency: Counter = Counter()
        for text in self._documents:
            self._document_frequency.update(set(char_ngrams(text)))

    @staticmethod
    def _profile_text(skill: SkillRecord) -> str:
        return _normalize_text(' '.join([skill.name, skill.description, ' '.join(skill.tags)]))

    @property
    def categories(self) -> tuple[str, ...]:
        return tuple(self._profiles)

    def bm25(self, query_text: str, corpus_texts: list[str]) -> float:
        """Mean BM25 score of the query against a corpus; same inputs, same float."""
        return _mean_doc_bm25(query_text, list(corpus_texts or []), self.k1, self.b)

    def propose(self, skill: SkillRecord, top_k: int = 2) -> list[tuple[str, float, str]]:
        """Top-k (category, score, reason) for an uncategorized skill; [] if no evidence."""
        if skill.categories or top_k <= 0 or not self._profiles:
            return []
        query = self._profile_text(skill)
        if not query:
            return []
        # Each category profile is one document in a shared corpus, so IDF and length
        # normalisation are identical across categories and the scores are comparable.
        scores = _bm25_scores(query, self._documents, self.k1, self.b)
        scored = [(score, category) for category, score in zip(self._profiles, scores) if score > 0.0]
        scored.sort(key=lambda row: (-row[0], row[1]))
        return [(category, round(score, 6), self._reason(query, category))
                for score, category in scored[:top_k]]

    def _reason(self, query: str, category: str) -> str:
        profile = self._profiles[category]
        shared = set(char_ngrams(query)) & profile['grams']
        ranked = sorted(shared, key=lambda gram: (
            -_idf(gram, self._document_frequency[gram], self._total), gram))[:3]
        members = len(profile['skills'])
        if not ranked:
            return 'no shared char-ngram; nearest of %d profile(s)' % len(self._profiles)
        sizes = sorted({len(gram) for gram in ranked})
        label = '%d-%d-grams' % (sizes[0], sizes[-1]) if len(sizes) > 1 else '%d-grams' % sizes[0]
        return 'shared %s: %s; %d known skill(s) in %r' % (
            label, ', '.join(repr(gram) for gram in ranked), members, category)


__all__ = [
    'CAPABILITY_VERSION',
    'CACHE_SCHEMA',
    'CategoryProposer',
    'DEFAULT_CACHE_DIR',
    'MENTION_STEMS',
    'SKILL_ROOTS',
    'SkillIndex',
    'SkillRecord',
    'char_ngrams',
    'fingerprint_for',
    'parse_skill_file',
    'record_from_dict',
    'records_from_cache',
    'skill_roots',
]
