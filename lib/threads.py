"""Thread management — jsonl-backed conversation logs."""
from __future__ import annotations

import json
from typing import Optional

from . import bus
from .peer_msg import PeerMsg


def create_thread(thread_id: str, participants: list[str]) -> None:
    """Write the header line."""
    bus.ensure_bus_root()
    p = bus.thread_path(thread_id)
    if p.exists():
        return  # idempotent
    header = {
        "type": "header",
        "thread_id": thread_id,
        "participants": sorted(set(participants)),
        "created_at": bus.now_iso(),
    }
    with p.open("w") as f:
        f.write(json.dumps(header) + "\n")


def append_msg(thread_id: str, msg: PeerMsg, from_aoe_id: str) -> None:
    """Append a message line to a thread."""
    p = bus.thread_path(thread_id)
    entry = {
        "type": "msg",
        "from_label": msg.sender_label,
        "from_id": from_aoe_id,
        "msg_type": msg.msg_type,
        "body": msg.body,
        "at": msg.at,
    }
    with p.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def read_thread(thread_id: str) -> Optional[dict]:
    """Return {header, msgs} or None if thread doesn't exist."""
    p = bus.thread_path(thread_id)
    if not p.exists():
        return None
    header = None
    msgs = []
    for line in p.read_text().splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") == "header":
            header = obj
        elif obj.get("type") == "msg":
            msgs.append(obj)
    if header is None:
        return None
    return {"header": header, "msgs": msgs}


def count_rounds(thread_id: str) -> int:
    """A 'round' = one ask/reply exchange. Counted as msgs // 2 (rounded up)."""
    data = read_thread(thread_id)
    if data is None:
        return 0
    return (len(data["msgs"]) + 1) // 2


def recent_outbound_for(self_aoe_id: str, since_secs: int = 600) -> list[dict]:
    """Scan all threads, return msgs sent BY self_aoe_id within window."""
    import time
    from datetime import datetime, timezone

    cutoff = time.time() - since_secs
    out = []
    threads_dir = bus.BUS_ROOT / "threads"
    if not threads_dir.exists():
        return out
    for f in threads_dir.glob("*.jsonl"):
        for line in f.read_text().splitlines():
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "msg":
                continue
            if obj.get("from_id") != self_aoe_id:
                continue
            try:
                ts = datetime.strptime(obj["at"], "%Y-%m-%dT%H:%M:%SZ")
                ts = ts.replace(tzinfo=timezone.utc).timestamp()
            except (ValueError, KeyError):
                continue
            if ts >= cutoff:
                out.append({**obj, "thread_id": f.stem})
    return out
