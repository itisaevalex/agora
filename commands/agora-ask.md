---
description: Open a thread and send a peer-msg to a linked aoe session
argument-hint: <peer-label> <your message>
allowed-tools: Bash(agora:*)
---

The user wants to send a question or proposal to a linked peer aoe session. Parse `$ARGUMENTS` to extract the target label (first token, or quoted-string) and the message body (rest).

Run:

!`agora ask $ARGUMENTS`

Notes:
- The target MUST already be in the user's `/agora-links` — if not, the CLI will error and the user should `/agora-link <target>` first.
- The CLI assigns a fresh thread id (`t_<8hex>`) and reports it. Memorize this id — if the peer replies, you'll see a `<peer-msg>` block in your next prompt with the same `thread=...` attr.
- If the user just wants to preview without actually sending, suggest they add `--dry` at the end.

After the CLI runs, report the thread id and one-line summary of what was sent. Do NOT predict what the peer will say — they'll reply async on their own clock.
