#!/bin/bash
# SessionStart hook: bootstrap ~/.claude/ from watapon-euco/Environment.
#
# Detection: if ~/.claude/CLAUDE.md already exists (an already-provisioned
# local machine), do nothing. Ephemeral cloud containers start with no
# ~/.claude/CLAUDE.md, so the sync always runs there. This avoids relying
# on undocumented environment variables like CLAUDE_CODE_REMOTE.

set -uo pipefail

if [ -f "$HOME/.claude/CLAUDE.md" ]; then
  exit 0
fi

DOTFILES_REPO="https://github.com/watapon-euco/Environment"
CLONE_DIR="/tmp/_env-dotfiles"

if [ ! -d "$CLONE_DIR/.git" ]; then
  rm -rf "$CLONE_DIR"
  if ! git clone --depth 1 --quiet "$DOTFILES_REPO" "$CLONE_DIR"; then
    echo "environment sync: clone failed; continuing without shared config" >&2
    exit 0
  fi
fi

bash "$CLONE_DIR/setup.sh"
