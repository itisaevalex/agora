"""Per-session link list. Stored at ~/.aoe-bus/sessions/<aoe-id>/links.json."""
from __future__ import annotations

import json
from typing import Optional

from . import bus


def load(self_aoe_id: str) -> list[dict]:
    """Return current links; empty list if none."""
    p = bus.links_path(self_aoe_id)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def add(self_aoe_id: str, peer_aoe_id: str, peer_label: str) -> tuple[bool, str]:
    """Idempotent. Returns (added_now, message)."""
    if peer_aoe_id == self_aoe_id:
        return False, "refusing to link a session to itself"
    current = load(self_aoe_id)
    if any(L["aoe_id"] == peer_aoe_id for L in current):
        return False, f"already linked to {peer_label}"
    current.append({
        "aoe_id": peer_aoe_id,
        "label": peer_label,
        "added_at": bus.now_iso(),
    })
    _save(self_aoe_id, current)
    bus.audit("link.add", self_id=self_aoe_id, peer_id=peer_aoe_id, peer_label=peer_label)
    return True, f"linked to {peer_label}"


def remove(self_aoe_id: str, peer_label_or_id: str) -> tuple[bool, str]:
    """Remove by label or aoe_id. Returns (removed, message)."""
    current = load(self_aoe_id)
    kept = [L for L in current if L["label"] != peer_label_or_id
            and not L["aoe_id"].startswith(peer_label_or_id)]
    if len(kept) == len(current):
        return False, f"no link matching {peer_label_or_id!r}"
    _save(self_aoe_id, kept)
    bus.audit("link.remove", self_id=self_aoe_id, target=peer_label_or_id)
    return True, f"removed link matching {peer_label_or_id!r}"


def find(self_aoe_id: str, label_or_id: str) -> Optional[dict]:
    """Find a linked peer by label or aoe_id prefix. Returns dict or None."""
    for L in load(self_aoe_id):
        if L["label"] == label_or_id or L["aoe_id"].startswith(label_or_id):
            return L
    return None


def _save(self_aoe_id: str, links: list[dict]) -> None:
    p = bus.links_path(self_aoe_id)
    p.write_text(json.dumps(links, indent=2) + "\n")
