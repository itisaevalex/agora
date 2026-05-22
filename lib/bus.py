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
