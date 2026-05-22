---
description: Continue a peer thread (reply to an ask or earlier reply)
argument-hint: <thread-id> <your reply>
allowed-tools: Bash(aoe-bus:*)
---

The user is continuing a peer thread. The first arg is the thread id (`t_xxxxxxxx`), rest is the reply body.

If you've just received a `<peer-msg type="ask" thread="t_..."/>` and the user wants you to respond, use this command with the same thread id.

Run:

!`aoe-bus reply $ARGUMENTS`

If the CLI errors with "not a participant", you're trying to reply to a thread you're not in — that's a bug, report it.

If the CLI errors with "no thread", the thread id is wrong — check spelling.

Important: do NOT reply to the same thread more than 3 times. If you and the peer disagree after 3 rounds, the next action MUST be `/bus-escalate <thread> <reason>` to pull the human in.
