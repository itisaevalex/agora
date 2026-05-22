---
description: Unlink this session from a peer
argument-hint: <peer-label-or-aoe-id-prefix>
allowed-tools: Bash(aoe-bus:*)
---

The user wants to remove a peer link from this session.

!`aoe-bus unlink "$ARGUMENTS"`

Report the result. If `· no link matching`, mention the user can run `/bus-links` to see what's currently linked.
