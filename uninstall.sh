#!/bin/bash
# Uninstall Claude Code Usage Monitor

set -e

HOOKS_DIR="${1:-$HOME/.claude/hooks}"

echo "Uninstalling Claude Code Usage Monitor..."

# Remove cron
(crontab -l 2>/dev/null | grep -v "usage_monitor.py") | crontab - 2>/dev/null || true

# Remove files (keep usage.db as backup)
rm -f "$HOOKS_DIR/usage_monitor.py"
rm -f "$HOOKS_DIR/usage_template.html"

echo "Done! Cron removed, scripts deleted."
echo "Note: $HOOKS_DIR/usage.db kept as backup. Remove manually if not needed."
