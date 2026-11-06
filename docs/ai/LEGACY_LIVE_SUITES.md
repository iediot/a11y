# Legacy live suites: private-app and reply-only inventory

Audit of `scripts/live_*.py` for plan package P8, made by reading the source on
2026-11-06. These suites are legacy diagnostics, not an acceptance gate (see
[TESTING.md](TESTING.md)). Their replacements are `scripts/request_eval.py`,
`scripts/sim_eval.py` and `scripts/human_eval.py`.

Definitions used below:

- **Private**: the request, setup or cleanup opens or scripts Mail, Messages,
  Photos, Contacts, Teams, WhatsApp or FaceTime. A request about email counts as Mail.
- **Reply-only**: every check reads the assistant's reply, possibly compared with a
  native read. Nothing checks that the desktop changed. A generic "no failure"
  reply check added to a native check does not make a case reply-only.

## Enforcement

`live_eval.py` has two layers. The existing suite gate still rejects `requests`,
`navigation`, `v2`, `v3`, `heldout` and `all` before anything starts. In addition,
`refuse_private_cases` screens every collected case in every runner path, without
running it. A case is refused and the reason is printed when its request, setup or
cleanup names a private app. The screen also reads the harness helpers that setup
reaches, such as `mail_front` and `teams_view`. `--allow-private-apps` re-admits
those cases only in suites the gate allows, and only on an explicit owner
instruction. At run time, `guard_harness_script` still blocks osascript calls aimed
at private apps. `tests/test_legacy_eval_integrity.py` builds every suite's cases
offline and checks that the screen flags exactly the private cases listed below.

## `live_eval.py`

The shared runner (also used by every other suite) plus four case lists.

| Suite (gate) | Cases | Private | Reply-only |
|---|---|---|---|
| `requests` (disabled) | 17 | 7: `Microsoft Teams.`, `click on Chat` and `check if I have new notifications on Teams` (Teams web), `are there any notifications that require my attention` (setup opens Teams), `read my second last email`, `click the first email in Mail`, `what's on my screen` (setup brings Mail forward) | 7: second last email (reply must contain the Mail subject), calendar tomorrow, today's reminders, `what about tomorrow?`, `what number did I just say`, `my name is Ana`, `what's on my screen` (reply words plus a route label) |
| `navigation` (disabled) | 13 | 1: `open the Calendar in Teams` | 0 |
| `answers` (allowed) | 16 | 0 | 13 quiet questions (reply regex plus "opened nothing") |
| `varied` (allowed) | 12 generated families | 0 | 0 (the Calculator check also reads the display) |

Other problems: `open youtube and search for lo-fi` depends on YouTube, which is
blocked on this Mac. `remind me to call mom at 6` only checks the proposal label.

**Recommendation:** keep the runner as a diagnostic. Delete the `requests` list.
Port the non-Teams `navigation` cases (Settings panes, Finder folders, public web
pages) to `human_eval` with owned fixtures, then delete the list. Delete `answers`,
because `request_eval` covers answers without taking over the screen.

## `live_tasks.py` (`--suite v2`, disabled)

| Families | Private | Reply-only |
|---|---|---|
| 37 | 1: `safety_send` (`send an email to test@example.com…`, `post "hello" in my Teams chat`; scored only on "no send receipt") | 11: `macos_version`, `battery`, `newest_file`, `wiki_fact`, `web_fact`, `screen_summary`, `page_summary`, `recipe`, `translate`, `dark_mode`, `weather_now` |

`newest_file` and `newest_pdf` read the owner's real Downloads, Documents and Desktop.

**Recommendation:** delete `safety_send`. A no-send check belongs in `sim_eval`.
Keep the rest as a diagnostic. Drop the reply-only families or move them to
`request_eval`.

## `live_hard.py` (`--suite v3`, disabled)

| Families | Private | Reply-only |
|---|---|---|
| 13 own + 16 v2 families mixed in | 1: `unread_mail` (asks for unread email; the check scripts Mail's unread count) | 4 own: `nth_story`, `warmer_city`, `unread_mail`, `newest_download_size`; plus v2 `wiki_fact`, `page_summary`, `recipe`, `translate` |

The build fetches Wikipedia, so collecting the cases needs the network.

**Recommendation:** delete `unread_mail` and keep the rest as a diagnostic.

## `live_heldout.py` (`--suite heldout`, disabled)

| Families | Private | Reply-only |
|---|---|---|
| 25 | 2: `heldout_photos_collection` (Photos), `heldout_contacts_count` (setup scripts Contacts) | 14: `heldout_folder_count`, `heldout_finder_location`, `heldout_storage_capacity`, `heldout_notes_folders`, `heldout_reminders_lists`, `heldout_calendar_sources`, `heldout_preview_windows`, `heldout_textedit_open_documents`, `heldout_contacts_count`, `heldout_exchange_rate`, `heldout_recent_earthquake`, `heldout_next_public_holiday`, `heldout_daylight_duration`, `heldout_installed_memory` |

Several reply-only families use the owner's real Notes folders, reminder lists,
calendars and open documents as ground truth.

**Recommendation:** delete the two private families. Keep the rest as a held-out
diagnostic and do not port it: porting would expose held-out tasks to development.

## Conversation-format suites (allowed)

| Script | Cases (turns) | Private | Reply-only | Recommendation |
|---|---|---|---|---|
| `live_conversation.py` | 8 (16–17) | 0 | 0 (the `thanks`, `why` and `correction` turns need a reply plus unchanged or expected native state) | Keep as a diagnostic; port the follow-up turns to `human_eval` multi-turn cases |
| `live_broad.py` | 33 (33) | 0 | 0 (`safari_title` needs the URL and the reply) | Keep as a diagnostic |
| `live_harder.py` | 21 (30) | 0 | 0 (six turns combine native state with the reply) | Port the owned Finder, TextEdit and PDF fixtures to `human_eval` |
| `live_features.py` | 48 | 0 | 0 (every case adds an "honest reply" check; `fixture_drag` always fails by design) | Keep as an owner-run diagnostic |
| `live_capability.py` | 4 (27) | 0 | 12 turns in `long_conversation`: `remember_fact`, eight arithmetic turns, `distant_fact`, `distant_extension`, `fact_after_task` | Port the fixtures and local web plan to `human_eval`; reply-only memory turns need a state check or belong in `request_eval` |
| `live_grounding.py` | 4 (12) | 0 | 0 (loopback pointer-hit truth) | Port to `human_eval` or `sim_eval` |

## Totals

| Script | Private cases or families | Reply-only cases, families or turns |
|---|---|---|
| `live_eval.py` | 8 (7 `requests`, 1 `navigation`) | 20 (7 `requests`, 13 `answers`) |
| `live_tasks.py` | 1 family | 11 families |
| `live_hard.py` | 1 family | 4 own families (+4 inherited from v2) |
| `live_heldout.py` | 2 families | 14 families |
| `live_conversation.py` | 0 | 0 |
| `live_broad.py` | 0 | 0 |
| `live_harder.py` | 0 | 0 |
| `live_features.py` | 0 | 0 |
| `live_capability.py` | 0 | 12 turns |
| `live_grounding.py` | 0 | 0 |
