# Graph Report - custom-desktop  (2026-09-30)

## Corpus Check
- 177 files · ~154,064 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 7 file(s) not represented in the graph (top: .css 2, .in 2, (none) 1)

## Summary
- 3255 nodes · 7354 edges · 156 communities (99 shown, 57 thin omitted)
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 241 edges (avg confidence: 0.89)
- Token cost: 115,271 input · 0 output

## Community Hubs (Navigation)
- Widget Logic & Tests
- CLIVE Agent Tests
- Email Integration
- CLIVE Service & Settings
- Profiles & News Parsing
- CLIVE Policy & Agent Core
- Entry Points & App Access
- Extension Styles & Notifications
- Integration Registry
- Calendar Integration Tests
- LangGraph Agent Nodes
- Extension Store & Reminders
- CLIVE Chat Page
- Shell Extension Main
- Overall Appearance Page
- News Provider
- Attachment Text Extraction
- Customize Backends & Presets
- Daemon Calendar Providers
- Shell Smoke Checks
- Thunderbird Calendar Reader
- Desktop Entry Files
- Customize Page
- CLIVE Settings Dialog
- App Access Page
- Widget Edit Mode
- Icon Resolution
- Dash to Dock Chrome
- Integration Base & Guards
- Widgets Page
- Polling Daemon
- Profiles Section UI
- CLIVE Shell & Transport
- Weather & Provider Base
- Window Tiling
- App Access Rows
- Customize Setting Rows
- System Network Provider
- Keyboard Shortcuts Model
- Manage Launchers Page
- CLIVE Desktop Control
- Dock Backend
- Reminders & Tasks Integration
- Desktop Store
- CLIVE Settings Tests
- Workspaces & Background Effects
- Model Listing Tests
- Access Store
- CLIVE Key & Usage Controls
- Application Lifecycle
- Config Model
- Chat History Storage
- Window Rules Editor
- Top Bar Panel Controller
- Window Animations
- Installed App Index
- Customizer Core
- Shortcut Capture Dialog
- Ollama Model Client
- Transport Tests
- Attachment Handling
- Folder Color Rendering
- Notes Integration
- Customize Preview Session
- Chrome Styling Logic
- Top Bar Blur
- Profile Tests
- Attachment Tests
- Apps Integration
- Calendar Integration Tools
- Terminal Sandbox Integration
- CLIVE Card Logic
- Shell Extension Installer
- Custom Launcher Page
- Desktop Icons Settings
- Chrome Colour Controls
- News Feeds Dialog
- Window Effect Styles
- Folder Colors Store
- CLIVE Page Layout
- Tiling Workspace Memory
- DING Style Scoping
- EDS Calendar Backend
- Files Integration
- Git Integration
- Access Registry Tests
- Desktop Control Tests
- CLIVE Settings Persistence Tests
- Desktop Icon Tests
- Media Integration
- CLIVE UI Smoke
- Reminders Page
- Touchpad Gestures
- Window Snapping
- README Architecture
- Config Migration Tests
- Network Provider Tests
- Model Errors
- Window Effects
- Install Script
- CLIVE Model Picker
- App Picker Editors
- Desktop D-Bus Bridge
- Desktop Settings Sync
- Shell Smoke Runner
- Folder Color Tests
- Todo Provider
- Panel Edge Reveal
- CLIVE D-Bus Client
- Service Access Tests
- Fake IMAP Fixture
- Attachment Turn Assembly
- Chrome Backend
- Window Rule Matching
- Composer Paste & Drop
- Panel Autohide Logic
- Attachment Format Tests
- Layout Preview Drawing
- Context File Reading
- CLIVE Client
- Wallpaper Slideshow
- Folder Color Rows
- Panel Reveal Animation
- Panel Settle Timer
- README Customize Features
- Window Rules Runtime
- README CLIVE Access
- README Models & Accounts
- Thunderbird Calendar Tests
- README CLIVE Dependencies
- Folder Colors UI Smoke
- Attachment Chips
- DING Integration
- News Settings Tests
- README Shell & State
- Fake CLIVE Client
- Customize Registry Tests
- Window Rule Tests
- Weather Forecast Tests
- Privacy Tests
- Image Withholding Tests
- Preset Tests
- Todo Store Tests
- App Icon Artwork
- Folder Icon Template
- Window Crop Rect
- Smoke Test Wallpaper

## God Nodes (most connected - your core abstractions)
1. `markup()` - 73 edges
2. `OverallPage` - 68 edges
3. `WidgetsPage` - 60 edges
4. `PanelController` - 60 edges
5. `Agent` - 53 edges
6. `ClivePage` - 48 edges
7. `CliveSettings` - 46 edges
8. `DesktopForgeExtension` - 40 edges
9. `Integration` - 39 edges
10. `Registry` - 37 edges

## Surprising Connections (you probably didn't know these)
- `FolderColorTests` --uses--> `FolderColors`  [INFERRED]
  tests/test_folder_colors.py → desktop_forge/backend/folder_colors.py
- `CustomizerTests` --uses--> `DashToDock`  [INFERRED]
  tests/test_customize.py → desktop_forge/backend/shell_chrome.py
- `AgentAccessTests` --uses--> `Agent`  [INFERRED]
  tests/test_clive_access.py → desktop_forge/clive/agent.py
- `FollowupCheckTests` --uses--> `Agent`  [INFERRED]
  tests/test_clive_access.py → desktop_forge/clive/agent.py
- `ServiceAccessTests` --uses--> `Agent`  [INFERRED]
  tests/test_clive_access.py → desktop_forge/clive/agent.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Desktop Forge config/state data flow (app -> daemon providers -> Shell extension)** — readme_desktop_forge, readme_config_json, readme_desktop_json, readme_desktop_forged_daemon, readme_providers, readme_state_json, readme_gnome_shell_extension [EXTRACTED 1.00]
- **CLIVE access enforcement layers** — readme_app_access, readme_side_door_closure, readme_app_permissions, readme_terminal_sandbox, readme_git_integration [INFERRED 0.85]
- **CLIVE optional Python runtime dependencies** — requirements_clive_langgraph, requirements_clive_langgraph_checkpoint_sqlite, requirements_clive_httpx, requirements_clive_jsonschema [INFERRED 0.85]

## Communities (156 total, 57 thin omitted)

### Community 0 - "Widget Logic & Tests"
Cohesion: 0.04
Nodes (66): DEFAULT_NEWS_FEEDS, describeConnection(), displayHostname(), formatRate(), formatTemperature(), headlinePanDistance(), headlineTickerDuration(), MAIN_THERMAL_KINDS (+58 more)

### Community 1 - "CLIVE Agent Tests"
Cohesion: 0.06
Nodes (11): AgentAccessTests, chat(), hook(), plan(), tool(), AgentTests, chat(), ApprovalModeTests (+3 more)

### Community 2 - "Email Integration"
Cohesion: 0.05
Nodes (41): account_for(), add_account(), _clear_password(), _compose(), _credentials(), decode_id(), _delete(), _delete_preview() (+33 more)

### Community 3 - "CLIVE Service & Settings"
Cohesion: 0.06
Nodes (29): safe_message(), available_models(), check_integration(), check_key(), downloaded_models(), main(), probe_model(), public_state() (+21 more)

### Community 4 - "Profiles & News Parsing"
Cohesion: 0.06
Nodes (10): check_values(), clean_name(), ImportReport, parse(), Profile, ProfileStore, serialize(), FakeResponse (+2 more)

### Community 5 - "CLIVE Policy & Agent Core"
Cohesion: 0.05
Nodes (19): complete_app_permissions(), State, user_preferences(), auto_approved(), check_scope(), normalize_plan(), plan_defaults(), plan_schema() (+11 more)

### Community 6 - "Entry Points & App Access"
Cohesion: 0.08
Nodes (7): activate(), capture(), desktop_json(), fail(), wait(), ExtensionStoreTests, ExtensionWidgetLogicTests

### Community 7 - "Extension Styles & Notifications"
Cohesion: 0.06
Nodes (27): DARK_GLASS, DEFAULT_STYLE, LIGHT_GLASS, WIDGET_ACCENTS, WIDGET_CLASSES, WIDGET_PROVIDER, ACTIONS, SHORTCUT_NAMES (+19 more)

### Community 8 - "Integration Registry"
Cohesion: 0.09
Nodes (6): Integration, account_integrations(), _build_integrations(), Registry, change(), change()

### Community 9 - "Calendar Integration Tests"
Cohesion: 0.06
Nodes (7): CalendarTests, FakeCalendar, GitTests, IntegrationCase, MailTests, NotesTests, TerminalTests

### Community 10 - "LangGraph Agent Nodes"
Cohesion: 0.09
Nodes (12): Agent, approval_node(), complete_node(), confirm_node(), plan_node(), scope_node(), think_node(), tools_node() (+4 more)

### Community 11 - "Extension Store & Reminders"
Cohesion: 0.09
Nodes (33): addReminder(), addTodo(), completeReminder(), CONFIG_PATH, isOwnWrite(), lastWritten, loadReminders(), loadTodos() (+25 more)

### Community 12 - "CLIVE Chat Page"
Cohesion: 0.08
Nodes (5): ClivePage, shown(), loaded(), sent(), linked_text()

### Community 13 - "Shell Extension Main"
Cohesion: 0.13
Nodes (6): chromeFingerprint(), DesktopForgeExtension, widgetFingerprint(), monitorForEntry(), readJson(), newsOptions()

### Community 15 - "News Provider"
Cohesion: 0.13
Nodes (27): _categories(), _child(), _child_text(), _clean_text(), _deduplicate(), _fetch_feed(), _fetch_feed_once(), filter_signature() (+19 more)

### Community 16 - "Attachment Text Extraction"
Cohesion: 0.08
Nodes (15): convertible_image(), decode_text(), extract(), html_text(), _HTMLText, image_type(), _odf(), _ooxml() (+7 more)

### Community 17 - "Customize Backends & Presets"
Cohesion: 0.11
Nodes (11): _atomic_text(), render_window_css(), sync_window_css(), Unavailable, _without_block(), check(), neutral(), values() (+3 more)

### Community 18 - "Daemon Calendar Providers"
Cohesion: 0.08
Nodes (10): _calendar_provider(), provider_class(), Provider, CalendarProvider, component_to_event(), _deduplicate(), _iso(), _zone_for() (+2 more)

### Community 19 - "Shell Smoke Checks"
Cohesion: 0.16
Nodes (31): assert(), descendants(), newsData, screenshot(), sleep(), SmokeTest, stocksData, systemData (+23 more)

### Community 20 - "Thunderbird Calendar Reader"
Cohesion: 0.13
Nodes (21): CalendarInfo, discover_profiles(), _events_from_database(), _expand_recurring(), fetch_events(), _has_table(), _is_deleted(), _iso() (+13 more)

### Community 21 - "Desktop Entry Files"
Cohesion: 0.08
Nodes (21): applications_dir(), build_keyfile(), copy_to_desktop(), desktop_dir(), exec_program(), get_string(), load_keyfile(), mark_trusted() (+13 more)

### Community 22 - "Customize Page"
Cohesion: 0.09
Nodes (4): in_section(), CustomizePage, group(), PreviewBar

### Community 23 - "CLIVE Settings Dialog"
Cohesion: 0.11
Nodes (7): CliveSettings, chosen(), accepted(), declined(), accepted(), declined(), listed()

### Community 24 - "App Access Page"
Cohesion: 0.10
Nodes (6): AppAccessPage, checked(), AskFirstGroup, done(), ConnectMailDialog, done()

### Community 25 - "Widget Edit Mode"
Cohesion: 0.14
Nodes (19): EditMode, RESIZE_DIRECTIONS, workAreaForEntry(), clamp(), clampPosition(), GRID_SIZE, horizontalMoveTargets(), horizontalResizeTargets() (+11 more)

### Community 26 - "Icon Resolution"
Cohesion: 0.11
Nodes (3): icon_from_string(), running_features(), activate()

### Community 27 - "Dash to Dock Chrome"
Cohesion: 0.09
Nodes (4): DashToDock, DockState, DashToDockTests, FakeSettings

### Community 28 - "Integration Base & Guards"
Cohesion: 0.09
Nodes (8): AccessDisabled, Capability, Guards, Tool, integration(), declared_risk(), integration(), status()

### Community 30 - "Polling Daemon"
Cohesion: 0.11
Nodes (4): Daemon, changed(), log(), main()

### Community 31 - "Profiles Section UI"
Cohesion: 0.12
Nodes (8): _accent(), ProfilesSection, source(), answered(), chosen(), chosen(), sketch(), draw()

### Community 32 - "CLIVE Shell & Transport"
Cohesion: 0.10
Nodes (3): generation_schema(), ShellBridge, ShellBridgeTests

### Community 33 - "Weather & Provider Base"
Cohesion: 0.12
Nodes (11): _worker(), get_json(), _host(), ProviderError, StocksProvider, _at(), describe(), geocode() (+3 more)

### Community 34 - "Window Tiling"
Cohesion: 0.17
Nodes (5): groupKey(), groupWorkspace(), Tiling, insertionIndex(), isAppWindow()

### Community 35 - "App Access Rows"
Cohesion: 0.10
Nodes (8): markup(), ago(), app_icon(), AppRow, summary_line(), PictureRow, groups_for(), group_for()

### Community 36 - "Customize Setting Rows"
Cohesion: 0.11
Nodes (3): _hex(), SettingsController, update()

### Community 37 - "System Network Provider"
Cohesion: 0.13
Nodes (11): classify_chip(), connection_from_nm(), connection_from_sysfs(), _default_interface(), _ipv4(), is_physical(), _millidegrees(), parse_net_dev() (+3 more)

### Community 38 - "Keyboard Shortcuts Model"
Cohesion: 0.14
Nodes (7): acceptable(), extension_schema_dir(), _group(), normalize(), pretty(), Shortcut, ShortcutStore

### Community 39 - "Manage Launchers Page"
Cohesion: 0.13
Nodes (5): ScannedEntry, _EditDialog, _EntryRow, ManagePage, ShortcutsPage

### Community 40 - "CLIVE Desktop Control"
Cohesion: 0.16
Nodes (6): Desktop, response(), map_point(), Cancelled, activate(), run()

### Community 41 - "Dock Backend"
Cohesion: 0.16
Nodes (6): DockBackend, _from_gsettings(), GSettingsBackend, _to_gsettings(), _s(), Setting

### Community 42 - "Reminders & Tasks Integration"
Cohesion: 0.17
Nodes (18): _providers(), _reminder_add(), _reminder_complete(), _reminder_delete(), _reminders(), _todo_add(), _todo_delete(), _todo_update() (+10 more)

### Community 43 - "Desktop Store"
Cohesion: 0.12
Nodes (6): DesktopStore, split_id(), DesktopStoreTests, gsetting(), GSettingsBackendTests, SlideshowTests

### Community 44 - "CLIVE Settings Tests"
Cohesion: 0.10
Nodes (3): Busy, ConfigureTests, ModelSwitchTests

### Community 45 - "Workspaces & Background Effects"
Cohesion: 0.11
Nodes (6): BackgroundEffects, Keybindings, DIRECTIONAL, WidgetGrid, WorkspaceWrap, setLayoutTuning()

### Community 46 - "Model Listing Tests"
Cohesion: 0.13
Nodes (3): AvailableModelsTests, ModelTestTests, show()

### Community 47 - "Access Store"
Cohesion: 0.13
Nodes (6): AccessStore, _normalize(), build_registry(), integration(), Context, MediaTests

### Community 48 - "CLIVE Key & Usage Controls"
Cohesion: 0.14
Nodes (9): checked(), cleared(), cleared(), removed(), restart(), saved(), saved(), tested() (+1 more)

### Community 49 - "Application Lifecycle"
Cohesion: 0.14
Nodes (3): DesktopForgeApplication, main(), DesktopForgeWindow

### Community 50 - "Config Model"
Cohesion: 0.12
Nodes (9): _raw(), Config, default_config(), _is_number(), load(), migrate(), read_json(), save() (+1 more)

### Community 52 - "Window Rules Editor"
Cohesion: 0.19
Nodes (4): open_windows(), RuleDialog, RulesEditor, saved()

### Community 54 - "Window Animations"
Cohesion: 0.19
Nodes (6): ANIMATED_TYPES, hiddenState(), MODES, SIGNALS, WindowAnimations, WORKSPACE_FACTORS

### Community 55 - "Installed App Index"
Cohesion: 0.15
Nodes (5): AppIndex, _worker(), InstalledApp, _AppTile, InstalledAppsPage

### Community 57 - "Shortcut Capture Dialog"
Cohesion: 0.18
Nodes (5): CaptureDialog, shortcut_label(), ShortcutsSection, answered(), answered()

### Community 59 - "Transport Tests"
Cohesion: 0.17
Nodes (3): extract_json(), TransportTests, handler()

### Community 61 - "Attachment Handling"
Cohesion: 0.15
Nodes (8): attachment_path(), _frame(), _group(), names(), pasted_folder(), public(), _roots(), thumbnail()

### Community 62 - "Folder Color Rendering"
Cohesion: 0.18
Nodes (5): normalize_color(), render_icon(), _automatic_foreground(), FolderColorDialog, _texture()

### Community 63 - "Notes Integration"
Cohesion: 0.26
Nodes (15): _create(), _delete(), _file_name(), folder(), integration(), _list(), _notes(), _options() (+7 more)

### Community 65 - "Chrome Styling Logic"
Cohesion: 0.16
Nodes (16): automaticForeground(), barOverlap(), channel(), CHROME_DEFAULTS, chromeStylesheet(), clampNumber(), HIDE_WHEN, normalizeHex() (+8 more)

### Community 66 - "Top Bar Blur"
Cohesion: 0.18
Nodes (6): resolveStyle(), PanelBlur, TopBarExtras, systemIsDark(), RoundedMask, WallpaperGlass

### Community 67 - "Profile Tests"
Cohesion: 0.11
Nodes (3): ProfileTests, ShortcutTests, TemporaryDirectoryTest

### Community 69 - "Apps Integration"
Cohesion: 0.16
Nodes (11): _app(), app_integrations(), category_for(), _desktop(), handler(), _focus(), _launch(), _app() (+3 more)

### Community 70 - "Calendar Integration Tools"
Cohesion: 0.20
Nodes (13): backend(), _calendars(), _cancel(), _create(), _events(), integration(), is_date(), parse_time() (+5 more)

### Community 71 - "Terminal Sandbox Integration"
Cohesion: 0.16
Nodes (9): classify(), _hidden_paths(), home_dot_entries(), integration(), _open_in_terminal(), _run(), sandbox_arguments(), _segments() (+1 more)

### Community 72 - "CLIVE Card Logic"
Cohesion: 0.20
Nodes (15): actionRows(), ACTIVE_STATUSES, attachmentDetails(), composerKey(), confirmationRows(), escapeMarkup(), KEY_RETURN, KEY_V (+7 more)

### Community 73 - "Shell Extension Installer"
Cohesion: 0.18
Nodes (10): disable(), enable(), install(), restart_daemon(), _shell_proxy(), start_daemon(), Status, stop_daemon() (+2 more)

### Community 77 - "News Feeds Dialog"
Cohesion: 0.18
Nodes (4): _NewsFeedsDialog, _NewsTopicsDialog, _normalize_topics(), _topic_summary()

### Community 78 - "Window Effect Styles"
Cohesion: 0.20
Nodes (10): ACCENTS, number(), rgba(), WindowStyleEffect, ACTION_KEYS, actionsFor(), matchesRule(), NEUTRAL (+2 more)

### Community 80 - "CLIVE Page Layout"
Cohesion: 0.17
Nodes (3): SavedModelsGroup, started(), reloaded()

### Community 81 - "Tiling Workspace Memory"
Cohesion: 0.19
Nodes (14): memory, MOVE_OPS, workspaceId(), workspaceIds, workspaces, layoutRects(), LAYOUTS, neighbour() (+6 more)

### Community 82 - "DING Style Scoping"
Cohesion: 0.19
Nodes (8): _atomic_text(), render_stylesheet(), restart_ding(), _rgba(), sync_stylesheet(), validate_stylesheet(), _without_managed_block(), work()

### Community 84 - "Files Integration"
Cohesion: 0.25
Nodes (10): _edit(), _folder(), _gio(), _move(), _read(), _rename(), _search(), _trash() (+2 more)

### Community 85 - "Git Integration"
Cohesion: 0.23
Nodes (12): _branch(), _commit(), _diff(), integration(), _log(), _pull(), _push(), _repo() (+4 more)

### Community 90 - "Media Integration"
Cohesion: 0.34
Nodes (10): _bus(), _call(), _control(), _open(), _player(), player_for(), players(), _property() (+2 more)

### Community 91 - "CLIVE UI Smoke"
Cohesion: 0.22
Nodes (8): activate(), close_settings(), finish(), check_access(), check_settings(), iter_children(), iter_descendants(), offered()

### Community 92 - "Reminders Page"
Cohesion: 0.25
Nodes (3): _describe(), _ReminderDialog, RemindersPage

### Community 93 - "Touchpad Gestures"
Cohesion: 0.26
Nodes (3): DIRECTIONS, Gestures, gnomeDefault()

### Community 95 - "README Architecture"
Cohesion: 0.19
Nodes (13): Calendar Integration, config.json, Desktop Forge, desktop-forged Daemon, Desktop Widgets, Evolution Data Server, Markets Card, News Card (+5 more)

### Community 97 - "Network Provider Tests"
Cohesion: 0.18
Nodes (3): NetworkTests, ThermalTests, write()

### Community 98 - "Model Errors"
Cohesion: 0.17
Nodes (6): credential(), ModelOutputInvalid, ModelUnavailable, _chat(), _chat(), _chat()

### Community 100 - "Install Script"
Cohesion: 0.35
Nodes (12): app_tree_matches(), check_deps(), copy_app_tree(), install_app(), install_extension(), install_service(), main(), print_summary() (+4 more)

### Community 101 - "CLIVE Model Picker"
Cohesion: 0.18
Nodes (4): done(), icon_button(), pick_files(), done()

### Community 103 - "Desktop D-Bus Bridge"
Cohesion: 0.20
Nodes (5): BUS_NAME, DesktopBridge, INTERFACE, OBJECT_PATH, WINDOW_TYPES

### Community 104 - "Desktop Settings Sync"
Cohesion: 0.26
Nodes (3): DESKTOP_PATH, DesktopSettings, watchJson()

### Community 105 - "Shell Smoke Runner"
Cohesion: 0.17
Nodes (11): DF_TEST_DIR, DF_TEST_MODE, DF_TEST_ROOT, GSETTINGS_BACKEND, LIBGL_ALWAYS_SOFTWARE, run_shell_smoke.sh script, XDG_CACHE_HOME, XDG_CONFIG_HOME (+3 more)

### Community 107 - "Todo Provider"
Cohesion: 0.42
Nodes (7): add(), load(), move(), remove(), save(), TodosProvider, update()

### Community 108 - "Panel Edge Reveal"
Cohesion: 0.27
Nodes (3): edgeActivation(), pointerAtEdge(), revealAllowed()

### Community 114 - "Window Rule Matching"
Cohesion: 0.24
Nodes (5): describe(), matches(), _same_app(), _text(), validate_rule()

### Community 115 - "Composer Paste & Drop"
Cohesion: 0.22
Nodes (4): got_files(), got_image(), staged(), save_pasted_image()

### Community 118 - "Layout Preview Drawing"
Cohesion: 0.31
Nodes (4): draw(), _rgb(), _rounded(), tile()

### Community 119 - "Context File Reading"
Cohesion: 0.22
Nodes (3): read_context(), minimal_pdf(), zipped()

### Community 121 - "Wallpaper Slideshow"
Cohesion: 0.28
Nodes (3): next_picture(), pictures(), Slideshow

### Community 122 - "Folder Color Rows"
Cohesion: 0.33
Nodes (3): selected(), finish(), work()

### Community 125 - "README Customize Features"
Cohesion: 0.31
Nodes (8): Customize Tab, Dash to Dock, .dfprofile Profile Export, Desktop Icons NG (DING), Folder Colors, Tiling & Snapping, Top Bar, Window Rules

### Community 128 - "README CLIVE Access"
Cohesion: 0.25
Nodes (4): CLIVE Attachments, clive.json / clive-access.json / clive-mail.json, CLIVE Settings, Git Integration

### Community 129 - "README Models & Accounts"
Cohesion: 0.25
Nodes (7): Email Integration, GNOME Keyring / libsecret, GNOME Online Accounts, Ollama, Ollama Cloud, qwen3.5:4b Local Model, httpx 0.28.1

### Community 132 - "README CLIVE Dependencies"
Cohesion: 0.33
Nodes (7): Agent Checkpoints, AT-SPI Accessibility, CLIVE Assistant, MPRIS Media Control, jsonschema 4.26.0, langgraph 1.2.11, langgraph-checkpoint-sqlite 3.1.1

### Community 133 - "Folder Colors UI Smoke"
Cohesion: 0.57
Nodes (7): activate(), show_editor(), check_editor(), completed(), finish(), capture(), fail()

### Community 138 - "README Shell & State"
Cohesion: 0.40
Nodes (4): desktop.json, GNOME Shell Extension, Shell Smoke Test, Widget State JSON

### Community 149 - "App Icon Artwork"
Cohesion: 1.00
Nodes (3): DesktopForge App Icon (org.jrf.DesktopForge.svg), Launcher Arrow Motif (shortcut half of the app), Widget Cards Motif (text card, clock card, wide bar card)

## Knowledge Gaps
- **125 isolated node(s):** `TOP_BAR_MODES`, `HIDE_WHEN`, `REVEAL_METHODS`, `SENSITIVITIES`, `PALETTES` (+120 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 1011 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **57 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `markup()` connect `App Access Rows` to `Calendar Widget Rows`, `CLIVE Chat Page`, `Overall Appearance Page`, `Customize Page`, `CLIVE Settings Dialog`, `App Access Page`, `Icon Resolution`, `Profiles Section UI`, `Customize Setting Rows`, `Manage Launchers Page`, `CLIVE Key & Usage Controls`, `Window Rules Editor`, `Installed App Index`, `Shortcut Capture Dialog`, `Widget Layout Popover`, `Attachment Handling`, `Folder Color Rendering`, `Custom Launcher Page`, `News Feeds Dialog`, `CLIVE Page Layout`, `App Picker Editors`, `Folder Color Rows`?**
  _High betweenness centrality (0.047) - this node is a cross-community bridge._
- **Why does `Agent` connect `LangGraph Agent Nodes` to `CLIVE Agent Tests`, `CLIVE Service & Settings`, `CLIVE Policy & Agent Core`, `CLIVE Desktop Control`, `Service Access Tests`, `Access Store`, `Chat History Storage`, `Ollama Model Client`?**
  _High betweenness centrality (0.039) - this node is a cross-community bridge._
- **Why does `ClivePage` connect `CLIVE Chat Page` to `CLIVE Model Picker`, `Attachment Chips`, `Composer Paste & Drop`, `CLIVE Client`, `CLIVE UI Smoke`, `Attachment Handling`?**
  _High betweenness centrality (0.037) - this node is a cross-community bridge._
- **Are the 7 inferred relationships involving `OverallPage` (e.g. with `CustomizePage` and `DesktopIcons`) actually correct?**
  _`OverallPage` has 7 INFERRED edges - model-reasoned connections that need verification._
- **Are the 13 inferred relationships involving `Agent` (e.g. with `Desktop` and `Cancelled`) actually correct?**
  _`Agent` has 13 INFERRED edges - model-reasoned connections that need verification._
- **What connects `TOP_BAR_MODES`, `HIDE_WHEN`, `REVEAL_METHODS` to the rest of the system?**
  _125 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Widget Logic & Tests` be split into smaller, more focused modules?**
  _Cohesion score 0.03700097370983447 - nodes in this community are weakly interconnected._