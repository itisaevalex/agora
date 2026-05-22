"""Escalation — push to human-inbox.md + desktop notification."""
from __future__ import annotations

import shutil
import subprocess
from typing import Optional

from . import bus, threads


def write(self_label: str, ref: str, reason: str, thread_id: Optional[str] = None) -> str:
    """Append an escalation entry to human-inbox.md. Returns the formatted block."""
    bus.ensure_bus_root()
    p = bus.human_inbox_path()

    block_lines = [
        f"## {bus.now_iso()} — {self_label} → human",
        f"**Ref:** {ref}",
        f"**Reason:** {reason}",
    ]

    # If this is a thread escalation, include last 3 msgs for context
    if thread_id:
        data = threads.read_thread(thread_id)
        if data is not None and data["msgs"]:
            block_lines.append(f"**Thread:** `{thread_id}` ({len(data['msgs'])} msgs)")
            block_lines.append("**Last exchanges:**")
            for m in data["msgs"][-3:]:
                body_preview = m["body"].replace("\n", " ").strip()
                if len(body_preview) > 200:
                    body_preview = body_preview[:200] + "..."
                block_lines.append(
                    f"  - **{m['from_label']}** [{m['msg_type']}] {body_preview}"
                )

    block = "\n".join(block_lines) + "\n\n---\n\n"

    with p.open("a") as f:
        f.write(block)

    bus.audit("escalate", self_label=self_label, ref=ref, thread=thread_id)
    return block


def fire_desktop_notification(self_label: str, reason: str) -> bool:
    """Best-effort `notify-send`. Returns True if dispatched."""
    if not shutil.which("notify-send"):
        return False
    try:
        subprocess.run(
            ["notify-send", "-u", "normal", "-i", "dialog-information",
             f"aoe-bus: {self_label} escalating",
             reason[:300]],
            timeout=3, check=False,
        )
        return True
    except (subprocess.TimeoutExpired, OSError):
        return False
