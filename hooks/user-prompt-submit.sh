#!/usr/bin/env bash
# agora UserPromptSubmit hook.
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

# Resolve AOE_INSTANCE_ID. Claude Code may strip env when forking hooks, so
# fall back to deriving it from the tmux session name (format:
# aoe_<title>_<aoe-id-prefix-8>). Look up the full ID from aoe's sessions.json.
if [[ -z "${AOE_INSTANCE_ID:-}" && -n "${TMUX:-}" ]]; then
    SESSION_NAME=$(tmux display-message -p '#S' 2>/dev/null)
    if [[ "$SESSION_NAME" =~ _([a-f0-9]{8})$ ]]; then
        PREFIX="${BASH_REMATCH[1]}"
        AOE_INSTANCE_ID=$(python3 -c "
import json, sys
try:
    data = json.load(open('$HOME/.config/agent-of-empires/profiles/default/sessions.json'))
    for e in data:
        if e.get('id','').startswith('$PREFIX'):
            print(e['id']); break
except: pass
" 2>/dev/null)
        export AOE_INSTANCE_ID
    fi
fi

# Still no AOE_INSTANCE_ID? Silent no-op.
[[ -z "${AOE_INSTANCE_ID:-}" ]] && exit 0

# Locate the bus binary
BIN=""
for candidate in "$HOME/.local/bin/agora" "$(command -v agora 2>/dev/null)"; do
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
