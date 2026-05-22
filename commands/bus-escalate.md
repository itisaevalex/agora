---
description: Pull the human into a thread or surface a freeform concern
argument-hint: <thread-id-or-freeform-tag> <reason for escalation>
allowed-tools: Bash(aoe-bus:*)
---

The user (or you — when stuck on a peer thread) wants to pull the human into a decision. First arg is either a thread id (`t_xxxxxxxx`) or a freeform reference tag (anything else). Rest is the reason.

Run:

!`aoe-bus escalate $ARGUMENTS`

This will:
1. Append a block to `~/.aoe-bus/human-inbox.md` (the operator's tab usually tails this)
2. Fire a `notify-send` desktop popup
3. If a thread is referenced, CC linked peers with `type="escalate-cc"` so they know human is now involved

After escalating, **stop acting on the thread**. Wait for the human's reply in your next prompt — do not auto-continue.
