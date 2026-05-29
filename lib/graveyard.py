"""
Graveyard — index of dead claude sessions available for necromancy.

Lazy index built over ``~/.claude/projects/<proj>/<uuid>.jsonl``. Each entry
captures enough metadata for a content-based search ("grave-dig") so a caller
can find the right session to consult without knowing UUIDs.

Stdlib-only. The index file at ``~/.agora/graveyard/index.jsonl`` is
authoritative; it's rebuilt whenever any source jsonl is newer than the index
mtime.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Iterable, Optional

from . import bus


# ---------- Paths ----------

CLAUDE_PROJECTS_ROOT = Path.home() / ".claude" / "projects"
GRAVEYARD_ROOT = bus.BUS_ROOT / "graveyard"
INDEX_PATH = GRAVEYARD_ROOT / "index.jsonl"
LIVE_DIR = GRAVEYARD_ROOT / "live"

# Limit per-jsonl scan: parsing a 100MB transcript is wasteful when we only need
# first/last user message + a keyword fingerprint. Cap reads to the first and
# last N lines.
HEAD_LINES = 200
TAIL_LINES = 200

# UUID regex — claude session ids are always v4 UUIDs (8-4-4-4-12 hex).
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


# ---------- Data model ----------

@dataclass
class GraveEntry:
    """One indexed dead session."""
    uuid: str
    path: str               # absolute jsonl path
    mtime: float            # unix epoch seconds
    size_mb: float
    turns: int              # message count (approx — line count is a fine proxy)
    label: str              # human-facing name (AoE title if known, else uuid prefix)
    first_user: str         # first non-system user message (truncated)
    last_user: str          # last non-system user message (truncated)
    keywords: list[str] = field(default_factory=list)

    def to_index_line(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_index_line(cls, line: str) -> Optional["GraveEntry"]:
        try:
            d = json.loads(line)
            return cls(**d)
        except (json.JSONDecodeError, TypeError):
            return None


# ---------- Index lifecycle ----------

def ensure_graveyard_root() -> None:
    """Idempotent. Creates ~/.agora/graveyard/ and live/."""
    GRAVEYARD_ROOT.mkdir(parents=True, exist_ok=True)
    LIVE_DIR.mkdir(parents=True, exist_ok=True)


def index_is_stale() -> bool:
    """True if any jsonl under CLAUDE_PROJECTS_ROOT is newer than INDEX_PATH.

    Cheap path: compare the newest jsonl's mtime to the index's mtime.
    """
    if not INDEX_PATH.exists():
        return True
    if not CLAUDE_PROJECTS_ROOT.exists():
        return False  # nothing to index
    idx_mtime = INDEX_PATH.stat().st_mtime
    for jsonl in iter_jsonl_paths():
        try:
            if jsonl.stat().st_mtime > idx_mtime:
                return True
        except OSError:
            continue
    return False


def iter_jsonl_paths() -> Iterable[Path]:
    """Yield every claude-session jsonl path under CLAUDE_PROJECTS_ROOT."""
    if not CLAUDE_PROJECTS_ROOT.exists():
        return
    for proj_dir in CLAUDE_PROJECTS_ROOT.iterdir():
        if not proj_dir.is_dir():
            continue
        for jsonl in proj_dir.glob("*.jsonl"):
            if UUID_RE.match(jsonl.stem):
                yield jsonl


def build_index(force: bool = False) -> list[GraveEntry]:
    """Build (or refresh) the graveyard index. Returns all entries.

    Incremental: entries whose source jsonl mtime matches the indexed mtime
    are reused as-is. Only jsonls newer than their indexed copy — plus newly
    appeared jsonls — are re-scanned. With 167 sessions and one new write
    per minute on one session, refresh cost drops from full-rescan (~30s) to
    one-jsonl-rescan (<1s).

    ``force=True`` rebuilds every entry from scratch.
    """
    ensure_graveyard_root()
    # Build a label map from the default AoE profile so we can name sessions
    # by their AoE title where one exists. agent_session_id is the link.
    aoe_label_by_uuid = _aoe_label_map()

    if force:
        cached_by_uuid: dict[str, GraveEntry] = {}
    else:
        # If nothing changed, short-circuit on the load_index path.
        if not index_is_stale():
            return load_index()
        cached_by_uuid = {e.uuid: e for e in load_index()}

    entries: list[GraveEntry] = []
    for jsonl in iter_jsonl_paths():
        uuid = jsonl.stem
        try:
            current_mtime = jsonl.stat().st_mtime
        except OSError:
            continue
        cached = cached_by_uuid.get(uuid)
        if cached is not None and cached.mtime == current_mtime:
            # Source unchanged since we indexed it. Reuse — but refresh label
            # in case AoE registry changed (cheap; no jsonl read).
            cached.label = aoe_label_by_uuid.get(uuid) or cached.label
            entries.append(cached)
            continue
        entry = _scan_jsonl(jsonl, aoe_label_by_uuid)
        if entry is not None:
            entries.append(entry)

    # Write index atomically via tmp file.
    tmp = INDEX_PATH.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(e.to_index_line() + "\n")
    tmp.replace(INDEX_PATH)
    rebuilt = sum(1 for e in entries if e.uuid not in cached_by_uuid or
                  cached_by_uuid[e.uuid].mtime != e.mtime)
    bus.audit("graveyard.index_built", entries=len(entries), rebuilt=rebuilt)
    return entries


def load_index() -> list[GraveEntry]:
    """Read the on-disk index without rebuilding."""
    if not INDEX_PATH.exists():
        return []
    out = []
    with INDEX_PATH.open(encoding="utf-8") as f:
        for line in f:
            e = GraveEntry.from_index_line(line)
            if e is not None:
                out.append(e)
    return out


# ---------- Scanning ----------

def _aoe_label_map() -> dict[str, str]:
    """Map claude-session UUID → AoE title from default profile (best-effort).

    AoE stores ``agent_session_id`` on each session record once the poller
    has captured it. That's the link from a long-lived AoE pane to its current
    claude jsonl. The mapping is best-effort: many old jsonls have no AoE
    record at all (orphaned, container, manually-started claude).
    """
    out: dict[str, str] = {}
    if not bus.SESSIONS_JSON.exists():
        return out
    try:
        data = json.loads(bus.SESSIONS_JSON.read_text())
    except (json.JSONDecodeError, OSError):
        return out
    entries = data if isinstance(data, list) else data.get("sessions", [])
    for s in entries:
        title = s.get("title")
        sid = s.get("agent_session_id")
        if title and sid:
            out[sid] = title
    return out


def _scan_jsonl(path: Path, aoe_label_by_uuid: dict[str, str]) -> Optional[GraveEntry]:
    """Read one jsonl and extract index metadata. Returns None on unreadable file."""
    try:
        st = path.stat()
    except OSError:
        return None
    uuid = path.stem
    size_mb = st.st_size / (1024 * 1024)

    first_user = ""
    last_user = ""
    turns = 0

    # Stream the file once to count message turns AND grab first_user.
    # LOW-1: count only user/assistant lines; tool-result and system entries
    # would inflate the count and make the displayed `turns` field a poor
    # signal of session depth. Counting requires a JSON parse per line.
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                try:
                    m = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if m.get("type") in ("user", "assistant"):
                    turns += 1
                if not first_user and i < HEAD_LINES:
                    msg = _extract_user_text(line)
                    if msg:
                        first_user = _truncate(msg)
    except OSError:
        return None

    last_user = _tail_user_message(path, max_lines=TAIL_LINES)

    label = aoe_label_by_uuid.get(uuid) or _label_from_first_user(first_user) or uuid[:8]
    keywords = _extract_keywords(first_user + " " + last_user)

    return GraveEntry(
        uuid=uuid,
        path=str(path),
        mtime=st.st_mtime,
        size_mb=round(size_mb, 2),
        turns=turns,
        label=label,
        first_user=first_user,
        last_user=last_user,
        keywords=keywords,
    )


def _extract_user_text(line: str) -> str:
    """Pull a real user message out of a jsonl line, or '' if it isn't one."""
    try:
        m = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return ""
    if m.get("type") != "user":
        return ""
    content = m.get("message", {}).get("content", "")
    if isinstance(content, list):
        # Tool-result entries are dicts; extract text fragments only.
        content = "".join(
            c.get("text", "") for c in content
            if isinstance(c, dict) and c.get("type") == "text"
        )
    text = str(content).strip()
    # Skip system reminders, tool-result echoes, and synthetic context blobs
    # that aren't real user messages.
    if not text:
        return ""
    if text.startswith("<") or "system-reminder" in text[:200]:
        return ""
    if "tool_use_id" in text[:80] or "tool_result" in text[:80]:
        return ""
    if len(text) < 10:
        return ""
    return text


def _tail_user_message(path: Path, max_lines: int = 200) -> str:
    """Find the last real user message by scanning the tail of the file.

    Reads the whole file when small (< 5 MB) since that's still <100ms.
    For larger files, uses a backwards-chunked scan stop after max_lines
    candidate user lines.
    """
    try:
        if path.stat().st_size < 5 * 1024 * 1024:
            # Small file — just walk forward, remember the last.
            last = ""
            with path.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    msg = _extract_user_text(line)
                    if msg:
                        last = msg
            return _truncate(last)
    except OSError:
        return ""

    # Large file: read the last ~256 KB and scan.
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            end = f.tell()
            chunk = min(end, 256 * 1024)
            f.seek(end - chunk)
            tail = f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""

    lines = tail.splitlines()
    # Drop the first (probably partial) line.
    if lines:
        lines = lines[1:]
    last = ""
    for line in lines[-max_lines:]:
        msg = _extract_user_text(line)
        if msg:
            last = msg
    return _truncate(last)


def _truncate(s: str, limit: int = 240) -> str:
    s = s.replace("\n", " ").strip()
    if len(s) <= limit:
        return s
    return s[: limit - 1] + "…"


def _label_from_first_user(first_user: str) -> str:
    """Derive a slug-ish label from the first user message ('' on no signal)."""
    if not first_user:
        return ""
    # Strip common openers ("hey claude", "yo", etc.) and pick first 4-5 words.
    cleaned = re.sub(r"^(hey claude|hey|yo|sup|hi)[\s,]+", "", first_user, flags=re.I)
    words = re.findall(r"\b[a-zA-Z][a-zA-Z0-9_-]{2,}\b", cleaned)[:5]
    if not words:
        return ""
    return "-".join(w.lower() for w in words)[:48]


# Stop-words intentionally narrow: agora's queries are usually technical
# noun-phrases ("vietnam backfill", "aoe session anchor"), not English prose.
_STOP_WORDS = {
    "the", "and", "for", "you", "this", "that", "with", "from", "have",
    "are", "was", "but", "not", "all", "can", "your", "what", "when",
    "how", "did", "its", "out", "now", "just", "any", "one", "two", "get",
    "got", "see", "say", "use", "let", "yes", "yeah", "okay", "ok", "ah",
}


def _extract_keywords(text: str, limit: int = 24) -> list[str]:
    """Pick distinctive tokens for substring matching during grave-dig."""
    if not text:
        return []
    tokens = re.findall(r"\b[a-zA-Z][a-zA-Z0-9_/.-]{2,}\b", text.lower())
    seen: dict[str, int] = {}
    for t in tokens:
        if t in _STOP_WORDS or len(t) > 32:
            continue
        seen[t] = seen.get(t, 0) + 1
    # Most-frequent first, capped.
    ranked = sorted(seen.items(), key=lambda kv: (-kv[1], kv[0]))
    return [w for w, _ in ranked[:limit]]


# ---------- Search ----------

@dataclass
class DigResult:
    entry: GraveEntry
    score: float
    hits: list[str]      # which query tokens matched


def grave_dig(query: str, limit: int = 5) -> list[DigResult]:
    """Search the graveyard for sessions matching ``query``.

    Scoring (simple but defensible):
      - +3 for each query token in keywords
      - +2 for each query token substring in label
      - +1 for each query token substring in first_user/last_user
      - +recency bonus: log-decay from mtime, clipped to +2 total
      - +size bonus: log-decay from size_mb, clipped to +1 (meatier sessions
        tend to have more knowledge)

    Returns top-``limit`` results, score-descending. Returns [] if no candidate
    scores above 0.
    """
    import math

    entries = build_index()  # auto-loads if up to date
    q_tokens = _extract_keywords(query, limit=16)
    if not q_tokens:
        # Fall back to raw substrings — single-word or very short queries.
        q_tokens = [w.lower() for w in re.findall(r"\b\w{2,}\b", query)]
    if not q_tokens:
        return []

    now = time.time()
    results: list[DigResult] = []
    for e in entries:
        score = 0.0
        hits: list[str] = []
        kw_set = set(e.keywords)
        label_lo = e.label.lower()
        first_lo = e.first_user.lower()
        last_lo = e.last_user.lower()
        for t in q_tokens:
            matched = False
            if t in kw_set:
                score += 3
                matched = True
            elif t in label_lo:
                score += 2
                matched = True
            elif t in first_lo or t in last_lo:
                score += 1
                matched = True
            if matched:
                hits.append(t)
        if score <= 0:
            continue
        # Recency: -log(days_old + 1) scaled
        age_days = max(0.0, (now - e.mtime) / 86400.0)
        score += max(0.0, 2.0 - math.log(age_days + 1.0))
        # Size: log-scale, clipped
        score += min(1.0, math.log(e.size_mb + 1.0) * 0.4)
        results.append(DigResult(entry=e, score=round(score, 2), hits=hits))

    results.sort(key=lambda r: -r.score)
    return results[:limit]


def resolve(label_or_uuid: str) -> Optional[GraveEntry]:
    """Resolve a label or UUID prefix to a single GraveEntry.

    Returns None if no match or if the match is ambiguous (e.g. label prefix
    matches multiple entries). Caller should fall back to grave_dig.
    """
    entries = build_index()
    # Try exact UUID first
    for e in entries:
        if e.uuid == label_or_uuid:
            return e
    # Then exact label
    for e in entries:
        if e.label == label_or_uuid:
            return e
    # Then UUID prefix (unambiguous only)
    pre = label_or_uuid.lower()
    matches = [e for e in entries if e.uuid.startswith(pre)]
    if len(matches) == 1:
        return matches[0]
    # Then label prefix (unambiguous only)
    matches = [e for e in entries if e.label.lower().startswith(pre)]
    if len(matches) == 1:
        return matches[0]
    return None
