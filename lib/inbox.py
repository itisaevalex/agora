"""Per-session inbox.md — peer-msgs awaiting injection by the hook."""
from __future__ import annotations

from . import bus
from .peer_msg import PeerMsg


def append_to(self_aoe_id: str, msg: PeerMsg) -> None:
    """Append a peer-msg to the target's inbox.md so their hook can pick it up."""
    p = bus.inbox_path(self_aoe_id)
    with p.open("a") as f:
        f.write(msg.to_wire() + "\n\n")


def read_and_clear(self_aoe_id: str) -> str:
    """Return all pending inbox content for self, then truncate to empty.

    Used by the UserPromptSubmit hook: read once, inject, never inject again.
    """
    p = bus.inbox_path(self_aoe_id)
    if not p.exists():
        return ""
    content = p.read_text()
    if not content.strip():
        return ""
    # Archive to inbox-archive.md before clearing
    archive = bus.session_dir(self_aoe_id) / "inbox-archive.md"
    with archive.open("a") as f:
        f.write(content)
        f.write("\n---\n\n")
    p.write_text("")
    return content


def peek(self_aoe_id: str) -> str:
    """Read inbox WITHOUT clearing — for /agora-inbox debug command."""
    p = bus.inbox_path(self_aoe_id)
    if not p.exists():
        return ""
    return p.read_text()
