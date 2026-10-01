# Desktop Forge

Desktop Forge is a GTK4/libadwaita utility for Fedora GNOME that provides:

1. **Desktop shortcuts** — create launchers for installed apps or scripts and
   repair launchers already on the desktop.
2. **Desktop widgets** — weather, stocks, calendar, reminders, To-Do, news,
   clock, system monitoring, and the optional **CLIVE** AI assistant, drawn
   behind application windows.
3. **Desktop customization** — window effects, animations, automatic tiling
   and snapping, gestures, shortcuts, window rules, the top bar, dock,
   wallpaper and notifications, with presets and shareable profiles, all set
   graphically in **Customize**.

Built and tested on Fedora 44, GNOME Shell 50, and Wayland.

## Install

```bash
./install.sh
```

Then launch **Desktop Forge** from the app grid, or run `desktop-forge`.

## CLIVE assistant

CLIVE is an assistant for your whole desktop: it can search and organise your
email, move meetings, find and change files, keep notes and tasks, control
music, work in a terminal or a Git repository, and operate the graphical apps
you have installed. It works out which apps a request needs and chains them —
"find the email from John about Friday's meeting and move the meeting to 3 PM"
searches your mail, finds the event and updates it — and it shows which app it
is using as it goes ("Searching Gmail…", "Updating Calendar…").

**You decide which apps CLIVE can use.** Everything it can reach is listed in
**App Access**, each with its own switch. See [App Access](#app-access).

Install its optional Python environment and local model:

```bash
./install.sh --clive
desktop-forge --clive
```

The setup downloads Ollama and `qwen3.5:4b` (about 3.4 GB), starts separate user
services, and binds the local model server to `127.0.0.1`. Python 3.14, GNOME
accessibility/AT-SPI, GNOME Keyring/libsecret, GStreamer with PipeWire, the GNOME
desktop portal, bubblewrap (for the Terminal sandbox) and poppler-utils (for
reading PDFs) are used; all are present on Fedora 44. Add **CLIVE** from
**Widgets → Add widget**. After updating an already loaded GNOME extension, log
out and back in to load the new card code.

### Models

CLIVE runs on Ollama: a local model on this machine, or an Ollama Cloud model.
The model name in the desktop card's header and in the expanded chat's header
is a switcher: it lists your cloud and local models with the active one
checked, and the expanded chat adds a **Cloud | Local** toggle. The **CLIVE**
menu in the top panel has a one-click **Use local model** / **Use cloud model**.
A switch applies from CLIVE's next request, mid-task included, with no restart.
If the cloud fails during a task, CLIVE finishes it locally (unless **Fall back
to the local model** is off), and the header shows the model actually in use.

To use Ollama Cloud, open **CLIVE Settings → AI Models**:

1. Create an [Ollama API key](https://ollama.com/settings/keys), paste it into
   **Ollama API key** and press **Save key** (it goes to GNOME Keyring, on its
   own). **Check** confirms it in a few seconds.
2. Turn on **Use Ollama Cloud** and confirm the billing terms once.
3. Press **Save**. **Test** exercises tool calling, structured output and
   streaming on both models; image support is advisory.

**Cloud model** and **Local model** are dropdowns of what Ollama reports: the
Ollama Cloud catalog, and the models installed on this machine. If the local list
is empty although you have downloaded models, another Ollama server (such as a
system-wide `ollama.service`) is holding port 11434, and the row says how to stop it.
**Models in the switcher** adds or removes models and downloads local models by name. Ollama's [Free plan](https://ollama.com/pricing)
has a monthly allowance; CLIVE never buys credits, but a key on a funded account
can spend its balance. Live web search needs a key even with cloud reasoning off.

### App Access

**CLIVE Settings → App Access** lists every app CLIVE could use, grouped by
category, with a search box, **Enable All / Disable All** (for what is shown),
and **Pause all app access** — the emergency stop, also on the desktop card's
shield button and in the top-panel **CLIVE** menu, which stops a running task
too. Each app shows whether it reads only or reads and writes, whether it is
working, and when CLIVE last used it. Expanding it shows one switch per
capability ("Read emails", "Send emails", "Cancel events", "Run as
administrator"…) plus a **Check** button and the app's own settings.

- **The app switch overrides its capability switches.** Turned off, nothing
  of that app is available, whatever is set underneath; turned back on, the
  capability switches come back as they were.
- **It is enforced where tools run, not in the interface.** Every action is
  checked against the switches at the moment it runs, so turning an app off
  stops the very next action, even mid-task. A switched-off app is never
  "widened" into an approved task, in any approval mode.
- **Side doors are closed too.** With Gmail off, CLIVE cannot open Thunderbird,
  Evolution or `mail.google.com` either; with Calendar off, GNOME Calendar and
  `calendar.google.com`; with Files off, the Files app; with Notes off, the
  notes folder is closed to the file tools and the Terminal; a browser window
  showing a switched-off service is refused. Results CLIVE read from an app you
  then switch off are removed from what the model sees.
- **Defaults keep what CLIVE could already do.** Files, To-Do & Reminders, Web
  and the apps installed when App Access arrived start on (their new
  capabilities, such as changing a file's text, start off). Calendar starts on
  for reading only. Notes, Terminal, Git and playback control start off. **An
  app installed later starts off**, marked New. Connecting an email account
  turns that account on.
- **Files you attach yourself are not subject to these switches** — you handed
  them over.

**App Permissions** holds the task approval mode and **Ask before**: every
high-risk action of the apps that are on — sending email, deleting files or
messages, cancelling events, pushing commits, installing software,
administrator commands. These show exactly what will happen (recipients,
subject and text; the file; the event's title and time; the command) and wait
for **Confirm**, **even when Task approval is Auto-approve everything**. Each can
be set to not ask, except administrator commands.

### Integrations

| App | What CLIVE can do |
|---|---|
| Files | Find, read (text, PDF and office documents), create, change (the old version goes to Trash), move, rename, and move to Trash visible files in your home folder |
| To-Do & Reminders | The desktop's own lists |
| Calendar | GNOME Calendar and online-account calendars through Evolution Data Server: find, create, change, reschedule and cancel events. Thunderbird's calendars are read-only |
| Email | Each connected account: search (Gmail search syntax on Gmail), read without marking read, find messages waiting on you and yours waiting on others, label, flag, move, archive, draft replies into Drafts, send, and delete to Trash |
| Notes | Markdown notes in one folder (default `~/Notes`; an Obsidian vault works) |
| Web & Browser | Web search and page reading through Ollama, and opening pages |
| Terminal | Shell commands in a sandbox (below) |
| Git | Status, history, diffs, commits, branches, pull and push |
| Installed apps | Each graphical app: open it, read its controls through accessibility, see its window, click and type. Media players and browsers can also be controlled through MPRIS (play, pause, skip, volume, `spotify:` links) |

**Email.** In App Access, **Connect an Email Account…** offers the mail
accounts in GNOME Online Accounts (Google and Microsoft sign in with OAuth, so
CLIVE stores no password), or any IMAP/SMTP account with an app password kept
in GNOME Keyring. Each account is its own App Access entry.

**Terminal.** Commands run in a bubblewrap sandbox built from your switches: the
system is read-only, there is no session bus, keyring, Wayland or X11 socket,
no view of other processes, and no network unless **Use the network** is on.
Credentials, browser and mail profiles, CLIVE's own data, and folders of
switched-off apps are hidden, and with Files off the whole home folder is.
**Change files** lets commands write in your visible folders; hidden files and
folders (shell startup files, autostart, settings) stay read-only.
**Install and remove software** always asks first; recognising package-manager
commands is best effort, and user-level installs are possible whenever Change
files and Use the network are on. **Run as administrator** cannot be sandboxed,
so CLIVE opens the command in a Terminal window for you to read and run with
your password; it never sees the password or the output. Terminal apps
themselves are never driven by CLIVE.

**Git.** Commands run as argument lists, never through a shell, with hooks,
fsmonitor, external diff and textconv programs switched off. Reading and
committing run inside the same sandbox (committing can write only the
repository). Pull and push need your network and keys, so they run outside it:
use them with repositories you trust. Push always asks first.

**Desktop apps.** Screenshots are cropped to the app's own window, so other
windows on the screen never reach the model. Authentication and lock-screen
surfaces are never available.

### Chatting and attaching files

In the expanded chat the composer stays at the bottom: **Enter** sends,
**Shift+Enter** starts a new line. The desktop card's composer works the same
way. Files can be attached with the paperclip, by dragging them onto the chat,
or with **Ctrl+V** (copied files, or an image such as a screenshot). Staged
files are shared by the card and the chat, are checked the moment they are
added (a file CLIVE cannot use says why, by name), show a preview, and can be
removed before sending. A message can be just files.

CLIVE reads PDF (through `pdftotext`), Word (`.docx`), OpenDocument text,
spreadsheets and presentations, Excel (`.xlsx`), PowerPoint (`.pptx`), web
pages, RTF, CSV/TSV, JSON, Markdown, source code and other text in UTF-8,
UTF-16 or older encodings, and PNG, JPEG, GIF and WebP images (BMP, TIFF and
HEIC are converted; large images are scaled down). Files may come from your
home folder (outside hidden folders), removable drives, temporary and network
folders, or the document portal — not from system folders. Up to 8 files and
25 MB each; text is sized to fit the model's context, and a file cut to fit
says so. Images go only to models that can view them. Files stay with their
conversation, so a follow-up question still has them. GNOME Shell cannot take
files dropped onto the desktop card itself, so dragging a file over the card
opens the CLIVE window to drop it in.

The card's header holds the model switcher, the App Access shield, a new-chat
button and one that opens the expanded chat; below about 300 px the model name
gives way to its icon. Answers show links as buttons and list the last few
actions CLIVE took. To type on the desktop, click the card's text box: GNOME
only delivers the keyboard to the desktop under an explicit grab, so the card
takes one (Esc or a click elsewhere hands it back); a problem that arrives
while you type is still shown. A task preview, a confirmation and the task's
approved permissions appear as cards at the end of the conversation, with their
buttons beside them; the top-panel **CLIVE** menu can approve, open a waiting
confirmation, stop, pause app access, and switch models while a window covers
the card. When the card is covered or not on the desktop, CLIVE sends a
notification when it needs you or finishes; the text never includes message
contents, since notifications can appear on the lock screen.

### CLIVE Settings

**AI Models**, **App Access**, **App Permissions**, **Attachments** (files sent
with every task), **Memory & Context** (extra instructions, how many earlier
messages CLIVE reads, history retention, clearing history), **Appearance**
(compact card, action details), **Notifications**, **Automation** (maximum task
steps, and a scheduled check of your email accounts for messages that may need
a reply — headers only, nothing sent to a model, and only accounts whose
follow-up tracking is on), **Privacy** (whether images and attached files may
go to a cloud model — when withheld, the model is told), and **Advanced**
(local context window, logging model replies, restarting the service). The
desktop card's **Manage models…** and **Manage app access…** open these
directly (`desktop-forge --clive-settings=models`).

### Data and privacy

When a cloud model is in use, messages and tool results are sent to Ollama, as
are screenshots and attachments unless Privacy says otherwise. Chats, actions,
attachments' extracted text, and agent checkpoints (which never contain file
contents) stay in `~/.local/share/desktop-forge/clive/`; settings are in
`~/.config/desktop-forge/clive.json`, App Access in `clive-access.json` and mail
accounts in `clive-mail.json` beside it, with passwords and API keys in GNOME
Keyring. Deleting a chat removes its messages, actions, checkpoints and pasted
images. An interrupted task stays paused after a restart.

Troubleshooting:

```bash
systemctl --user status desktop-forge-clive desktop-forge-ollama
journalctl --user -u desktop-forge-clive -n 50
systemctl --user restart desktop-forge-clive
```

**Advanced → Log model replies** (or `CLIVE_DEBUG=1` for the service) writes a
shortened copy of every planning reply to the journal.

## Customize

The **Customize** tab holds every desktop setting, by section down the side,
with a search box above them (try "blur", "gaps" or "clock"). Changes apply to
the desktop straight away; a bar offers **Revert** until you press **Keep**.
The undo arrow in a section's header resets that section to its defaults.

Most of it is applied by Desktop Forge's Shell extension from
`~/.config/desktop-forge/desktop.json`, which the app writes complete. GNOME's
own settings (animations on or off, edge snapping, workspaces, touchpad and
mouse, clock, wallpaper, lock screen, banners) and Dash to Dock's are changed
where GNOME keeps them. After installing an update, log out and back in once:
until then, Customize says that the running extension is older than the app.

### Profiles and presets

**Profiles & Presets** shows eight built-in looks with a drawn sketch of each:
Minimal, Productivity, macOS-inspired, Windows-inspired, Hyprland-inspired,
Glass, Compact and Gaming. Choosing one applies it to the desktop for 20
seconds; press **Keep**, or it goes back by itself (closing the window also
goes back). A preset sets only the look and feel -- window effects,
animations, tiling, the top bar, the dock and banners -- and resets those to
neutral first, so switching from Hyprland-inspired to Minimal also switches
tiling off. Input devices, the clock, the lock screen, pictures, shortcuts and
window rules are never touched by a preset.

**Save as New Profile** keeps the whole desktop as it is now; **Your
Profiles** can then be switched to, updated, renamed, duplicated, exported and
deleted. Presets are read-only; duplicate one to change it. The **In Use** row
says which profile is active and marks it **Modified** once anything differs.
Profiles export as `.dfprofile` files (JSON with a format marker). Importing
one checks every value: numbers are clamped to their range, settings this
version does not know are left out, and picture paths from another computer
are pointed out. The dock and top bar are never put on the same screen edge.
Profiles are stored in `~/.config/desktop-forge/profiles/`.

### What each section does

- **Windows** — opacity for the focused window, other windows and a window
  being moved; dimming of other windows; rounded corners; a focus border (in
  the accent colour or your own, or a two-colour gradient that can turn
  slowly) and a border on other windows; and shadows for GTK apps. Apps listed
  under **Exceptions** are never touched, and fullscreen windows never are, so
  games and video can be shown directly. Corners and borders are drawn on
  Wayland windows; X11 windows get opacity and dimming only, because their
  picture includes Mutter's shadow. Shadows apply to GTK apps opened after the
  change.
- **Animations** — on or off, overall speed, the style for opening, closing
  and minimizing windows (GNOME default, fade, zoom, slide, pop or none), their
  duration, and how fast workspaces switch. GNOME default keeps GNOME's own.
- **Tiling & Snapping** — automatic tiling per workspace and display in eight
  layouts, picked from drawn previews: main and stack; dwindle, where each new
  window splits the one it opened from along its longer side, as in Hyprland;
  centered main; scrolling columns, a strip of columns with the ones out of
  view waiting under the edge until focused; columns; rows; grid; and one at a
  time. Gaps inside and around, smart gaps, the main area's size and where new
  windows go are settings, and windows glide into place. Dialogs, fixed-size
  windows and windows a rule floats stay free; minimized, fullscreen and
  floated windows keep their place for when they come back, and a maximized
  window covers its tile without moving the others. Dragging a tiled window
  onto another swaps them, and resizing a window sets the main area (in
  dwindle, its split). A switcher in the top bar shows each workspace's layout
  and changes it. Snapping can fill corners (quarters) and leave gaps; while
  tiling or either of these is on, it replaces GNOME's edge snapping, with a
  preview of where the window will go.
- **Workspaces & Displays** — dynamic or fixed workspaces, how many, primary
  display only, and wrapping from the last workspace to the first.
- **Top Bar** — everything from the former Overall page (see below), plus a
  floating bar with rounded ends, wallpaper blur behind it, the workspace
  indicator, the clock's position and what it shows.
- **Dock** — Dash to Dock's position, visibility, size, length and colours,
  plus its background mode, compact mode, running-app marker, trash, drives,
  Show Apps button, click action and every-display option.
- **Desktop & Widgets** — desktop icons and folder colours (below), the
  widgets' grid and magnet distance, and the hot corner.
- **Wallpaper & Lock** — light and dark pictures, fit and colour, blur and dim
  behind the desktop, a slideshow from a folder (run by `desktop-forged`), and
  GNOME's lock screen settings.
- **Notifications** — banners on or off (off is Do Not Disturb), their
  position, how long they stay and their opacity.
- **Mouse & Touchpad** — GNOME's pointer settings, dragging windows with a
  held key, and three- and four-finger swipes and three-finger pinches mapped
  to the overview, app grid, show desktop, workspaces, window actions or
  tiling. Changing any three-finger swipe hands all three-finger swipes to
  Desktop Forge (likewise for four); those left on GNOME default still do
  GNOME's action, without following your fingers. Two-finger gestures stay with
  apps.
- **Keyboard Shortcuts** — Desktop Forge's own (tiling, focus and swap by
  direction, snapping, centring, showing the top bar, the scratchpad, opening
  CLIVE; none is set until you choose one), a chosen set of GNOME's, and custom
  commands. The scratchpad keeps one window hidden on every workspace: one
  shortcut sends the focused window there (or takes it back), the other drops
  it down over everything at the top of the screen and hides it again. A
  combination already in use is named, and can be moved.
- **Window Rules** — for windows matched by app, window class, title (text or
  pattern) and kind: float or tile, workspace, display, size, centre,
  maximize, fullscreen, always on top, every workspace, opacity and no effects.
  Earlier rules win; the editor counts the open windows a rule matches. Rules
  apply when a window opens and when they change.

Nothing in Customize runs on the lock screen, and nothing there changes the
work area when the screen locks. Each feature in the extension is separate: one
that fails is switched off until the next login and the rest keep working.

## Desktop icons, dock, and top bar

Open **Customize → Desktop & Widgets** to choose DING's Tiny, Small, Standard, or
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

Open **Customize → Top Bar** and **Customize → Dock** to customize the GNOME
top bar and Dash to Dock independently.
Each surface has its own background color, opacity, and text/icon color. Leave
automatic contrast on to choose a readable black or white foreground, or turn
it off to pick the foreground yourself.

The top bar can sit at the top or bottom of the primary display and use a
custom height. **Visibility** is Always Visible, Intelligent Auto-hide (hidden
while a window covers the bar) or Always Hidden (hidden until you reach the
edge). **Hiding Behavior** holds the rest:

- **Hide when** — any window touches the bar, only the focused app's windows,
  or only maximized and tiled windows.
- **Reveal by** — hovering at the screen edge, or pushing against it. Pushing
  never takes a click from the top pixel row of a maximized application.
- **Edge sensitivity**, **Reveal delay**, **Hide delay** and **Animation**.
- **Reveal over fullscreen apps** — off by default, so videos and games stay
  clear of the bar.

A hidden bar is moved fully off its monitor and hidden, so it never draws onto
a neighbouring display. Windows being dragged or resized are ignored until
they are dropped, and bursts of window changes are decided once, so moving a
window across the bar does not make it flicker. A window straddling in from
another monitor counts as covering it. Opening a top-bar menu from the keyboard
(Super+V, Super+S) reveals the bar first. The bar's timing is its own and no
longer follows Dash to Dock. Hiding bars overlay application content, so
revealing one never resizes a maximized window, and the lock screen always
shows the bar with its indicators without resizing anything behind it.
Desktop Icons NG receives a separate permanent inset for the bar's shown
footprint, keeping every desktop icon clear when the bar reveals. Dock controls
use Dash to Dock's own settings
for visibility, all four screen edges, icon size, and maximum length. Desktop
Forge prevents the two surfaces from being assigned to the same horizontal
edge. If Dash to Dock is unavailable or locked by system policy, the rest of
the rest of Customize remains usable.

## Folder colors

Open **Customize → Desktop & Widgets → Folder Colors → Add Folder** to give a local folder its own
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
own colors, opacity and behavior are configurable from **Customize**. Card
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

The System card can also show **temperatures** and **network details**, each
switched on in its settings. Temperatures come straight from the kernel's
hardware sensors: by default the CPU, graphics and drive readings (or every
sensor), in Celsius or Fahrenheit, turning amber within 10° of a sensor's own
limit and red at it. Network details name the connection (Wi-Fi network and
signal, or the wired link), its IP address (which can be hidden), any active
VPN, and live download and upload rates. Connection names come from
NetworkManager when it is running. Rates count physical interfaces only, so VPN
traffic is not counted twice. Switching a section on grows a card that would
otherwise cut it off.

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
Terminal tabs and text stay visible inside the card while moving or resizing;
resizing updates the terminal grid, and finishing the edit keeps its shells running.
Click **+** beside the terminal's three-bar menu to open a new tab, or press **Ctrl+Shift+T**.
The terminal's menu (or right-click menu) includes **Smaller text**, **Larger text**,
and **Reset text size**. Use **Ctrl+−**, **Ctrl++** (or **Ctrl+=**), and **Ctrl+0**;
the text size is saved separately for each terminal widget and applies to its tabs.
Scroll through up to 10,000 lines of history with the mouse wheel, the overlay
scrollbar, or **Shift+Page Up/Down**. New output keeps your reading position;
**Scroll to latest output** in the menu returns to the bottom.

**Layouts** on the Widgets page saves where every widget is and which are
shown under a name, and **Use** puts them back -- one arrangement for work,
another for the evening. Widgets added since keep their place; the cards move
without being rebuilt.

## How it fits together

```text
desktop-forge (GTK app) ──writes──> ~/.config/desktop-forge/config.json
                        ──writes──> ~/.config/desktop-forge/desktop.json (Customize)
                                      |
                                      v
desktop-forged (Python/systemd user service)
  providers ──write──> ~/.local/share/desktop-forge/state/*.json
  reminder scheduler
                                      |
                                      v
GNOME Shell extension (GJS) draws and updates the desktop cards,
  and applies desktop.json: effects, animations, tiling, rules, gestures
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
| `desktop_forge/customize/` | Settings registry, backends, preview, profiles, presets, rules, shortcuts |
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
| System | `/proc`, `/sys`, NetworkManager | No |

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
rendering checks without the unrelated folder-color and CLIVE exercises, and
`bash tests/run_shell_smoke.sh --top-bar` for the top bar on two virtual
monitors. `bash tests/run_shell_smoke.sh --features` drives the Customize
features against real windows: it checks rounded corners and a focus border
pixel by pixel, window rules, animations, tiling, snapping, shortcuts,
gestures, workspace wrapping, the floating bar's work area, and that nothing
runs while the screen is locked. `bash tests/run_shell_smoke.sh --terminal` checks
real terminal clients: compact sizing, live movement and resizing, edit previews,
saved layouts, text-size shortcuts and persistence, history scrolling, session
preservation, and cleanup. It requires VTE for GTK 4.
`python3 tests/customize_ui_smoke.py` checks
the Customize tab against in-memory GNOME settings: live changes and revert,
the preset countdown, profiles, search, rules and shortcuts.

With CLIVE installed, run its integration and transport tests using
`~/.local/share/desktop-forge/clive-venv/bin/python -m unittest discover -s tests`.
These cover App Access enforcement (switches changed mid-task, side doors,
redaction, confirmations in every approval mode), each integration against
fakes or temporary folders (a real bubblewrap sandbox and real Git included),
attachments in every supported format, model switching, approval boundaries,
cancellation, fallback, action journaling and history deletion.
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

Reset any folder colors in **Customize** first to restore their previous icons,
and set **Customize → Windows → Window shadows** back to **App default** to
remove Desktop Forge's block from the GTK user stylesheets. GNOME and Dash to
Dock settings changed in Customize stay as they are; the section reset buttons
put them back to their defaults.

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
