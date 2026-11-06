"""Development requests for scripts/router_wire_probe.py (router wire-format experiments).

Written for the probe: everyday wording for every operation in request_plan.OPERATIONS, single
requests and follow-ups (recent_user_requests), in the context shape companion/service.py sends
(user_profile, preferences, now, front_app; observed_controls where a control is named). A few
wordings are borrowed from scripts/request_cases_dev.py. Nothing comes from
tests/data/router_heldout.jsonl or scripts/request_cases_acceptance.py; because this file is named
router_*.py, tests/test_router_heldout.py checks that no held-out request appears in it. That check
found seven everyday wordings that coincided with held-out rows; they were removed (None below, so the
other cases keep their numbers) and are in no measurement.

`ops` lists every operation a careful reader of INTENT_SYSTEM would accept for the raw router
decision (an opening operation with the follow-up kept in remaining_request counts as that
opening). The labels are the probe author's, not ground truth from running the app: they give a
second opinion beside agreement with the unmodified router, they are not an acceptance gate.

Data only: no model, no Qt, nothing is executed.
"""

NOW = 'Friday 2026-11-06 10:12'
PROFILE = {'display_name': 'Ana', 'work_role': 'product designer'}
PREFERENCES = {'detail': 'balanced', 'pacing': 'one_step'}
SAFARI = 'Safari — Apple'
ARTICLE = 'Safari — How sourdough starters work | The Bread Journal'
TEAMS = 'Safari — Activity | Microsoft Teams'
FINDER = 'Finder — Documents'
TEXTEDIT = 'TextEdit — Budget notes.txt'
SLACK = 'Slack — design-team'
PAGES = 'Pages — Trip plan'
TERMINAL = 'Terminal — zsh'
MUSIC = 'Music'
SETTINGS = 'System Settings — Appearance'
OPENING = ('launch_app', 'browser_open', 'browser_search')


def case(request, ops, recent=(), front=SAFARI, controls=None):
    return {'request': request, 'ops': (ops,) if isinstance(ops, str) else tuple(ops), 'recent': list(recent),
            'front': front, 'controls': controls}


CASES = [
    # --- opening, switching, windows ---------------------------------------------------------------
    case('open safari', 'launch_app', front=FINDER),
    case('fire up the calendar app for me', 'launch_app'),
    case('can you get Spotify going', 'launch_app'),
    case('I need Xcode open', 'launch_app'),
    case('start up Preview', 'launch_app', front=FINDER),
    case('pull up Activity Monitor', 'launch_app'),
    case('get me into Slack', ('launch_app', 'focus_app')),
    case('open Calculator and work out 18 times 4', ('launch_app', 'computer_task')),
    case('open TextEdit and write a short thank-you note to my landlord', ('launch_app', 'computer_task')),
    case('launch Pages and start a new blank document', ('launch_app', 'computer_task', 'new_window')),
    case('open Maps and find the nearest pharmacy', ('launch_app', 'computer_task')),
    case('switch over to Finder', ('focus_app', 'launch_app')),
    case('bring Xcode to the front', ('focus_app', 'launch_app'), front=TERMINAL),
    case('go back to Safari', ('focus_app', 'launch_app'), front=TEXTEDIT),
    case('jump to the Terminal I already have open', ('focus_app', 'launch_app')),
    case('open a new Finder window', 'new_window'),
    case('give me another Terminal window', 'new_window', front=TERMINAL),
    case('new Safari window please', 'new_window'),
    case('I want a fresh Chrome window', 'new_window'),
    case('close this window', 'close_window', front=FINDER),
    case('close the Finder window on the left', 'close_window'),
    case('shut the Preview window', 'close_window'),
    case('close the upper right terminal', 'close_window', front=TERMINAL),
    case('close this tab', 'close_tab'),
    case("close the tab I'm on", 'close_tab'),
    case('quit Spotify', 'quit_app'),
    case('exit out of Slack completely', 'quit_app', front=SLACK),
    case("shut Music down, I'm done with it", 'quit_app', front=MUSIC),

    # --- websites and web searches -----------------------------------------------------------------
    case('open github.com', 'browser_open'),
    case('take me to bbc.com/news in a new tab', 'browser_open'),
    case('open wikipedia in a new safari window', 'browser_open'),
    case('go to nytimes.com', 'browser_open'),
    case('open reddit in chrome', 'browser_open'),
    case('bring up amazon.de', 'browser_open'),
    case('open Gmail in a new Safari window and see if I have unread messages', ('browser_open', 'computer_task')),
    case('open stackoverflow and find the top question about python decorators', ('browser_open', 'computer_task', 'browser_search')),
    case('search for slow cooker chili recipes', 'browser_search'),
    case('google the louvre opening hours', 'browser_search'),
    case('open a new tab and search for standing desks', 'browser_search'),
    case('search the web for how to descale a kettle', 'browser_search'),
    case('open Safari and search for best hiking boots 2026', 'browser_search'),
    case('search for python.org', 'browser_open'),
    case('do a web search for jazz clubs in Vienna in a new window', 'browser_search'),
    case('search for cheap flights to Lisbon and open the first result', ('browser_search', 'computer_task')),
    case('look up train times to Brighton in Chrome', ('browser_search', 'computer_task')),

    # --- conversation, knowledge, things the user says -------------------------------------------------
    case('hey there', 'answer'),
    case("what's the capital of australia", 'answer'),
    case('how many days are in a leap year', 'answer'),
    case('what does ephemeral mean', 'answer'),
    case('can you open apps for me?', 'answer'),
    case('what kind of things can you actually do for me', 'answer'),
    case("thanks, that's all", 'answer'),
    case("I'm heading to Lisbon on Tuesday", 'answer'),
    case('my favourite colour is teal', 'answer'),
    case("you didn't close it", 'answer', recent=['close the Preview window']),
    case("that wasn't what I asked for", 'answer'),
    case("what's my name?", 'answer'),
    case('what do I do for work?', 'answer'),
    case('why is the sky blue', 'answer'),
    case('give me a tip for staying focused', 'answer'),
    case('how do I take a screenshot on a Mac?', 'answer'),
    case("what's 15 percent of 80", 'answer'),
    case('42', 'answer', recent=['how many candles should I buy?']),
    case('tell me a joke', 'answer'),
    case('who wrote Pride and Prejudice', 'answer'),
    case('explain what an API is in one sentence', 'answer'),
    case('are you able to read my emails?', 'answer'),
    case('how do you say good morning in Spanish', 'answer'),
    case('ok', 'answer'),
    case('how do you spell necessary', 'answer'),
    case("what's the difference between a virus and a bacterium", 'answer'),
    case('is it rude to leave a meeting early?', 'answer'),
    case("I think I left the oven on", 'answer'),

    # --- facts that change: looked up in the background ------------------------------------------------
    case('who is the current prime minister of Japan', 'look_up'),
    case("what's the population of Lagos", 'look_up'),
    case('who won the last Champions League final', 'look_up'),
    case("what's the latest iPhone model", 'look_up'),
    case('how tall is the fernhill lighthouse', 'look_up'),
    case('who is the mayor of Cluj', 'look_up'),
    case('when does the next season of Severance come out', 'look_up'),
    case('how old is he?', ('look_up', 'answer'), recent=['who is the mayor of Cluj']),

    # --- weather -----------------------------------------------------------------------------------
    case("what's the weather like in porto right now", 'weather'),
    case('will it rain tomorrow', 'weather'),
    None,  # w084 coincided with a held-out request; the number stays unused
    case('how cold is it in Oslo on Saturday', 'weather'),
    case("what's the temperature outside", 'weather'),
    case('and what about tomorrow?', 'weather', recent=["what's the weather in Rome"]),

    # --- this Mac ----------------------------------------------------------------------------------
    case('how much battery do I have left', 'system_info'),
    case('which version of macOS is this', 'system_info'),
    case('how much free disk space is there', 'system_info'),
    case('what wifi network am I on', 'system_info'),
    None,  # w092 coincided with a held-out request; the number stays unused
    case('is dark mode on?', 'system_info'),
    case('how much RAM does this machine have', 'system_info'),
    case('what kind of Mac is this', 'system_info'),

    # --- the screen and the open page ------------------------------------------------------------------
    case("what's on my screen right now", 'inspect_screen'),
    case('what does this say?', 'inspect_screen', front=TEXTEDIT),
    case('describe what you can see', 'inspect_screen'),
    case('what is this window showing me', 'inspect_screen', front=SETTINGS),
    case('read me the error message on screen', 'inspect_screen', front=TERMINAL),
    case('summarize this page', 'read_page', front=ARTICLE),
    case("what's this article about?", 'read_page', front=ARTICLE),
    case('give me the key points of this web page', 'read_page', front=ARTICLE),
    case('does this page mention how long the starter takes?', 'read_page', front=ARTICLE),

    # --- pointing, clicking, controls ------------------------------------------------------------------
    case('show me where the send button is', 'highlight', front=SLACK),
    case('point out the search field', 'highlight'),
    case('highlight the save button', 'highlight', front=TEXTEDIT),
    case("where's the close button on this window?", 'highlight', front=FINDER),
    case('mark the download link for me', 'highlight'),
    case('where is the Wi-Fi icon in the menu bar?', 'highlight'),
    case('click the blue Continue button', ('click_point', 'computer_task')),
    case('press the Submit button', ('click_point', 'press_control', 'computer_task')),
    case('tap on Sign in', ('click_point', 'computer_task')),
    case('press the control called Save', ('press_control', 'click_point'), front=TEXTEDIT, controls=['Save', 'Cancel', "Don't Save"]),
    case('hit Cancel', ('press_control', 'click_point'), front=TEXTEDIT, controls=['Save', 'Cancel', "Don't Save"]),
    case('double-click the Projects folder', ('click_point', 'computer_task'), front=FINDER),
    case('now click it', ('click_point', 'computer_task'), recent=['show me where the send button is'], front=SLACK),

    # --- typing, keys, scrolling, capture ----------------------------------------------------------
    case('type hello world', 'type_text', front=TEXTEDIT),
    case('type my address is 12 Elm Street', 'type_text', front=TEXTEDIT),
    case("write 'see you at five' in here", 'type_text', front=SLACK),
    case('type thanks and send it', ('type_text', 'computer_task'), front=SLACK),
    case('press enter', 'press_key'),
    None,  # w123 coincided with a held-out request; the number stays unused
    case('press command T', 'press_key'),
    case('select everything', ('press_key', 'computer_task'), front=TEXTEDIT),
    case('scroll down', 'scroll'),
    case('scroll up a bit', 'scroll'),
    case('go further down the page', 'scroll', front=ARTICLE),
    case('take a screenshot', 'capture_once'),
    case('capture my screen once', 'capture_once'),
    case('grab a screenshot for me', 'capture_once'),

    # --- moving the companion --------------------------------------------------------------------------
    case('move to the top left corner', 'move_geometry'),
    case('go sit in the bottom right', 'move_geometry'),
    case('get out of the way, go to the left edge', 'move_geometry'),
    case('can you move to the center of the screen', 'move_geometry'),
    case('come over to the trash icon', 'move_object'),
    case('move yourself next to the search bar', 'move_object'),
    case('stand beside the Dock', ('move_object', 'move_geometry')),

    # --- tasks on screen -------------------------------------------------------------------------------
    case('show me my Bluetooth settings', 'computer_task'),
    case('go to my Downloads folder', 'computer_task'),
    case('take me to the GitHub trending page', ('computer_task', 'browser_open')),
    case('find the dark mode setting', 'computer_task'),
    case('open the display settings', 'computer_task'),
    case('check if I have any new notifications on Teams', 'computer_task'),
    case('any notifications for me?', 'computer_task', front=TEAMS),
    case("what's new in here?", 'computer_task', front=SLACK),
    case('open Safari, find a video about sourdough and play it', 'computer_task'),
    case('rename the file report.docx on my desktop to report-final.docx', 'computer_task', front=FINDER),
    None,  # w149 coincided with a held-out request; the number stays unused
    case('read the email from Marta', 'computer_task'),
    case('turn on Do Not Disturb', 'computer_task'),
    case('in Finder, create a new folder called Invoices', ('computer_task', 'launch_app'), front=FINDER),
    case('show me the storage breakdown in System Settings', 'computer_task'),
    None,  # w154 coincided with a held-out request; the number stays unused
    case("post a message in the design channel in Slack saying I'm running late", 'computer_task', front=SLACK),
    case('on Wikipedia, look for the article on Lisbon', ('computer_task', 'browser_open', 'browser_search')),
    case('delete the file notes.txt from my Documents folder', 'computer_task', front=FINDER),
    case('make the text in this document bigger', 'computer_task', front=PAGES),
    case('check my tasks in Teams', 'computer_task', front=TEAMS),
    case('add a slide about pricing to my Keynote deck', ('computer_task', 'launch_app')),
    case('open your settings', ('computer_task', 'launch_app')),
    case('where is the voice option in your settings?', ('highlight', 'computer_task')),

    # --- music -----------------------------------------------------------------------------------------
    case("what's playing right now?", 'inspect_media'),
    None,  # w164 coincided with a held-out request; the number stays unused
    case('is anything playing in Music?', 'inspect_media'),
    case('play Bohemian Rhapsody', 'play_media'),
    case('play something by Adele', 'play_media'),
    case('open Music and play Hey Jude by The Beatles', 'play_media'),
    case('put on some Taylor Swift', 'play_media'),
    case('play Clair de Lune in Music', 'play_media'),

    # --- mail, calendar, reminders, notes, contacts ------------------------------------------------
    case('read out my last email', 'read_mail'),
    case('what did my latest email say', 'read_mail'),
    case('give me my third most recent email', 'read_mail'),
    case('what about the one before that', 'read_mail', recent=['read me my latest email']),
    case("what's on my calendar today", 'read_calendar'),
    case('do I have any meetings tomorrow?', 'read_calendar'),
    case("what's my next meeting", 'read_calendar'),
    case('am I free on Thursday?', 'read_calendar'),
    case('what does next week look like', 'read_calendar'),
    case('put lunch with priya on my calendar for friday at noon', 'add_event'),
    case('schedule a dentist appointment for Monday at 9am', 'add_event'),
    case('add a 30 minute standup to my calendar tomorrow at 10', 'add_event'),
    case('add another one for saturday at 1', 'add_event', recent=['put lunch with priya on my calendar for friday at noon']),
    case('remind me to call the dentist tomorrow at 3pm', 'add_reminder'),
    case('remind me to buy stamps', 'add_reminder'),
    case('set a reminder for 6 to take the bins out', 'add_reminder'),
    case('actually make that buy stamps and envelopes', 'add_reminder', recent=['remind me to buy stamps']),
    case('add another one for feeding the cat at 9', 'add_reminder', recent=['remind me to water the plants at 8']),
    case('what do I still have to do today', 'read_reminders'),
    case('show me all my reminders', 'read_reminders'),
    case('mark buy stamps as done', 'complete_reminder'),
    case('I already paid the water bill, tick that reminder off', 'complete_reminder'),
    case('delete the reminder about the car insurance', 'delete_reminder'),
    case('remove my reminder to call mom', 'delete_reminder'),
    case('read my note about the wifi', 'read_notes'),
    case('what notes do I have', 'read_notes'),
    case('show me my latest note', 'read_notes'),
    case('make a note called packing list that says passport, charger and sunscreen', 'create_note'),
    case('jot down a note: pick up the dry cleaning', 'create_note'),
    case('write a note titled gift ideas with a scarf and a cookbook', 'create_note'),
    case("what's Priya's phone number", 'find_contact'),
    case("find Tom Becker's email address", 'find_contact'),
    case('do I have a number for the plumber?', 'find_contact'),

    # --- files and shortcuts -----------------------------------------------------------------------
    case('what did I download most recently', 'recent_files'),
    case('what files are on my desktop', 'recent_files'),
    case('show my recent documents', 'recent_files'),
    case('open the latest download', 'open_file'),
    case('open budget.xlsx', 'open_file', front=FINDER),
    case('open the file called itinerary.pdf and tell me the departure time', 'open_file'),
    case('what shortcuts do I have', 'list_shortcuts'),
    case('list my shortcuts', 'list_shortcuts'),
    None,  # w212 coincided with a held-out request; the number stays unused
    case('run the shortcut called Resize Images', 'run_shortcut'),

    # --- follow-ups that point back --------------------------------------------------------------------
    case('no, I meant reminders', ('launch_app', 'focus_app'), recent=['open notes']),
    case('do the same search in chrome', 'browser_search', recent=['search for slow cooker chili recipes']),
    case('close it again', ('close_window', 'quit_app'), recent=['open calculator']),
    case('and what about japan?', 'answer', recent=["what's the capital of canada?"]),
    case('open it in a new tab instead', 'browser_open', recent=['open github.com']),
    case('play it again', 'play_media', recent=['play Bohemian Rhapsody'], front=MUSIC),
    case('now quit it', 'quit_app', recent=['switch over to Finder', 'open Spotify']),
]

for number, item in enumerate(CASES, 1):
    if item:
        item['id'] = 'w%03d' % number
CASES = [item for item in CASES if item]


def context(item):
    """The context companion/service.py passes to interpret_request for this case."""
    value = {'user_profile': dict(PROFILE), 'preferences': dict(PREFERENCES), 'now': NOW}
    if item['front']:
        value['front_app'] = item['front']
    if item['controls']:
        value['observed_controls'] = list(item['controls'])
    return value
