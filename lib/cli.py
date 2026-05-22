"""
agora CLI. Invoked by slash commands and the UserPromptSubmit hook.

Subcommands:
  link <peer>           — remember peer (by label, or aoe-id prefix)
  unlink <peer>         — forget
  links                 — show current links
  ask <target> <msg>    — open a thread, send a peer-msg via aoe send
  reply <thread> <msg>  — continue a thread
  escalate <ref> <why>  — push to human-inbox; fires notify-send
  status                — roll-up of bus activity across all sessions
  pause             — global kill switch on
  resume            — global kill switch off
  whoami                — print this session's identity (debug)

All sub-sends take --dry to preview without firing aoe send / writing audit.
"""
from __future__ import annotations

import argparse
import time
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib import bus, escalate, inbox, links, peer_msg, safety, status, threads  # noqa: E402



def _require_self() -> bus.SessionIdentity:
    me = bus.detect_self()
    if me is None:
        print("error: not running inside an aoe session (AOE_INSTANCE_ID not set)",
              file=sys.stderr)
        sys.exit(2)
    return me


def cmd_whoami(args: argparse.Namespace) -> int:
    me = _require_self()
    print(f"label:   {me.label}")
    print(f"aoe-id:  {me.aoe_id}")
    print(f"bus:     {'enabled' if bus.bus_enabled() else 'PAUSED'}")
    print(f"root:    {bus.BUS_ROOT}")
    return 0


def cmd_link(args: argparse.Namespace) -> int:
    me = _require_self()
    target = " ".join(args.target).strip()
    if not target:
        print("usage: link <peer-label-or-aoe-id-prefix>", file=sys.stderr)
        return 2

    # Resolve target — try exact label first, then aoe-id prefix
    found = bus.lookup_session_by_label(target)
    if found is None:
        for s in bus.list_sessions():
            if s.aoe_id.startswith(target):
                found = s
                break
    if found is None:
        print(f"error: no aoe session matching {target!r}", file=sys.stderr)
        print("available labels:", file=sys.stderr)
        for s in bus.list_sessions():
            print(f"  - {s.label}", file=sys.stderr)
        return 1

    added, msg = links.add(me.aoe_id, found.aoe_id, found.label)
    icon = "✓" if added else "·"
    print(f"{icon} {msg}")
    return 0


def cmd_unlink(args: argparse.Namespace) -> int:
    me = _require_self()
    target = " ".join(args.target).strip()
    removed, msg = links.remove(me.aoe_id, target)
    icon = "✓" if removed else "·"
    print(f"{icon} {msg}")
    return 0 if removed else 1


def cmd_links(args: argparse.Namespace) -> int:
    me = _require_self()
    current = links.load(me.aoe_id)
    if not current:
        print(f"({me.label}) no peer links yet — use /link <peer-label> to add one")
        return 0
    print(f"({me.label}) links:")
    for L in current:
        print(f"  → {L['label']:40s} [{L['aoe_id'][:12]}]  added {L['added_at']}")
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    me = _require_self()
    body = " ".join(args.body).strip()
    if not body:
        print("error: empty message body", file=sys.stderr)
        return 2

    # Resolve target: must be one of self's links (safety — refuse to msg strangers)
    peer = links.find(me.aoe_id, args.target)
    if peer is None:
        print(f"error: {args.target!r} is not linked. Use /agora-link first.",
              file=sys.stderr)
        return 1

    thread_id = bus.new_thread_id()
    msg = peer_msg.PeerMsg(
        sender_label=me.label,
        thread=thread_id,
        msg_type="ask",
        body=body,
        at=bus.now_iso(),
    )

    # Safety rails — budget + loop detection (round cap doesn't apply to a new ask).
    # Run BEFORE the dry-run short-circuit so previews show blocks.
    allowed, reason = safety.check_send(
        me.aoe_id, peer["aoe_id"], "ask", body, thread_id=None,
    )
    if not allowed:
        print(f"BLOCKED: {reason}", file=sys.stderr)
        return 3

    if args.dry:
        print(f"[DRY] would create thread {thread_id} and send to {peer['label']}:")
        print(msg.to_wire())
        return 0

    if not bus.bus_enabled():
        print("error: bus is paused. Use /agora-resume to enable.", file=sys.stderr)
        return 1

    # Persist before sending so we have a record even if aoe send fails
    threads.create_thread(thread_id, [me.aoe_id, peer["aoe_id"]])
    threads.append_msg(thread_id, msg, me.aoe_id)
    inbox.append_to(peer["aoe_id"], msg)

    ok, output = bus.aoe_send(peer["aoe_id"], msg.to_wire())
    if not ok:
        bus.audit("ask.send_failed", thread=thread_id, peer=peer["aoe_id"], error=output)
        print(f"warning: thread {thread_id} created and peer's inbox updated, "
              f"but aoe send failed: {output}", file=sys.stderr)
        print(f"the peer will see this on their next prompt-submit via the hook.")
        return 0  # not a hard failure — inbox path still works

    bus.audit("ask.sent", thread=thread_id, peer=peer["aoe_id"], peer_label=peer["label"])
    print(f"✓ sent ask to {peer['label']} on thread {thread_id}")
    print(f"  body: {body[:120]}{'...' if len(body) > 120 else ''}")
    return 0


def cmd_reply(args: argparse.Namespace) -> int:
    me = _require_self()
    body = " ".join(args.body).strip()
    if not body:
        print("error: empty reply body", file=sys.stderr)
        return 2

    data = threads.read_thread(args.thread)
    if data is None:
        print(f"error: no thread {args.thread!r} found", file=sys.stderr)
        return 1

    participants = data["header"]["participants"]
    if me.aoe_id not in participants:
        print(f"error: {me.label!r} is not a participant of thread {args.thread}",
              file=sys.stderr)
        return 1

    # Counterparty = the other participant(s)
    others = [p for p in participants if p != me.aoe_id]
    if not others:
        print("error: thread has no counterparty", file=sys.stderr)
        return 1
    other_id = others[0]
    # Look up label for display
    other_label = other_id[:12]
    for s in bus.list_sessions():
        if s.aoe_id == other_id:
            other_label = s.label
            break

    msg = peer_msg.PeerMsg(
        sender_label=me.label,
        thread=args.thread,
        msg_type="reply",
        body=body,
        at=bus.now_iso(),
    )

    # Safety rails — budget + loop + round cap. Before dry-short-circuit.
    allowed, reason = safety.check_send(
        me.aoe_id, other_id, "reply", body, thread_id=args.thread,
    )
    if not allowed:
        print(f"BLOCKED: {reason}", file=sys.stderr)
        return 3

    if args.dry:
        print(f"[DRY] would send reply on thread {args.thread} to {other_label}:")
        print(msg.to_wire())
        return 0

    if not bus.bus_enabled():
        print("error: bus is paused", file=sys.stderr)
        return 1

    threads.append_msg(args.thread, msg, me.aoe_id)
    inbox.append_to(other_id, msg)
    ok, output = bus.aoe_send(other_id, msg.to_wire())
    if not ok:
        bus.audit("reply.send_failed", thread=args.thread, error=output)
        print(f"warning: aoe send failed: {output} (inbox still updated)",
              file=sys.stderr)
        return 0

    bus.audit("reply.sent", thread=args.thread, peer=other_id)
    print(f"✓ sent reply on thread {args.thread} to {other_label}")
    return 0


def cmd_escalate(args: argparse.Namespace) -> int:
    me = _require_self()
    reason = " ".join(args.reason).strip()
    if not reason:
        print("error: empty reason", file=sys.stderr)
        return 2

    # If ref looks like a thread id, treat it as such; otherwise it's freeform
    thread_id = args.ref if args.ref.startswith("t_") else None

    block = escalate.write(me.label, args.ref, reason, thread_id=thread_id)

    notified = escalate.fire_desktop_notification(me.label, reason)

    # Also CC linked peers with type=escalate-cc so they know human is involved
    if thread_id and bus.bus_enabled():
        data = threads.read_thread(thread_id)
        if data is not None:
            cc_msg = peer_msg.PeerMsg(
                sender_label=me.label, thread=thread_id, msg_type="escalate-cc",
                body=f"Escalated to human. Reason: {reason}",
                at=bus.now_iso(),
            )
            threads.append_msg(thread_id, cc_msg, me.aoe_id)
            for p in data["header"]["participants"]:
                if p != me.aoe_id:
                    inbox.append_to(p, cc_msg)
                    bus.aoe_send(p, cc_msg.to_wire())

    print(f"✓ escalated — appended to {bus.human_inbox_path()}")
    if notified:
        print("✓ desktop notification fired (notify-send)")
    print(f"  AOE Admin tab can: tail -f {bus.human_inbox_path()}")
    return 0


def cmd_hook_inject(args: argparse.Namespace) -> int:
    """Called by the UserPromptSubmit hook. Atomically reads+clears self inbox.

    Prints the formatted context block to stdout if there's anything new.
    Silent (no output, exit 0) when inbox is empty or we're not in a session.
    """
    me = bus.detect_self()
    if me is None:
        return 0
    if not bus.bus_enabled():
        return 0

    content = inbox.read_and_clear(me.aoe_id)
    if not content.strip():
        return 0

    # Wrap with a clear separator so the agent knows these are peer messages,
    # not user input. The peer-msg tags inside remain parseable by lib.peer_msg.parse.
    current_links = links.load(me.aoe_id)
    link_summary = ""
    if current_links:
        names = ", ".join(L["label"] for L in current_links)
        link_summary = f"\nYour current peer links: {names}\n"

    print("=" * 72)
    print(f"AOE-BUS INBOX — {len(content.strip().splitlines())} lines of peer-msgs")
    print("These were sent into this session via `aoe send`. They are NOT from the")
    print("user. To respond, use `/agora-reply <thread-id> <your reply>`. To pull the")
    print("human in if stuck, use `/agora-escalate <thread-id> <reason>`.")
    print(link_summary)
    print("=" * 72)
    print(content.strip())
    print("=" * 72)
    bus.audit("hook.injected", self_id=me.aoe_id, bytes=len(content))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Print a roll-up of bus state across all sessions.

    With --watch, re-prints every N seconds (default 2) using ANSI clear.
    """
    interval = args.watch
    compact = args.compact

    if interval <= 0:
        rows = status.collect()
        print(status.render(rows, compact=compact), end="")
        return 0

    # Watch mode — clear+repaint loop until Ctrl+C
    import time
    try:
        while True:
            rows = status.collect()
            # ANSI clear screen + cursor home
            print("\033[2J\033[H", end="")
            from datetime import datetime
            print(f"agora status · {datetime.now().strftime('%H:%M:%S')} · refresh {interval}s · Ctrl+C to exit")
            print()
            print(status.render(rows, compact=compact), end="")
            time.sleep(interval)
    except KeyboardInterrupt:
        print()  # graceful newline
        return 0


def cmd_pause(args: argparse.Namespace) -> int:
    bus.pause_bus()
    print("✓ bus paused — no outbound messages until /agora-resume")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    bus.resume_bus()
    print("✓ bus resumed")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agora")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("whoami").set_defaults(func=cmd_whoami)

    p_link = sub.add_parser("link")
    p_link.add_argument("target", nargs="+")
    p_link.set_defaults(func=cmd_link)

    p_unlink = sub.add_parser("unlink")
    p_unlink.add_argument("target", nargs="+")
    p_unlink.set_defaults(func=cmd_unlink)

    sub.add_parser("links").set_defaults(func=cmd_links)

    p_ask = sub.add_parser("ask")
    p_ask.add_argument("target")
    p_ask.add_argument("body", nargs="+")
    p_ask.add_argument("--dry", action="store_true")
    p_ask.set_defaults(func=cmd_ask)

    p_reply = sub.add_parser("reply")
    p_reply.add_argument("thread")
    p_reply.add_argument("body", nargs="+")
    p_reply.add_argument("--dry", action="store_true")
    p_reply.set_defaults(func=cmd_reply)

    p_esc = sub.add_parser("escalate")
    p_esc.add_argument("ref")
    p_esc.add_argument("reason", nargs="+")
    p_esc.set_defaults(func=cmd_escalate)

    sub.add_parser("pause").set_defaults(func=cmd_pause)
    sub.add_parser("resume").set_defaults(func=cmd_resume)
    sub.add_parser("hook-inject").set_defaults(func=cmd_hook_inject)

    p_status = sub.add_parser("status",
                              help="roll-up of bus activity across all sessions")
    p_status.add_argument("--watch", type=int, default=0, metavar="SECS",
                          help="repaint every N seconds (default 0 = one-shot)")
    p_status.add_argument("--compact", action="store_true",
                          help="one-line-per-session, no detail block")
    p_status.set_defaults(func=cmd_status)


    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
