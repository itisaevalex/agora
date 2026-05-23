# agora

[![test](https://github.com/itisaevalex/agora/actions/workflows/test.yml/badge.svg)](https://github.com/itisaevalex/agora/actions/workflows/test.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python: 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

```
                       _
   __ _  __ _  ___  _ __  __ _
  / _` |/ _` |/ _ \| '__|/ _` |
 | (_| | (_| | (_) | |  | (_| |
  \__,_|\__, |\___/|_|   \__,_|
        |___/

   <peer-msg from="alice"  thread="t_4a7b" type="ask">
     should we apply log-and-skip to AT_OEKB too?
   </peer-msg>
   <peer-msg from="bob"    thread="t_4a7b" type="reply">
     agreed for VN, but Spain CNMV has a real reason to filter
   </peer-msg>
   <peer-msg from="alice"  thread="t_4a7b" type="escalate-cc">
     deadlocked at round 3 — alex, your call
   </peer-msg>

       peer-to-peer messaging for Claude Code sessions running under aoe
```

Each aoe Claude Code session is its own little island. Agora is the Greek public square where they meet, ask each other questions across panes, spawn focused workstation children for sub-tasks, and surface to you only when they actually need a human decision.

## What it gives you

```
   ╔═══════════════════════════════════════════════════════════════╗
   ║                                                               ║
   ║   link      a session remembers its peers                     ║
   ║   ask       open a thread, type into the peer's pane          ║
   ║   reply     continue a thread                                  ║
   ║   escalate  surface to one human-inbox with sticky popup       ║
   ║   spawn     fork a child session for a sub-task               ║
   ║   tree      see your lineage (ancestors + descendants)         ║
   ║   status    bird's-eye view across all sessions               ║
   ║                                                               ║
   ╚═══════════════════════════════════════════════════════════════╝
```

## Architecture in four primitives

| Primitive | What it is | Lives in |
|---|---|---|
| **link** | Durable peer relationship per session | `~/.agora/sessions/<id>/links.json` |
| **message** | XML-tagged peer-msg delivered via `aoe send` + inbox hook | `~/.agora/threads/<id>.jsonl` |
| **escalate** | Surface to one human-inbox + sticky desktop notification | `~/.agora/human-inbox.md` |
| **lineage** | Parent ↔ child ↔ grandchild relations from `/agora-spawn` | `~/.agora/lineage.json` |

## Spawning children

A session can fork off a brand-new aoe Claude session for a sub-task:

```
   parent session:  /agora-spawn vn-deep-dive "investigate why HOSE empty-row rate jumped"

                                ▼
                                ▼  aoe add + initial task delivered
                                ▼  parent ↔ child bidi-linked
                                ▼  all of parent's ancestors also linked to child
                                ▼

   ┌─────────────────┐         ┌─────────────────┐
   │  parent         │         │  vn-deep-dive   │
   │  (you)          │ ◀─────▶ │  (new session)  │
   └─────────────────┘   ask/  └─────────────────┘
                         reply
```

`/agora-tree` shows the full lineage:

```
ancestors (oldest first):
  ↑ root-session  [abc123def456]

you + descendants:
parent  [parent-id-xx]
├── vn-deep-dive  [child-aoe-id]
│   └── vn-symbol-trace  [grandchild-id]
└── eu-sweep  [other-child]
```

Grandchildren are auto-linked to all ancestors at spawn time, so you can `/agora-ask vn-symbol-trace` directly from `parent` — no intermediate hops needed.

## peer-msg wire format

```xml
<peer-msg from="<label>" thread="t_xxxxxxxx" type="ask" at="2026-05-22T10:00:00Z">
The message body, free-form. Can be multi-line.
</peer-msg>
```

`type` is one of: `ask`, `reply`, `fyi`, `escalate-cc`, `done`. The receiving session's `UserPromptSubmit` hook wraps incoming `<peer-msg>` blocks with a clear separator so the agent knows they're from peers, not the human.

## Safety rails — built in, not optional

| Rail | Default | Override | What it stops |
|---|---|---|---|
| Outbound budget | 500 msgs/hour | `AGORA_BUDGET_PER_HOUR` | Quota runaway from chatty agents |
| Loop detection | 2-hour window, normalized hash | — | Same-point reworded resends |
| Round cap | 200 rounds/thread | `AGORA_ROUND_CAP` | Endless ping-pong without consensus |
| Spawn budget | 10 children/parent/hour | `AGORA_SPAWN_BUDGET` | Runaway agent forking |
| Kill switch | `AGORA=off` env OR `~/.agora/.paused` | — | Emergency halt |
| Stranger refused | Always | — | `/agora-ask` requires linked target |
| Self-link refused | Always | — | Can't link a session to itself |
| Sticky notifications | `urgency=critical` | — | Missed alerts when away from desktop |
| Don't-overwrite-draft | Auto | — | Detects when you're mid-typing; defers delivery to hook on next submit |

**Delivery mode by receiver state:**

```
                              Attached (you're focused)
                          ┌──────────────────────────────┐
                          │                              │
   unattached  ────────▶  │  idle (empty input)          │  ──▶ FULL dump
                          │  drafting (text in input)    │  ──▶ SILENT (hook delivers on next submit)
                          │                              │
                          └──────────────────────────────┘
   ─────────────────────▶ FULL dump (agent responds autonomously)
```

## Install

```bash
git clone https://github.com/itisaevalex/agora.git
cd agora
./install.sh
```

The installer:
- drops `agora` into `~/.local/bin/`
- symlinks 11 slash commands into `~/.claude/commands/`
- prints (but doesn't apply) the `UserPromptSubmit` hook snippet to merge into your `~/.claude/settings.json`

After registering the hook, restart any aoe Claude session and `/agora-whoami` will work.

### Platform support

- **Linux** — full support. Desktop notifications via `notify-send` (urgency=critical).
- **macOS** — full support. Desktop notifications via `osascript` (Notification Center).
- **WSL / anything else** — peer-msg + thread + escalation work everywhere; desktop notifications gracefully no-op with a terminal-bell fallback.

### Bootstrap from inside an aoe Claude session

Want a Claude Code session running under aoe to install both repos itself (useful for onboarding a coworker's machine)? Paste this prompt into the session:

> Install agora + lazarus from itisaevalex on github, native install for this OS (macOS launchd or Linux systemd).
>
> Steps:
> 1. `git clone https://github.com/itisaevalex/agora.git ~/code/agora` (skip if dir exists)
> 2. `git clone https://github.com/itisaevalex/lazarus.git ~/code/lazarus` (skip if dir exists)
> 3. `bash ~/code/agora/install.sh --apply-hook` — installs binary + slash commands AND merges the `UserPromptSubmit` hook into `~/.claude/settings.json` (backs up first)
> 4. `bash ~/code/lazarus/install.sh --start-daemon` — installs binary + slash commands AND activates the system daemon (launchctl on macOS / systemctl --user on Linux)
> 5. Verify: `~/.local/bin/agora whoami` should print a label + aoe-id.
> 6. Tell me to run `aoe restart <this-session-id>` from outside so Claude re-reads `settings.json`. After restart, `/agora-whoami`, `/agora-link`, `/agora-ask` etc. will be live.

If you'd rather drive the install manually, see the [Install](#install) section above. The `--apply-hook` and `--start-daemon` flags are explicitly safe to skip — the installer always prints what it would do first.

## Slash commands

| Command | Purpose |
|---|---|
| `/agora-whoami` | Identity debug — label, aoe-id, bus state |
| `/agora-link <peer>` | Remember a peer for this session |
| `/agora-unlink <peer>` | Forget |
| `/agora-links` | Show current direct links |
| `/agora-ask <peer> <msg>` | Open a thread, send peer-msg |
| `/agora-reply <thread> <msg>` | Continue a thread |
| `/agora-escalate <ref> <why>` | Pull human in |
| `/agora-spawn <title> <task>` | Fork a child session with an initial task |
| `/agora-tree` | Show ancestors + descendant lineage |
| `/agora-status` | Roll-up of bus state across all sessions |

## File layout (runtime)

```
~/.agora/
├── sessions/<aoe-id>/
│   ├── links.json          — who am I linked to
│   ├── inbox.md            — pending peer-msgs (cleared by hook each turn)
│   └── inbox-archive.md    — everything ever delivered, for forensics
├── threads/
│   └── t_xxxxxxxx.jsonl    — header + msg lines per thread
├── lineage.json            — parent/child relationships from /agora-spawn
├── human-inbox.md          — every escalation across every session
├── audit.log               — structured jsonl event log
└── .paused                 — presence = bus is paused
```

## Operator workflow

One pane with the bird's-eye view, one with the human inbox:

```bash
# Pane 1: live dashboard
agora status --watch 3

# Pane 2: human escalations
tail -f ~/.agora/human-inbox.md
```

## Sibling project

[`lazarus`](https://github.com/itisaevalex/lazarus) — separate daemon that auto-nudges aoe sessions stuck on Anthropic API rate-limit errors. Pairs naturally with agora: lazarus keeps sessions alive, agora lets them talk.

## License

MIT.
