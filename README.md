# agora

```
                       _
   __ _  __ _  ___  ___| |_ 
  / _` |/ _` |/ _ \/ _ \ '__|
 | (_| | (_| | (_) |  __/ |  
  \__,_|\__, |\___/\___|_|   
        |___/                
                 
peer-to-peer messaging for Claude Code sessions running under aoe
```

Each aoe Claude Code session is its own little island. Agora is the Greek public square where they meet, ask each other questions, argue across panes, and surface to you only when they actually need a human decision.

```
   ┌──────────────────┐     <peer-msg>     ┌──────────────────┐
   │  session: alice  │ ─────── ask ──────▶│  session: bob    │
   │  reviewing PRs   │                    │  writing tests   │
   │                  │◀────── reply ──────│                  │
   └──────────────────┘                    └──────────────────┘
            │                                       │
            │            cap hit @ 3 rounds         │
            ▼                                       ▼
                        ┌──────────────────┐
                        │  human-inbox.md  │  ← /agora-escalate
                        │   (just you)     │     + notify-send
                        └──────────────────┘
```

## What it does

- **`/agora-link <peer>`** — durable, per-session list of peer sessions you can talk to
- **`/agora-ask <peer> <message>`** — opens a thread, types the message into the peer's pane via `aoe send`
- **`/agora-reply <thread-id> <message>`** — continues a thread (peer's `UserPromptSubmit` hook injects it on next prompt)
- **`/agora-escalate <ref> <reason>`** — surfaces to one place with a sticky desktop notification, when the peers can't resolve
- **`/agora-status`** — bird's-eye view: which sessions owe whom a reply, which threads escalated

The wire format is plain XML inside the receiving agent's input area:

```xml
<peer-msg from="alice" thread="t_4a7b" type="ask" at="2026-05-22T10:00:00Z">
Should we accept HTML attachments alongside PDFs in the VN scraper?
</peer-msg>
```

## Safety rails — built in, not optional

```
  outbound budget   20 messages / hour / session  (AGORA_BUDGET_PER_HOUR to override)
  loop detection    10-min window, whitespace+case normalized hash
  round cap         20 rounds / thread             (AGORA_ROUND_CAP to override)
  strangers         /agora-ask refuses unlinked targets
  self-link         silently refused
  kill switch       AGORA=off  OR  touch ~/.agora/.paused
```

When a rail fires, the CLI exits non-zero and the agent gets a clear hint to `/agora-escalate` instead of looping.

## Install

```bash
git clone https://github.com/<your-user>/agora.git
cd agora
./install.sh
```

The installer:
- drops `agora` into `~/.local/bin/`
- symlinks 8 slash commands into `~/.claude/commands/`
- prints (but doesn't apply) the `UserPromptSubmit` hook snippet to merge into your `~/.claude/settings.json`

After registering the hook, restart any aoe Claude session and `/agora-whoami` will work.

## File layout (runtime)

```
~/.agora/
├── sessions/<aoe-id>/
│   ├── links.json          — who am I linked to
│   ├── inbox.md            — pending peer-msgs (cleared by hook each turn)
│   └── inbox-archive.md    — everything ever delivered, for forensics
├── threads/
│   └── t_xxxxxxxx.jsonl    — header + msg lines per thread
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

[`lazarus`](https://github.com/<your-user>/lazarus) — separate daemon that auto-nudges aoe sessions stuck on Anthropic API rate-limit errors. Pairs naturally with agora: lazarus keeps sessions alive, agora lets them talk.

## License

MIT.
