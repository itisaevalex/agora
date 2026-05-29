---
description: List dead claude sessions or currently-live necromancies
---

Inspect the graveyard — every claude session jsonl indexed under
`~/.claude/projects/*/`. Newest-first by default. Use `--live` to see which
necromancies are currently raised and how long they've been idle.

Usage:

```
agora graveyard              # all dead sessions, newest first
agora graveyard --limit 0    # show all (default caps at 20)
agora graveyard --live       # only currently-resurrected sessions
```

Live necromancies are auto-released after 5 minutes idle. Use
`agora release <uuid-prefix>` to release one manually before its TTL.
