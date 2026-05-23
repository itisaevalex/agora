#!/usr/bin/env bash
# agora installer. Idempotent. Safe to re-run.
#
# What it does:
#   1. Drops `agora` binary wrapper into ~/.local/bin/ (with REPO baked in)
#   2. Symlinks commands/*.md into ~/.claude/commands/ so they're discoverable
#      as /agora-* slash commands
#   3. Either PRINTS or APPLIES the settings.json snippet for registering the
#      UserPromptSubmit hook — controlled by --apply-hook (default: print only)
#   4. Runs a self-test that exercises link/links/unlink/whoami
#
# Flags:
#   --apply-hook   Merge the UserPromptSubmit hook into ~/.claude/settings.json
#                  (backs up to settings.json.bak-agora-<timestamp> first).
#                  Idempotent: skips if our hook is already registered.

set -euo pipefail

APPLY_HOOK=0
for arg in "$@"; do
    case "$arg" in
        --apply-hook) APPLY_HOOK=1 ;;
        -h|--help)
            sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "unknown flag: $arg" >&2; exit 2 ;;
    esac
done

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
CLAUDE_CMD_DIR="$HOME/.claude/commands"
SETTINGS_JSON="$HOME/.claude/settings.json"

echo "agora installer"
echo "  repo:        $REPO"
echo "  binary:      $BIN_DIR/agora"
echo "  commands ->: $CLAUDE_CMD_DIR/agora-*.md"
echo "  apply hook:  $([ $APPLY_HOOK -eq 1 ] && echo yes || echo no)"
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

# ---- 3. Hook registration (print or apply) ----
HOOK_CMD="$REPO/hooks/user-prompt-submit.sh"

if [[ $APPLY_HOOK -eq 1 ]]; then
    echo "─── APPLYING: merging UserPromptSubmit hook into settings.json ──"
    mkdir -p "$(dirname "$SETTINGS_JSON")"
    BACKUP="$SETTINGS_JSON.bak-agora-$(date +%Y%m%d-%H%M%S)"
    if [[ -f "$SETTINGS_JSON" ]]; then
        cp "$SETTINGS_JSON" "$BACKUP"
        echo "✓ backed up: $BACKUP"
    fi

    python3 - "$SETTINGS_JSON" "$HOOK_CMD" <<'PY'
import json, sys, pathlib

settings_path = pathlib.Path(sys.argv[1])
hook_cmd = sys.argv[2]

data = {}
if settings_path.exists() and settings_path.stat().st_size > 0:
    try:
        data = json.loads(settings_path.read_text())
    except json.JSONDecodeError as e:
        print(f"✗ settings.json is malformed JSON: {e}", file=sys.stderr)
        sys.exit(3)

hooks = data.setdefault("hooks", {})
ups = hooks.setdefault("UserPromptSubmit", [])

# Already registered?
def has_our_hook():
    for matcher_block in ups:
        for h in matcher_block.get("hooks", []):
            if h.get("command") == hook_cmd:
                return True
    return False

if has_our_hook():
    print("✓ agora hook already present — no edit needed")
else:
    ups.append({
        "matcher": ".*",
        "hooks": [{"type": "command", "command": hook_cmd}],
    })
    settings_path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"✓ merged agora UserPromptSubmit hook into {settings_path}")
    print("  (restart any aoe Claude session to activate)")
PY
    echo ""
else
    echo ""
    echo "─── NEXT: register the UserPromptSubmit hook ───────────────────────"
    echo "Either re-run with --apply-hook OR add this to ~/.claude/settings.json"
    echo "under 'hooks' (merge if a hooks block already exists):"
    echo ""
    cat <<EOF
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": ".*",
        "hooks": [
          { "type": "command", "command": "$HOOK_CMD" }
        ]
      }
    ]
  }
EOF
    echo ""
    echo "(I did NOT edit settings.json — pass --apply-hook to do it automatically.)"
    echo ""
fi

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
