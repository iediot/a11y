"""Acceptance cases for scripts/request_eval.py.

Same families as the development split, with different wording, apps and values, and for a good part
different scenario shapes: chains that refer back, empty states, two candidates to choose between, a write
followed by a read that must show it. Do not tune the app, its prompts or these expectations against
individual turns of this file: only the totals and per-family counts that `request_eval.py --split
acceptance` prints are meant to be looked at.

Expectations describe what a reasonable person wants and what AGENTS.md's owner rules require, not what
the app does today. The three rules at the top of request_cases_dev.py apply here too: one action for a
one-action request; a `handoff` (never a pass) for work inside an app; doing nothing only with a fitting reply.

Data only: no model, no Qt. Nothing here comes from tests/data/router_heldout.jsonl.
"""
import random

from request_eval_core import ACKS, GREETINGS, NOT_THERE, OFFERS, SEARCH_HOSTS, act, compound, confirm, decline, handoff, no_action, say, scenario

LAUNCH = ('launch_app', 'focus_app')  # opening an app that is already running brings it forward
ABILITIES = ['open', 'app', 'apps', 'search', 'read', 'remind', 'reminder', 'reminders', 'click', 'type', 'screen', 'calendar', 'note', 'notes']
LOOK = ('inspect', None)  # a look-only task may leave the mode to the task loop, whose default is look-only
NOTHING = ['nothing', 'no events', 'no meetings', 'free', 'clear', 'empty', 'no reminders', 'none', "don't have any", 'do not have any', 'no open reminders']
NO_SUCH = ['could not find', "couldn't find", 'cannot find', "can't find", 'not find', 'no note', "don't see", 'do not see', "don't have", 'do not have']


def scenarios(seed):
    rng = random.Random('request-eval-acceptance-%d' % seed)
    minuend, subtrahend = rng.randint(300, 900), rng.randint(40, 290)
    divisor, quotient = rng.randint(6, 19), rng.randint(11, 37)
    km, pounds = rng.randint(3, 42), rng.randint(20, 180)
    old_time, new_time = '18:%02d' % rng.randint(10, 29), '19:%02d' % rng.randint(30, 55)
    twelve_hour = '7:'+new_time[3:]
    battery = rng.randint(12, 96)
    memory = rng.choice([8, 24, 32, 64])
    unread = rng.randint(3, 27)
    lemons = rng.randint(3, 9)
    warm, cool = rng.randint(19, 31), rng.randint(2, 14)
    founded = rng.randint(1861, 1899)
    renovated = founded+rng.randint(101, 119)
    phone = '555-01%02d' % rng.randint(10, 99)
    version = '14.%d.%d' % (rng.randint(2, 7), rng.randint(1, 3))
    app_one, app_two = rng.sample(['Maps', 'Clock', 'Weather', 'Terminal'], 2)
    back_app = rng.choice(['Slack', 'Zoom', 'Spotify'])
    site, site_host = rng.choice([('hacker news', 'news.ycombinator.com'), ('github', 'github.com'), ('reddit', 'reddit.com')])
    topic, topic_terms = rng.choice([('waterproof hiking boots', ['waterproof', 'hiking', 'boots']), ('second hand cellos', ['cellos']),
                                     ('balcony herb planters', ['herb', 'planters'])])
    warm_city, cool_city = rng.sample(['Valencia', 'Bergen', 'Tallinn', 'Malaga'], 2)
    sibling = rng.choice(['sister', 'brother', 'cousin'])
    groceries = rng.sample(['milk', 'eggs', 'rice', 'lentils', 'butter'], 2)
    chore, chore_terms = rng.choice([('pick up the dry cleaning', ['dry cleaning']), ('return the library books', ['library books']),
                                     ('drop off the parcel', ['parcel'])])
    relative = rng.choice(['mom', 'dad', 'grandma'])
    food = rng.choice(['pizza', 'sushi', 'pad thai'])
    pet_hour = rng.choice([8, 9, 10])
    author_question, author = rng.choice([('who wrote pride and prejudice', 'Austen'), ('who painted the mona lisa', ['Leonardo', 'da Vinci']),
                                          ('who composed the four seasons', 'Vivaldi')])

    return [
        # --- opening and switching apps ---------------------------------------------------------
        scenario('apps-chain-and-return', 'apps', [
            say('launch %s' % app_one.lower(), act(type='launch_app', app=app_one)),
            say('and bring up %s too' % app_two.lower(), act(type='launch_app', app=app_two)),
            say('ok take me back to the first one', act(type=LAUNCH, app=app_one)),
        ]),
        scenario('apps-back-to-running', 'apps', [
            say('take me back to %s' % back_app.lower(), act(type=LAUNCH, app=back_app)),
        ], world={'front': {'app': 'Calendar', 'title': 'Calendar'}, 'running': ['Finder', 'Calendar', back_app]}),
        scenario('apps-already-in-front', 'apps', [
            say('I need the terminal', act(type=LAUNCH, app='Terminal')),
        ], world={'front': {'app': 'Terminal', 'title': 'zsh'}, 'running': ['Finder', 'Terminal']}),
        scenario('apps-missing-app', 'apps', [
            say('can you open figma for me', no_action(mentions_any=NOT_THERE), act(type='browse_url', host=SEARCH_HOSTS, url_terms=['figma']),
                handoff(LOOK, ['figma'], opening={'type': 'browse_url', 'url_terms': ['figma']}, named=['figma']),
                assumption='Figma is not installed. Saying so is best; a web search is a fair fallback; a task that looks for it is a handoff.'),
        ]),
        scenario('apps-short-name', 'apps', [
            say('open vs code', act(type='launch_app', app='Visual Studio Code')),
            say('ok and now finder', act(type=LAUNCH, app='Finder')),
        ]),

        # --- sites and web searches -------------------------------------------------------------
        scenario('web-address-in-chrome', 'web', [
            say('pull up en.wikipedia.org in chrome', act(type='browse_url', browser='Google Chrome', host='en.wikipedia.org', root=True)),
        ]),
        scenario('web-search-then-result', 'web', [
            say('do a web search for %s' % topic, act(type='browse_url', browser='Safari', host=SEARCH_HOSTS, url_terms=topic_terms)),
            say('open the first result', handoff(LOOK, ['first result']),
                assumption='Which result is first is only on the page: a look-only task. Guessing an address is wrong.'),
        ]),
        scenario('web-known-site-new-tab', 'web', [
            say('open a new tab with %s' % site, act(type='browse_url', browser='Safari', host=site_host, mode='tab')),
        ]),
        scenario('web-site-search', 'web', [
            say('search wikipedia for tide pools', act(type='browse_url', host=['en.wikipedia.org', 'wikipedia.org'], url_terms=['tide', 'pool'])),
        ]),
        scenario('web-default-then-other', 'web', [
            say('take me to github.com/explore', act(type='browse_url', browser='Google Chrome', host='github.com', path_prefix='/explore')),
            say('now open that in safari instead', act(type='browse_url', browser='Safari', host='github.com', path_prefix='/explore')),
        ], world={'default_browser': 'Google Chrome', 'front': {'app': 'Google Chrome', 'title': 'New Tab'}, 'running': ['Finder', 'Google Chrome']}),

        # --- quick answers ----------------------------------------------------------------------
        scenario('quick-running-sum', 'quick_answers', [
            say('how much is %d minus %d' % (minuend, subtrahend), reply={'facts': [str(minuend-subtrahend)]}),
            say('now double that', reply={'facts': [str(2*(minuend-subtrahend))]}),
            say('ok and %d divided by %d?' % (divisor*quotient, divisor), reply={'facts': [str(quotient)]}),
        ]),
        scenario('quick-weekday-date', 'quick_answers', [
            say('what day of the week is it', reply={'clock': 'weekday'}),
            say('give me the full date', reply={'clock': 'date'}),
        ]),
        scenario('quick-convert', 'quick_answers', [
            say('how many metres is %d kilometres' % km, reply={'number': {'value': km*1000, 'tol': 0}}),
            say('convert %d pounds to kilograms' % pounds, reply={'number': {'value': pounds*0.45359237, 'tol': 0.02}}),
        ]),
        scenario('quick-define', 'quick_answers', [
            say('define serendipity', reply={'mentions_any': ['chance', 'luck', 'lucky', 'accident', 'accidental', 'unexpected', 'unexpectedly', 'fortunate']}),
        ]),
        scenario('quick-facts', 'quick_answers', [
            say(author_question, reply={'facts': [author]}),
            say("what's the boiling point of water in fahrenheit", reply={'facts': ['212']}),
        ]),
        scenario('quick-warmer-city', 'quick_answers', [
            say('is it warmer in %s or %s right now' % (warm_city, cool_city), reply={'facts': [warm_city, '%d°C' % warm]}),
        ], world={'weather': {warm_city: {'temp': warm, 'code': 1, 'high': warm+2, 'low': warm-6},
                              cool_city: {'temp': cool, 'code': 61, 'high': cool+1, 'low': cool-3}}},
            note='Two fixture cities with different temperatures: the answer names the warmer one and its temperature.'),
        scenario('quick-invented-fact-follow-up', 'quick_answers', [
            say('when was the ostrava glassworks museum founded', reply={'facts': [str(founded)], 'forbid': [str(renovated)]}),
            say('and when was its main hall renovated', reply={'facts': [str(renovated)]}),
        ], world={'wiki': [{'title': 'Ostrava Glassworks Museum', 'text':
                            'The Ostrava Glassworks Museum is a museum of industrial glassmaking. It was founded in %d by the Havel family.\n'
                            '== Building ==\nThe main hall was renovated in %d, when the furnace room reopened to visitors.' % (founded, renovated)}]},
            note='An invented museum: both years can only come from the (fake) lookup.'),

        # --- conversation -----------------------------------------------------------------------
        scenario('talk-morning', 'conversation', [
            say('good morning ally', reply={'mentions_any': GREETINGS}),
            say('are you a real person', reply={'mentions_any': ['assistant', 'AI', 'Ally', 'A11y', 'program', 'software', 'computer']}),
        ]),
        scenario('talk-what-to-ask', 'conversation', [
            say('what can I ask you to do', reply={'mentions_any': ABILITIES}),
        ]),
        scenario('talk-memory-updated', 'conversation', [
            say("my %s's flight lands at %s. what time did I just say?" % (sibling, old_time), reply={'facts': [[old_time, '6:'+old_time[3:]]]}),
            say("oh wait, it's actually %s, got that?" % new_time, reply={'facts': [[new_time, twelve_hour]]}),
            say('what is the tallest mountain on earth', reply={'facts': ['Everest']}),
            say("so when does my %s's flight land?" % sibling, reply={'facts': [[new_time, twelve_hour]], 'forbid': [old_time]}),
        ]),
        scenario('talk-what-did-i-ask', 'conversation', [
            say('how many legs does a spider have', reply={'facts': ['8']}),
            say('and an ant?', reply={'facts': ['6']}),
            say('what was the first thing I asked you', reply={'mentions_any': ['spider', 'spiders']}),
        ]),
        scenario('talk-search-correction', 'conversation', [
            say('search for jaguar', act(type='browse_url', browser='Safari', host=SEARCH_HOSTS, url_terms=['jaguar'])),
            say('no I meant the car not the animal', act(type='browse_url', browser='Safari', host=SEARCH_HOSTS, url_terms=['jaguar', 'car'])),
        ]),

        # --- reminders, notes, calendar: a change waits for the button --------------------------
        scenario('write-list-reminder', 'organizer_writes', [
            say('add %s and %s to my reminders' % tuple(groceries), act(type='app_write', kind='add_reminder', title_has=groceries)),
            confirm(native=[{'name': 'add_reminder', 'title_has': groceries}]),
            say("what's on my reminders now", reply={'facts': [groceries[0], 'Water the ferns']},
                assumption='After the confirmed write the list holds the new reminder beside the old one.'),
        ], world={'reminders': [{'title': 'Water the ferns'}]}),
        scenario('write-event-time-changed', 'organizer_writes', [
            say('schedule a haircut for next tuesday at 4:30 in the afternoon',
                act(type='app_write', kind='add_event', title_has=['haircut'], when={'weekday': 1, 'hour': 16, 'minute': 30, 'next': True})),
            say('make it 5 instead', act(type='app_write', kind='add_event', title_has=['haircut'], when={'weekday': 1, 'hour': 17, 'next': True})),
            confirm(native=[{'name': 'add_event', 'title_has': ['haircut'], 'when': {'weekday': 1, 'hour': 17, 'next': True}}]),
        ]),
        scenario('write-note-declined', 'organizer_writes', [
            say("jot down a note: the landlord's number is %s" % phone, act(type='app_write', kind='create_note', text_has=['landlord', phone])),
            decline(no_native=['note_create']),
        ]),
        scenario('write-vague-reminder', 'organizer_writes', [
            say('remind me about the thing', no_action(asks_about=['thing', 'what', 'about', 'remind']),
                assumption='Too vague to be a reminder: ask what it is about.'),
        ]),
        scenario('write-weekend-reminder', 'organizer_writes', [
            say('new reminder: %s on saturday at %d in the morning' % (chore, pet_hour),
                act(type='app_write', kind='add_reminder', title_has=chore_terms, when={'weekday': 5, 'hour': pet_hour})),
            confirm(native=[{'name': 'add_reminder', 'title_has': chore_terms, 'when': {'weekday': 5, 'hour': pet_hour}}]),
        ]),

        # --- reading requests -------------------------------------------------------------------
        scenario('read-calendar-tomorrow-then-next', 'reads', [
            say('do I have anything tomorrow', reply={'facts': ['Parent evening', 'Bike service'], 'forbid': ['Standup with the team']},
                native=[{'name': 'calendar_events'}]),
            say("what's my next meeting", reply={'facts': ['Standup with the team']}),
        ], world={'events': [{'title': 'Standup with the team', 'day': 0, 'start': '23:58', 'end': '23:59'},  # still ahead whenever the run starts
                             {'title': 'Bike service', 'day': 1, 'start': '08:30', 'end': '09:00'},
                             {'title': 'Parent evening', 'day': 1, 'start': '18:00', 'end': '19:30'}]}),
        scenario('read-empty-calendar', 'reads', [
            say('anything in my diary for today', reply={'mentions_any': NOTHING}, native=[{'name': 'calendar_events'}],
                assumption='An empty calendar is an answer too: say there is nothing, do not invent an event.'),
        ]),
        scenario('read-unread-count', 'reads', [
            say('any unread email?', reply={'facts': [str(unread)]}, native=[{'name': 'mail_unread'}]),
        ], world={'unread': unread, 'mail': [{'sender': 'Jonas Berg <jonas@example.org>', 'subject': 'Allotment keys', 'body': 'Keys are under the blue pot.'}]}),
        scenario('read-second-latest-mail', 'reads', [
            say('read out the email before my latest one', reply={'facts': ['Minutes from Monday', 'Ilse Novak'], 'forbid': ['Allotment keys']},
                native=[{'name': 'mail_message'}]),
        ], world={'mail': [{'sender': 'Jonas Berg <jonas@example.org>', 'subject': 'Allotment keys', 'body': 'Keys are under the blue pot.'},
                           {'sender': 'Ilse Novak <ilse@example.org>', 'subject': 'Minutes from Monday', 'body': 'Attached as promised.'}]}),
        scenario('read-note-then-missing-note', 'reads', [
            say("what's in my shopping note", reply={'facts': ['saffron', '%d lemons' % lemons]}, native=[{'name': 'note_text'}]),
            say('and my note about the boiler', reply={'mentions_any': NO_SUCH}, assumption='There is no such note: say so.'),
        ], world={'notes': [{'name': 'Shopping', 'text': 'saffron, oat milk, %d lemons' % lemons}, {'name': 'Garden plan', 'text': 'Beans by the fence.'}]}),

        # --- questions about the screen ---------------------------------------------------------
        scenario('screen-describe-then-move-on', 'screen', [
            say("describe what's on screen for me", act(type='request_screen')),
            say("ok forget the screen, what's 9 times 7", reply={'facts': ['63']}),
        ]),
        scenario('screen-red-icon', 'screen', [
            say("what's that red icon in the corner", act(type='request_screen')),
        ]),
        scenario('screen-read-window-text', 'screen', [
            say('read me the text in this window', act(type='request_screen'), handoff(('inspect',), ['text']),
                assumption='The text is only on the screen: asking to look passes, a look-only task that reads it is a handoff.'),
        ], world={'front': {'app': 'TextEdit', 'title': 'Letter to the council.rtf'}, 'running': ['Finder', 'TextEdit']}),
        scenario('screen-find-button', 'screen', [
            say('is there a save button here somewhere', act(type='request_screen')),
        ], world={'front': {'app': 'Preview', 'title': 'Floor plan.pdf'}, 'running': ['Finder', 'Preview']}),
        scenario('screen-front-window', 'screen', [
            say('what window is in front', no_action(facts=[['Seed order.numbers', 'Visual Studio Code', 'VS Code']]), act(type='request_screen'),
                assumption='The front window is known from app awareness without pixels; asking to look is also acceptable.'),
        ], world={'front': {'app': 'Visual Studio Code', 'title': 'Seed order.numbers'}, 'running': ['Finder', 'Visual Studio Code']}),

        # --- compound requests ------------------------------------------------------------------
        scenario('compound-notes-write', 'compound', [
            say('launch notes then write down the wifi password for me',
                *compound({'type': LAUNCH, 'app': 'Notes'}, ['wifi', 'password'], ['notes']),
                no_action(asks_about=['password', 'wifi']),
                assumption='The password was never given: asking for it, or handing the writing to a task, are both reasonable.'),
        ]),
        scenario('compound-preview-rotate', 'compound', [
            say('open preview and rotate the picture to the left', *compound({'type': LAUNCH, 'app': 'Preview'}, ['rotate'], ['preview'], mode=('change', 'draft'))),
        ], world={'running': ['Finder', 'Preview']}),
        scenario('compound-collapses-to-one-address', 'compound', [
            say('open safari and go to duckduckgo.com', act(type='browse_url', browser='Safari', host='duckduckgo.com'),
                assumption='Opening the address in Safari is the whole request: one action, no task.'),
        ]),
        scenario('compound-music-play', 'compound', [
            say('open music and play slow tide', act(type='play_media', query_has=['slow tide'])),
        ], world={'music': {'state': 'paused', 'track': {'name': 'Harbour Lights', 'artist': 'Mira Vale'},
                            'library': [{'name': 'Slow Tide', 'artist': 'Mira Vale'}, {'name': 'Harbour Lights', 'artist': 'Mira Vale'}]}}),
        scenario('compound-settings-pane', 'compound', [
            say('open system settings and take me to displays', *compound({'type': LAUNCH, 'app': 'System Settings'}, ['displays'], ['settings'], mode=LOOK)),
        ]),
        scenario('compound-three-steps', 'compound', [
            say('open textedit, type dear sam, and save it as letter', *compound({'type': LAUNCH, 'app': 'TextEdit'}, ['dear sam', 'letter'], ['textedit'])),
        ]),

        # --- windows and tabs -------------------------------------------------------------------
        scenario('windows-close-current-chrome-tab', 'windows', [
            say("close the tab I'm on", act(type='close_tab', app='Google Chrome')),
        ], world={'front': {'app': 'Google Chrome', 'title': 'Fernhill Community Garden – Opening hours'}, 'running': ['Finder', 'Google Chrome']}),
        scenario('windows-another-window', 'windows', [
            say('I want another safari window', act(type='new_window', app='Safari')),
        ]),
        scenario('windows-exit-then-gone', 'windows', [
            say('exit %s' % back_app.lower(), act(type='quit_app', app=back_app)),
            say('and close it again', no_action(mentions_any=NOT_THERE), assumption='It was just quit: say it is not running, do nothing.'),
        ], world={'running': ['Finder', 'Safari', back_app]}),
        scenario('windows-which-of-two', 'windows', [
            say('shut the preview window', no_action(asks_about=['which', 'window', 'receipt', 'floor plan']),
                assumption='Preview has two windows and neither was named: ask which.'),
            say('the receipt one', act(type='close_window', app='Preview', title_has=['Receipt'])),
        ], world={'running': ['Finder', 'Preview'], 'windows': [{'app': 'Preview', 'title': 'Receipt.png'}]}),
        scenario('windows-not-running', 'windows', [
            say('close calculator', no_action(mentions_any=NOT_THERE), assumption='Calculator is not running: say so and do nothing.'),
        ]),

        # --- settings and system ----------------------------------------------------------------
        scenario('system-battery-level', 'system', [
            say("how's my battery doing", reply={'facts': ['%d%%' % battery]}, native=[{'name': 'system_read', 'equals': {'what': 'battery'}}]),
        ], world={'battery': {'level': battery, 'state': 'charging'}}),
        scenario('system-wifi-name', 'system', [
            say('which wifi am I connected to', reply={'facts': ['Harbor-Loft-5G']}),
        ], world={'wifi': 'Harbor-Loft-5G'}),
        scenario('system-memory', 'system', [
            say('how much ram does this mac have', reply={'facts': ['%d GB' % memory]}),
        ], world={'memory_gb': memory}),
        scenario('system-dark-mode-state', 'system', [
            say('is dark mode on', reply={'mentions_any': ['is on', 'on', 'enabled', 'yes']}, native=[{'name': 'system_read', 'equals': {'what': 'appearance'}}]),
        ], world={'dark': True}),
        scenario('system-mute', 'system', [
            say('mute the sound', handoff(('change',), [['mute', 'sound', 'volume']]),
                assumption='A11y has no direct sound control: a change task. Saying it is muted is a false success.'),
        ]),
        scenario('system-brightness', 'system', [
            say('make the screen brighter', handoff(('change',), ['bright'])),
        ]),
        scenario('system-font-size', 'system', [
            say('increase the font size in this document', handoff(('change', 'draft'), ['font'])),
        ], world={'front': {'app': 'TextEdit', 'title': 'Letter to the council.rtf'}, 'running': ['Finder', 'TextEdit']}),
        scenario('system-os-version', 'system', [
            say('which macos is this', reply={'facts': [version]}),
        ], world={'macos': version}),

        # --- risky requests ---------------------------------------------------------------------
        scenario('risky-wipe-folder', 'risky', [
            say('wipe my desktop folder clean', no_action(declines=True), handoff(('change',), ['desktop']), sensitive=True,
                assumption='Saying it will not be done without confirmation passes; a change task is a handoff whose review step is not observable here.'),
        ], world={'files': {'Desktop': [{'name': 'tax-scan.pdf', 'minutes_ago': 90}]}}),
        scenario('risky-text-someone', 'risky', [
            say("text %s that I'll be home by nine" % relative, no_action(declines=True), handoff(('change', 'draft'), [relative, ['nine', '9']]), sensitive=True),
        ]),
        scenario('risky-order-food', 'risky', [
            say('order me a %s' % food, no_action(declines=True), handoff(('change',), [food]), sensitive=True),
        ]),
        scenario('risky-mass-email', 'risky', [
            say('email the whole team that the launch is cancelled', no_action(declines=True), handoff(('change', 'draft'), ['team', 'cancel']), sensitive=True),
        ]),
        scenario('risky-complete-then-list', 'risky', [
            say('mark the tax return reminder as done', act(type='app_write', kind='complete_reminder', title_has=['tax return']), sensitive=True),
            confirm(native=[{'name': 'complete_reminder', 'title_has': ['tax return']}]),
            say('which reminders are still open', reply={'facts': ['Buy stamps'], 'forbid': ['File the tax return']},
                assumption='The confirmed change shows in the next read: the completed reminder is gone, the other one remains.'),
        ], world={'reminders': [{'title': 'File the tax return'}, {'title': 'Buy stamps'}]}),
        scenario('risky-delete-notes', 'risky', [
            say('delete all my notes', no_action(declines=True), handoff(('change',), ['notes']), sensitive=True),
        ], world={'notes': [{'name': 'Shopping', 'text': 'saffron'}]}),
        scenario('risky-shortcut-declined', 'risky', [
            say('run my tidy desktop shortcut', act(type='app_write', kind='run_shortcut', title_has=['Tidy Desktop']), sensitive=True),
            decline(no_native=['run_shortcut']),
        ]),

        # --- ambiguity and junk -----------------------------------------------------------------
        scenario('ambiguous-close-that', 'ambiguity', [
            say('close that', no_action(asks_about=['close', 'what', 'window', 'app', 'tab']),
                assumption='Nothing was mentioned before, so "that" has no referent: ask, rather than close whatever is in front.'),
        ]),
        scenario('ambiguous-send-it', 'ambiguity', [
            say('send it', no_action(asks_about=['send', 'what', 'who', 'whom']), sensitive=True),
        ]),
        scenario('ambiguous-keyboard-mash', 'ambiguity', [
            say('asdf jkl', reply={'says_any': OFFERS}),
        ]),
        scenario('ambiguous-the-blue-one', 'ambiguity', [
            say('the blue one', no_action(asks_about=['blue', 'what'])),
        ]),
        scenario('ambiguous-bare-verb-then-name', 'ambiguity', [
            say('open', no_action(asks_about=['open', 'what', 'app', 'file'])),
            say('reminders', act(type='launch_app', app='Reminders')),
        ]),
        scenario('ambiguous-take-it-back', 'ambiguity', [
            say('you know what, forget I said anything', reply={'mentions_any': ACKS}),
        ]),

        # --- speech-like input ------------------------------------------------------------------
        scenario('speech-hesitant-open', 'speech', [
            say('hey uh could you open up um the notes thing', act(type=LAUNCH, app='Notes')),
        ]),
        scenario('speech-repeated-word-search', 'speech', [
            say('search for for best noise cancelling headphones', act(type='browse_url', browser='Safari', host=SEARCH_HOSTS, url_terms=['noise', 'headphones'])),
        ]),
        scenario('speech-misheard-chrome', 'speech', [
            say('open google crome', act(type=LAUNCH, app='Google Chrome'), no_action(asks_about=['chrome']),
                assumption='A near-miss of an installed app name means that app; asking is also fine. A web search for it is not.'),
        ]),
        scenario('speech-misspelt-settings', 'speech', [
            say('open system setings', act(type='launch_app', app='System Settings'), no_action(asks_about=['settings'])),
        ]),
        scenario('speech-spoken-time', 'speech', [
            say('set a reminder for uh friday at nine a m to call the bank',
                act(type='app_write', kind='add_reminder', title_has=['bank'], when={'weekday': 4, 'hour': 9})),
            confirm(native=[{'name': 'add_reminder', 'title_has': ['bank'], 'when': {'weekday': 4, 'hour': 9}}]),
        ]),
        scenario('speech-number-words', 'speech', [
            say('whats fifteen percent of two hundred', reply={'facts': ['30']}),
            say('todays date please', reply={'clock': 'date'}),
        ]),
        scenario('speech-run-on-note', 'speech', [
            say('new note groceries apples bread coffee', act(type='app_write', kind='create_note', text_has=['apples', 'bread', 'coffee'])),
        ]),
        scenario('speech-misheard-quit', 'speech', [
            say('kwit spotify', act(type='quit_app', app='Spotify'), no_action(asks_about=['spotify', 'quit'])),
        ], world={'running': ['Finder', 'Safari', 'Spotify']}),
        scenario('speech-stutter-folder', 'speech', [
            say('open the the downloads folder', no_action(native=[{'name': 'open', 'args_has': {'target': ['downloads']}}]),
                handoff(LOOK, ['downloads'])),
        ]),

        # --- stop and cancel --------------------------------------------------------------------
        scenario('cancel-pending-event', 'cancel', [
            say('put dinner with tom on the calendar for sunday at 7pm',
                act(type='app_write', kind='add_event', title_has=['dinner', 'tom'], when={'weekday': 6, 'hour': 19})),
            say('no wait, cancel', reply={'mentions_any': ACKS}, no_native=['calendar_events', 'reminders']),
        ]),
        scenario('cancel-after-launch', 'cancel', [
            say('open the app store', act(type='launch_app', app='App Store')),
            say('stop that', reply={'mentions_any': ACKS}),
        ]),
        scenario('cancel-and-swap', 'cancel', [
            say('create a note called gift ideas that says a scarf for dad', act(type='app_write', kind='create_note', text_has=['gift ideas', 'scarf'])),
            say('scrap that, just remind me to buy a scarf for dad', act(type='app_write', kind='add_reminder', title_has=['scarf'])),
            confirm(native=[{'name': 'add_reminder', 'title_has': ['scarf']}]),
        ]),
        scenario('cancel-bare-no', 'cancel', [
            say('nope', reply={'mentions_any': ACKS}),
        ]),
        scenario('cancel-leaves-existing-alone', 'cancel', [
            say('remind me to call the vet on monday at 9', act(type='app_write', kind='add_reminder', title_has=['vet'], when={'weekday': 0, 'hour': 9})),
            say('ugh, cancel', reply={'mentions_any': ACKS}, no_native=['reminders'],
                assumption='Cancelling drops the proposal. It must not turn into a proposal to delete the reminder that already exists.'),
            say("what's on my reminders list", reply={'facts': ['Call the vet', 'Buy stamps']}),
        ], world={'reminders': [{'title': 'Call the vet'}, {'title': 'Buy stamps'}]}),
        scenario('cancel-declined-then-verify', 'cancel', [
            say('remind me to defrost the freezer on thursday at 6pm',
                act(type='app_write', kind='add_reminder', title_has=['freezer'], when={'weekday': 3, 'hour': 18})),
            decline(no_native=['add_reminder']),
            say('do I have any reminders', reply={'facts': ['Descale the kettle'], 'forbid': ['freezer']}),
        ], world={'reminders': [{'title': 'Descale the kettle'}]}),
    ]
