---
description: Pull the human into a thread or surface a freeform concern
argument-hint: <thread-id-or-freeform-tag> <reason>
allowed-tools: Bash(agora:*)
---

The user (or you — when stuck on a peer thread) wants to pull the human into a decision.

**PARSE `$ARGUMENTS`:**
- `REF` = first token (thread id like `t_xxxxxxxx` or any freeform tag)
- `REASON` = everything else (any chars allowed)

**INVOKE the Bash tool with a heredoc:**

```bash
agora escalate --body-stdin "<REF>" <<'AGORA_END_OF_MSG'
<REASON>
AGORA_END_OF_MSG
```

This will:
1. Append a block to `~/.agora/human-inbox.md`
2. Fire a sticky desktop notification (`urgency=critical`)
3. If a thread is referenced, CC linked peers with `type="escalate-cc"`

After escalating, **stop acting on the thread**. Wait for the human's reply in your next prompt.
