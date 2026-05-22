# aoe-bus

Peer-to-peer messaging for Claude Code sessions running under Agent of Empires.

## What it does

Lets a session say "I'm working with `slovenia-crl`, ping me when you have something" and have the two agents actually talk to each other — via `aoe send` — instead of forcing the human to copy-paste between panes.

Designed around three primitives:

- **link** — durable peer relationship a session remembers across turns
- **message** — a single XML-tagged note delivered into another pane via `aoe send`
- **escalate** — pull the human in when peers can't resolve, surfaced in one human-inbox

## Safety rails (built in, not optional)

- Hard cap on outbound messages per hour per session (default 20)
- Loop detection — refuse to send a near-duplicate of a recently-sent message
- Round cap per thread — 3 rounds, then the next move MUST be `/escalate`
- All slash commands have `--dry` mode for previewing without firing
- A global `AOE_BUS=off` kill switch

## File layout (runtime, at `~/.aoe-bus/`)

```
sessions/<aoe-id>/links.json     # per-session: who I'm linked to
sessions/<aoe-id>/inbox.md       # unread peer-msgs awaiting injection
threads/<thread-id>.jsonl        # full conversation log per thread
human-inbox.md                   # all escalations, one place
audit.log                        # everything that happened
```

## Slash commands

| Command | Purpose |
|---|---|
| `/link <target>` | Remember target as a peer for this session |
| `/unlink <target>` | Forget |
| `/links` | Show current links |
| `/ask <target\|@all-links> <message>` | Open a thread, send a peer message |
| `/reply <thread> <message>` | Continue a thread |
| `/escalate <thread\|freeform> <reason>` | Surface to human-inbox |
| `/bus-pause` / `/bus-resume` | Kill switch |

## peer-msg format

```xml
<peer-msg from="<label>" thread="<id>" type="ask|reply|fyi|escalate-cc" at="<iso>">
  <body of the message>
</peer-msg>
```

## Status

v0 — minimal viable peer messaging. Build log in commits.

## License

MIT (this is generic infra, not financialreports IP).
