---
description: Open a thread and send a peer-msg to a linked aoe session
argument-hint: <peer-label> <your message>
allowed-tools: Bash(agora:*)
---

The user wants to send a peer-msg via agora.

**PARSE `$ARGUMENTS`:**
- `TARGET` = first whitespace-separated token (peer label or aoe-id prefix)
- `BODY` = everything else (may contain parens, dashes, backticks, `--flags`, anything)

**INVOKE the Bash tool** with a heredoc form so the body bypasses bash word-splitting and argparse flag-parsing. This is critical — without it, parens crash bash and any `--word` mid-body trips argparse:

```bash
agora ask --body-stdin "<TARGET>" <<'AGORA_END_OF_MSG'
<BODY>
AGORA_END_OF_MSG
```

If the body literally contains the string `AGORA_END_OF_MSG`, pick a different delimiter (e.g. `AGORA_BODY_X9`).

**After the CLI runs:**
- exit 0: report the thread id printed by the CLI (`t_xxxxxxxx`)
- exit 1 + "is not linked": the target isn't in `/agora-links` — tell the user to `/agora-link <target>` first
- exit 3 (BLOCKED): a safety rail fired — relay the reason; usually means `/agora-escalate` is the next move

Do NOT predict what the peer will say — they reply async on their own clock.
