#!/usr/bin/env bash
# Run real St actors and shaders in a separate, software-rendered compositor.
set -euo pipefail
monitors=(--virtual-monitor=1200x850)
x11=(--no-x11)
export DF_TEST_MODE=full
case "${1:-}" in
    '') export DF_TEST_NEWS_ONLY=0 ;;
    --news) export DF_TEST_NEWS_ONLY=1 ;;
    # Two displays side by side, for the top bar's multi-monitor behavior.
    --top-bar) export DF_TEST_NEWS_ONLY=0 DF_TEST_MODE=topbar
               monitors+=(--virtual-monitor=1000x700) ;;
    # The desktop.json modules: window effects, rules, animations, chrome.
    # Features start Xwayland: tiling must take X11 apps too.
    --features) export DF_TEST_NEWS_ONLY=0 DF_TEST_MODE=features
                x11=() ;;
    --terminal) export DF_TEST_NEWS_ONLY=0 DF_TEST_MODE=terminal ;;
    *) echo 'Usage: tests/run_shell_smoke.sh [--news|--top-bar|--features|--terminal]' >&2; exit 2 ;;
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
if [ "$DF_TEST_MODE" = terminal ]; then
    # Exercise the checkout, not the app installed ahead of it by bin/desktop-forge.
    mkdir -p "$test_dir/bin"
    cat > "$test_dir/bin/desktop-forge" <<'EOF'
#!/bin/sh
exec python3 "$DF_TEST_ROOT/tests/shell-smoke/terminal-client.py" "$@"
EOF
    chmod +x "$test_dir/bin/desktop-forge"
    export PATH="$test_dir/bin:$PATH" GDK_BACKEND=wayland PYTHONDONTWRITEBYTECODE=1 SHELL=/bin/sh
    python3 - <<'PY'
import json, os
path = os.path.join(os.environ["XDG_CONFIG_HOME"], "desktop-forge", "config.json")
with open(path) as f:
    config = json.load(f)
config["widgets"] = [
    {"id": "terminal-test", "type": "terminal", "enabled": True,
     "monitor": 0, "x": 80, "y": 100, "width": 296, "height": 240,
     "options": {}, "style": {}},
    {"id": "terminal-peer", "type": "terminal", "enabled": True,
     "monitor": 0, "x": 800, "y": 500, "width": 320, "height": 240,
     "options": {}, "style": {}},
]
config["edit_layout"] = True  # terminals finish starting after the modal overlay opens
with open(path, "w") as f:
    json.dump(config, f)
PY
fi
glib-compile-schemas "$test_root/extension/schemas"
ln -s "$test_root/extension" "$XDG_DATA_HOME/gnome-shell/extensions/desktop-forge@jrf.local"
ln -s "$test_root/tests/shell-smoke" "$XDG_DATA_HOME/gnome-shell/extensions/desktop-forge-smoke@jrf.local"
gsettings set org.gnome.shell enabled-extensions "['desktop-forge@jrf.local', 'desktop-forge-smoke@jrf.local']"
gsettings set org.gnome.shell.extensions.dash-to-dock dock-position 'LEFT'
gsettings set org.gnome.shell.extensions.dash-to-dock animation-time 0.2
gsettings set org.gnome.shell.extensions.dash-to-dock show-delay 0.1
gsettings set org.gnome.shell.extensions.dash-to-dock hide-delay 0.2
# Left on deliberately: the top bar must reveal on a plain edge hover
# whether or not the dock demands pressure.
gsettings set org.gnome.shell.extensions.dash-to-dock require-pressure-to-show true
gsettings set org.gnome.shell.extensions.dash-to-dock intellihide-mode 'MAXIMIZED_WINDOWS'
gsettings set org.gnome.shell welcome-dialog-last-shown-version '50.4'
# The features run animates for real; the others need settled frames.
if [ "$DF_TEST_MODE" = features ]; then
    gsettings set org.gnome.desktop.interface enable-animations true
else
    gsettings set org.gnome.desktop.interface enable-animations false
fi
gsettings set org.gnome.desktop.interface enable-hot-corners false
gsettings set org.gnome.desktop.background picture-uri "file://$test_root/tests/shell-smoke/wallpaper.svg"
gsettings set org.gnome.desktop.background picture-uri-dark "file://$test_root/tests/shell-smoke/wallpaper.svg"
printf 'Shell test artifacts: %s\n' "$test_dir"
status=0
timeout 180s dbus-run-session -- gnome-shell --headless "${x11[@]}" \
    --wayland-display=df-smoke "${monitors[@]}" \
    > "$test_dir/shell.log" 2>&1 || status=$?
if [ -f "$test_dir/result.json" ]; then
    python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(json.dumps(d,indent=2)); sys.exit(0 if d["ok"] else 1)' "$test_dir/result.json"
    if [ "$DF_TEST_MODE" = full ]; then
        python3 "$test_root/tests/check_shell_smoke.py" "$test_dir"
    elif { [ "$DF_TEST_MODE" = features ] || [ "$DF_TEST_MODE" = terminal ]; } &&
         grep -E 'JS ERROR|CRITICAL|needs an allocation|GLib-GObject-WARNING' "$test_dir/shell.log"; then
        echo 'Shell log contains errors' >&2
        exit 1
    elif grep -E 'JS ERROR|Clutter-CRITICAL|St-CRITICAL' "$test_dir/shell.log"; then
        echo 'Shell log contains errors' >&2
        exit 1
    fi
else
    tail -80 "$test_dir/shell.log"
    exit 1
fi
