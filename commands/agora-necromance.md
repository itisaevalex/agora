---
description: Raise a dead claude session to consult its accumulated knowledge
---

Raise a dormant `<uuid>.jsonl` session under the dedicated `graveyard` AoE
profile (kept out of your main dashboard, exempt from lazarus). The
resurrected session loads its full transcript on `claude-opus-4-8 --effort
xhigh` by default and is asked your question via the standard agora
peer-msg machinery. It writes back through `/agora-reply`, then is culled
after 5 minutes idle.

Read-only by convention — the necromancy preamble forbids file modifications
and side-effecting commands. Soft guarantee; calibrated for advice, not
unilateral action.

Usage:

```
agora necromance <label-or-uuid> "<question>"
agora necromance <label-or-uuid> --summary "<question>"   # cheaper, lower fidelity
agora necromance <label-or-uuid> --body-stdin <<< "<question>"
```

If you don't know which dead session has the knowledge you need, dig first:

```
agora grave-dig "<content query>"
```

To manually release a live necromancy before its TTL:

```
agora release <uuid-prefix>
```

To inspect the graveyard:

```
agora graveyard              # list dead sessions (newest first)
agora graveyard --live       # list currently-resurrected sessions
```

When you raise a session, the response arrives in your inbox on a fresh
thread (`t_necro_<uuid8>_<unix>`). Treat that thread as a one-shot consult;
follow-up questions within 5 minutes reuse the live resurrection and refresh
its idle timer.
