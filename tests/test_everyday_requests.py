"""Everyday requests that failed in the screen-free request eval (scripts/request_eval.py, dev split).

The router model is doubled with the interpretation it gave live; these tests cover what code does
with it: a plain open stays a direct action, a near miss of an app's name opens that app, a pending
proposal is cancelled or changed rather than hunted for, and named days are computed in code."""
import os
os.environ.setdefault('A11Y_WEB_ANSWERS', '0')
os.environ.setdefault('A11Y_ROUTES', '0')
from datetime import datetime
import tempfile
import unittest
from unittest.mock import Mock, patch

from companion import mac_apps
from companion.app_names import closest_app, launchable
from companion.desktop_context import DesktopContext
from companion.models import Identity
from companion.quick_open import plan as quick_plan
from companion.request_plan import INTENT_SYSTEM, semantic_plan
from companion.service import Companion, stated_day, stated_time, upcoming
from companion.storage import Store
from test_desktop_context import FakeBridge

CATALOG = [{'name': name, 'bundle_id': bundle} for name, bundle in (
    ('Safari', 'com.apple.Safari'), ('Google Chrome', 'com.google.Chrome'), ('Notes', 'com.apple.Notes'), ('Calendar', 'com.apple.iCal'),
    ('Calculator', 'com.apple.calculator'), ('Mail', 'com.apple.mail'), ('Maps', 'com.apple.Maps'), ('Slack', 'com.tinyspeck.slackmacgap'),
    ('System Settings', 'com.apple.systempreferences'), ('Reminders', 'com.apple.reminders'))]
FRIDAY = datetime(2026, 11, 6, 9, 20)


class NearMissAppNameTests(unittest.TestCase):
    def test_a_misspelt_installed_app_is_that_app(self):
        self.assertEqual(closest_app('safarie', CATALOG)['name'], 'Safari')
        self.assertEqual(closest_app('calendr', CATALOG)['name'], 'Calendar')
        self.assertEqual(closest_app('calcualtor', CATALOG)['name'], 'Calculator')
        self.assertEqual(closest_app('settigns', CATALOG)['name'], 'System Settings')  # one word of the name

    def test_short_names_and_ties_are_not_guessed(self):
        self.assertIsNone(closest_app('mial', CATALOG))     # Mail or Maps: too short to guess
        self.assertIsNone(closest_app('canva', CATALOG))
        self.assertIsNone(closest_app('photoshop', CATALOG))
        self.assertIsNone(closest_app('safarie', CATALOG+[{'name': 'Safaris', 'bundle_id': 'x.safaris'}]))  # a tie
        self.assertEqual([app['name'] for app in launchable('Notes', CATALOG)], ['Notes'])  # exact names first

    def test_quick_open_opens_the_near_miss_instead_of_searching_for_it(self):
        self.assertEqual(quick_plan('open safarie', CATALOG)['operation'], 'launch_app')
        self.assertEqual(quick_plan('open calendr', CATALOG)['app_name'], 'Calendar')
        self.assertEqual(quick_plan('open photoshop', CATALOG)['operation'], 'browser_search')
        self.assertEqual(quick_plan('go to slack instead', CATALOG), {'operation': 'launch_app', 'app_name': 'Slack', 'target': '',
                                                                      'browser_mode': 'default', 'remaining_request': ''})


class StatedDayTests(unittest.TestCase):
    def when(self, model, request):
        return upcoming(stated_day(stated_time(model, request, FRIDAY), request, FRIDAY), request, FRIDAY)

    def test_next_monday_is_the_coming_monday_not_the_following_friday(self):
        self.assertEqual(self.when('2026-11-13T10:00', 'set a reminder to renew my passport next monday at 10'), '2026-11-09T10:00')

    def test_a_weekday_named_today_and_noon(self):
        self.assertEqual(self.when('2026-11-06T12:00', 'put lunch with dana on my calendar for friday at noon'), '2026-11-06T12:00')
        self.assertEqual(self.when('2026-11-06T09:00', 'lunch with dana friday at noon'), '2026-11-06T12:00')
        self.assertEqual(self.when('2026-11-06T12:00', 'lunch next friday at noon'), '2026-11-13T12:00')
        self.assertEqual(self.when('2026-11-13T18:00', 'dinner on friday at 6'), '2026-11-06T18:00')

    def test_tomorrow_and_tonight_keep_the_clock_time(self):
        self.assertEqual(self.when('2026-11-06T15:00', 'remind me tomorrow at 3'), '2026-11-07T15:00')
        self.assertEqual(self.when('2026-11-07T20:00', 'take the bins out tonight at eight'), '2026-11-06T20:00')
        self.assertEqual(self.when('', 'remind me tomorrow'), '')  # nothing to place


class RemindersTests(unittest.TestCase):
    def test_listing_without_a_date_still_lists_the_open_ones(self):
        bridge = Mock()
        bridge.reminders.return_value = [{'title': 'Book the dentist', 'id': 'r1'}, {'title': 'Order printer ink', 'id': 'r2'}]
        reply = mac_apps.read_reminders(bridge, 'today', FRIDAY)
        self.assertTrue(reply.startswith('Nothing is due today. You have 2 open reminders:'), reply)
        self.assertIn('Book the dentist', reply)
        self.assertIn('Order printer ink', reply)
        bridge.reminders.return_value = []
        self.assertEqual(mac_apps.read_reminders(bridge, 'today', FRIDAY), 'Nothing is due today.')

    def test_a_described_reminder_is_found_by_its_own_words(self):
        items = [{'title': 'Book the dentist', 'id': 'r1'}, {'title': 'Order printer ink', 'id': 'r2'}]
        key = lambda item: item['title']
        self.assertEqual(mac_apps.best_match('dentist reminder', items, key)['id'], 'r1')
        self.assertEqual(mac_apps.best_match('the one about the dentist', items, key)['id'], 'r1')
        self.assertIsNone(mac_apps.best_match('dentist and ink', items, key))  # not one item
        self.assertIsNone(mac_apps.best_match('it', items, key))
        self.assertIsNone(mac_apps.best_match('reminder', items, key))  # nothing of the item's own

    def test_deleting_or_completing_nothing_named_asks_and_reads_no_reminders(self):
        bridge = Mock()
        bridge.reminders.return_value = [{'title': 'Book the dentist', 'id': 'r1'}]
        self.assertEqual(mac_apps.prepare('delete_reminder', ' ', bridge=bridge, now=FRIDAY), ('answer', 'What should I delete?'))
        self.assertEqual(mac_apps.prepare('complete_reminder', '', bridge=bridge, now=FRIDAY), ('answer', 'Which reminder is done?'))
        bridge.reminders.assert_not_called()
        self.assertEqual(mac_apps.prepare('delete_reminder', 'dentist reminder', bridge=bridge, now=FRIDAY)[1]['id'], 'r1')


class RoutingFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        bridge = FakeBridge()
        bridge.catalog = Mock(return_value=list(CATALOG))
        bridge.app_state = Mock(return_value={'running': False})
        bridge.reminders = Mock(return_value=[{'title': 'Water the plants', 'id': 'r1'}])
        self.provider = Mock(spec=['interpret_request', 'complete_structured'])
        self.app = Companion(Store(self.temp.name), Identity('company', 'employee'), self.provider)
        self.app.desktop_context = DesktopContext(bridge, clock=lambda: 100)
        self.app.desktop_context.enabled = True
        for patcher in (patch('companion.mac_apps.available', return_value=True), patch('companion.mail_reader.available', return_value=True)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def route(self, operation, target='', **extra):
        self.provider.interpret_request.return_value = dict({'operation': operation, 'app_name': '', 'target': target, 'reply': '',
                                                             'browser_mode': 'default', 'remaining_request': ''}, **extra)


class PlainOpenTests(RoutingFixture):
    def test_politeness_and_fillers_before_the_name_do_not_make_a_task(self):
        for request in ('could you pull up my notes app please', 'um can you uh open the the notes please', 'hey ally, show me notes'):
            self.route('launch_app', app_name='Notes', acts_on_computer=True, referent='destination')
            plan = self.app.prepare_request(request)
            self.assertEqual(plan['operation'], 'launch_app', request)
        response = self.app.ask('could you pull up my notes app please')
        self.assertEqual(response['desktop_action']['type'], 'launch_app')
        self.assertEqual(response['desktop_action']['bundle_id'], 'com.apple.Notes')

    def test_words_after_the_name_or_the_app_as_a_place_are_still_work_in_it(self):
        self.route('launch_app', app_name='System Settings', acts_on_computer=True)
        self.assertEqual(self.app.prepare_request('in System Settings, open Displays')['operation'], 'computer_task')
        self.route('focus_app', app_name='Calendar', acts_on_computer=True)
        self.assertEqual(self.app.prepare_request('switch Calendar to the day view')['operation'], 'computer_task')
        self.route('launch_app', 'Calendar', app_name='Slack', acts_on_computer=True)
        self.assertEqual(self.app.prepare_request('open the Calendar in Slack')['operation'], 'computer_task')

    def test_the_query_echoed_as_remaining_work_is_not_more_work(self):
        plan = semantic_plan({'operation': 'browser_search', 'app_name': 'Safari', 'target': 'cheap flights to porto', 'browser_mode': 'tab',
                              'remaining_request': 'cheap flights to porto'}, 'ok so like search for um cheap flights to porto')
        self.assertEqual(plan['remaining_request'], '')
        plan = semantic_plan({'operation': 'browser_search', 'app_name': 'Safari', 'target': 'cats', 'browser_mode': 'default',
                              'remaining_request': 'open the first result'}, 'search for cats and open the first result')
        self.assertEqual(plan['remaining_request'], 'open the first result')

    def test_an_unmentioned_browser_or_window_gives_way_to_the_default(self):
        self.route('browser_search', 'louvre opening hours', app_name='Safari', browser_mode='window', acts_on_computer=True)
        with patch('companion.desktop_context.default_browser_id', return_value='com.google.Chrome'):
            response = self.app.ask('google the louvre opening hours')
        action = response['desktop_action']
        self.assertEqual((action['type'], action['bundle_id'], action['browser_mode']), ('browse_url', 'com.google.Chrome', 'default'))
        self.assertIn('louvre+opening+hours', action['url'])
        self.route('browser_open', 'https://github.com', app_name='Chrome', browser_mode='window', acts_on_computer=True)
        action = self.app.ask('open github in a new chrome window')['desktop_action']
        self.assertEqual((action['bundle_id'], action['browser_mode']), ('com.google.Chrome', 'window'))  # asked for, kept

    def test_a_misspelt_app_is_opened_without_a_model_call(self):
        response = self.app.ask('open safarie')
        self.assertEqual(response['desktop_action']['bundle_id'], 'com.apple.Safari')
        self.provider.interpret_request.assert_not_called()

    def test_a_real_word_near_an_app_name_is_left_to_the_model(self):
        # "calender" is a dictionary word (review 2026-11-06: code no longer guesses a correctly spelt
        # word is a typo). The model names it as an app to open; then the near miss is that app.
        with patch('companion.app_names._DICTIONARY', frozenset({'calender'})):
            self.route('launch_app', app_name='calender', acts_on_computer=True, referent='destination')
            response = self.app.ask('open calender')
        self.provider.interpret_request.assert_called_once()
        self.assertEqual(response['desktop_action']['bundle_id'], 'com.apple.iCal')


class PendingProposalTests(RoutingFixture):
    def propose_reminder(self):
        self.route('add_reminder', 'water the plants', app_name='Reminders', when='2026-11-06T19:00', acts_on_computer=True)
        self.provider.complete_structured.return_value = {'title': 'water the plants'}
        response = self.app.ask('remind me to water the plants at 7pm')
        self.assertEqual(response['desktop_action']['kind'], 'add_reminder')
        self.assertEqual(self.app.proposed['offered'], response['desktop_action']['label'])

    def test_the_router_sees_the_offered_button_and_cancelling_it_touches_nothing(self):
        self.propose_reminder()
        self.route('answer', reply='Okay, cancelled.', acts_on_computer=False, referent='other')
        response = self.app.ask('cancel that')
        context = self.provider.interpret_request.call_args.kwargs['context']
        self.assertEqual(context['pending_proposal']['request'], 'remind me to water the plants at 7pm')
        self.assertIn('Add reminder', context['pending_proposal']['offered'])
        self.assertIn('pending_proposal', INTENT_SYSTEM)  # the router is told what the entry means
        self.assertEqual(response['message'], 'Okay, cancelled.')
        self.assertIsNone(response['desktop_action'])
        self.app.desktop_context.bridge.reminders.assert_not_called()
        self.provider.complete_structured.assert_called_once()  # the title call; no follow-up rewrite, no memory rewrite
        self.assertIsNone(self.app.proposed)  # the next request has nothing pending

    def test_replacing_the_proposal_uses_the_new_request_not_the_old_title(self):
        self.route('create_note', 'ideas | try the new bakery', app_name='Notes', acts_on_computer=True)
        self.app.ask('make a note called ideas that says try the new bakery')
        self.route('add_reminder', 'try the new bakery', app_name='Reminders', acts_on_computer=True)
        self.provider.complete_structured.return_value = {'title': 'try the new bakery'}
        response = self.app.ask("wait no, don't. make it a reminder to try the new bakery instead")
        self.assertEqual((response['desktop_action']['kind'], response['desktop_action']['title']), ('add_reminder', 'try the new bakery'))
        self.assertNotIn('last_task', str(self.provider.complete_structured.call_args_list))  # no follow-up rewrite

    def test_a_time_phrase_is_not_a_title(self):
        self.route('add_event', 'lunch with dana', app_name='Calendar', when='2026-11-06T12:00', acts_on_computer=True)
        self.provider.complete_structured.return_value = {'title': 'friday at noon'}
        response = self.app.ask('put lunch with dana on my calendar for friday at noon')
        self.assertEqual(response['desktop_action']['title'], 'lunch with dana')

    def test_a_pressed_button_is_reported_as_done_not_pending(self):
        self.propose_reminder()
        label = self.app.proposed['offered']
        self.app.store.add_exchange(self.app.identity, label, 'Added the reminder to Reminders.')  # AppWriteWorker done()
        self.route('answer', reply='Sure.', acts_on_computer=False, referent='other')
        self.app.ask('thanks')
        context = self.provider.interpret_request.call_args.kwargs['context']
        self.assertNotIn('pending_proposal', context)
        self.assertEqual(context['last_confirmed_action']['offered'], label)

    def test_opening_an_app_leaves_nothing_pending(self):
        self.app.ask('open notes')
        self.assertIsNone(self.app.proposed)


class FocusVerifyTests(RoutingFixture):
    def test_switching_apps_is_verified_from_the_observation_without_a_model_call(self):
        self.app.last_action_result = {'type': 'focus_app', 'bundle_id': 'com.apple.finder', 'label': 'Switch to Finder', 'status': 'submitted'}
        self.app.desktop_context.bridge.app_state.return_value = {'running': True, 'active': True}
        response = self.app.ask('switch to finder', mode='verify')
        self.assertEqual(response['message'], 'Finder is in front.')
        self.provider.complete_structured.assert_not_called()
        self.app.desktop_context.bridge.app_state.return_value = {'running': True, 'active': False}
        self.assertIn('not in front yet', self.app.ask('switch to finder', mode='verify')['message'])


WEDNESDAY = datetime(2026, 11, 4, 10, 0)
APPS = [{'name': name, 'bundle_id': bundle} for name, bundle in (
    ('Finder', 'com.apple.finder'), ('Preview', 'com.apple.Preview'), ('Weather', 'com.apple.weather'), ('Notion', 'notion.id'),
    ('Contacts', 'com.apple.AddressBook'), ('Messages', 'com.apple.MobileSMS'), ('FaceTime', 'com.apple.FaceTime'),
    ('Numbers', 'com.apple.iWork.Numbers'), ('Reminders', 'com.apple.reminders'), ('Safari', 'com.apple.Safari'))]
WORDS = frozenset('tinder binder review feather leather motion contract contact massage number remainder calender'.split())


class StatedDayReviewTests(unittest.TestCase):
    """Review of 0038b56: the code's day replaced dates the model had right."""
    def when(self, model, request, now=FRIDAY):
        return upcoming(stated_day(stated_time(model, request, now), request, now), request, now)

    def test_a_relative_phrase_or_explicit_date_keeps_the_models_date(self):
        self.assertEqual(self.when('2026-11-08T09:00', 'remind me the day after tomorrow at 9am to pay rent'), '2026-11-08T09:00')
        self.assertEqual(self.when('2026-11-16T09:00', 'remind me a week from monday at 9am'), '2026-11-16T09:00')
        self.assertEqual(self.when('2026-11-13T10:00', 'remind me friday next week at 10am', WEDNESDAY), '2026-11-13T10:00')
        self.assertEqual(self.when('2026-11-16T09:00', 'remind me on monday the 16th at 9am'), '2026-11-16T09:00')
        self.assertEqual(self.when('2026-11-16T09:00', 'remind me on 16 november, a monday, at 9am'), '2026-11-16T09:00')

    def test_a_day_in_the_title_or_two_day_words_keep_the_models_date(self):
        self.assertEqual(self.when('2026-11-05T17:00', "remind me to plan friday's party tomorrow at 5pm", WEDNESDAY), '2026-11-05T17:00')
        self.assertEqual(self.when('2026-11-07T15:00', 'remind me tomorrow to cancel the monday meeting at 3pm'), '2026-11-07T15:00')
        self.assertEqual(self.when('2026-11-05T17:00', "remind me about friday's party at 5pm", WEDNESDAY), '2026-11-05T17:00')

    def test_next_weekday_is_never_this_weeks(self):
        self.assertEqual(self.when('2026-11-13T10:00', 'next friday at 10am dentist', WEDNESDAY), '2026-11-13T10:00')
        self.assertEqual(self.when('2026-11-06T10:00', 'next friday at 10am dentist', WEDNESDAY), '2026-11-13T10:00')  # model wrong
        self.assertEqual(self.when('2026-11-06T10:00', 'this friday at 10am dentist', WEDNESDAY), '2026-11-06T10:00')

    def test_a_weekday_named_today_whose_time_has_passed_is_a_week_on(self):
        self.assertEqual(self.when('2026-11-06T09:00', 'remind me friday at 9', datetime(2026, 11, 6, 10, 0)), '2026-11-13T09:00')
        self.assertEqual(self.when('2026-11-06T11:00', 'remind me friday at 11', datetime(2026, 11, 6, 10, 0)), '2026-11-06T11:00')

    def test_midnight_ends_the_named_day(self):
        ten = datetime(2026, 11, 6, 10, 0)
        self.assertEqual(self.when('2026-11-07T00:00', 'remind me tonight at midnight to lock up', ten), '2026-11-07T00:00')
        self.assertEqual(self.when('2026-11-06T00:00', 'remind me tonight at midnight to lock up', ten), '2026-11-07T00:00')
        self.assertEqual(self.when('2026-11-06T22:00', 'remind me at midnight to lock up', ten), '2026-11-07T00:00')
        self.assertEqual(self.when('2026-11-07T00:00', 'remind me friday at midnight', WEDNESDAY), '2026-11-07T00:00')
        self.assertEqual(self.when('2026-11-06T00:00', 'remind me friday at midnight', WEDNESDAY), '2026-11-07T00:00')
        self.assertEqual(self.when('2026-11-07T00:00', 'remind me at 12 am to take pills', ten), '2026-11-07T00:00')
        self.assertEqual(self.when('2026-11-06T09:00', 'remind me at noon to eat', ten), '2026-11-06T12:00')
        self.assertEqual(self.when('2026-11-20T12:00', 'remind me on the 20th at midnight', ten), '2026-11-21T00:00')

    def test_two_stated_times_leave_the_models_time(self):
        self.assertEqual(self.when('2026-11-07T15:00', 'lunch at noon then call at 3pm tomorrow'), '2026-11-07T15:00')
        self.assertEqual(self.when('2026-11-07T17:00', 'remind me at 5 to move the 3pm meeting to 4pm'), '2026-11-07T17:00')


class FuzzyAppReviewTests(unittest.TestCase):
    """Review of 0038b56: real words opened the wrong app, and private apps became reachable."""
    def setUp(self):
        patcher = patch('companion.app_names._DICTIONARY', WORDS)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_real_words_and_other_first_letters_are_not_apps(self):
        for word in ('tinder', 'binder', 'review', 'feather', 'leather', 'motion', 'remainders'):
            self.assertIsNone(closest_app(word, APPS), word)
            plan = quick_plan('open '+word, APPS)
            self.assertFalse(plan and plan['operation'] == 'launch_app', (word, plan))

    def test_private_apps_are_never_guessed(self):
        for word in ('contact', 'contracts', 'massages', 'messager', 'mesages', 'facetim', 'facetimes'):
            self.assertIsNone(closest_app(word, APPS), word)
            self.assertIsNone(closest_app(word, APPS, allow_words=True), word)
            plan = quick_plan('open '+word, APPS)
            self.assertNotIn((plan or {}).get('app_name'), ('Contacts', 'Messages', 'FaceTime'), word)
        self.assertEqual(launchable('Messages', APPS)[0]['name'], 'Messages')  # named exactly, it opens

    def test_typos_and_plurals_still_open_the_app(self):
        self.assertEqual(closest_app('numbers', APPS), None)  # exact names are resolve_app's
        self.assertEqual(closest_app('number', APPS)['name'], 'Numbers')
        self.assertIsNone(closest_app('safri', APPS))  # five letters: too short to guess
        self.assertEqual(closest_app('safarie', APPS)['name'], 'Safari')
        self.assertEqual(closest_app('reminderss', APPS)['name'], 'Reminders')
        self.assertEqual(launchable('feather', APPS), [])  # even from the model, Weather starts with W

    def test_without_a_word_list_the_first_letter_still_decides(self):
        with patch('companion.app_names._DICTIONARY', frozenset()):
            self.assertIsNone(closest_app('tinder', APPS))
            self.assertIsNone(closest_app('review', APPS))
            self.assertEqual(closest_app('calender', CATALOG)['name'], 'Calendar')


class BeyondAppNameReviewTests(RoutingFixture):
    """Review of 0038b56: with no router target, the rest of the request was dropped."""
    def test_content_words_around_the_app_keep_the_work(self):
        for request, app in (('open bluetooth settings', 'System Settings'), ('go to the wifi settings', 'System Settings'),
                             ('open a private safari window', 'Safari'), ('open incognito chrome', 'Google Chrome'),
                             ("open yesterday's photos", 'Photos'), ('draft my resume, open pages', 'Pages')):
            self.route('launch_app', app_name=app, acts_on_computer=True, referent='destination')
            plan = self.app.prepare_request(request)
            self.assertEqual(plan['operation'], 'computer_task', request)
            self.assertEqual(plan['target'], request)  # the whole request is the task's goal

    def test_the_app_as_the_place_or_a_picked_target_is_more_than_an_open(self):
        self.assertTrue(Companion.beyond_app_name('open it in preview', 'Preview'))
        self.assertFalse(Companion.beyond_app_name('open preview', 'Preview'))
        self.assertFalse(Companion.beyond_app_name('open calender', 'Calendar'))  # misheard, still the name (dev eval)
        self.assertTrue(Companion.beyond_app_name('open calender settings', 'Calendar'))
        self.assertTrue(Companion.beyond_app_name('open slack', 'Slack', 'general channel'))
        self.assertFalse(Companion.beyond_app_name('open slack', 'Slack', 'Slack'))


class RemindersReviewTests(unittest.TestCase):
    def test_several_described_matches_are_not_one(self):
        items = [{'title': 'Book the dentist', 'id': 'r1'}, {'title': 'Call the dentist back', 'id': 'r2'}]
        self.assertIsNone(mac_apps.best_match('dentist reminder', items, lambda item: item['title']))

    def test_reminders_not_due_today_are_counted(self):
        bridge = Mock()
        today = datetime(2026, 11, 6, 15, 0).timestamp()
        bridge.reminders.return_value = [{'title': 'Book the dentist', 'id': 'r1', 'due': today},
                                         {'title': 'Order printer ink', 'id': 'r2'}, {'title': 'Renew passport', 'id': 'r3'}]
        reply = mac_apps.read_reminders(bridge, 'today', FRIDAY)
        self.assertIn('Book the dentist', reply)
        self.assertNotIn('Order printer ink', reply)
        self.assertTrue(reply.endswith('2 more are not due today.'), reply)


class ProposalOutcomeReviewTests(PendingProposalTests):
    """Review of 0038b56: a failed write was reported as confirmed, and a pending reply went unchecked."""
    def test_a_failed_write_is_not_reported_as_confirmed(self):
        self.propose_reminder()
        label = self.app.proposed['offered']
        self.app.store.add_exchange(self.app.identity, label, 'I couldn’t do that. Reminders did not answer.')
        self.route('answer', reply='Sure.', acts_on_computer=False, referent='other')
        self.app.ask('thanks')
        context = self.provider.interpret_request.call_args.kwargs['context']
        self.assertNotIn('last_confirmed_action', context)
        self.assertNotIn('pending_proposal', context)

    def test_follow_up_rewriting_waits_only_while_the_proposal_is_pending(self):
        self.propose_reminder()
        self.app.last_task = 'remind me to water the plants at 7pm'
        self.provider.complete_structured.reset_mock()
        self.provider.complete_structured.return_value = {'request': 'remind me to water the plants at 8pm'}
        self.assertEqual(self.app.resolve_follow_up('same at 8pm'), 'same at 8pm')  # pending: the router sees the button
        self.provider.complete_structured.assert_not_called()
        self.app.store.add_exchange(self.app.identity, self.app.proposed['offered'], 'Added the reminder to Reminders.')
        self.assertEqual(self.app.resolve_follow_up('same at 8pm'), 'remind me to water the plants at 8pm')

    def test_a_reply_claiming_completion_while_pending_is_checked(self):
        self.propose_reminder()
        self.route('answer', reply='Done, I added the reminder.', acts_on_computer=False, referent='other')
        self.provider.complete_structured.return_value = {'message': 'It is waiting for you to confirm it below.'}
        response = self.app.ask('is it in?')
        self.assertEqual(response['message'], 'It is waiting for you to confirm it below.')
        self.assertIsNone(response['desktop_action'])
        self.propose_reminder()
        self.route('answer', reply='All done, saved it.', acts_on_computer=False, referent='other')
        self.provider.complete_structured.return_value = {'message': 'Done, I added the reminder.'}
        response = self.app.ask('ok great')
        self.assertIn('Nothing has been changed yet', response['message'])
        self.app.desktop_context.bridge.reminders.assert_not_called()


if __name__ == '__main__':
    unittest.main()
