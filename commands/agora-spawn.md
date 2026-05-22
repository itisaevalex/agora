---
description: Spawn a child aoe Claude session linked to this one with an initial task
argument-hint: <title> <initial task to drop into the new session>
allowed-tools: Bash(agora:*)
---

Create a brand-new aoe Claude Code session as a child of this one. The new session is:

- Auto bidi-linked with this session (you can `/agora-ask <title>` immediately)
- Auto bidi-linked with all of this session's ancestors (grandparent can talk to grandchild directly)
- Registered in the lineage tree (visible via `/agora-tree`)
- Started with the initial task dropped as its first prompt

Useful when:
- You want a focused sub-window for a specific task that you might come back to later
- Different from the in-process Agent tool — this is a separate pane with its own context, its own conversation history, and survives the parent's death

Run:

!`agora spawn $ARGUMENTS`

After spawning, the CLI prints the new aoe-id and reminds you of the immediate `/agora-ask <title> <q>` form. The child takes a few seconds to boot; the initial task is dropped automatically after boot.

Spawn budget defaults to 10 children per parent per hour (`AGORA_SPAWN_BUDGET` to override).
