---
description: Manually release a live necromancy before its TTL expires
---

Stop and remove a currently-resurrected session immediately, instead of
waiting for the 5-minute idle TTL. Identified by UUID prefix (unambiguous
match required — first 8 chars is plenty).

Usage:

```
agora release <uuid-prefix>     # e.g. agora release 67d19f77
```

To see which necromancies are live:

```
agora graveyard --live
```
