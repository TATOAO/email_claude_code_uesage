#!/bin/bash
# Install Claude Code Usage Monitor
# Usage: ./install.sh [email] [hooks_dir]

set -e

EMAIL="${1:-w_wt_t@126.com}"
HOOKS_DIR="${2:-$HOME/.claude/hooks}"

echo "Installing Claude Code Usage Monitor..."
echo "  Email: $EMAIL"
echo "  Hooks dir: $HOOKS_DIR"

# Check dependencies
command -v python3 >/dev/null 2>&1 || { echo "Error: python3 not found"; exit 1; }
command -v msmtp >/dev/null 2>&1 || { echo "Error: msmtp not found (needed for email sending)"; exit 1; }
python3 -c "import jinja2" 2>/dev/null || { echo "Error: jinja2 not installed (pip install jinja2)"; exit 1; }

# Create hooks dir
mkdir -p "$HOOKS_DIR"

# Copy files
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cp "$SCRIPT_DIR/usage_monitor.py" "$HOOKS_DIR/"
cp "$SCRIPT_DIR/usage_template.html" "$HOOKS_DIR/"

# Update email in script if custom
if [ "$EMAIL" != "w_wt_t@126.com" ]; then
    sed -i "s/w_wt_t@126.com/$EMAIL/g" "$HOOKS_DIR/usage_monitor.py"
fi

# Initialize DB (first scan may take a while)
echo "Initializing database and running first scan..."
python3 "$HOOKS_DIR/usage_monitor.py" 2>/dev/null || true

# Install cron
CRON_LINE="0 * * * * /usr/bin/python3 $HOOKS_DIR/usage_monitor.py 2>>/tmp/usage_monitor.log"
(crontab -l 2>/dev/null | grep -v "usage_monitor.py"; echo "$CRON_LINE") | crontab -

echo ""
echo "Done! Usage monitor installed."
echo "  Script: $HOOKS_DIR/usage_monitor.py"
echo "  Template: $HOOKS_DIR/usage_template.html"
echo "  Database: $HOOKS_DIR/usage.db"
echo "  Cron: every hour at :00"
echo "  Log: /tmp/usage_monitor.log"
echo ""
echo "To adjust config (quota, reset time, etc.):"
echo "  sqlite3 $HOOKS_DIR/usage.db \"UPDATE config SET value='YOUR_VALUE' WHERE key='weekly_output_quota'\""
