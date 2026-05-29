---
description: Search the graveyard for dead sessions matching a content query
---

Search `~/.claude/projects/*/<uuid>.jsonl` for dormant sessions whose first or
last user message — plus extracted keywords — match a query. Returns ranked
candidates with size, recency, hit tokens, and a one-line hook so you can
decide which one to `agora necromance`.

The index is built lazily on first call and reused while jsonls are unchanged.

Usage:

```
agora grave-dig "<content query>"
agora grave-dig --limit 10 "<content query>"
```

Example output:

```
3 candidate(s) for "aoe sandboxed session id anchor":

  1. aoe-pr1572  (67d19f77…)
     score=11.43  hits=aoe,sandboxed,anchor  mtime=20h ago  size=3.3M  turns=847
     ↳ okay closing you now then, njbrake can handle the stuff later.

  2. aoe-bus-build  (0d3c54ab…)
     score=8.21  hits=aoe,session  mtime=7d ago  size=5.4M  turns=1204
     ↳ lets refactor the bus daemon so it persists across…

  → agora necromance aoe-pr1572 "<your question>"
```

Once you've picked the right candidate, raise it with `/agora-necromance`.
