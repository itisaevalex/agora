---
description: Start the aoe-bus watchdog (auto-nudges rate-limited stuck sessions)
allowed-tools: Bash(aoe-bus:*)
---

Start the watchdog loop. It polls every aoe session, detects stall patterns (Anthropic 5xx/overloaded/rate-limit), waits a cooldown, then sends "continue" to nudge them back to life. Runs until Ctrl+C.

Run:

!`aoe-bus watchdog`

For a single pass (good for testing): `aoe-bus watchdog --once --verbose`
For dry-run (logs decisions without firing nudges): add `--dry`.
