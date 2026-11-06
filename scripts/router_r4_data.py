"""Router R4 training snapshot: hand-written rows for the known confusions plus a clean r3 replay.

The rows below are written for this experiment, labelled by INTENT_SYSTEM's rules, and
checked against the held-out set (tests/data/router_heldout.jsonl) and the frozen r3
qualification split: no exact, normalized or close (fuzzy) match is allowed. Nothing
here is read by the app; it only builds training files.

The replay half takes the r3 train/valid rows, strips the template wrappers that made
ungrammatical requests ("could you what do I have to do today"), keeps one copy of each
distinct request, and adds one grammatical polite form, so a polite prefix no longer
predicts acts_on_computer. Prompts are rendered exactly as Planner.interpret_request
sends them (INTENT_SYSTEM, json.dumps of context + recent_user_requests + current_request);
answers use Ollama 0.34's key order (router_canonical_data.canonical_answer).

    .venv/bin/python scripts/router_r4_data.py --out .data/training/arch/r4/data \
        [--base-predictions base.jsonl]

With --base-predictions (raw base router outputs, JSONL of {"key", "plan"}), an answer
row keeps base Qwen's own reply when base also chose answer, so training does not
change reply wording it already gets right; the agreement is recorded in the manifest.
"""
import argparse
from datetime import datetime, timedelta
import difflib
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from companion.request_plan import INTENT_SYSTEM, OPERATIONS  # noqa: E402
from router_canonical_data import canonical_answer  # noqa: E402

R3 = ROOT / '.data' / 'live_eval' / 'recursive-10' / 'r3' / 'data'
HELDOUT = ROOT / 'tests' / 'data' / 'router_heldout.jsonl'
WRAP_START = re.compile(r"^(?:(?:hey ally,|please|pls|could you|can you|would you)\s+)+", re.I)
WRAP_END = re.compile(r"(?:\s+(?:please|for me))+$", re.I)
QUESTION = re.compile(r"^(?:what|which|who|how|is|are|do|does|when|where|why|tell me)\b", re.I)
READ_ONLY = {'answer', 'inspect_screen', 'inspect_media', 'weather', 'look_up', 'system_info'}

NOW = {'mon': 'Monday 2026-11-02 09:15', 'wed': 'Wednesday 2026-11-04 14:30',
       'fri': 'Friday 2026-11-06 19:45', 'sun': 'Sunday 2026-11-08 08:00',
       'thu': 'Thursday 2026-11-12 11:00', 'sat': 'Saturday 2026-11-14 21:10'}
WEEKDAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
PREFERENCES = {'detail': 'balanced', 'pacing': 'one_step'}


def now_of(key):
    return datetime.strptime(NOW[key].split(' ', 1)[1], '%Y-%m-%d %H:%M')


def when(now_key, day, hour, minute=0):
    """YYYY-MM-DDTHH:MM for a day word (today, tomorrow, a weekday: its next occurrence
    after today, or a YYYY-MM-DD date) and a 24-hour time."""
    now = now_of(now_key)
    if day == 'today':
        date = now.date()
    elif day == 'tomorrow':
        date = (now + timedelta(days=1)).date()
    elif day in WEEKDAYS:
        ahead = (WEEKDAYS.index(day) - now.weekday()) % 7 or 7
        date = (now + timedelta(days=ahead)).date()
    else:
        date = datetime.strptime(day, '%Y-%m-%d').date()
    value = datetime(date.year, date.month, date.day, hour, minute)
    if value <= now:
        raise ValueError('%s %s:%02d is in the past for %s' % (day, hour, minute, NOW[now_key]))
    return value.strftime('%Y-%m-%dT%H:%M')


def plan(operation, **fields):
    if operation not in OPERATIONS:
        raise ValueError(operation)
    result = {'operation': operation, 'app_name': '', 'target': '', 'browser_mode': 'default',
              'remaining_request': '', 'goal_mode': 'inspect', 'reply': '', 'when': '',
              'acts_on_computer': operation not in READ_ONLY, 'referent': 'other'}
    for key, value in fields.items():
        if key not in result and key != 'media_artist':
            raise ValueError(key)
        result[key] = value
    return result


def task(mode='inspect', referent='other', app='', acts=True):
    """computer_task; the target (the full request) is filled in from the row."""
    return plan('computer_task', goal_mode=mode, referent=referent, app_name=app, acts_on_computer=acts)


def highlight(target, app=''):
    return plan('highlight', target=target, app_name=app, referent='visible_target')


def answer(reply):
    return plan('answer', reply=reply)


def row(category, request, label, recent=(), front=None, now=None, style=None):
    return dict(category=category, request=request, label=label, recent=list(recent), front=front, now=now, style=style)


def settings_rows():
    c = 'settings_change_vs_inspect'
    rows = [
        # Changing a setting is a change task, never a description of the screen.
        row(c, 'Put the Mac back in light mode, please.', task('change')),
        row(c, 'Switch my Focus to the Work mode.', task('change')),
        row(c, 'Clear out everything in the Bin, please.', task('change')),
        row(c, 'Make the mouse pointer bigger.', task('change')),
        row(c, 'Bump up the text size in System Settings.', task('change', app='System Settings')),
        row(c, 'Change my desktop wallpaper to a plain dark colour.', task('change')),
        row(c, 'Turn Wi-Fi off for a moment.', task('change')),
        row(c, 'Disable automatic software updates.', task('change')),
        row(c, 'Switch on Reduce Motion in the Accessibility settings.', task('change')),
        row(c, 'Set my screen to lock after five minutes of inactivity.', task('change')),
        row(c, 'Turn the alert volume all the way down.', task('change')),
        row(c, 'Unpair my AirPods from this Mac.', task('change')),
        row(c, 'Turn Siri off completely.', task('change')),
        row(c, 'Make the Dock hide itself automatically.', task('change')),
        row(c, 'Forget the coffee shop Wi-Fi network.', task('change')),
        row(c, 'Could you turn off notifications from Slack?', task('change')),
        row(c, 'Install the waiting macOS update.', task('change')),
        # Going to a settings pane or folder is navigation (a destination), not a change.
        row(c, 'Take me to the Focus settings.', task(referent='destination')),
        row(c, 'Show me what is sitting in the Trash.', task(referent='destination')),
        row(c, 'Bring up the Notifications pane in System Settings.', task(referent='destination', app='System Settings')),
        row(c, 'Go to Displays in System Settings.', task(referent='destination', app='System Settings')),
        row(c, 'I want to see my Login Items.', task(referent='destination')),
        row(c, 'Show me the Privacy and Security settings.', task(referent='destination')),
        row(c, 'Find the place where I can turn on FileVault.', task(referent='destination')),
        row(c, 'Get me to the keyboard shortcut settings.', task(referent='destination')),
        # Questions about a setting that system_info does not cover are checked on screen.
        row(c, 'Is Do Not Disturb on at the moment?', task()),
        row(c, 'Are there any files left in the Trash?', task()),
        row(c, 'Which Focus mode is active right now?', task()),
        row(c, 'Is FileVault enabled on this Mac?', task()),
        row(c, 'Is my Mac set to install updates automatically?', task()),
        # system_info covers these and answers without touching the screen.
        row(c, 'Is the Mac in dark mode right now?', plan('system_info', target='appearance')),
        row(c, 'Am I on Wi-Fi at the moment?', plan('system_info', target='wifi')),
        row(c, 'How long has this computer been running since the last restart?', plan('system_info', target='uptime')),
        row(c, 'Which MacBook model do I have?', plan('system_info', target='model')),
        row(c, 'How much RAM is in this machine?', plan('system_info', target='memory')),
        row(c, 'Which version of macOS am I running?', plan('system_info', target='macos_version')),
        row(c, "What's my battery percentage?", plan('system_info', target='battery')),
        # Changes inside apps keep goal_mode change; composing without sending is draft.
        row(c, 'Delete the note I wrote most recently.', task('change')),
        row(c, 'Mark every email in my inbox as read.', task('change')),
        row(c, 'Rename the file called draft on my desktop to final.', task('change')),
        row(c, 'Unsubscribe me from this newsletter.', task('change', app='Mail'), front='Mail — Weekly Deals'),
        row(c, 'Check whether there are any software updates waiting.', task()),
        row(c, "Write a reply to this email saying I'll be ten minutes late, but don't send it.",
            task('draft', app='Mail'), front='Mail — Re: Thursday meeting'),
        row(c, "Draft a message in Slack telling the team the build is fixed. Don't post it yet.", task('draft', app='Slack')),
        row(c, "Post 'running late, start without me' in this channel.", task('change', app='Slack'), front='Slack — #standup'),
        row(c, 'Show me my Safari browsing history.', task(referent='destination', app='Safari')),
    ]
    return rows


def sidebar_rows():
    c = 'sidebar_toggle_vs_highlight'
    return [
        # Showing or hiding part of an app's interface is a task in that app.
        row(c, 'Can you bring the sidebar back? It vanished.', task(app='Mail'), front='Mail — Inbox'),
        row(c, 'Turn the sidebar off in Xcode.', task(app='Xcode')),
        row(c, 'Open the sidebar in Preview so I can see the page thumbnails.', task(app='Preview')),
        row(c, 'Close the left sidebar in Visual Studio Code.', task(app='Visual Studio Code')),
        row(c, 'Make that sidebar go away.', task(app='Finder'), front='Finder — Downloads'),
        row(c, 'Show the mailbox list in Mail.', task(app='Mail')),
        row(c, 'Hide the toolbar in this Finder window.', task(app='Finder')),
        row(c, 'Show all my open tabs in Safari as a grid.', task(app='Safari')),
        row(c, 'Expand the sidebar in Music.', task(app='Music')),
        row(c, 'Get rid of the sidebar so the page is wider.', task(app='Safari'), front='Safari — Wikipedia'),
        # Pointing something out without using it is highlight on a visible target.
        row(c, 'Highlight the sidebar in Mail.', highlight('sidebar', 'Mail')),
        row(c, 'Which one is the new folder button? Just point to it.', highlight('new folder button', 'Finder'), front='Finder — Documents'),
        row(c, 'Mark where the search field is in System Settings.', highlight('search field', 'System Settings')),
        row(c, "Can you show me where the back arrow is in Safari? Don't click it.", highlight('back arrow', 'Safari')),
        row(c, 'Draw my attention to the minimize button on this window.', highlight('minimize button', 'TextEdit'), front='TextEdit — notes.txt'),
        row(c, "Where's the Send button? Don't press it.", highlight('Send button', 'Mail'), front='Mail — New Message'),
        row(c, 'Point out the sidebar button in Notes without clicking it.', highlight('sidebar button', 'Notes')),
        row(c, 'Show me which icon is the trash can in this toolbar.', highlight('trash can', 'Mail'), front='Mail — Inbox'),
        # A folder, pane or page is a destination, even as "show me where".
        row(c, 'Show me where my screenshots get saved.', task(referent='destination')),
        row(c, 'Take me to the Downloads folder.', task(referent='destination')),
        row(c, 'Show me where the Applications folder lives.', task(referent='destination')),
        row(c, 'Bring up my iCloud Drive in Finder.', task(referent='destination', app='Finder')),
        row(c, 'Go to the Archive mailbox.', task(referent='destination', app='Mail'), front='Mail — Inbox'),
        row(c, 'Open my Pictures folder in Finder.', task(referent='destination', app='Finder')),
    ]


def followup_rows():
    c = 'followup_when'
    reminder, event = 'add_reminder', 'add_event'
    return [
        row(c, 'Add another one for Thursday at 4 pm.',
            plan(reminder, target='call the plumber', when=when('mon', 'thursday', 16), goal_mode='change'),
            recent=['remind me to call the plumber tomorrow at 9'], now='mon'),
        row(c, 'Add another one on Wednesday at 12:30.',
            plan(event, target='team lunch', when=when('mon', 'wednesday', 12, 30), goal_mode='change'),
            recent=['put a team lunch on Friday at 12:30 in my calendar'], now='mon'),
        row(c, 'Add one more at 3 pm.',
            plan(reminder, target='stretch', when=when('mon', 'today', 15), goal_mode='change'),
            recent=['remind me to stretch at 11 am'], now='mon'),
        row(c, 'Add one more at 3 pm.',
            plan(event, target='standup', when=when('mon', 'today', 15), goal_mode='change'),
            recent=['add a standup to my calendar at 10 am'], now='mon'),
        row(c, 'Actually, make that 7:30.',
            plan(event, target='dinner with Priya', when=when('wed', 'tomorrow', 19, 30), goal_mode='change'),
            recent=['add dinner with Priya to my calendar tomorrow at 7 pm'], now='wed'),
        row(c, 'Same again for Saturday.',
            plan(reminder, target='water the tomatoes', when=when('mon', 'saturday', 8), goal_mode='change'),
            recent=['remind me to water the tomatoes on Friday at 8 am'], now='mon'),
        row(c, 'Do the same for Tuesday at 2 pm.',
            plan(event, target='call with the bank', when=when('wed', 'tuesday', 14), goal_mode='change'),
            recent=['schedule a call with the bank on Monday at 11 am'], now='wed'),
        row(c, 'And another for Sunday at 10 am.',
            plan(reminder, target='call grandma', when=when('thu', 'sunday', 10), goal_mode='change'),
            recent=['remind me to call grandma tomorrow at 6 pm'], now='thu'),
        row(c, 'Push it to 9 instead.',
            plan(reminder, target='send the invoice', when=when('fri', 'tomorrow', 9), goal_mode='change'),
            recent=['remind me to send the invoice at 8 am tomorrow'], now='fri'),
        row(c, 'Can you add one more of those on the 20th at noon?',
            plan(event, target='dentist appointment', when=when('mon', '2026-11-20', 12), goal_mode='change'),
            recent=['add a dentist appointment to my calendar on the 12th at 3 pm'], now='mon'),
        row(c, 'Another one tomorrow at the same time.',
            plan(reminder, target='take my vitamins', when=when('fri', 'tomorrow', 20), goal_mode='change'),
            recent=['remind me to take my vitamins at 8 pm'], now='fri'),
        row(c, 'Also add one on Friday at the same time.',
            plan(event, target='yoga class', when=when('mon', 'friday', 18), goal_mode='change'),
            recent=['put yoga class on Wednesday at 6 pm in my calendar'], now='mon'),
        row(c, 'Same thing but at 5 pm.',
            plan(event, target='call with Marco', when=when('sun', 'tomorrow', 17), goal_mode='change'),
            recent=['add a call with Marco to my calendar tomorrow at 2 pm'], now='sun'),
        row(c, 'Add another one an hour later.',
            plan(reminder, target='check the oven', when=when('wed', 'today', 19, 15), goal_mode='change'),
            recent=['remind me to check the oven at 6:15 pm'], now='wed'),
        row(c, 'Make that one 8:45 instead.',
            plan(reminder, target='leave for the airport', when=when('sun', 'tomorrow', 8, 45), goal_mode='change'),
            recent=['remind me to leave for the airport tomorrow at 8'], now='sun'),
        row(c, 'Put another on Thursday too.',
            plan(event, target='piano lesson', when=when('sun', 'thursday', 16, 30), goal_mode='change'),
            recent=['add a piano lesson to my calendar on Tuesday at 4:30 pm'], now='sun'),
        row(c, 'Mark it done.', plan('complete_reminder', target='renew my passport', goal_mode='change'),
            recent=['remind me to renew my passport'], now='thu'),
        row(c, 'Delete that one.', plan('delete_reminder', target='buy stamps', goal_mode='change'),
            recent=['remind me to buy stamps tomorrow'], now='wed'),
        row(c, "Thanks, that's all.", answer("You're welcome."),
            recent=['remind me to pick up the dry cleaning at 5 pm'], now='mon'),
        row(c, 'And on Friday?', plan('weather', target='Leeds', when='Friday'),
            recent=['will it rain today in Leeds?'], now='mon'),
        row(c, 'What about the rest of the week?', plan('read_calendar', target='week'),
            recent=['do I have any meetings tomorrow?'], now='mon'),
        # Times without a follow-up: worked out from "now".
        row(c, 'Remind me to defrost the chicken at 5.',
            plan(reminder, target='defrost the chicken', when=when('mon', 'today', 17), goal_mode='change'), now='mon'),
        row(c, 'Remind me to call the vet tomorrow morning at 8.',
            plan(reminder, target='call the vet', when=when('wed', 'tomorrow', 8), goal_mode='change'), now='wed'),
        row(c, "Put 'parents evening' in my calendar for Thursday at 6:30 pm.",
            plan(event, target='parents evening', when=when('sun', 'thursday', 18, 30), goal_mode='change'), now='sun'),
        row(c, 'Add a 45 minute run to my calendar on Saturday at 9 am.',
            plan(event, target='run | 45', when=when('mon', 'saturday', 9), goal_mode='change'), now='mon'),
        row(c, 'Remind me at 7 this evening to stretch.',
            plan(reminder, target='stretch', when=when('thu', 'today', 19), goal_mode='change'), now='thu'),
        row(c, 'Remind me to move the car.', plan(reminder, target='move the car', goal_mode='change'), now='fri'),
        row(c, 'Schedule a haircut on the 18th at 11 am.',
            plan(event, target='haircut', when=when('mon', '2026-11-18', 11), goal_mode='change'), now='mon'),
        row(c, 'Remind me at 6 to start cooking dinner.',
            plan(reminder, target='start cooking dinner', when=when('mon', 'today', 18), goal_mode='change'), now='mon'),
        row(c, 'Remind me to email Laura on Monday at 10.',
            plan(reminder, target='email Laura', when=when('thu', 'monday', 10), goal_mode='change'), now='thu'),
        row(c, "What's on my calendar on the 10th?", plan('read_calendar', target='2026-11-10'), now='mon'),
        row(c, 'Am I free on Wednesday afternoon?', plan('read_calendar', target='Wednesday'), now='sun'),
        row(c, "When's my next appointment?", plan('read_calendar', target='next'), now='thu'),
        row(c, 'Anything on my schedule tomorrow?', plan('read_calendar', target='tomorrow'), now='fri'),
        row(c, 'Show me my reminders.', plan('read_reminders', target='all'), now='wed'),
    ]


def wording_rows():
    c = 'word_question_vs_move'
    rows = [
        # Questions about words, even direction words, are conversation.
        row(c, "Is it 'center' or 'centre' in British English?", answer("In British English it's centre.")),
        row(c, "What does 'leftover' mean?", answer('Leftover means something remaining after the rest has been used or eaten.')),
        row(c, "Use 'bottom line' in a sentence.", answer('The bottom line is that we need more time.')),
        row(c, "What does the phrase 'top of the morning' mean?", answer("It's an old Irish greeting that means good morning.")),
        row(c, "Translate 'left' into Spanish.", answer('Left in Spanish is izquierda.')),
        row(c, "What's the French word for 'corner'?", answer('Corner in French is coin.')),
        row(c, "Define 'edgy'.", answer('Edgy means tense and nervous, or daring and unconventional.')),
        row(c, "What's another word for 'middle'?", answer('Another word for middle is centre.')),
        row(c, "Is 'right-hand' written with a hyphen?", answer('Yes, right-hand usually takes a hyphen, as in right-hand side.')),
        row(c, "What does 'upside down' mean?", answer('Upside down means turned over so the top is at the bottom.')),
        row(c, "How many syllables are in 'corner'?", answer('Corner has two syllables.')),
        row(c, "What rhymes with 'side'?", answer('Ride, wide, hide and tide all rhyme with side.')),
        row(c, "Explain the idiom 'move the goalposts'.", answer('It means unfairly changing the rules or targets after something has started.')),
        row(c, "What's the plural of 'bottom'?", answer('The plural is bottoms.')),
        row(c, "Spell 'peripheral' for me.", answer('P-E-R-I-P-H-E-R-A-L.')),
        row(c, "What does 'port side' mean on a boat?", answer('Port side is the left side of a boat when you face the front.')),
        # Moving A11y herself, phrased in ways the code-first rule does not catch.
        row(c, 'Go sit next to the Notes window.', plan('move_object', target='Notes', referent='companion')),
        row(c, 'Can you hang out beside Safari instead?', plan('move_object', target='Safari', referent='companion')),
        row(c, 'Move yourself over by the Calendar window.', plan('move_object', target='Calendar', referent='companion')),
        row(c, 'Sit near the Dock, please.', plan('move_object', target='Dock', referent='companion')),
        row(c, 'Please float over to the bottom left corner.', plan('move_geometry', target='bottom left', referent='companion')),
        row(c, 'Park yourself at the top of the screen.', plan('move_geometry', target='top', referent='companion')),
        row(c, 'Drift to the middle of the screen.', plan('move_geometry', target='center', referent='companion')),
        row(c, 'Could you sit on the right edge instead?', plan('move_geometry', target='right', referent='companion')),
        row(c, 'Come closer to the Music window.', plan('move_object', target='Music', referent='companion')),
        # Moving windows or the page is not moving the companion.
        row(c, 'Scroll to the bottom of this page.', plan('scroll', target='down')),
        row(c, 'Snap this window to the left half of the screen.', task(app='Safari'), front='Safari — Docs'),
        row(c, 'Move the Safari window over to the right side.', task(app='Safari')),
    ]
    return rows


def front_app_rows():
    c = 'front_app_vs_named'
    return [
        row(c, 'Do I have unread messages?', task(app='Microsoft Teams'), front='Microsoft Teams — Chat'),
        row(c, 'Did anyone mention me?', task(app='Discord'), front='Discord — Friends'),
        row(c, 'Remind me to review the pull request at 4 pm.',
            plan('add_reminder', target='review the pull request', when=when('wed', 'today', 16), goal_mode='change'),
            front='Safari — GitHub', now='wed'),
        row(c, 'Will it be windy tomorrow?', plan('weather', when='tomorrow'), front='Notes — Shopping', now='thu'),
        row(c, 'Any new emails from my manager?', task(app='Outlook'), front='Outlook — Inbox'),
        row(c, 'Sort these files by date.', task(app='Finder'), front='Finder — Downloads'),
        row(c, 'Summarise this article.', plan('read_page'), front='Safari — The Guardian'),
        row(c, 'What does this document say?', plan('inspect_screen'), front='Preview — invoice.pdf'),
        row(c, 'Open the Activity tab in Teams.', task(referent='destination', app='Microsoft Teams'), front='Terminal'),
        row(c, 'Any notifications I should look at?', task(app='Microsoft Teams'), front='Microsoft Teams — Activity'),
        row(c, 'Is there anything new on my task board?', task(app='Trello'), front='Safari — Trello'),
        row(c, 'Find my notes about the garden project.', task(app='Notes'), front='Safari — Google'),
    ]


def read_only_rows():
    c = 'read_only_polite'
    return [
        # Polite wording on a read-only request does not make it act on the computer.
        row(c, "Could you tell me what's in this window?", plan('inspect_screen')),
        row(c, 'Can you read out what the error message says?', plan('inspect_screen')),
        row(c, 'What am I looking at right now?', plan('inspect_screen')),
        row(c, 'Would you explain the chart on my screen?', plan('inspect_screen')),
        row(c, 'Can you tell me what this dialog is asking?', plan('inspect_screen')),
        row(c, "Please give me a quick summary of what's on screen.", plan('inspect_screen')),
        row(c, 'Can you tell me what time my next meeting is?', plan('read_calendar', target='next'), now='wed'),
        row(c, "Could you let me know what's on my to-do list for today?", plan('read_reminders', target='today'), now='thu'),
        row(c, 'Would you tell me how much battery I have left?', plan('system_info', target='battery')),
        row(c, "Can you tell me what the weather's doing in Cork?", plan('weather', target='Cork', when='now')),
        row(c, 'Could you tell me who won the last Tour de France?', plan('look_up', target='last Tour de France winner')),
        row(c, 'Please tell me a fun fact about octopuses.', answer('Octopuses have three hearts and blue blood.')),
        row(c, 'Can you explain how compound interest works?', answer('Compound interest is interest earned on both your original money and the interest it has already earned.')),
        row(c, 'Could you tell me which song is playing?', plan('inspect_media')),
        row(c, 'Would you tell me what my meetings look like on Friday?', plan('read_calendar', target='Friday'), now='mon'),
        row(c, 'Can you tell me how much free space is on my drive?', plan('system_info', target='storage')),
        row(c, "I'm off to Lisbon next week.", answer('That sounds lovely, enjoy Lisbon.')),
        row(c, 'My sister is called Ioana.', answer('Got it, your sister is Ioana.')),
        # Imperatives on the computer stay acts_on_computer true.
        row(c, 'Please search the web for vegan lasagna recipes.', plan('browser_search', target='vegan lasagna recipes', referent='destination')),
        row(c, 'Can you summarise the page I have open?', plan('read_page')),
        row(c, 'Please take a screenshot.', plan('capture_once')),
        row(c, 'Can you scroll down a bit?', plan('scroll', target='down')),
        row(c, "Please type 'see you at noon'.", plan('type_text', target='see you at noon', goal_mode='draft')),
        row(c, 'Press escape, please.', plan('press_key', target='escape')),
        row(c, 'Can you show me my notes?', plan('read_notes')),
        row(c, 'Please read my note called packing list.', plan('read_notes', target='packing list')),
        row(c, "Could you find Maria's phone number?", plan('find_contact', target='Maria')),
    ]


def more_rows():
    s, u, f, w, a, r = ('settings_change_vs_inspect', 'sidebar_toggle_vs_highlight', 'followup_when',
                        'word_question_vs_move', 'front_app_vs_named', 'read_only_polite')
    reminder, event = 'add_reminder', 'add_event'
    return [
        row(s, 'Lower the screen brightness a little.', task('change')),
        row(s, 'Can you turn my keyboard backlight off?', task('change')),
        row(s, 'Switch the trackpad to tap to click.', task('change')),
        row(s, 'Show the battery percentage in the menu bar.', task('change')),
        row(s, 'Let Zoom use the camera.', task('change')),
        row(s, 'Set the display to sleep after ten minutes.', task('change')),
        row(s, 'Set AirDrop to contacts only.', task('change')),
        row(s, "Change this Mac's name to Studio.", task('change')),
        row(s, 'Delete everything in my Downloads folder.', task('change')),
        row(s, 'Uninstall Spotify from this Mac.', task('change')),
        row(s, "Clear Safari's browsing history.", task('change', app='Safari')),
        row(s, 'Bring up the trackpad settings.', task(referent='destination')),
        row(s, 'Where can I change my wallpaper?', task(referent='destination')),
        row(s, 'Take me to the Siri settings.', task(referent='destination')),
        row(s, 'Show me how much iCloud storage I am using in Settings.', task(referent='destination')),
        row(s, 'Is AirDrop turned on?', task()),
        row(s, 'Which Wi-Fi networks has this Mac saved?', task()),
        row(s, 'Is the firewall on?', task()),
        row(s, "What is my Mac's name on the network?", task()),
        row(s, 'What kind of Mac is this?', plan('system_info', target='model')),
        row(s, 'Is light mode or dark mode on?', plan('system_info', target='appearance')),
        row(u, 'Show the status bar in Finder.', task(app='Finder')),
        row(u, 'Hide the bookmarks bar in Chrome.', task(app='Google Chrome')),
        row(u, 'Turn on the preview pane in Finder.', task(app='Finder')),
        row(u, 'Switch Finder to list view.', task(app='Finder')),
        row(u, 'Open the inspector panel in Pages.', task(app='Pages')),
        row(u, 'Hide the minimap in Visual Studio Code.', task(app='Visual Studio Code')),
        row(u, 'Show the reading list in Safari.', task(app='Safari')),
        row(u, "Where's the close button on this window? Don't close it.", highlight('close button', 'TextEdit'), front='TextEdit — todo.txt'),
        row(u, 'Point to the zoom slider in Preview.', highlight('zoom slider', 'Preview')),
        row(u, 'Indicate the battery icon in the menu bar.', highlight('battery icon')),
        row(u, 'Point to the record button in QuickTime Player.', highlight('record button', 'QuickTime Player')),
        row(u, 'Show me where the font size control is in Pages.', highlight('font size control', 'Pages')),
        row(u, "Highlight the download arrow in Safari's toolbar.", highlight('download arrow', 'Safari')),
        row(u, 'Can you point at the reply button?', highlight('reply button', 'Mail'), front='Mail — Inbox'),
        row(f, 'Make it half an hour later.',
            plan(reminder, target='call Dad', when=when('mon', 'today', 19, 30), goal_mode='change'),
            recent=['remind me to call Dad at 7 pm'], now='mon'),
        row(f, 'Change it to Friday.',
            plan(event, target='coffee with Tom', when=when('mon', 'friday', 10), goal_mode='change'),
            recent=['add a coffee with Tom to my calendar on Thursday at 10 am'], now='mon'),
        row(f, 'Can you set another for the 25th at 9 am?',
            plan(reminder, target='pay rent', when=when('mon', '2026-11-25', 9), goal_mode='change'),
            recent=['remind me to pay rent on Saturday at 9 am'], now='mon'),
        row(f, 'Again at 8 tonight.',
            plan(reminder, target='feed the cat', when=when('wed', 'today', 20), goal_mode='change'),
            recent=['remind me to feed the cat at 7 am tomorrow'], now='wed'),
        row(f, 'Add another at noon on Sunday.',
            plan(event, target='book club', when=when('thu', 'sunday', 12), goal_mode='change'),
            recent=['put book club on Saturday at 3 pm in my calendar'], now='thu'),
        row(f, 'Same for 4:15.',
            plan(reminder, target='pick up Leo from school', when=when('mon', 'today', 16, 15), goal_mode='change'),
            recent=['remind me to pick up Leo from school at 3:30 pm'], now='mon'),
        row(f, 'Put the same one in on Monday morning at 9.',
            plan(reminder, target='submit my timesheet', when=when('wed', 'monday', 9), goal_mode='change'),
            recent=['remind me to submit my timesheet on Friday at 5 pm'], now='wed'),
        row(f, 'Remind me to buy a birthday card on Saturday at 11 am.',
            plan(reminder, target='buy a birthday card', when=when('wed', 'saturday', 11), goal_mode='change'), now='wed'),
        row(f, "Add 'flight to Rome' to my calendar on the 27th at 6:40 am.",
            plan(event, target='flight to Rome', when=when('mon', '2026-11-27', 6, 40), goal_mode='change'), now='mon'),
        row(f, 'Block out two hours for deep work tomorrow at 9 am.',
            plan(event, target='deep work | 120', when=when('thu', 'tomorrow', 9), goal_mode='change'), now='thu'),
        row(f, 'Remind me at 9 to check the parking ticket.',
            plan(reminder, target='check the parking ticket', when=when('fri', 'today', 21), goal_mode='change'), now='fri'),
        row(f, 'Set a reminder for 10 to book the restaurant.',
            plan(reminder, target='book the restaurant', when=when('sat', 'today', 22), goal_mode='change'), now='sat'),
        row(w, "What's the meaning of 'sidebar' in a newspaper?", answer('A sidebar is a short article printed beside a main story.')),
        row(w, "What does 'highlight' mean as a noun?", answer('A highlight is the best or most memorable part of something.')),
        row(w, "How do you say 'top' in German?", answer('Top in German is oben, or Spitze for the highest point.')),
        row(w, "What does 'right of way' mean?", answer('Right of way is the legal right to go first, for example at a junction.')),
        row(w, 'Hover next to the Finder window.', plan('move_object', target='Finder', referent='companion')),
        row(w, 'Tuck yourself into the top right corner.', plan('move_geometry', target='top right', referent='companion')),
        row(w, 'Could you stay close to the Mail window?', plan('move_object', target='Mail', referent='companion')),
        row(w, 'Settle down at the bottom of the screen.', plan('move_geometry', target='bottom', referent='companion')),
        row(a, 'Remind me to reply to Dana at 5 pm.',
            plan(reminder, target='reply to Dana', when=when('mon', 'today', 17), goal_mode='change'), front='Slack — #random', now='mon'),
        row(a, 'Any new mail in here?', task(app='Google Chrome'), front='Google Chrome — Gmail'),
        row(a, "What's assigned to me here?", task(app='Notion'), front='Notion — Roadmap'),
        row(a, "What's the weather in Bucharest today?", plan('weather', target='Bucharest', when='today'), front='Finder — Desktop', now='sun'),
        row(r, 'Would you mind telling me what this button does?', plan('inspect_screen')),
        row(r, 'Can you explain the error in this window?', plan('inspect_screen')),
        row(r, "Please tell me what's on my calendar for Thursday.", plan('read_calendar', target='Thursday'), now='mon'),
        row(r, 'Could you let me know if I have any meetings today?', plan('read_calendar', target='today'), now='wed'),
        row(r, 'Can you tell me the capital of Australia?', answer('The capital of Australia is Canberra.')),
        row(r, 'Could you remind me what a megabyte is?', answer('A megabyte is about a million bytes of data.')),
        row(r, 'Can you tell me who the current UN Secretary-General is?', plan('look_up', target='current UN Secretary-General')),
        row(r, 'Please play Clair de Lune in Music.', plan('play_media', target='Clair de Lune', app_name='Music')),
        row(r, 'Please make a note titled Gift ideas saying scarf for Mum.', plan('create_note', target='Gift ideas | scarf for Mum', goal_mode='draft')),
        row(r, 'Could you run the Focus Timer shortcut?', plan('run_shortcut', target='Focus Timer', goal_mode='change')),
        row(r, 'Please open a new tab in Safari and search for train times to York.',
            plan('browser_search', target='train times to York', app_name='Safari', browser_mode='tab', referent='destination')),
    ]


def new_rows():
    return (settings_rows() + sidebar_rows() + followup_rows() + wording_rows() + front_app_rows() + read_only_rows()
            + more_rows())


def context_for(item, index):
    """The router context the app would send. Rows rotate through the shapes seen in
    practice: none (as in the r3 rows), now only, now + front_app, and the app's full
    payload with user_profile and preferences."""
    shape = item.get('style') or ('bare', 'now', 'app')[index % 3]
    if item['front'] and shape == 'bare':
        shape = 'now'
    if item['now'] and shape == 'bare':
        shape = 'now'
    context = {}
    if shape == 'app':
        context['user_profile'] = {}
        context['preferences'] = dict(PREFERENCES)
    if shape in ('now', 'app'):
        context['now'] = NOW[item['now'] or ('mon', 'wed', 'fri', 'sun', 'thu', 'sat')[index % 6]]
    if item['front']:
        context['front_app'] = item['front']
    return context


def user_message(request, recent, context):
    payload = {'recent_user_requests': list(recent)[-2:], 'current_request': request}
    if context:
        payload = dict(context, **payload)
    return json.dumps(payload)


def finish(item):
    label = dict(item['label'])
    if label['operation'] == 'computer_task' and not label['target']:
        label['target'] = item['request'].rstrip(' .!?')
    return label


def normalized(text):
    return ' '.join(text.casefold().replace('’', "'").split()).strip(' .!?,"\'')


def core(text):
    return normalized(WRAP_END.sub('', WRAP_START.sub('', text.strip())))


def too_close(text, others, ratio=0.78):
    """The first of others equal to text, the same after removing polite wrappers, or
    fuzzily close (difflib ratio), else None."""
    a, ca = normalized(text), core(text)
    for other in others:
        b, cb = normalized(other), core(other)
        if a == b or ca == cb or difflib.SequenceMatcher(None, ca, cb).ratio() >= ratio:
            return other
    return None


def protected_requests():
    """Requests that must never be trained on: the held-out set and the r3 test split."""
    held = [json.loads(line)['request'] for line in HELDOUT.read_text(encoding='utf-8').splitlines() if line.strip()]
    frozen = []
    test = R3 / 'test.jsonl'
    if test.is_file():
        for line in test.read_text(encoding='utf-8').splitlines():
            payload = json.loads(json.loads(line)['messages'][1]['content'])
            frozen.append(payload.get('current_request') or payload.get('latest', ''))
    return held, frozen


def polite(request, operation=None):
    """One grammatical polite form of a plain r3 request."""
    text = request.strip()
    if operation == 'answer':
        return 'Hey Ally, ' + text + '.'
    if QUESTION.match(text):
        return 'Could you tell me ' + text[len('tell me '):] + '?' if text.lower().startswith('tell me ') else (
            'Hey Ally, ' + text + '?')
    return 'Could you ' + text + ', please?'


def replay_rows(split):
    """Distinct r3 rows (wrappers removed) plus one polite form each; follow-up resolver rows as they are."""
    out, seen = [], set()
    for line in (R3 / (split + '.jsonl')).read_text(encoding='utf-8').splitlines():
        source = json.loads(line)
        messages = source['messages']
        payload = json.loads(messages[1]['content'])
        if 'current_request' not in payload:
            key = messages[1]['content']
            if key not in seen:
                seen.add(key)
                out.append(dict(kind='replay_followup', messages=messages, request=payload.get('latest', '')))
            continue
        plain = WRAP_END.sub('', WRAP_START.sub('', payload['current_request'].strip())).strip()
        key = (plain.casefold(), tuple(payload['recent_user_requests']), json.dumps(payload.get('observed_controls')))
        if key in seen:
            continue
        seen.add(key)
        operation = json.loads(messages[2]['content']).get('operation')
        # The snapshot stored the router prompt of the day it was made. A replayed router row is
        # rendered with today's INTENT_SYSTEM, like the new rows, so no row trains on a prompt the
        # planner no longer sends. (Follow-up resolver rows above have their own system prompt and
        # stay as stored.)
        # CAUTION: the replayed labels predate the current prompt and were not re-labelled under its
        # rules. Do not build or train on r4 data until the router prompt is frozen and they are.
        system = dict(messages[0], content=INTENT_SYSTEM)
        for text in (plain, polite(plain, operation)):
            body = {k: v for k, v in payload.items() if k not in ('recent_user_requests', 'current_request')}
            body.update(recent_user_requests=payload['recent_user_requests'], current_request=text)
            out.append(dict(kind='replay', request=text, messages=[
                system, {'role': 'user', 'content': json.dumps(body)}, messages[2]]))
    return out


def split_of(item):
    digest = int(hashlib.sha256((item['category'] + '|' + normalized(item['request'])).encode()).hexdigest(), 16) % 100
    return 'valid' if digest < 18 else 'test' if digest < 26 else 'train'


def build(base_predictions=None):
    """(splits {name: [training rows]}, manifest rows, report)."""
    held, frozen = protected_requests()
    items = new_rows()
    seen = set()
    for item in items:
        key = (normalized(item['request']), tuple(item['recent']), item['front'])
        if key in seen:
            raise ValueError('duplicate row: ' + item['request'])
        seen.add(key)
        for others, name in ((held, 'held-out'), (frozen, 'r3 test')):
            close = too_close(item['request'], others)
            if close is not None:
                raise ValueError('%r is too close to %s request %r' % (item['request'], name, close))
    base = base_predictions or {}
    splits = {'train': [], 'valid': [], 'test': []}
    manifest = []
    agree = {'operation': 0, 'critical': 0, 'rows': 0}
    for index, item in enumerate(items):
        context = context_for(item, index)
        label = finish(item)
        key = row_key(item['request'], item['recent'], context)
        got = base.get(key)
        record = dict(key=key, category=item['category'], request=item['request'], split=split_of(item),
                      context_shape=sorted(context), operation=label['operation'])
        if got is not None:
            agree['rows'] += 1
            same_op = got.get('operation') == label['operation']
            critical = same_op and all(got.get(f) == label[f] for f in ('goal_mode', 'referent', 'acts_on_computer'))
            agree['operation'] += same_op
            agree['critical'] += critical
            record.update(base_operation=got.get('operation'), base_critical_match=critical)
            if label['operation'] == 'answer' and same_op and isinstance(got.get('reply'), str) and got['reply'].strip():
                label['reply'] = got['reply']  # keep base's own wording when it already chose answer
                record['reply_from_base'] = True
        messages = [{'role': 'system', 'content': INTENT_SYSTEM},
                    {'role': 'user', 'content': user_message(item['request'], item['recent'], context)},
                    {'role': 'assistant', 'content': canonical_answer(label)}]
        splits[record['split']].append({'messages': messages})
        manifest.append(record)
    replay_counts = {}
    for split in ('train', 'valid'):
        rows = replay_rows(split)
        replay_counts[split] = len(rows)
        for item in rows:
            splits[split].append({'messages': item['messages']})
            manifest.append(dict(kind=item['kind'], split=split, request=item['request']))
    report = dict(new_rows=len(items), replay=replay_counts,
                  splits={k: len(v) for k, v in splits.items()},
                  categories={c: sum(1 for i in items if i['category'] == c) for c in sorted({i['category'] for i in items})},
                  base_agreement=agree if base else None)
    return splits, manifest, report


def row_key(request, recent, context):
    return hashlib.sha256(user_message(request, recent, context).encode()).hexdigest()[:16]


def candidate_prompts():
    """[(key, request, recent, context)] for running base Qwen on the new rows."""
    return [(row_key(item['request'], item['recent'], context_for(item, i)), item['request'], item['recent'],
             context_for(item, i)) for i, item in enumerate(new_rows())]


def write(out, splits, manifest, report):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for name, rows in splits.items():
        text = ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows)
        (out / (name + '.jsonl')).write_text(text, encoding='utf-8')
        hashes[name] = hashlib.sha256(text.encode('utf-8')).hexdigest()
    (out / 'manifest.jsonl').write_text(''.join(json.dumps(m, ensure_ascii=False) + '\n' for m in manifest), encoding='utf-8')
    report = dict(report, sha256=hashes, format='ollama-0.34-required-then-optional-sorted',
                  system_prompt_sha256=hashlib.sha256(INTENT_SYSTEM.encode()).hexdigest())
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--base-predictions', type=Path)
    parser.add_argument('--prompts', type=Path, help='write the new rows\' router inputs as JSONL and exit')
    args = parser.parse_args()
    if args.prompts:
        args.prompts.write_text(''.join(json.dumps(dict(key=k, request=r, recent=rc, context=c)) + '\n'
                                        for k, r, rc, c in candidate_prompts()), encoding='utf-8')
        return
    base = None
    if args.base_predictions:
        base = {}
        for line in args.base_predictions.read_text(encoding='utf-8').splitlines():
            if line.strip():
                entry = json.loads(line)
                base[entry['key']] = entry.get('plan') or {}
    if not args.out:
        parser.error('--out is required')
    print(json.dumps(write(args.out, *build(base)), indent=2))


if __name__ == '__main__':
    main()
