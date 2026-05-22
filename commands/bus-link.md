---
description: Link this aoe session to a peer so they can talk via aoe-bus
argument-hint: <peer-label-or-aoe-id-prefix>
allowed-tools: Bash(aoe-bus:*)
---

The user wants to link this session to a peer aoe session. Run the CLI to add the link, then report what happened.

!`aoe-bus link "$ARGUMENTS"`

If the CLI printed `✓ linked to <label>`, confirm to the user that the link is durable (stored in `~/.aoe-bus/sessions/<self>/links.json`) and that you'll remember it across turns. Mention they can use `/bus-links` to see all current links, or `/bus-unlink <label>` to remove this one.

If it printed `· already linked to <label>`, tell the user it was already linked — no action needed.

If it errored with "no aoe session matching", list the available labels the CLI showed and ask the user which one they meant.
