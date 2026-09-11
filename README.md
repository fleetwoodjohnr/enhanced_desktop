# Desktop Forge

Desktop Forge is a GTK4/libadwaita utility for Fedora GNOME that provides:

1. **Desktop shortcuts** — create launchers for installed apps or scripts and
   repair launchers already on the desktop.
2. **Desktop widgets** — weather, stocks, calendar, reminders, To-Do, news,
   clock, system monitoring, and the optional **CLIVE** AI assistant, drawn
   behind application windows.

Built and tested on Fedora 44, GNOME Shell 50, and Wayland.

## Install

```bash
./install.sh
```

Then launch **Desktop Forge** from the app grid, or run `desktop-forge`.

## CLIVE assistant

CLIVE is a text assistant with a native desktop card, expanded chat, and local
chat history. Its LangGraph agent can research the web, launch apps, find and
read visible files, create new text files, move or trash files, manage the
existing To-Do and Reminders lists, and operate every normal graphical desktop
app that appears in the application menu. It uses
structured accessibility controls when an app provides them, and falls back to
the consented shared screen for pointer, text, navigation, and shortcuts when
apps such as LibreOffice or Thunderbird do not expose a complete tree.

Install its optional Python environment and local model:

```bash
./install.sh --clive
desktop-forge --clive
```

The setup downloads Ollama and `qwen3.5:4b` (about 3.4 GB for the model), starts
separate user services, and binds the local model server to `127.0.0.1`.
Python 3.14, GNOME accessibility/AT-SPI, GNOME Keyring/libsecret, GStreamer with
PipeWire, and the GNOME desktop portal are required for all features. These
were available on the Fedora 44 development machine. Existing widget data
providers do not need the optional AI dependencies.

Add **CLIVE** from **Widgets → Add widget**. After updating an already loaded
GNOME extension, log out and back in to load the new card code. Expanded chat
works immediately from the CLIVE tab or `desktop-forge --clive`.

For cloud reasoning, open the gear button in CLIVE:

1. Create an [Ollama API key](https://ollama.com/settings/keys), paste it into
   **Ollama API key**, and press **Save key**. It is stored in GNOME Keyring,
   on its own — no other setting can refuse it or discard it. Press **Check**
   to have Ollama confirm the key in a couple of seconds.
2. Turn on **Use Ollama Cloud**. The first time, CLIVE asks you to confirm the
   billing terms below; that answer is remembered.
3. Press **Save** in the dialog header. Nothing is written until you do, and
   the header says "Unsaved changes" until then.
4. Press **Test** on **Test cloud and local models**. A spinner runs while it
   does, and the full result replaces that row's description, wrapped and
   selectable so it can be copied.
   This checks an actual image/tool exchange, structured output, and streaming,
   and can take several minutes on the local model. Tool calling is required.
   Image support is only advisory: a model without it is tested text-only and
   reported as one that will not receive desktop screenshots. If Ollama does
   not say what a cloud model supports, the test exercises the model directly
   instead of refusing it. The default cloud candidate is `gemma4:31b`; choose
   another accessible model if your account denies it.

**Advanced** holds two limits that take effect on the next task, without
restarting the service: **Local context window** (2048–131072 tokens the local
model may hold — larger costs memory) and **Maximum task steps** (4–64 tool
rounds before CLIVE stops and reports).

**Task approval** chooses how much of the task preview CLIVE may skip:

- **Always ask** (default) — every task waits for **Approve**.
- **Auto-approve read-only tools** — web search and fetch, file search and
  read, listing apps, To-Dos and reminders, and reading an app's accessibility
  tree run straight away. Writing, moving or trashing files, launching apps,
  opening pages and desktop control still wait. Screenshots also still wait:
  they send screen pixels to the active model.
- **Auto-approve everything** — nothing waits. CLIVE asks you to confirm this
  once; the answer is remembered.

Auto-approval removes the pause, not the boundary. Every tool call is still
checked against the approved task, and a call that goes beyond it still widens
the task and comes back for approval whenever the wider scope is no longer
covered by the mode. Terminal execution, paths outside your home folder, and
hidden or credential files remain refused in every mode, and every action is
still recorded in the task log.

**Extra instructions** are appended to CLIVE's own instructions on every task,
up to 4000 characters — how you like answers written, which folders you mean by
default. They are added to CLIVE's rules, never in place of them: they cannot
grant permissions or authorize actions.

**Attached files** are sent with every task, up to 8 of them. Text is truncated
at 20,000 characters and images need a model that reports vision, which
**Test** tells you. Files must be in your home folder and outside hidden
folders — the same boundary the file tools use. A file that has since been
moved is skipped, and the task says so rather than failing.

Ollama's [Free plan](https://ollama.com/pricing) currently includes a monthly
starter allowance for a limited set of models, with one concurrent request.
It is not unlimited cloud hosting. CLIVE cannot inspect or enforce account
billing: it never buys credits, but a key attached to a funded account can
consume that account's balance. Use an unfunded Free account to keep cloud
usage at $0. Cloud access remains off until you configure it, and account
eligibility must be checked with your key. API authentication follows
[Ollama's cloud API](https://docs.ollama.com/cloud).

If cloud authentication, quota, or connectivity fails, inference switches to
the local model for the rest of that task. Already completed tools are not
replayed. A cloud model that answers but cannot produce the JSON CLIVE plans
with is a different case. Ollama does not enforce a response schema on every
model, so CLIVE states the required shape in the prompt as well, reads the
object out of fenced or narrated output and out of reasoning the model emitted
instead of an answer, treats permissions the model omitted as none rather than
as everything, and asks once more when the shape is still wrong. If that fails
it stops the task and names the model to change, rather than spending minutes
reaching the same place on the local model. Local chat and desktop/file tasks
work without a cloud key; live web search and fetching still require the key
and an internet connection — so a saved key is worth having even with **Use
Ollama Cloud** left off, and the model test says so. On the Ryzen 3 3200U
development laptop, local generation measured roughly 3–4 tokens per second,
so multi-step tasks can take several minutes.

The desktop card's header carries everything but the conversation: the model in
use beside the name, a button that starts a new chat, and one that opens the
expanded chat in a window. Starting a new chat is dimmed when the conversation
is already empty, since there would be nothing for it to clear. Turns are shown
as bubbles — yours on the right, CLIVE's on the left — so the transcript keeps
the rows the old speaker labels and separate model row spent.

To chat from the desktop card, click its text box. GNOME only delivers the
keyboard to the desktop under an explicit grab, so the card takes one: while it
holds the keyboard the activity line says so, the box is outlined, and system
shortcuts are suspended. Press **Esc** or click anywhere outside the card to
hand the keyboard back. The card also releases it by itself when a task starts,
when a window covers the card, or when the overview opens. A covered card stops
taking input entirely — the **CLIVE** entry in the top panel is then the way to
reach the expanded chat.

In the expanded chat the composer stays pinned at the bottom: **Enter** sends
and **Shift+Enter** starts a new line. An empty conversation offers three
starting points that fill in the composer rather than sending anything. Each
completed action gets its own collapsible entry named after the tool that ran.

The paperclip beside **Send** attaches up to 8 files to one message. They are
listed above the composer and can be removed one at a time before sending; the
list clears once the message goes. Text files are passed to the model as text
and images as images, under the same home-folder boundary as the file tools.
Only the paths cross D-Bus — CLIVE's own service reads the files.

The desktop card has the same paperclip, beside its own **Send**. GNOME Shell
has no GTK file dialog, so the card asks the desktop portal instead; the picker
opens as its own window, which means the card hands back the keyboard while you
choose. Staged files are listed at the end of the transcript rather than on a
row of their own, and are removed the same way. A file the service refuses —
outside your home folder, hidden, too large, or neither text nor an image —
leaves the message unsent and names itself on the activity line, so the list
stays put until you drop it. Use **Attached files** in settings instead for
files you want on every task.

CLIVE presents a task preview before actions. The preview and its **Approve
task** button are one card at the end of the conversation, in both the desktop
card and the expanded chat, so what you are approving and the button that
approves it are always on screen together. Approve once for its listed
tools, files, and apps; expanding that scope requires another approval. A task
waiting for approval can also be approved from the **CLIVE** top-panel menu,
which is the only route left when a window covers the card. The card stays on
screen after the button goes, so what a task was allowed to do remains readable
while it runs — including a task that **Task approval** let through without
stopping.
**Stop** is available in the card, expanded chat, and GNOME top panel while
a task is active. GNOME separately asks for screen-sharing permission when
pointer/screenshot tools first need it. For GUI tasks, name the app explicitly.
Desktop Forge's Shell extension keeps app identity and focus checks reliable on
Wayland even when accessibility is incomplete. Terminal execution, terminal-app
control, authentication prompts, and lock-screen surfaces remain excluded. File
tools stay in visible paths inside your home folder, and text writes create new
files without overwriting.

When cloud reasoning is enabled, task messages and tool results are sent to
Ollama; approved desktop screenshots can be sent too. Screenshots are held
only for the current task and are not saved in chat history. Chat, action
results, and LangGraph checkpoints stay in
`~/.local/share/desktop-forge/clive/`; model settings live in
`~/.config/desktop-forge/clive.json`. Use the trash button beside the
conversation list, or **Clear all chat history**, to remove messages, action
records, and checkpoints; both ask for confirmation first. There is no
cross-chat preference memory. An interrupted task stays paused after restart
so uncertain actions are never resumed automatically.

Troubleshooting:

```bash
systemctl --user status desktop-forge-clive desktop-forge-ollama
journalctl --user -u desktop-forge-clive -n 50
systemctl --user restart desktop-forge-clive
```

To see what a model actually returns when CLIVE reports that it did not produce
the required JSON, set `CLIVE_DEBUG=1` for the service and read the journal. It
logs a truncated copy of every planning reply, so leave it off unless you are
diagnosing a model:

```bash
systemctl --user set-environment CLIVE_DEBUG=1
systemctl --user restart desktop-forge-clive
```

## Desktop icons, dock, and top bar

Open **Overall → Desktop Icons** to choose DING's Tiny, Small, Standard, or
Large desktop grid, switch among installed GNOME icon packs, or add a Solid,
Frosted Glass, or Liquid Glass backplate. The glass controls include tint,
strength, automatic or custom label contrast, and an optional monochrome
artwork finish. **System** removes Desktop Forge's styling and leaves the
recognizable original artwork unchanged. Icon-pack changes are system-wide,
so they also appear in Files, the dock, and the app grid.

The glass effect is drawn with a translucent tint, specular gradients, rim,
and shadow. DING is a separate GTK desktop window and cannot sample the live
wallpaper for true backdrop blur. Desktop Forge keeps its rules scoped to that
window and stores them as a marked, reversible import in the GTK 4 user
stylesheet; unrelated user CSS is preserved. DING must be installed and
enabled for desktop sizing and materials.

Open **Overall** to customize the GNOME top bar and Dash to Dock independently.
Each surface has its own background color, opacity, and text/icon color. Leave
automatic contrast on to choose a readable black or white foreground, or turn
it off to pick the foreground yourself.

The top bar can sit at the top or bottom of the primary display, use a custom
height, stay visible, use the dock's intelligent window-overlap rule, or reveal
only when the pointer reaches its screen edge. Its pressure/hover trigger,
animation, delays, fullscreen policy, and intelligent-hide mode follow Dash to
Dock's live behavior settings. Intelligent and auto-hidden bars overlay
application content, so revealing one never resizes a maximized window.
Desktop Icons NG receives a separate permanent inset for the bar's shown
footprint, keeping every desktop icon clear when the bar reveals. Dock controls
use Dash to Dock's own settings
for visibility, all four screen edges, icon size, and maximum length. Desktop
Forge prevents the two surfaces from being assigned to the same horizontal
edge. If Dash to Dock is unavailable or locked by system policy, the rest of
the Overall page remains usable.

## Folder colors

Open **Overall → Folder Colors → Add Folder** to give a local folder its own
color. Choose a preset or use the custom picker and hex entry, preview it, then
press **Apply**. Each folder can have a different color in Files and Desktop
Icons NG. **Reset** restores its previous icon; an icon subsequently changed in
Files is kept. Coloring a folder does not color its contents or children.

Colors and reset history are saved separately in
`~/.local/share/desktop-forge/folder-colors.json`, with generated SVG icons in
`folder-icons/` beside it. Keep these icons in place while using folder colors.
If a folder moves, use **Locate** on its unavailable row to update the location.
Other file managers and symbolic sidebar icons may not display custom colors.

## Desktop setup

On a first install, log out and back in once so GNOME Shell can discover the
widget extension. The background data service starts immediately.

The optional calendar integrations use Evolution Data Server and
`python-dateutil`. On Fedora they can be installed with:

```bash
sudo dnf install -y evolution-data-server python3-dateutil
```

## Widgets

Calendar events are merged automatically from two places:

- calendars exposed by Evolution Data Server, including events created in the
  Fedora/GNOME Calendar app and calendars added through GNOME Online Accounts;
- enabled calendars in native and Flatpak Thunderbird profiles, read from
  Thunderbird's local calendar cache without modifying it.

Changes from either source are watched, so the calendar card refreshes without
restarting Desktop Forge. Identical events present through both sources are
shown once.

Cards use adaptive frosted glass with native GNOME Shell wallpaper blur. The
wallpaper is rendered separately from labels and applications so refreshing
content and moving windows do not contaminate the glass surface. They
follow the system light or dark appearance by default, and each widget gets a
distinct system-style accent colour. Appearance controls can instead pin the
glass to light or dark, use one accent, adjust blur and opacity, or apply
custom tint and text colours.

The GNOME top panel and Dash to Dock follow the system appearance by default,
independently of any light or dark override chosen just for the cards. Their
own colors, opacity and behavior are configurable from **Overall**. Card
surfaces use a quiet, shadow-free treatment in both appearances.

The Reminders card's **+** button creates reminders directly on the desktop.
The To-Do card also works entirely on the desktop: add or rename an item,
choose **To Do**, **In Progress**, **Blocked**, or **Done** from its status
menu, delete it, and drag its handle to change the saved manual order.

The News card combines compact headlines from six default feeds covering U.S.,
markets and economics, world news, technology, artificial intelligence, and
Linux and open source. The defaults use NPR National, Business, World and
Technology, MIT AI, and Phoronix. Hover anywhere over the card and scroll with a mouse wheel or
touchpad to browse the loaded feed; the title and visible article range stay
fixed. It automatically advances every 12 seconds when neither hovered nor
keyboard-focused, resuming from your position after you leave. Click a headline
to open it in the default browser. Feed URLs, the refresh interval, and
**Maximum headlines** (the number available in the scrollable feed) are
configurable in the widget's settings. **Topics** opens a checklist of subjects
-- world, U.S., politics, business and markets, technology, artificial
intelligence, science, health, sports, and Linux and open source -- and any
selection matches selected topics against headline titles and feed categories; each subject
stands for a set of keywords, so choosing artificial intelligence also catches
a story tagged only LLM. A topic switched off excludes matching stories even
when another selected topic matches: AI off also hides AI stories tagged
Technology or Science. Custom comma-separated keywords add matches while at
least one topic is on, but never override an excluded topic. **Select All**
allows every headline; **Clear All** turns news off. Upgrading preserves older
empty (unrestricted) selections by switching all topics on once.
Saving topics or sources clears results from the previous settings immediately
and requests an update. Failed saves keep the dialog open. Temporary feed
failures retry automatically; available feeds remain visible with a concise
warning about missing sources. Hovering a clipped
headline scrolls it as a wrap-around ticker, so the whole title can be read
however late you arrive at it. Incoming headlines wait until hover and keyboard
focus leave the card, then retain your first visible article and scroll offset.
Both RSS and Atom
are supported.

The Weather card shows current conditions above a forecast strip. **Forecast
detail** chooses whether that strip runs by the hour (the next six hours), by
the day (the next four days), or both, and **Refresh interval** sets how often
Open-Meteo is polled, from every minute to every hour. It defaults to hourly
detail refreshed every five minutes.

The Markets card supports the same mouse-wheel and touchpad scrolling, fixed
header, and 12-second automatic advance. Hover or keyboard focus pauses the
advance and quote refreshes; leaving resumes from the current symbol position.

News, Markets, and To-Do use thin overlay scrollbars that appear while hovering
over the card and fade away on exit. The thumb stays visible during dragging,
and showing it does not move the content. Cards without overflow show no bar.

Every card is desktop-only. Interactive cards accept input only while their
rectangle is uncovered; as soon as an application overlaps one, it moves back
behind the window and cannot intercept its pointer input. Widgets also stay
inside GNOME's usable work area, so they cannot cover the top panel or a dock.
Position and size remain managed through **Edit layout** in the app; drag any
edge or corner to resize in every direction. Moving and resizing use an
8-pixel grid and magnetize to nearby widget edges, centres, matching sizes,
and compact 8-pixel gutters. Edit mode keeps only the selected card's resize
handles visible and temporarily disables card blur for a cleaner workspace.

## How it fits together

```text
desktop-forge (GTK app) ──writes──> ~/.config/desktop-forge/config.json
                                      |
                                      v
desktop-forged (Python/systemd user service)
  providers ──write──> ~/.local/share/desktop-forge/state/*.json
  reminder scheduler
                                      |
                                      v
GNOME Shell extension (GJS) draws and updates the desktop cards
```

Providers perform external or system data access in the background service.
The extension reads JSON state and handles local reminder and To-Do edits, so
network work never blocks GNOME Shell.

| Path | What it is |
|---|---|
| `desktop_forge/backend/` | Desktop-entry writing, app enumeration, diagnostics |
| `desktop_forge/providers/` | Weather, stocks, calendars, reminders, To-Do, news, system |
| `desktop_forge/daemon/` | Polling, file watches, and reminder notifications |
| `desktop_forge/pages/` | GTK settings and launcher UI |
| `desktop_forge/clive/` | Optional D-Bus agent, model transport, task journal, desktop tools |
| `extension/` | GNOME Shell desktop widgets |

## Data sources

| Widget | Source | Key needed |
|---|---|---|
| Weather | Open-Meteo | No |
| Stocks | Yahoo chart endpoint | No |
| Calendar | Evolution Data Server and Thunderbird cache | No |
| Reminders | Local JSON store | No |
| To-Do | Local JSON store | No |
| News | Configurable public RSS and Atom feeds | No |
| System | `/proc`, `/sys` | No |

The Yahoo endpoint is unofficial and may change. It is isolated behind
`providers/base.py`, so it can be replaced without changing the widgets.

## Managing it

```bash
systemctl --user status desktop-forged
journalctl --user -u desktop-forged -f
gnome-extensions info desktop-forge@jrf.local
```

## Tests

Run the unit tests with `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests`.
On GNOME Shell 50, `bash tests/run_shell_smoke.sh` also runs the real widgets in
an isolated headless compositor. It checks scrollbar hover/drag behavior,
window overlap, refreshes, themes, and cleanup, then compares screenshots for
stray pixels. It also opens the settings UI and verifies real folder-color
metadata writes and reset using temporary folders. This requires GJS, GTK4
Python bindings, GVfs, and Pillow. Logs and screenshots stay in the printed
temporary directory; the test does not change your desktop configuration.
Use `bash tests/run_shell_smoke.sh --news` for the news, settings-save, and
rendering checks without the unrelated folder-color and CLIVE exercises.

With CLIVE installed, run its integration and transport tests using
`~/.local/share/desktop-forge/clive-venv/bin/python -m unittest discover -s tests`.
These cover approval boundaries, cancellation, interrupted streams, fallback,
recovery of structured output a model returned in its own shape, action
journaling, history deletion, and portal input guards.
`tests/test_clive_settings.py` covers the settings contract itself — validation
ranges, and the rule that a rejected model name and a locked keyring can never
discard each other's half of a save — and needs no optional dependencies. The
Shell smoke test exercises the real CLIVE card and GTK settings with a fake
service, typing into the card through a virtual keyboard to prove the desktop
keyboard grab is taken and released on every path.
`python3 tests/clive_desktop_smoke.py` verifies real AT-SPI text input and button
activation in its own temporary GTK window. Live portal screen streaming
requires GNOME's consent dialog; the automated tests use a fake portal bus.
Cloud validation requires your own API key and is separate from these tests.

## Uninstall

Reset any folder colors in **Overall** first to restore their previous icons.

```bash
systemctl --user disable --now desktop-forged.service
systemctl --user disable --now desktop-forge-clive.service desktop-forge-ollama.service
rm -f  ~/.config/systemd/user/desktop-forged.service
rm -f  ~/.config/systemd/user/desktop-forge-clive.service ~/.config/systemd/user/desktop-forge-ollama.service
rm -f  ~/.local/share/dbus-1/services/org.jrf.DesktopForge.Clive.service
rm -rf ~/.local/share/gnome-shell/extensions/desktop-forge@jrf.local
rm -f  ~/.local/bin/desktop-forge ~/.local/bin/desktop-forged ~/.local/bin/desktop-forge-clive
rm -f  ~/.local/share/applications/org.jrf.DesktopForge.desktop
rm -rf ~/.local/share/desktop-forge ~/.config/desktop-forge
```

Shortcuts created on the desktop are ordinary `.desktop` files and are left
alone. Remove CLIVE's saved API key before uninstalling, with **Remove** beside
the saved key in its settings.
Ollama models in `~/.ollama/models` are retained; remove them with Ollama if
you no longer use them. If CLIVE installed the `~/.local/bin/ollama` symlink,
remove that symlink as well.
