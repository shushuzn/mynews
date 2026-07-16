#!/bin/bash
# Setup script to install pre-commit hook
# Run this after cloning the repository

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GIT_HOOKS_DIR="$(git rev-parse --show-toplevel)/.git/hooks"
HOOK_SOURCE="${SCRIPT_DIR}/hooks/pre-commit"

if [ ! -f "$HOOK_SOURCE" ]; then
    echo "Error: hooks/pre-commit not found"
    exit 1
fi

cp "$HOOK_SOURCE" "${GIT_HOOKS_DIR}/pre-commit"
chmod +x "${GIT_HOOKS_DIR}/pre-commit"

echo "✅ pre-commit hook installed"
