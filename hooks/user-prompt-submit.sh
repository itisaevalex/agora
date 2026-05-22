#!/usr/bin/env bash
# aoe-bus UserPromptSubmit hook.
#
# Reads stdin (the prompt event JSON from Claude Code) and prints additional
# context to stdout when this session has unread peer-msgs in its inbox.
#
# Silent no-op if:
#   - not running inside an aoe session (no AOE_INSTANCE_ID)
#   - bus is paused
#   - inbox is empty
#
# Exit codes:
#   0  always — failures should not block Claude prompts

set -u

# Drain stdin so Claude Code doesn't block (we don't actually need it)
cat > /dev/null || true

# Only inject if we're inside an aoe session
[[ -z "${AOE_INSTANCE_ID:-}" ]] && exit 0

# Locate the bus binary
BIN=""
for candidate in "$HOME/.local/bin/aoe-bus" "$(command -v aoe-bus 2>/dev/null)"; do
    if [[ -n "$candidate" && -x "$candidate" ]]; then
        BIN="$candidate"
        break
    fi
done
[[ -z "$BIN" ]] && exit 0

# Use the internal --hook-inject subcommand which atomically reads+clears inbox
INJECTED=$("$BIN" hook-inject 2>/dev/null) || exit 0

if [[ -n "$INJECTED" ]]; then
    printf '%s\n' "$INJECTED"
fi
exit 0
