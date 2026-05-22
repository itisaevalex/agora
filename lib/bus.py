"""
agora core library.

Stdlib-only. All I/O is explicit (no module-level side effects). Designed to be
imported by slash-command scripts and the UserPromptSubmit hook.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


BUS_ROOT = Path(os.environ.get("AGORA_ROOT", Path.home() / ".agora"))
AOE_CONFIG = Path.home() / ".config" / "agent-of-empires"
SESSIONS_JSON = AOE_CONFIG / "profiles" / "default" / "sessions.json"

DEFAULT_OUTBOUND_BUDGET_PER_HOUR = 20
DEFAULT_ROUND_CAP = 3
DUP_WINDOW_SECS = 600  # 10 min — refuse near-dupes inside this window


# ---------- Identity ----------

@dataclass(frozen=True)
class SessionIdentity:
    aoe_id: str          # full uuid-ish, e.g. 52413c4ad9b54092
    label: str           # human-facing title, e.g. "AOE Admin"


def detect_self() -> Optional[SessionIdentity]:
    """Return who-am-I or None if we're not running inside an aoe session."""
    aoe_id = os.environ.get("AOE_INSTANCE_ID")
    if not aoe_id:
        return None
    label = _lookup_label(aoe_id) or aoe_id[:12]
    return SessionIdentity(aoe_id=aoe_id, label=label)


def lookup_session_by_label(label: str) -> Optional[SessionIdentity]:
    """Resolve a human label to a session identity using aoe's sessions.json."""
    if not SESSIONS_JSON.exists():
        return None
    try:
        data = json.loads(SESSIONS_JSON.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    for entry in data if isinstance(data, list) else data.get("sessions", []):
        if entry.get("title") == label:
            return SessionIdentity(aoe_id=entry["id"], label=label)
    return None


def list_sessions() -> list[SessionIdentity]:
    """All known aoe sessions in the default profile."""
    if not SESSIONS_JSON.exists():
        return []
    try:
        data = json.loads(SESSIONS_JSON.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    entries = data if isinstance(data, list) else data.get("sessions", [])
    out = []
    for e in entries:
        if "id" in e and "title" in e:
            out.append(SessionIdentity(aoe_id=e["id"], label=e["title"]))
    return out


def _lookup_label(aoe_id: str) -> Optional[str]:
    for s in list_sessions():
        if s.aoe_id == aoe_id or s.aoe_id.startswith(aoe_id):
            return s.label
    return None


# ---------- Storage paths ----------

def ensure_bus_root() -> None:
    """Idempotent. Creates ~/.agora/ and standard subdirs."""
    (BUS_ROOT / "sessions").mkdir(parents=True, exist_ok=True)
    (BUS_ROOT / "threads").mkdir(parents=True, exist_ok=True)
    audit = BUS_ROOT / "audit.log"
    audit.touch(exist_ok=True)
    human = BUS_ROOT / "human-inbox.md"
    if not human.exists():
        human.write_text("# Human inbox — agora escalations\n\n")


def session_dir(aoe_id: str) -> Path:
    p = BUS_ROOT / "sessions" / aoe_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def inbox_path(aoe_id: str) -> Path:
    return session_dir(aoe_id) / "inbox.md"


def links_path(aoe_id: str) -> Path:
    return session_dir(aoe_id) / "links.json"


def thread_path(thread_id: str) -> Path:
    return BUS_ROOT / "threads" / f"{thread_id}.jsonl"


def audit_log_path() -> Path:
    return BUS_ROOT / "audit.log"


def human_inbox_path() -> Path:
    return BUS_ROOT / "human-inbox.md"


# ---------- Audit ----------

def audit(event: str, **fields) -> None:
    """Append one structured line to audit.log. Best-effort, never raises."""
    try:
        ensure_bus_root()
        entry = {"ts": now_iso(), "event": event, **fields}
        with audit_log_path().open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


# ---------- Time + IDs ----------

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_thread_id() -> str:
    return "t_" + hashlib.sha256(f"{time.time_ns()}".encode()).hexdigest()[:8]


def msg_hash(target_id: str, msg_type: str, body: str) -> str:
    """Stable hash for loop-detection.

    Normalization: lowercase, collapse all internal whitespace runs to a single
    space, strip leading/trailing. Catches the common 'agent reworded the same
    point' near-duplicates that would otherwise slip past exact-match detection.
    """
    norm = " ".join(body.lower().split())
    return hashlib.sha256(f"{target_id}|{msg_type}|{norm}".encode()).hexdigest()[:16]


# ---------- Kill switch ----------

def bus_enabled() -> bool:
    if os.environ.get("AGORA", "").lower() in ("off", "0", "false"):
        return False
    flag = BUS_ROOT / ".paused"
    return not flag.exists()


def pause_bus() -> None:
    ensure_bus_root()
    (BUS_ROOT / ".paused").touch()


def resume_bus() -> None:
    flag = BUS_ROOT / ".paused"
    if flag.exists():
        flag.unlink()


# ---------- aoe send (the actual cross-pane primitive) ----------

def aoe_send(target_aoe_id: str, text: str, dry_run: bool = False) -> tuple[bool, str]:
    """Pipe text into another aoe session's pane. Returns (ok, output_or_error)."""
    if dry_run:
        return True, f"[DRY] would send to {target_aoe_id}:\n{text}"
    if not bus_enabled():
        return False, "bus is paused (AGORA=off or ~/.agora/.paused exists)"
    try:
        proc = subprocess.run(
            ["aoe", "send", target_aoe_id, text],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0:
            return False, proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
        return True, proc.stdout.strip()
    except FileNotFoundError:
        return False, "aoe binary not found on PATH"
    except subprocess.TimeoutExpired:
        return False, "aoe send timed out after 10s"
    except Exception as e:
        return False, f"aoe send failed: {e}"


# Empirical tmux send-keys limit on Linux: ~3 KB delivers reliably, ~4 KB is the
# pty buffer cap, beyond that bytes are silently dropped or tmux errors with
# "command too long". For peer-msgs over this size, we send a small nudge instead
# and rely on the receiver's UserPromptSubmit hook to inject the full body from
# inbox.md (which has no size limit).
TMUX_SAFE_SIZE_BYTES = 3000


def _find_tmux_session_for(aoe_id: str) -> Optional[str]:
    """Return the tmux session name for an aoe-id, or None if not found."""
    try:
        proc = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True, text=True, timeout=3,
        )
        if proc.returncode != 0:
            return None
        prefix = aoe_id[:8]
        for sess in proc.stdout.splitlines():
            if sess.endswith(prefix) or f"_{prefix}" in sess:
                return sess
        return None
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        return None


def pane_is_attached(aoe_id: str) -> bool:
    """Return True if a tmux client is currently attached to the target pane.

    Used to avoid typing peer-msgs into a pane the user is actively watching/
    interacting with (which would appear mid-input as if magically inserted).
    """
    try:
        proc = subprocess.run(
            ["tmux", "list-clients", "-F", "#{session_name}"],
            capture_output=True, text=True, timeout=3,
        )
        if proc.returncode != 0:
            return False
        prefix = aoe_id[:8]
        for sess in proc.stdout.splitlines():
            if sess.endswith(prefix) or f"_{prefix}" in sess:
                return True
        return False
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        return False


def input_is_empty(aoe_id: str) -> bool:
    """Return True if the target pane's input box appears to be empty.

    Captures the last few lines of pane content; finds the prompt marker
    line (lines starting with `❯`) and checks if there's any draft text
    after it. If empty → user is idle (or claude is mid-thinking, no
    input box rendered), safe to inject. If non-empty → user is drafting,
    do not disturb.
    """
    try:
        tmux_session = _find_tmux_session_for(aoe_id)
        if not tmux_session:
            return False
        proc = subprocess.run(
            ["tmux", "capture-pane", "-p", "-t", tmux_session, "-S", "-5"],
            capture_output=True, text=True, timeout=3,
        )
        if proc.returncode != 0:
            return False
        # Strip ANSI escape codes
        import re
        clean = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", proc.stdout)
        # Find the most recent ❯-prefixed line
        for line in reversed(clean.splitlines()):
            stripped = line.strip()
            if stripped.startswith("❯"):
                # Drop the ❯ and whitespace, see what's left
                rest = stripped.lstrip("❯").strip()
                # An empty input shows just `❯ ` (optionally with a cursor block)
                # Common cursor glyphs in claude's TUI
                return rest in ("", "█", "▓", "▁", "_")
        # No ❯ line found in the last 5 lines — could mean claude is
        # mid-thinking (no input rendered). Treat as idle.
        return True
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        return False


def aoe_send_peer_msg(target_aoe_id: str, sender_label: str, thread: str,
                      wire_text: str, dry_run: bool = False) -> tuple[bool, str]:
    """Deliver a peer-msg via the right path for the receiver's state.

    Three delivery modes:

    1. SILENT (user attached to target pane): no tmux send at all. Body sits
       in inbox.md; the receiver's UserPromptSubmit hook injects it on the
       next prompt the human types. Prevents the 'a message just appeared
       in my input box from nowhere' surprise.

    2. NUDGE (large body, > TMUX_SAFE_SIZE_BYTES, target unattached): send a
       small notification via tmux so the agent wakes up, full body via hook
       from inbox.md.

    3. FULL (small body, target unattached): dump the full peer-msg into the
       target pane immediately. Agent responds autonomously without needing
       a human to trigger the hook.
    """
    attached = pane_is_attached(target_aoe_id)
    drafting = attached and not input_is_empty(target_aoe_id)

    if dry_run:
        if drafting:
            return True, f"[DRY] SILENT — user drafting in {target_aoe_id[:12]}, hook will deliver on next submit"
        if len(wire_text) >= TMUX_SAFE_SIZE_BYTES:
            return True, f"[DRY] NUDGE — large body ({len(wire_text)} bytes), tmux nudge + hook"
        if attached:
            return True, f"[DRY] FULL (attached but input empty) — agent in {target_aoe_id[:12]} will respond"
        return True, f"[DRY] FULL — unattached pane, full peer-msg into pane"

    # SILENT path: only when user is actively drafting in the receiving pane.
    # If attached but idle (just watching), fall through to FULL so agent-to-
    # agent autonomy still works.
    if drafting:
        audit("peer_msg.silent",
              target=target_aoe_id, reason="user drafting",
              body_bytes=len(wire_text))
        return True, "silent (user drafting, body in inbox.md for hook delivery)"

    # NUDGE path: tiny notice, full body via hook
    if len(wire_text) >= TMUX_SAFE_SIZE_BYTES:
        nudge = (
            f"📨 agora peer-msg from {sender_label} on thread {thread} "
            f"({len(wire_text)} bytes) — your UserPromptSubmit hook will "
            f"inject the full body from inbox.md on your next prompt."
        )
        audit("peer_msg.nudge",
              target=target_aoe_id, reason="large body",
              body_bytes=len(wire_text))
        return aoe_send(target_aoe_id, nudge)

    # FULL path: agent will see it as a pasted prompt and respond
    return aoe_send(target_aoe_id, wire_text)
