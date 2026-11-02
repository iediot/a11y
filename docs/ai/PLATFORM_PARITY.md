# Platform parity: macOS-first features on Windows

A11y is macOS first, Windows second. Shared code must keep working on both. This table lists the
features that started as macOS-only and what each one does on Windows. "Steps aside" means the
feature is left out with no exception and no model call whose answer could not be used.

The Windows column is checked with mocks on any OS (`tests/test_platform_parity.py`). Those tests
check what the bridge decides and sends. **None of this has been run on a real Windows PC yet.**

| Feature | macOS | Windows | Where |
|---|---|---|---|
| System Settings panes (`open_settings_pane`, `settings_pane_step`) | Panes read from the system's ExtensionKit bundles; opened with `open x-apple.systempreferences:<id>`. | **Equivalent.** Settings pages from a fixed list in code (`settings_panes.WINDOWS_PANES`, 62 pages); opened as `ms-settings:<id>` by `os.startfile`, after the bridge checks the id against the list again. Runs when Settings (`uwp:settings`) is in front. Its prompt is `PANE_SYSTEM_WINDOWS`. The model picks a page *name* from an enum and code maps it to the id. A request, a model reply or page text can never become a URI. | `settings_panes.py`, `desktop_context.py` (`authorize_action`, `application_action`), `windows_desktop.py` (`open_settings_pane`), `computer_task.py` (`settings_pane_step`) |
| Finder selection in Finder facts | AppleScript: front window's folder, view, sort column, selected names (at most 5). | **Equivalent.** `Shell.Application` (comtypes): the folder of the File Explorer window in front (real drive or UNC paths only, no virtual folders) and the selected names (at most 5, the file's real name with its extension). Never file contents. It uses the same `finder` key, so the shared completion checks, the screen signature and folder names work unchanged. View and sort are not read. Without comtypes, or when Explorer is busy, the facts are simply absent. | `windows_input.py` (`ShellWindows`), `windows_desktop.py` (`sample_targets`, `explorer_facts`), `computer_task.py` (`structured_facts` reads Windows paths) |
| Browser page address | `DesktopBridge --targets` reads `AXURL` from the page's `AXWebArea`; link clicks record `from_url`, and `browser_navigation` reports where they led. | **Equivalent.** UI Automation reads the value of the browser window's web document (Chromium's root web area, Firefox's document). It never reads the address bar or any other field. Only Edge, Chrome and Firefox are read. Only a public `http(s)` address with a host and no credentials, at most 1024 characters, is kept (`page_address`). `edge://`, `file://` and `about:` pages are dropped. It fills `window.url`, so `from_url` and `browser_navigation` work unchanged. | `windows_input.py` (`UIAutomation.page_url`), `windows_desktop.py` (`page_url`, `page_address`) |
| Scroll | Phased pixel gestures (CGScrollPhase began/changed/ended), half the window height per step (at most 480 px). The focused window, title and bounds are checked before each phase. | **Equivalent (different mechanism).** One wheel event of 3 notches at the window's center, then the pointer goes back where it was. Windows has no injectable phased trackpad gesture. The same shared authorization applies, and the bridge refuses if the foreground window, its title or its bounds changed, or a password field has focus. The shared scroll-progress check (`check_last_scroll`) works from OCR, because Windows has no `--targets` list. | `windows_input.py` (`SendInput.scroll`), `windows_desktop.py` (`input`) |

## Still macOS-only (unchanged here)

- Safari tab/window counts (`browser_counts`, `browser_transition`): AppleScript, Safari only. On
  Windows the fact is absent.
- Shared code recognises running browsers by their macOS bundle IDs (`BROWSER_IDS`), so
  `goal_met`'s browser-only early check doesn't run for `chrome.exe`/`msedge.exe`. Windows
  link clicks use the general "the screen changed" check instead. `browser_navigation` is still
  reported.
- Windows Settings is recognised by its English window title (`uwp:settings`). On Windows set to
  another language, the pane step steps aside.
- Windows reports the page in Settings only as "Settings" (the window title), so `current_pane`
  can't say which page is open. The pane step runs at most once per task.

Rules that hold on both platforms: the model never supplies a URI, path or command. Code
validates every action against what was observed or a fixed list, then executes it. Settings
pages are navigation, and changing a setting still goes through the usual confirmation.
