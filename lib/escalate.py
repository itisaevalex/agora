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
    """Best-effort `notify-send`. Returns True only if notify-send exited 0.

    Uses urgency=critical so the notification persists until manually dismissed
    (urgency=normal auto-dismisses in ~5s and was easy to miss).

    Emits a terminal bell (BEL char to /dev/tty) as a fallback signal if a
    controlling tty is available — useful when the operator is looking at the
    aoe TUI rather than the desktop notification corner.
    """
    bin_ = shutil.which("notify-send")
    if not bin_:
        bus.audit("notify.skipped", reason="notify-send not on PATH")
        _terminal_bell()
        return False

    try:
        proc = subprocess.run(
            [bin_, "-u", "critical", "-i", "dialog-warning",
             "-a", "agora",
             f"agora: {self_label} escalating",
             reason[:300]],
            timeout=3, check=False,
            capture_output=True, text=True,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        bus.audit("notify.failed", error=str(e))
        _terminal_bell()
        return False

    if proc.returncode != 0:
        bus.audit("notify.failed",
                  exit_code=proc.returncode,
                  stderr=(proc.stderr or "").strip()[:200])
        _terminal_bell()
        return False

    bus.audit("notify.sent", label=self_label)
    _terminal_bell()
    return True


def _terminal_bell() -> None:
    """Ring the terminal bell on the controlling tty (best-effort)."""
    try:
        with open("/dev/tty", "w") as f:
            f.write("\a")
            f.flush()
    except OSError:
        pass
