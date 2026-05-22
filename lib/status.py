"""Bus-state status — read-only roll-up of every session's bus activity."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import bus, links


def collect() -> list[dict[str, Any]]:
    """Walk all aoe sessions; return per-session bus state.

    For each session: label, aoe_id, link count, inbox-unread bytes, open threads
    (waiting-on-self, waiting-on-peer), recent escalations.
    """
    bus.ensure_bus_root()
    sessions = bus.list_sessions()
    threads_dir = bus.BUS_ROOT / "threads"

    # Index threads by participant for fast lookup
    by_participant: dict[str, list[dict]] = {}
    if threads_dir.exists():
        for f in sorted(threads_dir.glob("*.jsonl")):
            entries = []
            for line in f.read_text().splitlines():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            if not entries or entries[0].get("type") != "header":
                continue
            header = entries[0]
            msgs = [e for e in entries[1:] if e.get("type") == "msg"]
            for p in header.get("participants", []):
                by_participant.setdefault(p, []).append({
                    "thread_id": f.stem,
                    "header": header,
                    "msgs": msgs,
                })

    out = []
    for s in sessions:
        inbox_path = bus.inbox_path(s.aoe_id)
        inbox_bytes = inbox_path.stat().st_size if inbox_path.exists() else 0

        my_links = links.load(s.aoe_id)
        my_threads = by_participant.get(s.aoe_id, [])

        waiting_for_reply = []   # I sent the last msg → I'm waiting on peer
        waiting_on_me = []       # peer sent the last msg → I owe a reply
        escalated = []           # thread has an escalate-cc msg

        for t in my_threads:
            if not t["msgs"]:
                continue
            last = t["msgs"][-1]
            last_from = last.get("from_id")
            had_escalate = any(m.get("msg_type") == "escalate-cc" for m in t["msgs"])
            entry = {
                "thread": t["thread_id"],
                "rounds": (len(t["msgs"]) + 1) // 2,
                "last_msg_type": last.get("msg_type"),
                "last_msg_at": last.get("at"),
                "last_msg_from": last.get("from_label"),
            }
            if had_escalate:
                escalated.append(entry)
            elif last_from == s.aoe_id:
                waiting_for_reply.append(entry)
            else:
                waiting_on_me.append(entry)

        out.append({
            "label": s.label,
            "aoe_id": s.aoe_id,
            "short_id": s.aoe_id[:12],
            "link_count": len(my_links),
            "links": [L["label"] for L in my_links],
            "inbox_bytes": inbox_bytes,
            "waiting_on_me": waiting_on_me,
            "waiting_for_reply": waiting_for_reply,
            "escalated": escalated,
        })

    return out


def render(rows: list[dict[str, Any]], compact: bool = False) -> str:
    """Pretty-print the status roll-up. Compact mode is one-line-per-session."""
    if not rows:
        return "(no aoe sessions discovered)\n"

    lines = []
    header = (
        f"{'SESSION':<26s} {'LINKS':<6s} {'INBOX':<7s} "
        f"{'YOU OWE':<8s} {'AWAITING':<9s} {'ESCALATED':<10s}"
    )
    sep = "─" * len(header)
    lines.append(header)
    lines.append(sep)

    bell_total = 0
    for r in rows:
        owe = len(r["waiting_on_me"])
        await_ = len(r["waiting_for_reply"])
        esc = len(r["escalated"])
        inbox = "—" if r["inbox_bytes"] == 0 else f"{r['inbox_bytes']}B"
        label = r["label"]
        if len(label) > 25:
            label = label[:22] + "…"
        # Visual cue column — 🔔 if needs attention
        attention = (owe > 0 or esc > 0 or r["inbox_bytes"] > 0)
        if attention:
            bell_total += 1
            prefix = "● "
        else:
            prefix = "  "
        lines.append(
            f"{prefix}{label:<24s} {r['link_count']:<6d} {inbox:<7s} "
            f"{owe:<8d} {await_:<9d} {esc:<10d}"
        )

    if not compact:
        # Detail section for sessions that need attention
        attention_rows = [r for r in rows if r["waiting_on_me"] or r["escalated"] or r["inbox_bytes"]]
        if attention_rows:
            lines.append("")
            lines.append("DETAIL — sessions needing attention:")
            lines.append(sep)
            for r in attention_rows:
                lines.append(f"● {r['label']}")
                for w in r["waiting_on_me"]:
                    lines.append(
                        f"    YOU OWE REPLY  thread={w['thread']} "
                        f"from {w['last_msg_from']!r} ({w['last_msg_type']}, "
                        f"{w['rounds']} rounds, at {w['last_msg_at']})"
                    )
                for e in r["escalated"]:
                    lines.append(
                        f"    ESCALATED      thread={e['thread']} "
                        f"({e['rounds']} rounds, last by {e['last_msg_from']!r})"
                    )
                if r["inbox_bytes"]:
                    lines.append(
                        f"    INBOX UNREAD   {r['inbox_bytes']}B pending injection on next prompt"
                    )

    lines.append("")
    lines.append(
        f"summary: {bell_total}/{len(rows)} sessions need attention · "
        f"bus={'enabled' if bus.bus_enabled() else 'PAUSED'} · "
        f"human-inbox={bus.human_inbox_path()}"
    )
    return "\n".join(lines) + "\n"
