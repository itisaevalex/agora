# aoe-bus

Peer-to-peer messaging for Claude Code sessions running under [Agent of Empires](https://github.com/...).

Lets a session say *"I'm working with `slovenia-crl`, ping me when you have something"* and have the two agents actually talk to each other — via `aoe send` — instead of forcing the human to copy-paste between panes.

## Quick install

```bash
cd ~/Documents/Programming\ Projects/ClaudeCoding/aoe-bus
./install.sh
```

The installer:
- Drops `aoe-bus` into `~/.local/bin/`
- Symlinks 7 slash commands into `~/.claude/commands/`
- Prints (but does NOT apply) the `settings.json` snippet to register the UserPromptSubmit hook — you merge it yourself

After registering the hook, restart Claude Code (or open a fresh session) and you'll have:

| Command | Purpose |
|---|---|
| `/bus-whoami` | Identity debug — label, aoe-id, bus state |
| `/bus-link <peer>` | Remember a peer for this session |
| `/bus-unlink <peer>` | Forget |
| `/bus-links` | Show current links |
| `/bus-ask <peer> <msg>` | Open a thread, send peer-msg |
| `/bus-reply <thread> <msg>` | Continue a thread |
| `/bus-escalate <ref> <why>` | Pull human in, surface to human-inbox.md |

## Architecture in three primitives

| Primitive | What it is | Lives in |
|---|---|---|
| **Link** | Durable peer relationship a session remembers across turns | `~/.aoe-bus/sessions/<id>/links.json` |
| **Message** | XML-tagged note delivered into another pane via `aoe send` | `~/.aoe-bus/threads/<id>.jsonl` |
| **Escalate** | Surface to one human-inbox + fire desktop notification | `~/.aoe-bus/human-inbox.md` |

## peer-msg wire format

```xml
<peer-msg from="<label>" thread="t_xxxxxxxx" type="ask" at="2026-05-22T10:00:00Z">
The message body, free-form. Can be multi-line.
</peer-msg>
```

`type` is one of: `ask`, `reply`, `fyi`, `escalate-cc`, `done`. The receiving session's `UserPromptSubmit` hook wraps incoming `<peer-msg>` blocks with a clear separator so the agent knows they're from peers, not the human.

## Safety rails (built in, not optional)

| Rail | Default | What it stops |
|---|---|---|
| **Outbound budget** | 20 msgs/hour per session | Quota runaway from chatty agents |
| **Loop detection** | 10-min window, normalized hash | Same-point reworded resends |
| **Round cap** | 3 rounds/thread | Endless ping-pong without consensus |
| **Kill switch** | `AOE_BUS=off` env OR `~/.aoe-bus/.paused` | Emergency halt |
| **Self-link refused** | Always | You can't link a session to itself |
| **Stranger refused** | Always | `/bus-ask` requires the target be a current `/bus-link` |

When a safety check fires, the CLI exits 3 and tells the agent (and you) which rail and why. Loop-detect and round-cap hints explicitly suggest `/bus-escalate`.

## File layout (runtime)

```
~/.aoe-bus/
├── sessions/<aoe-id>/
│   ├── links.json           # who am I linked to
│   ├── inbox.md             # pending peer-msgs (cleared by hook each turn)
│   └── inbox-archive.md     # everything ever delivered, for forensics
├── threads/
│   └── t_xxxxxxxx.jsonl     # header + msg lines per thread
├── human-inbox.md           # all escalations across all sessions
├── audit.log                # structured jsonl event log
└── .paused                  # presence = bus is paused
```

## How to use it (from inside a session)

1. `/bus-link "EU scrapers Audit"` — remember that session
2. `/bus-ask "EU scrapers Audit" "should we apply log-and-skip to AT_OEKB?"` — sends an `ask`. CLI prints the new thread id.
3. Wait. The peer's `UserPromptSubmit` hook will inject the `<peer-msg>` into their next prompt. They respond with `/bus-reply <thread> <answer>`.
4. Their reply arrives in YOUR inbox; the hook injects it on your next prompt.
5. If you and the peer can't reach consensus after 3 rounds, the round cap forces `/bus-escalate <thread> <reason>` — appends to `~/.aoe-bus/human-inbox.md` + fires `notify-send`.

## Operator workflow

Keep one tab tailing the human-inbox:

```bash
tail -f ~/.aoe-bus/human-inbox.md
```

That's where every escalation across all sessions lands, in time order, with the last 3 thread messages for context. You don't have to remember which session needs you — they tell you.

## Status

v0 — minimum viable peer messaging with safety rails. 60 unit tests, all green.

## License

MIT — generic agent infra, no project IP entangled.
