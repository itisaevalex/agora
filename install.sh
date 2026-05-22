#!/usr/bin/env bash
# aoe-bus installer. Idempotent. Safe to re-run.
#
# What it does:
#   1. Drops `aoe-bus` binary wrapper into ~/.local/bin/ (with REPO baked in)
#   2. Symlinks commands/*.md into ~/.claude/commands/ so they're discoverable
#      as /bus-* slash commands
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

echo "aoe-bus installer"
echo "  repo:        $REPO"
echo "  binary:      $BIN_DIR/aoe-bus"
echo "  commands ->: $CLAUDE_CMD_DIR/bus-*.md"
echo ""

# ---- 1. Binary wrapper ----
mkdir -p "$BIN_DIR"
# Template has __AOE_BUS_REPO_PLACEHOLDER__ — replace at install time
sed "s|__AOE_BUS_REPO_PLACEHOLDER__|$REPO|g" "$REPO/bin/aoe-bus" > "$BIN_DIR/aoe-bus"
chmod +x "$BIN_DIR/aoe-bus"
echo "✓ installed: $BIN_DIR/aoe-bus"

# Check PATH
if ! command -v aoe-bus >/dev/null 2>&1; then
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

# ---- 4. Optional: systemd user unit for passive watchdog ----
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
SYSTEMD_UNIT="$SYSTEMD_USER_DIR/aoe-bus-watchdog.service"
if command -v systemctl >/dev/null 2>&1; then
    mkdir -p "$SYSTEMD_USER_DIR"
    sed "s|__AOE_BUS_REPO_PLACEHOLDER__|$REPO|g" \
        "$REPO/systemd/aoe-bus-watchdog.service" > "$SYSTEMD_UNIT"
    echo "✓ installed: $SYSTEMD_UNIT"
    echo ""
    echo "─── ACTIVATE THE PASSIVE WATCHDOG ──────────────────────────────────"
    echo "To enable auto-nudge for rate-limited stuck sessions (recommended):"
    echo ""
    echo "  # Make sure user systemd has access to DBus/DISPLAY for notify-send:"
    echo "  systemctl --user import-environment DISPLAY DBUS_SESSION_BUS_ADDRESS XDG_RUNTIME_DIR"
    echo ""
    echo "  # Enable + start:"
    echo "  systemctl --user daemon-reload"
    echo "  systemctl --user enable --now aoe-bus-watchdog"
    echo ""
    echo "  # Check status / tail logs:"
    echo "  systemctl --user status aoe-bus-watchdog"
    echo "  journalctl --user -u aoe-bus-watchdog -f"
    echo ""
    echo "  # Disable later:"
    echo "  systemctl --user disable --now aoe-bus-watchdog"
    echo ""
else
    echo "(systemctl not found — passive watchdog setup skipped; run manually: aoe-bus watchdog)"
fi

# ---- 5. Self-test ----
echo "─── SELF-TEST ──────────────────────────────────────────────────────"
if [[ -z "${AOE_INSTANCE_ID:-}" ]]; then
    echo "⚠ not inside an aoe session (no AOE_INSTANCE_ID) — skipping live test."
    echo "  Run /bus-whoami from inside any aoe session to verify."
else
    echo "Running in aoe session $AOE_INSTANCE_ID"
    echo ""
    echo "$ aoe-bus whoami"
    "$BIN_DIR/aoe-bus" whoami
    echo ""
    echo "$ aoe-bus links"
    "$BIN_DIR/aoe-bus" links
    echo ""
    echo "✓ install complete and verified"
fi

echo ""
echo "────────────────────────────────────────────────────────────────────"
echo "Available slash commands (after registering the hook):"
echo "  /bus-whoami     — show this session's identity"
echo "  /bus-link <peer>    — add a peer link"
echo "  /bus-unlink <peer>  — remove a peer link"
echo "  /bus-links          — list current links"
echo "  /bus-ask <peer> <msg>     — open a thread, send"
echo "  /bus-reply <thread> <msg> — continue a thread"
echo "  /bus-escalate <ref> <why> — pull human in"
echo ""
echo "Operator tab: tail -f ~/.aoe-bus/human-inbox.md"
