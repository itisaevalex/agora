---
description: Continue a peer thread (reply to an ask or earlier reply)
argument-hint: <thread-id> <your reply>
allowed-tools: Bash(agora:*)
---

The user is continuing a peer thread.

**PARSE `$ARGUMENTS`:**
- `THREAD` = first whitespace-separated token (looks like `t_xxxxxxxx`)
- `BODY` = everything else

**INVOKE the Bash tool with a heredoc so the body bypasses bash + argparse special chars:**

```bash
agora reply --body-stdin "<THREAD>" <<'AGORA_END_OF_MSG'
<BODY>
AGORA_END_OF_MSG
```

If the body literally contains `AGORA_END_OF_MSG`, pick a different delimiter.

**Errors:**
- "no thread" — wrong thread id; check `/agora-status` for the right one
- "not a participant" — you're trying to reply to someone else's thread
- exit 3 (BLOCKED): round cap hit → next move MUST be `/agora-escalate`, not another reply

Do not exceed the round cap. If you and the peer disagree, `/agora-escalate <thread> <reason>` to pull the human in.
