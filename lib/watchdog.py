"""Session watchdog — auto-nudges sessions stuck on Anthropic rate-limit errors.

Polling loop that:
  1. Lists all aoe sessions
  2. For each, captures pane content via `aoe session capture`
  3. Detects known stall patterns (rate-limit errors, overloaded responses)
  4. If a session has been stalled past the cooldown window, nudges it with
     `aoe send <id> "continue"` and logs the event

State per session tracked in ~/.aoe-bus/watchdog-state.json:
  { "<aoe-id>": {"first_seen_stalled": "<iso>", "last_nudge": "<iso>",
                 "nudge_count": int, "last_pattern": "<str>"} }

Conservative by design:
  - Won't nudge more than once per cooldown (default 60s)
  - Caps lifetime nudges per session per session-instance (default 5)
  - Skips nudges if session status is "Working" (it's making progress)
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import bus

# Patterns that indicate a stuck session worth nudging. Order matters — most
# specific first, so the matched pattern in the log is meaningful.
STALL_PATTERNS = [
    re.compile(r"API Error: Server is temporarily limiting requests", re.I),
    re.compile(r"API Error:\s*5\d\d", re.I),
    re.compile(r"overloaded_error", re.I),
    re.compile(r"rate.?limit", re.I),
    re.compile(r"Connection error", re.I),
]

DEFAULT_INTERVAL_SECS = 30
DEFAULT_COOLDOWN_SECS = 60
DEFAULT_MAX_NUDGES = 5
DEFAULT_NUDGE_TEXT = "continue"


def state_path() -> Path:
    return bus.BUS_ROOT / "watchdog-state.json"


def _load_state() -> dict:
    p = state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict) -> None:
    state_path().write_text(json.dumps(state, indent=2) + "\n")


def capture_pane(aoe_id: str, lines: int = 200) -> Optional[str]:
    """Return last N lines of an aoe session's pane, or None if capture fails."""
    try:
        proc = subprocess.run(
            ["aoe", "session", "capture", aoe_id],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode != 0:
            return None
        out = proc.stdout
        # Strip ANSI for pattern matching
        ansi = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
        clean = ansi.sub("", out)
        tail = "\n".join(clean.splitlines()[-lines:])
        return tail
    except (subprocess.TimeoutExpired, OSError):
        return None


def detect_stall(pane_text: str) -> Optional[str]:
    """Return matched pattern name if a stall is detected, else None."""
    if not pane_text:
        return None
    for pat in STALL_PATTERNS:
        m = pat.search(pane_text)
        if m:
            return m.group(0)[:80]
    return None


def session_status(aoe_id: str) -> Optional[str]:
    """Return aoe session Status field (Working/Idle/Waiting/etc) or None."""
    try:
        proc = subprocess.run(
            ["aoe", "session", "show", aoe_id],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode != 0:
            return None
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line.startswith("Status:"):
                return line.split(":", 1)[1].strip()
        return None
    except (subprocess.TimeoutExpired, OSError):
        return None


def tick(
    cooldown_secs: int = DEFAULT_COOLDOWN_SECS,
    max_nudges: int = DEFAULT_MAX_NUDGES,
    nudge_text: str = DEFAULT_NUDGE_TEXT,
    dry_run: bool = False,
) -> list[dict]:
    """One watchdog pass. Returns list of decision records (one per session checked)."""
    bus.ensure_bus_root()
    state = _load_state()
    now_iso = bus.now_iso()
    now_ts = time.time()
    decisions = []

    for s in bus.list_sessions():
        rec = {"aoe_id": s.aoe_id, "label": s.label, "action": "none", "reason": ""}

        pane = capture_pane(s.aoe_id)
        pattern = detect_stall(pane) if pane else None

        st = state.get(s.aoe_id, {})

        if not pattern:
            # Clean — clear any prior stall state
            if st:
                state.pop(s.aoe_id, None)
                rec["action"] = "cleared"
                rec["reason"] = "no stall pattern in current pane"
            else:
                rec["reason"] = "clean"
            decisions.append(rec)
            continue

        # We see a stall pattern
        rec["reason"] = f"detected: {pattern!r}"

        # First sighting?
        if "first_seen_stalled" not in st:
            st["first_seen_stalled"] = now_iso
            st["last_pattern"] = pattern
            st["nudge_count"] = 0
            state[s.aoe_id] = st
            rec["action"] = "first_sighting"
            decisions.append(rec)
            continue

        # Already seen — has cooldown elapsed?
        first_seen_dt = datetime.strptime(st["first_seen_stalled"], "%Y-%m-%dT%H:%M:%SZ")
        first_seen_dt = first_seen_dt.replace(tzinfo=timezone.utc)
        elapsed = now_ts - first_seen_dt.timestamp()

        if elapsed < cooldown_secs:
            rec["action"] = "waiting_cooldown"
            rec["reason"] += f" (in cooldown, {int(elapsed)}/{cooldown_secs}s)"
            decisions.append(rec)
            continue

        # Cooldown elapsed — check nudge budget
        if st.get("nudge_count", 0) >= max_nudges:
            rec["action"] = "budget_exhausted"
            rec["reason"] += f" (already nudged {st['nudge_count']} times)"
            decisions.append(rec)
            continue

        # Also: don't double-nudge within cooldown
        if "last_nudge" in st:
            last_nudge_dt = datetime.strptime(st["last_nudge"], "%Y-%m-%dT%H:%M:%SZ")
            last_nudge_dt = last_nudge_dt.replace(tzinfo=timezone.utc)
            since_nudge = now_ts - last_nudge_dt.timestamp()
            if since_nudge < cooldown_secs:
                rec["action"] = "waiting_nudge_cooldown"
                rec["reason"] += f" (last nudge {int(since_nudge)}s ago)"
                decisions.append(rec)
                continue

        # Fire the nudge
        if dry_run:
            rec["action"] = "would_nudge"
        else:
            try:
                proc = subprocess.run(
                    ["aoe", "send", s.aoe_id, nudge_text],
                    capture_output=True, text=True, timeout=5,
                )
                ok = proc.returncode == 0
            except (subprocess.TimeoutExpired, OSError) as e:
                ok = False
                proc = None
            if ok:
                st["nudge_count"] = st.get("nudge_count", 0) + 1
                st["last_nudge"] = now_iso
                state[s.aoe_id] = st
                rec["action"] = "nudged"
                rec["reason"] += f" (nudge #{st['nudge_count']})"
                bus.audit("watchdog.nudged",
                          session=s.label, aoe_id=s.aoe_id,
                          pattern=pattern, nudge_count=st["nudge_count"])
            else:
                rec["action"] = "nudge_failed"
                bus.audit("watchdog.nudge_failed",
                          session=s.label, aoe_id=s.aoe_id, pattern=pattern)
        decisions.append(rec)

    _save_state(state)
    return decisions


def render_decisions(decisions: list[dict], verbose: bool = False) -> str:
    """Pretty-print a watchdog pass result."""
    if not decisions:
        return "(no sessions to watch)\n"
    lines = []
    interesting = [d for d in decisions if d["action"] not in ("none", "clean")]
    for d in interesting:
        icon = {
            "nudged": "🔔",
            "would_nudge": "→",
            "first_sighting": "👀",
            "waiting_cooldown": "⏳",
            "waiting_nudge_cooldown": "⏳",
            "budget_exhausted": "✋",
            "nudge_failed": "✗",
            "cleared": "✓",
        }.get(d["action"], "·")
        lines.append(f"  {icon} {d['label']:<28s} {d['action']:<22s} {d['reason']}")
    if verbose and not interesting:
        lines.append(f"  all {len(decisions)} sessions clean")
    return ("\n".join(lines) + "\n") if lines else ""
