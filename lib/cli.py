"""
aoe-bus CLI. Invoked by slash commands and the UserPromptSubmit hook.

Subcommands:
  link <peer>           — remember peer (by label, or aoe-id prefix)
  unlink <peer>         — forget
  links                 — show current links
  ask <target> <msg>    — open a thread, send a peer-msg via aoe send
  reply <thread> <msg>  — continue a thread
  escalate <ref> <why>  — push to human-inbox; fires notify-send
  bus-pause             — global kill switch on
  bus-resume            — global kill switch off
  whoami                — print this session's identity (debug)

All subcommands take --dry to preview without firing aoe send / writing audit.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib import bus, inbox, links, peer_msg, threads  # noqa: E402


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
        print(f"error: {args.target!r} is not linked. Use /bus-link first.",
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

    if args.dry:
        print(f"[DRY] would create thread {thread_id} and send to {peer['label']}:")
        print(msg.to_wire())
        return 0

    if not bus.bus_enabled():
        print("error: bus is paused. Use /bus-resume to enable.", file=sys.stderr)
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
    print("escalate: not yet implemented (Task #6)", file=sys.stderr)
    return 2


def cmd_pause(args: argparse.Namespace) -> int:
    bus.pause_bus()
    print("✓ bus paused — no outbound messages until /bus-resume")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    bus.resume_bus()
    print("✓ bus resumed")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aoe-bus")
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

    sub.add_parser("bus-pause").set_defaults(func=cmd_pause)
    sub.add_parser("bus-resume").set_defaults(func=cmd_resume)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
