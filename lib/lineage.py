"""Lineage — tracks parent/child relationships between spawned sessions.

Stored as a single dict at ~/.agora/lineage.json:

    {
        "<aoe_id>": {
            "parent": "<aoe_id>" | None,
            "title": "<label>",
            "spawned_at": "<iso>",
            "task": "<initial task snippet>"
        }
    }

Ancestors and descendants are derived by walking the parent pointer (up) or
filtering the dict for entries with parent==self (down). Keeping it flat
lets us be lazy + audit-friendly.
"""
from __future__ import annotations

import json
from typing import Optional

from . import bus


def _path():
    return bus.BUS_ROOT / "lineage.json"


def load() -> dict:
    p = _path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save(lineage: dict) -> None:
    _path().write_text(json.dumps(lineage, indent=2) + "\n")


def task_path(aoe_id: str):
    """Where the FULL initial task body for a spawned session lives."""
    return bus.BUS_ROOT / "lineage" / aoe_id / "task.md"


def read_task(aoe_id: str) -> Optional[str]:
    """Read the full initial task for a spawned session, or None if not stored."""
    p = task_path(aoe_id)
    if not p.exists():
        return None
    try:
        return p.read_text()
    except OSError:
        return None


def register(aoe_id: str, title: str, parent_id: Optional[str] = None,
             task: str = "") -> None:
    """Record that aoe_id was spawned (optionally by parent_id).

    Stores a 200-char preview in lineage.json (for `agora tree` rendering) AND
    writes the full task body to ~/.agora/lineage/<aoe_id>/task.md so it stays
    recoverable if delivery to the child's inbox fails.
    """
    bus.ensure_bus_root()
    data = load()
    data[aoe_id] = {
        "parent": parent_id,
        "title": title,
        "spawned_at": bus.now_iso(),
        "task": task[:200],
    }
    save(data)

    # Durable full-body copy. Survives tmux truncation, child crashes, etc.
    if task:
        tp = task_path(aoe_id)
        tp.parent.mkdir(parents=True, exist_ok=True)
        tp.write_text(task)

    bus.audit("spawn.registered", aoe_id=aoe_id, title=title,
              parent=parent_id, task_bytes=len(task),
              task_preview=task[:80])


def ancestors(aoe_id: str) -> list[str]:
    """All ancestor aoe_ids, nearest first (parent, grandparent, ...)."""
    data = load()
    out = []
    cur = data.get(aoe_id, {}).get("parent")
    seen = {aoe_id}
    while cur and cur not in seen:
        out.append(cur)
        seen.add(cur)
        cur = data.get(cur, {}).get("parent")
    return out


def children(aoe_id: str) -> list[str]:
    """Direct children only."""
    return [k for k, v in load().items() if v.get("parent") == aoe_id]


def descendants(aoe_id: str) -> list[str]:
    """All descendants (children, grandchildren, ...) — DFS, depth-first order."""
    data = load()
    out = []
    stack = [aoe_id]
    seen = set()
    while stack:
        cur = stack.pop()
        for k, v in data.items():
            if v.get("parent") == cur and k not in seen:
                out.append(k)
                seen.add(k)
                stack.append(k)
    return out


def count_recent_children(parent_id: str, since_secs: int = 3600) -> int:
    """How many children parent_id has spawned within the window."""
    import time
    from datetime import datetime, timezone

    cutoff = time.time() - since_secs
    data = load()
    n = 0
    for v in data.values():
        if v.get("parent") != parent_id:
            continue
        try:
            ts = datetime.strptime(v["spawned_at"], "%Y-%m-%dT%H:%M:%SZ")
            ts = ts.replace(tzinfo=timezone.utc).timestamp()
        except (ValueError, KeyError):
            continue
        if ts >= cutoff:
            n += 1
    return n


def render_tree(root_id: str, sessions_lookup: Optional[dict] = None) -> str:
    """ASCII tree of descendants under root_id.

    sessions_lookup: optional dict aoe_id -> label, used when label may have
    changed since spawn time (we fall back to lineage.json's stored title).
    """
    data = load()
    if root_id not in data and not children(root_id):
        return "(no spawned children)\n"

    lines = []

    def _label(aid: str) -> str:
        if sessions_lookup and aid in sessions_lookup:
            return sessions_lookup[aid]
        return data.get(aid, {}).get("title", aid[:12])

    def _walk(aid: str, prefix: str = "", is_last: bool = True, depth: int = 0):
        if depth == 0:
            lines.append(f"{_label(aid)}  [{aid[:12]}]")
        else:
            branch = "└── " if is_last else "├── "
            lines.append(f"{prefix}{branch}{_label(aid)}  [{aid[:12]}]")
        kids = children(aid)
        for i, k in enumerate(kids):
            last = (i == len(kids) - 1)
            extension = "    " if is_last else "│   "
            new_prefix = prefix + (extension if depth > 0 else "")
            _walk(k, new_prefix, last, depth + 1)

    _walk(root_id)
    return "\n".join(lines) + "\n"
