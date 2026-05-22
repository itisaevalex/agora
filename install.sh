#!/usr/bin/env bash
# agora installer. Idempotent. Safe to re-run.
#
# What it does:
#   1. Drops `agora` binary wrapper into ~/.local/bin/ (with REPO baked in)
#   2. Symlinks commands/*.md into ~/.claude/commands/ so they're discoverable
#      as /agora-* slash commands
#   3. Prints the settings.json snippet for registering the UserPromptSubmit hook
#      (does NOT auto-edit settings.json — that's your call)
#   4. Runs a self-test that exercises link/links/unlink/whoami
#
# What it does NOT do:
#   - Send any real messages to other aoe sessions
#   - Modify ~/.claude/settings.json (you do that manually)
#   - Touch the financialreports repo or any other project

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
CLAUDE_CMD_DIR="$HOME/.claude/commands"

echo "agora installer"
echo "  repo:        $REPO"
echo "  binary:      $BIN_DIR/agora"
echo "  commands ->: $CLAUDE_CMD_DIR/agora-*.md"
echo ""

# ---- 1. Binary wrapper ----
mkdir -p "$BIN_DIR"
# Template has __AGORA_REPO_PLACEHOLDER__ — replace at install time
sed "s|__AGORA_REPO_PLACEHOLDER__|$REPO|g" "$REPO/bin/agora" > "$BIN_DIR/agora"
chmod +x "$BIN_DIR/agora"
echo "✓ installed: $BIN_DIR/agora"

# Check PATH
if ! command -v agora >/dev/null 2>&1; then
    echo "  ⚠ $BIN_DIR is not on PATH. Add to your shell rc:"
    echo "      export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

# ---- 2. Slash commands ----
mkdir -p "$CLAUDE_CMD_DIR"
for cmd in "$REPO/commands/"*.md; do
    name="$(basename "$cmd")"
    target="$CLAUDE_CMD_DIR/$name"
    # Use symlink so future repo edits propagate
    ln -sf "$cmd" "$target"
    echo "✓ symlinked: $target"
done

# ---- 3. Hook registration instructions (do not auto-edit settings.json) ----
echo ""
echo "─── NEXT: register the UserPromptSubmit hook ───────────────────────"
echo "Add this to ~/.claude/settings.json under 'hooks' (or merge if hooks block exists):"
echo ""
cat <<EOF
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": ".*",
        "hooks": [
          { "type": "command", "command": "$REPO/hooks/user-prompt-submit.sh" }
        ]
      }
    ]
  }
EOF
echo ""
echo "(I did NOT edit settings.json automatically — review and merge yourself.)"
echo ""

# (watchdog daemon lives in the sibling project 'lazarus')

# ---- 4. Self-test ----
echo "─── SELF-TEST ──────────────────────────────────────────────────────"
if [[ -z "${AOE_INSTANCE_ID:-}" ]]; then
    echo "⚠ not inside an aoe session (no AOE_INSTANCE_ID) — skipping live test."
    echo "  Run /agora-whoami from inside any aoe session to verify."
else
    echo "Running in aoe session $AOE_INSTANCE_ID"
    echo ""
    echo "$ agora whoami"
    "$BIN_DIR/agora" whoami
    echo ""
    echo "$ agora links"
    "$BIN_DIR/agora" links
    echo ""
    echo "✓ install complete and verified"
fi

echo ""
echo "────────────────────────────────────────────────────────────────────"
echo "Available slash commands (after registering the hook):"
echo "  /agora-whoami     — show this session's identity"
echo "  /agora-link <peer>    — add a peer link"
echo "  /agora-unlink <peer>  — remove a peer link"
echo "  /agora-links          — list current links"
echo "  /agora-ask <peer> <msg>     — open a thread, send"
echo "  /agora-reply <thread> <msg> — continue a thread"
echo "  /agora-escalate <ref> <why> — pull human in"
echo ""
echo "Operator tab: tail -f ~/.agora/human-inbox.md"
