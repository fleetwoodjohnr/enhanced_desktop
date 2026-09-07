#!/usr/bin/env bash
# Run real St actors and shaders in a separate, software-rendered compositor.
set -euo pipefail
case "${1:-}" in
    '') export DF_TEST_NEWS_ONLY=0 ;;
    --news) export DF_TEST_NEWS_ONLY=1 ;;
    *) echo 'Usage: tests/run_shell_smoke.sh [--news]' >&2; exit 2 ;;
esac
test_root="$(cd "$(dirname "$0")/.." && pwd)"
test_dir="$(mktemp -d /tmp/desktop-forge-shell-test-XXXXXX)"
export DF_TEST_ROOT="$test_root" DF_TEST_DIR="$test_dir"
export XDG_CONFIG_HOME="$test_dir/config" XDG_DATA_HOME="$test_dir/data"
export XDG_CACHE_HOME="$test_dir/cache" XDG_RUNTIME_DIR="$test_dir/runtime"
export GSETTINGS_BACKEND=keyfile LIBGL_ALWAYS_SOFTWARE=1
export XDG_SESSION_TYPE=wayland
unset DISPLAY WAYLAND_DISPLAY
mkdir -p "$XDG_CONFIG_HOME/desktop-forge" "$XDG_DATA_HOME/gnome-shell/extensions" \
    "$XDG_CACHE_HOME" "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"
cp "$test_root/tests/shell-smoke/config.json" "$XDG_CONFIG_HOME/desktop-forge/config.json"
ln -s "$test_root/extension" "$XDG_DATA_HOME/gnome-shell/extensions/desktop-forge@jrf.local"
ln -s "$test_root/tests/shell-smoke" "$XDG_DATA_HOME/gnome-shell/extensions/desktop-forge-smoke@jrf.local"
gsettings set org.gnome.shell enabled-extensions "['desktop-forge@jrf.local', 'desktop-forge-smoke@jrf.local']"
gsettings set org.gnome.shell welcome-dialog-last-shown-version '50.4'
gsettings set org.gnome.desktop.interface enable-animations false
gsettings set org.gnome.desktop.interface enable-hot-corners false
gsettings set org.gnome.desktop.background picture-uri "file://$test_root/tests/shell-smoke/wallpaper.svg"
gsettings set org.gnome.desktop.background picture-uri-dark "file://$test_root/tests/shell-smoke/wallpaper.svg"
printf 'Shell test artifacts: %s\n' "$test_dir"
status=0
timeout 140s dbus-run-session -- gnome-shell --headless --no-x11 \
    --wayland-display=df-smoke --virtual-monitor=1200x850 \
    > "$test_dir/shell.log" 2>&1 || status=$?
if [ -f "$test_dir/result.json" ]; then
    python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(json.dumps(d,indent=2)); sys.exit(0 if d["ok"] else 1)' "$test_dir/result.json"
    python3 "$test_root/tests/check_shell_smoke.py" "$test_dir"
else
    tail -80 "$test_dir/shell.log"
    exit 1
fi
