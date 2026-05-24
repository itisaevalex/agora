"""Spawn — create a new aoe session as a child of the current session.

Wraps `aoe add` + lineage registration + ancestor auto-linking so a parent
session (and all its ancestors) can directly /agora-ask the new child.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from typing import Optional

from . import bus, inbox, lineage, links, peer_msg as pm

DEFAULT_SPAWN_BUDGET = 10  # children per parent per hour


def _env_int(name: str, fallback: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return fallback
    try:
        n = int(raw)
        return n if n > 0 else fallback
    except ValueError:
        return fallback


def spawn(
    parent: bus.SessionIdentity,
    title: str,
    initial_task: str,
    project_path: Optional[str] = None,
    dry_run: bool = False,
) -> tuple[bool, str, Optional[str]]:
    """Create a child aoe session. Returns (ok, message, new_aoe_id).

    Steps:
      1. Budget check (parent has spawn quota left)
      2. `aoe add --cmd claude --yolo --launch -t <title>`
      3. Register lineage (parent + spawned_at)
      4. Bidirectional agora link parent ↔ child
      5. Also bidi-link child with all of parent's ancestors (so grandparent
         can /agora-ask grandchild directly)
      6. Drop initial_task into child via aoe send (becomes first prompt)
    """
    budget = _env_int("AGORA_SPAWN_BUDGET", DEFAULT_SPAWN_BUDGET)
    recent = lineage.count_recent_children(parent.aoe_id, since_secs=3600)
    if recent >= budget:
        return False, (f"spawn budget exhausted ({recent}/{budget} children "
                       f"in last hour). Adjust AGORA_SPAWN_BUDGET to override."), None

    # Validate title — must be shell-safe (aoe add takes it via subprocess argv,
    # but we restrict further to dodge any tmux/aoe quirks). Reject any chars
    # not in the allowed set; this also blocks injection like 'foo; rm -rf'.
    if not re.match(r"^[\w\-. ]+$", title):
        return False, f"invalid title {title!r}: alphanumerics, dashes, dots, underscores, spaces only", None

    project_path = project_path or os.getcwd()

    if dry_run:
        return True, (
            f"[DRY] would: aoe add -t {title!r} {project_path}, "
            f"register lineage under parent {parent.label}, "
            f"link bidirectionally with parent + {len(lineage.ancestors(parent.aoe_id))} ancestor(s), "
            f"drop initial task ({len(initial_task)} chars)"
        ), None

    if not bus.bus_enabled():
        return False, "bus is paused", None

    # 1. Create the aoe session
    try:
        proc = subprocess.run(
            ["aoe", "add", "--cmd", "claude", "--yolo", "--launch",
             "-t", title,
             "--extra-args", "--dangerously-skip-permissions",
             project_path],
            capture_output=True, text=True, timeout=20,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return False, f"aoe add failed: {e}", None

    if proc.returncode != 0:
        return False, f"aoe add returned {proc.returncode}: {(proc.stderr or proc.stdout)[:200]}", None

    # 2. Extract the new aoe_id from output ("ID:    <hex>")
    m = re.search(r"ID:\s+([0-9a-f]{12,})", proc.stdout)
    if not m:
        return False, f"could not parse aoe-id from output:\n{proc.stdout[:300]}", None
    child_id = m.group(1)

    # 3. Register lineage
    lineage.register(child_id, title, parent_id=parent.aoe_id, task=initial_task)

    # 4. Bidirectional link parent ↔ child
    links.add(parent.aoe_id, child_id, title)
    links.add(child_id, parent.aoe_id, parent.label)

    # 5. Link child with each ancestor (transitive talk-ability)
    for anc_id in lineage.ancestors(parent.aoe_id):
        anc_label = bus._lookup_label(anc_id) or anc_id[:12]
        links.add(anc_id, child_id, title)
        links.add(child_id, anc_id, anc_label)

    # 6. Wait briefly for the new claude pane to be ready, then deliver the
    #    initial task through the SAME machinery /agora-ask uses:
    #      - persist a PeerMsg to the child's inbox.md (durable; survives
    #        tmux send-keys truncation)
    #      - route via bus.aoe_send_peer_msg which picks NUDGE for large
    #        bodies, FULL for small bodies, SILENT for attached-and-drafting
    #
    #    Bug history: this used to be a raw `aoe send <child_id> <task>` via
    #    subprocess. tmux send-keys silently truncates / drops bodies past
    #    ~3-4 KB while still returning exit 0, producing a false-positive
    #    spawn.ok with an empty child pane. Routing through aoe_send_peer_msg
    #    closes that gap.
    time.sleep(2)
    thread_id = f"t_spawn_{child_id[:8]}"
    msg = pm.PeerMsg(
        sender_label=parent.label,
        thread=thread_id,
        msg_type="ask",
        body=initial_task,
        at=bus.now_iso(),
    )
    inbox.append_to(child_id, msg)

    ok, output = bus.aoe_send_peer_msg(child_id, parent.label, thread_id, msg.to_wire())
    if not ok:
        # The full body is in inbox.md and the durable lineage/<id>/task.md
        # copy is on disk — the failure is purely the nudge/full tmux send.
        # Receiver will discover backlog on next prompt-submit via the hook
        # OR via the piggyback notice on the next successful send.
        bus.audit("spawn.send_failed", child_id=child_id, error=output[:200])
        return True, (
            f"spawned {title} as {child_id[:12]} (lineage OK, task in "
            f"inbox.md) but tmux nudge failed: {output[:120]}"
        ), child_id

    bus.audit("spawn.ok", child_id=child_id, title=title,
              parent=parent.aoe_id, ancestors=len(lineage.ancestors(parent.aoe_id)),
              task_bytes=len(initial_task))
    return True, (
        f"spawned {title} as {child_id[:12]} · linked to parent {parent.label} "
        f"+ {len(lineage.ancestors(parent.aoe_id))} ancestor(s) · "
        f"task ({len(initial_task)} bytes) delivered"
    ), child_id
