#!/usr/bin/env bash
set -euo pipefail

APP_NAME="desktop-forge"
SHARE_DIR="$HOME/.local/share/$APP_NAME"
BIN_DIR="$HOME/.local/bin"
APPS_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
EXTENSION_UUID="desktop-forge@jrf.local"
EXTENSIONS_DIR="$HOME/.local/share/gnome-shell/extensions"

usage() {
    cat <<'USAGE'
Usage: ./install.sh [--clive]

Install Desktop Forge, its data service, and the GNOME desktop widget extension.
--clive also installs the AI service, Ollama, and its 3.4 GB local fallback model.
USAGE
}

require_cmd() {
    command -v "$1" >/dev/null 2>&1
}

check_deps() {
    local missing=()
    require_cmd python3 || missing+=("python3")
    if ! python3 -c "
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw
" >/dev/null 2>&1; then
        missing+=("python3-gobject / gtk4 / libadwaita bindings")
    fi

    if ((${#missing[@]})); then
        echo "Missing required dependencies:" >&2
        printf '  - %s\n' "${missing[@]}" >&2
        echo >&2
        echo "On Fedora, install everything needed with:" >&2
        echo "  sudo dnf install -y python3-gobject gtk4 libadwaita" >&2
        exit 1
    fi

    # The calendar and terminal widgets have optional dependencies --
    # everything else works without them, so a missing typelib is a warning,
    # not a failure.
    if ! python3 -c "import gi; gi.require_version('Vte', '3.91'); from gi.repository import Vte" >/dev/null 2>&1; then
        echo "note: VTE for GTK 4 not found — the terminal widget will be unavailable" >&2
        echo "  install it with: sudo dnf install -y vte291-gtk4" >&2
    fi

    if ! python3 -c "
import gi
gi.require_version('ECal', '2.0')
gi.require_version('EDataServer', '1.2')
from gi.repository import ECal, EDataServer
" >/dev/null 2>&1; then
        echo "note: Evolution Data Server bindings not found — the calendar widget will be unavailable" >&2
        echo "  install them with: sudo dnf install -y evolution-data-server" >&2
    fi

    if ! python3 -c "import dateutil" >/dev/null 2>&1; then
        echo "note: python-dateutil not found — recurring Thunderbird events will be unavailable" >&2
        echo "  install it with: sudo dnf install -y python3-dateutil" >&2
    fi
}

resolve_src_dir() {
    if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
        local candidate
        candidate="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        if [[ -d "$candidate/desktop_forge" && -f "$candidate/bin/desktop-forge" ]]; then
            echo "$candidate"
            return
        fi
    fi
    echo "install.sh must be run from inside a Desktop Forge checkout" >&2
    exit 1
}

copy_app_tree() {
    local src_dir="$1"
    rm -rf "$SHARE_DIR/desktop_forge"
    cp -a "$src_dir/desktop_forge" "$SHARE_DIR/desktop_forge"
    # rsync excludes these, and a stale cache is never worth shipping.
    find "$SHARE_DIR/desktop_forge" -name '__pycache__' -type d -prune -exec rm -rf {} +
}

app_tree_matches() {
    local src_dir="$1"
    diff -rq --exclude='__pycache__' "$src_dir/desktop_forge" "$SHARE_DIR/desktop_forge" >/dev/null 2>&1
}

install_app() {
    local src_dir="$1"

    mkdir -p "$SHARE_DIR" "$BIN_DIR" "$APPS_DIR" "$ICON_DIR"

    if require_cmd rsync; then
        rsync -a --delete --delete-excluded --exclude '__pycache__' \
            "$src_dir/desktop_forge/" "$SHARE_DIR/desktop_forge/"
    else
        copy_app_tree "$src_dir"
    fi

    # The installed copy is imported ahead of the checkout, so a bad copy is
    # what the app runs. rsync once wrote every new module as an empty file
    # and exited 0; the only symptom was tabs showing "unavailable". Check the
    # copy against the checkout, and redo it with plain cp if it does not match.
    if require_cmd diff && ! app_tree_matches "$src_dir"; then
        echo "warning: the installed copy of desktop_forge does not match the checkout — copying it again" >&2
        copy_app_tree "$src_dir"
        if ! app_tree_matches "$src_dir"; then
            echo "error: $SHARE_DIR/desktop_forge still differs from $src_dir/desktop_forge:" >&2
            diff -rq --exclude='__pycache__' "$src_dir/desktop_forge" "$SHARE_DIR/desktop_forge" >&2 || true
            exit 1
        fi
    fi

    chmod +x "$src_dir/bin/desktop-forge" "$src_dir/bin/desktop-forged"
    ln -sfn "$src_dir/bin/desktop-forge" "$BIN_DIR/desktop-forge"
    ln -sfn "$src_dir/bin/desktop-forged" "$BIN_DIR/desktop-forged"
    # Remove the launcher left by older releases that shipped a separate
    # widget renderer.
    rm -f "$BIN_DIR/desktop-forge-widgets"
    ln -sfn "$src_dir/packaging/org.jrf.DesktopForge.desktop" \
        "$APPS_DIR/org.jrf.DesktopForge.desktop"
    ln -sfn "$src_dir/packaging/icons/org.jrf.DesktopForge.svg" \
        "$ICON_DIR/org.jrf.DesktopForge.svg"

    require_cmd update-desktop-database && update-desktop-database "$APPS_DIR" || true
    require_cmd gtk-update-icon-cache &&
        gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true
    require_cmd desktop-file-validate &&
        desktop-file-validate "$APPS_DIR/org.jrf.DesktopForge.desktop" || true
}

install_service() {
    local src_dir="$1"

    if ! require_cmd systemctl || ! systemctl --user list-units >/dev/null 2>&1; then
        echo "warning: no systemd user session — the widget data service was not installed" >&2
        echo "  (log into a graphical session and re-run install.sh)" >&2
        return
    fi

    # systemd sets up the service's mount namespace before it runs, and a
    # ReadWritePaths entry that does not exist yet makes that fail outright
    # (226/NAMESPACE). The daemon creates these itself at startup, but that is
    # far too late -- they have to exist before the unit is started.
    mkdir -p "$HOME/.config/desktop-forge" "$HOME/.local/share/desktop-forge/state"

    mkdir -p "$SYSTEMD_USER_DIR"
    sed "s#@SRC_DIR@#$src_dir#g" \
        "$src_dir/packaging/systemd/desktop-forged.service.in" \
        > "$SYSTEMD_USER_DIR/desktop-forged.service"

    if ! systemctl --user daemon-reload || \
        ! systemctl --user enable desktop-forged.service || \
        ! systemctl --user restart desktop-forged.service; then
        echo "warning: could not start the widget data service — retry with:" >&2
        echo "  systemctl --user daemon-reload && systemctl --user enable --now desktop-forged.service" >&2
    fi
}

# The GNOME Shell extension is what actually draws the widgets. It is optional:
# decline it and the shortcut half of the app is unaffected.
install_extension() {
    local src_dir="$1"

    if [[ -n "${DESKTOP_FORGE_NO_EXTENSION:-}" ]]; then
        return
    fi

    if [[ "${XDG_CURRENT_DESKTOP:-}" != *GNOME* ]]; then
        echo "note: not a GNOME session — skipping the desktop widget extension" >&2
        return
    fi

    mkdir -p "$EXTENSIONS_DIR"
    rm -rf "${EXTENSIONS_DIR:?}/$EXTENSION_UUID"
    cp -a "$src_dir/extension" "$EXTENSIONS_DIR/$EXTENSION_UUID"
    # The keyboard shortcuts schema; without it the extension still runs,
    # only its shortcuts are unavailable.
    if require_cmd glib-compile-schemas; then
        glib-compile-schemas "$EXTENSIONS_DIR/$EXTENSION_UUID/schemas" ||
            echo "warning: could not compile the Desktop Forge shortcuts schema" >&2
    fi

    if require_cmd gnome-extensions; then
        # Enabling succeeds only once the shell has scanned the new directory,
        # which on Wayland does not happen until the session restarts. A
        # failure here is expected on a first install, not an error -- but it
        # is not nothing either: an extension that is on disk and switched off
        # draws no widgets, and saying nothing about it is how that ends up
        # looking like a broken install. print_summary reports the outcome.
        gnome-extensions enable "$EXTENSION_UUID" >/dev/null 2>&1 || true
    fi
}

print_summary() {
    local src_dir="$1"
    echo
    echo "Installed:"
    echo "  Launcher:      $BIN_DIR/desktop-forge"
    echo "  Data service:  $BIN_DIR/desktop-forged"
    echo "  Desktop entry: $APPS_DIR/org.jrf.DesktopForge.desktop"
    echo "  Checkout:      $src_dir"

    if require_cmd systemctl && systemctl --user is-active --quiet desktop-forged.service; then
        echo "  Widget data:   running (systemctl --user status desktop-forged)"
    else
        echo "  Widget data:   not running"
    fi

    if [[ -d "$EXTENSIONS_DIR/$EXTENSION_UUID" ]]; then
        local info state enabled
        # gnome-extensions exits 2 for an extension the running shell has not
        # scanned yet, which is the normal state right after a first install.
        # Under `set -e` with pipefail that would abort the installer, so the
        # failure is absorbed here.
        info="$(gnome-extensions info "$EXTENSION_UUID" 2>/dev/null || true)"
        state="$(awk -F': ' '/State:/{print $2}' <<<"$info")"
        enabled="$(awk -F': ' '/Enabled:/{print $2}' <<<"$info")"
        echo "  Widgets:       installed (state: ${state:-unknown})"

        # "Scanned but switched off" and "never scanned" both stop the widgets
        # appearing, but they need opposite things from the user: one is a
        # button press, the other is a new session. Telling everyone to log out
        # sends people who only needed the button round a loop that never ends.
        if [[ "$state" == "ACTIVE" ]]; then
            echo
            echo "  If this was an update, log out and back in once to load"
            echo "  the new widget code; GNOME Shell keeps active extensions cached."
        elif [[ -z "$state" ]]; then
            echo
            echo "  Log out and back in to start the desktop widgets."
            echo "  GNOME Shell on Wayland cannot load a new extension without it."
        elif [[ "$enabled" != "Yes" ]]; then
            echo
            echo "  The widgets are installed but switched off. GNOME Shell has"
            echo "  already scanned them, so no logout is needed — open Desktop"
            echo "  Forge, go to Widgets, and press Enable."
        else
            echo
            echo "  Log out and back in to start the desktop widgets."
        fi
    fi

    echo
    echo "Run 'desktop-forge' (make sure ~/.local/bin is on your PATH),"
    echo "or launch 'Desktop Forge' from the app grid."
}

main() {
    local with_clive=false
    while (($#)); do
        case "$1" in
            -h|--help) usage; exit 0 ;;
            --clive) with_clive=true ;;
            *) echo "unknown option: $1" >&2; usage >&2; exit 1 ;;
        esac
        shift
    done

    check_deps
    local src_dir
    src_dir="$(resolve_src_dir)"
    install_app "$src_dir"
    python3 -B -c 'import sys; sys.path.insert(0, sys.argv[1]); from desktop_forge import config; config.migrate()' "$src_dir"
    install_service "$src_dir"
    install_extension "$src_dir"
    if [[ "$with_clive" == true ]]; then
        bash "$src_dir/bin/setup-clive"
    fi
    print_summary "$src_dir"
}

main "$@"
