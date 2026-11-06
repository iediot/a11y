"""Outcome boundaries through the real task loop, without driving personal apps.

The fixture owns its current state. A submitted receipt or an optimistic planner
reply does not put the requested value into that state.

Each terminal path is tested in both directions on the evidence it has:
- the step model's "done": the verifier must quote something observed (screen text,
  the window title or a native fact), whatever ran before and whether or not any
  screen text was read;
- a code step that only knows its action was submitted (sort, menu, tab, back, sites):
  native state decides when it can (done with no model call, or not done whatever the
  verifier says); otherwise the verifier decides;
- a code step whose result code observed (every Calculator key accepted, early goal
  check): finishes with no further model call.

Not covered here (tracked separately): the list-row click, the notification view
answer, the section read, and the action limit, which stops without an observation.
"""
from copy import deepcopy
import json
import os
import time
import unittest
from unittest.mock import patch

from companion.computer_task import ComputerTask
from companion.desktop_context import DesktopContext
from companion.task_answers import ANSWER_SYSTEM
from companion.task_completion import MET_SYSTEM, completion_evidence


SAFARI = {'name': 'Safari', 'bundle_id': 'com.apple.Safari', 'pid': 11}
YES_TITLE = {'met': True, 'missing': '', 'evidence': 'Start Page'}  # a verifier that says yes, quoting the window title


class FixtureBridge:
    def __init__(self):
        self.value = {
            'status': 'available', 'platform': 'macOS', 'accessibility': 'granted',
            'app': {'name': 'Fixture Browser', 'bundle_id': 'org.a11y.fixture', 'pid': 11},
            'window': {'title': 'Shipment status', 'bounds': {'x': 0, 'y': 0, 'width': 800, 'height': 600}},
            'controls': [], 'targets': [], 'available_apps': [],
        }
        self.apps = []

    def sample(self):
        return deepcopy(self.value)

    def catalog(self):
        return list(self.apps)


class FixtureVision:
    enabled = True
    generation = 0

    def __init__(self):
        self.lines = []
        self.captures = 0

    def capture(self, *args, **kwargs):
        self.captures += 1
        return {
            'image_data': 'data:image/png;base64,fixture',
            'screen': {'x': 0, 'y': 0, 'width': 800, 'height': 600},
            'captured_monotonic': time.monotonic(),
            'text_regions': [{'text': line, 'x': .1, 'y': .1 + i * .08, 'width': .5, 'height': .05}
                             for i, line in enumerate(self.lines)],
        }

    @staticmethod
    def model_image(image):
        return image


class OptimisticPlanner:
    """The step model always claims success; the verifier has a separately controlled response
    (one reply, or a list used in order, the last one repeating; an Exception is raised)."""
    def __init__(self):
        self.steps = 0
        self.answers = 0
        self.observations = []
        self.verification = {'met': False, 'evidence': '', 'missing': 'The arrival date is not shown.'}
        self.on_verify = None
        self.on_answer = None
        self.answer = 'Arrival: Friday 19 June.'

    def complete_structured(self, system, messages, **kwargs):
        if system == MET_SYSTEM:
            observation = json.loads(messages[-1]['content'])
            self.observations.append(observation)
            if self.on_verify:
                self.on_verify()
            reply = self.verification
            if isinstance(reply, list):
                reply = reply.pop(0) if len(reply) > 1 else reply[0]
            if isinstance(reply, Exception):
                raise reply
            return deepcopy(reply)
        if system == ANSWER_SYSTEM:
            self.answers += 1
            if self.on_answer:
                self.on_answer()
            return {'message': self.answer}
        self.steps += 1
        return {'status': 'done', 'message': 'Arrival: Friday 19 June.', 'risk': 'navigation', 'action': None}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.settings = patch.dict(os.environ, {'A11Y_ROUTES': '0', 'A11Y_WEB_ANSWERS': '0'})
        self.settings.start()
        self.addCleanup(self.settings.stop)
        # No test here may reach a real browser: a page read must be supplied by the test.
        self.read_page = patch('companion.mac_apps.read_page', side_effect=AssertionError('no real browser in tests'))
        self.read_page.start()
        self.addCleanup(self.read_page.stop)
        self.sleep = patch('companion.computer_task.time.sleep')
        self.slept = self.sleep.start()
        self.addCleanup(self.sleep.stop)
        self.bridge = FixtureBridge()
        self.desktop = DesktopContext(self.bridge, clock=lambda: 100)
        self.desktop.enabled = True
        self.vision = FixtureVision()
        self.provider = OptimisticPlanner()

    def task(self, receipts=(), goal='Read the arrival date for this shipment.', max_steps=24):
        task = ComputerTask(goal, automatic=True, goal_mode='inspect', max_steps=max_steps)
        task.planned = True
        task.receipts = deepcopy(list(receipts))
        return task

    def propose(self, task):
        return task.propose(self.desktop, self.vision, self.provider, {'x': 0, 'y': 0})

    def run_code_steps(self, task, refuse=(), fail=(), after=None):
        """Run proposed actions as accepted by the app. Labels in refuse are refused before they ran
        and leave no receipt (the screen-changed retry path); labels in fail come back failed;
        after maps a label to another receipt recorded right behind it."""
        for _ in range(12):
            step = self.propose(task)
            if step['status'] != 'action':
                return step
            action = step['action']
            label = action.get('label', '')
            if label in refuse:
                task.pending = None
            else:
                task.record({'type': action['type'], 'label': label, 'status': 'failed' if label in fail else 'submitted'})
            if label in (after or {}):
                task.record(dict(after[label]))
        self.fail('The task did not end.')

    def verified_arrival(self):
        self.vision.lines = ['Shipment 918', 'Arrival: Friday 19 June.']
        self.provider.verification = {'met': True, 'evidence': self.vision.lines[-1], 'missing': ''}

    def calls(self):
        return (self.provider.steps, len(self.provider.observations), self.provider.answers)


WORKED = [{'type': 'press_key', 'key': 'enter', 'status': 'submitted'}]


class ModelDoneTests(Fixture):
    """The step model says done. Only quoted, observed evidence lets it stand."""

    def test_nothing_executed_and_a_screen_without_the_answer_cannot_finish(self):
        self.vision.lines = ['Welcome', 'Search shipments', 'Help']
        result = self.propose(self.task())
        self.assertEqual(result['status'], 'needs_user')
        self.assertIsNone(result['action'])
        self.assertNotIn('Friday', result['message'])
        self.assertEqual(self.vision.captures, 1)
        self.assertEqual(self.provider.observations[-1]['screen_text'], self.vision.lines)

    def test_opening_only_and_unrelated_ocr_are_not_the_requested_result(self):
        self.vision.lines = ['Welcome', 'Search shipments', 'Help']
        task = self.task([{'type': 'launch_app', 'status': 'submitted', 'settled': True}])
        result = self.propose(task)
        self.assertEqual(result['status'], 'needs_user')
        self.assertEqual(self.provider.observations[-1]['screen_text'], self.vision.lines)
        self.assertEqual(len(task.receipts), 1)

    def test_opening_only_with_the_result_on_screen_finishes(self):
        self.verified_arrival()
        result = self.propose(self.task([{'type': 'launch_app', 'status': 'submitted', 'settled': True}]))
        self.assertEqual((result['status'], result['message']), ('done', self.vision.lines[-1]))
        self.assertEqual(len(self.provider.observations), 1)

    def test_no_screen_text_does_not_exempt_the_models_done(self):
        # No OCR text at all: the window title is still observed, and it is not the arrival date.
        for receipts in ((), WORKED):
            with self.subTest(receipts=receipts):
                result = self.propose(self.task(receipts))
                self.assertEqual(result['status'], 'needs_user')
                self.assertNotIn('Friday', result['message'])
                self.assertEqual(self.provider.observations[-1]['screen_text'], [])

    def test_no_screen_text_but_a_quoted_native_fact_finishes(self):
        self.bridge.value['window']['document'] = 'file:///tmp/A11y-test-918.txt'
        self.provider.verification = {'met': True, 'missing': '', 'evidence': 'A11y-test-918.txt'}
        result = self.propose(self.task(WORKED, goal='Open A11y-test-918.txt.'))
        self.assertEqual(result['status'], 'done')
        self.assertEqual(self.provider.observations[-1]['facts']['document_name'], 'A11y-test-918.txt')

    def test_nothing_observable_at_all_is_not_done_and_asks_no_verifier(self):
        self.bridge.value['window']['title'] = ''
        self.provider.verification = {'met': True, 'missing': '', 'evidence': 'invented'}
        result = self.propose(self.task(WORKED))
        self.assertEqual(result['status'], 'needs_user')
        self.assertEqual(self.provider.observations, [])

    def test_verifier_cannot_quote_the_planners_invented_answer(self):
        self.vision.lines = ['Shipment 918', 'Arrival date unavailable']
        self.provider.verification = {'met': True, 'evidence': 'Arrival: Friday 19 June.', 'missing': ''}
        self.assertEqual(self.propose(self.task(WORKED))['status'], 'needs_user')

    def test_failed_verifier_never_becomes_a_success(self):
        self.verified_arrival()
        self.provider.verification = RuntimeError('Verifier unavailable')
        result = self.propose(self.task(WORKED))
        self.assertEqual(result['status'], 'needs_user')
        self.assertIsNone(result['action'])

    def test_verifier_uncertainty_is_not_normalized_into_success(self):
        self.verified_arrival()
        for missing in ('?', 'None of the dates', 'nothing about the arrival'):
            with self.subTest(missing=missing):
                self.provider.verification['missing'] = missing
                self.assertEqual(self.propose(self.task(WORKED))['status'], 'needs_user')

    def test_an_empty_missing_field_written_as_filler_does_not_refuse_a_quoted_result(self):
        # The local model leaves a closing brace or a filler word in `missing` (live: "} is not on
        # the screen", "None is not on the screen"). The quote is still required.
        self.verified_arrival()
        for missing in (' } ', 'None', 'N/A', 'nothing', 'null.'):
            with self.subTest(missing=missing):
                self.provider.verification['missing'] = missing
                self.assertEqual(self.propose(self.task(WORKED))['status'], 'done')
        self.provider.verification.update(missing='None', evidence='The arrival date is shown')
        self.assertEqual(self.propose(self.task(WORKED))['status'], 'needs_user')

    def test_named_unsupported_requirement_in_the_legacy_reply_shape_blocks_done(self):
        self.verified_arrival()
        self.provider.verification = {'met': True, 'quote': self.vision.lines[-1], 'unsupported': 'the delivery time'}
        self.assertEqual(self.propose(self.task(WORKED))['status'], 'needs_user')

    def test_observed_result_can_finish_even_when_no_actions_were_needed(self):
        self.verified_arrival()
        result = self.propose(self.task())
        self.assertEqual(result, {'status': 'done', 'message': self.vision.lines[-1], 'action': None})
        self.assertEqual(self.provider.observations[-1]['goal'], 'Read the arrival date for this shipment.')

    def test_observed_result_after_work_can_finish(self):
        self.verified_arrival()
        task = self.task(WORKED)
        result = self.propose(task)
        self.assertEqual(result['status'], 'done')
        self.assertEqual(result['message'], self.vision.lines[-1])
        self.assertEqual(self.provider.steps, 1)
        self.assertEqual(task.timings[-1]['verification_calls'], 1)

    def test_a_successful_check_is_never_reused_after_the_observation_changes(self):
        self.verified_arrival()
        task = self.task(WORKED)
        self.assertEqual(self.propose(task)['status'], 'done')
        self.vision.lines = ['Arrival date unavailable']
        self.provider.verification = {'met': False, 'missing': 'Arrival date is unavailable.', 'evidence': self.vision.lines[0]}
        self.assertEqual(self.propose(task)['status'], 'needs_user')
        self.assertEqual(len(self.provider.observations), 2)

    def test_access_paused_during_the_check_does_not_publish_success(self):
        self.verified_arrival()
        self.provider.on_verify = lambda: setattr(self.desktop, 'generation', self.desktop.generation + 1)
        with self.assertRaisesRegex(ValueError, 'access was paused'):
            self.propose(self.task(WORKED))
        self.assertEqual(self.provider.answers, 0)

    def test_report_only_does_not_bypass_missing_evidence(self):
        task = self.task(WORKED)
        result = task.report_only(self.provider, [], 'data:image/png;base64,fixture', {},
                                  lambda: None, self.bridge.value, [])
        self.assertEqual(result['status'], 'needs_user')
        self.assertIsNone(result['action'])

    def test_an_observation_with_nothing_to_quote_fails_without_a_verifier_call(self):
        task = self.task(WORKED)
        self.provider.verification = {'met': True, 'missing': '', 'evidence': 'invented'}
        self.assertTrue(task.still_missing(self.provider, {'app': {'name': 'Fixture Browser'}, 'window': {'title': ''}}, [], {}))
        self.assertEqual(self.provider.observations, [])

    def test_verifier_missing_field_must_be_text(self):
        observed = {'screen_text': ['Arrival: Friday 19 June.']}
        for missing in (None, False, [], {}, 'the arrival time'):
            with self.subTest(missing=missing):
                self.assertFalse(completion_evidence({'met': True, 'evidence': observed['screen_text'][0],
                                                       'missing': missing}, observed))
        self.assertTrue(completion_evidence({'met': True, 'evidence': observed['screen_text'][0], 'missing': ''}, observed))

    def planning(self, during=lambda task: None):
        """A task whose plan (a model call) starts at a website; `during` runs while it is made."""
        class Planner(OptimisticPlanner):
            def plan_task(self, observation):
                during(task)
                return {'start': 'website', 'url': 'https://example.invalid/', 'steps': [], 'done_when': ''}
        self.provider = Planner()
        self.bridge.value['app'] = dict(SAFARI)
        self.bridge.apps = [{'name': 'Safari', 'bundle_id': 'com.apple.Safari'}]
        self.desktop.enabled = True
        task = self.task(goal='Find the shipment arrival date on example.invalid')
        task.planned = False
        task._catalog = [{'name': 'Safari', 'bundle_id': 'com.apple.Safari'}]
        return task

    def test_a_pause_while_the_plan_was_made_offers_no_opening(self):
        cases = {'pause': lambda task: setattr(self.desktop, 'enabled', False),
                 'generation': lambda task: setattr(self.desktop, 'generation', self.desktop.generation + 1),
                 'cancel': lambda task: task.cancelled.set()}
        for how, during in cases.items():
            with self.subTest(how=how):
                task = self.planning(during)
                with self.assertRaisesRegex(ValueError, 'stopped or access was paused'):
                    self.propose(task)
                self.assertIsNone(task.pending)
                self.assertEqual(task.receipts, [])

    def test_the_plan_opening_is_offered_when_nothing_was_paused(self):
        step = self.propose(self.planning())
        self.assertEqual((step['status'], step['action']['type']), ('action', 'browse_url'))


class VerifierReuseTests(Fixture):
    """One verifier call per observation inside a propose call; nothing kept afterwards."""
    CLICK = {'type': 'click_point', 'label': 'Shipment details', 'status': 'submitted', 'screen': 'before'}

    def test_already_verified_early_completion_is_not_checked_twice(self):
        self.verified_arrival()
        task = self.task([self.CLICK])
        self.assertEqual(self.propose(task)['status'], 'done')
        self.assertEqual(self.calls(), (0, 1, 1))
        self.assertFalse(hasattr(task, '_completion_cache'), 'Private evidence must not outlive the decision call.')

    def test_a_refused_early_check_is_reused_for_the_models_done_on_the_same_observation(self):
        self.vision.lines = ['Shipment 918', 'Arrival date unavailable']
        task = self.task([self.CLICK])
        result = self.propose(task)
        self.assertEqual(result['status'], 'needs_user')
        self.assertEqual(self.provider.steps, 2)  # the done, then one correction
        self.assertEqual(len(self.provider.observations), 1)
        self.assertEqual(task.timings[-1]['verification_calls'], 1)

    def test_a_verifier_outage_is_not_kept_as_a_result(self):
        # The early check fails once; the model's done on the same screen asks again and is verified.
        self.verified_arrival()
        self.provider.verification = [RuntimeError('Verifier timed out'), self.provider.verification]
        result = self.propose(self.task([self.CLICK]))
        self.assertEqual(result['status'], 'done')
        self.assertEqual((self.provider.steps, len(self.provider.observations)), (1, 2))

    def test_a_check_is_only_reused_for_the_identical_observation(self):
        self.verified_arrival()
        task = self.task(WORKED)
        task._completion_cache = {}  # as inside one propose call
        snapshot = self.bridge.sample()
        shown = [{'text': line, 'x': .1, 'y': .1, 'width': .5, 'height': .05} for line in self.vision.lines]
        self.assertEqual(task.still_missing(self.provider, snapshot, shown, {}), '')
        self.assertEqual(task.still_missing(self.provider, snapshot, shown, {}), '')
        self.assertEqual(len(self.provider.observations), 1)
        self.provider.verification = {'met': False, 'missing': 'the arrival date', 'evidence': ''}
        other = [dict(shown[0], text='Arrival date unavailable')]
        self.assertEqual(task.still_missing(self.provider, snapshot, other, {}), 'the arrival date')
        self.assertEqual(len(self.provider.observations), 2)

    def test_access_pause_during_early_goal_check_blocks_its_answer(self):
        self.verified_arrival()
        task = self.task([self.CLICK])
        self.provider.on_verify = lambda: setattr(self.desktop, 'enabled', False)
        with self.assertRaisesRegex(ValueError, 'access was paused'):
            self.propose(task)
        self.assertEqual(self.provider.answers, 0, 'No answer may be written from the screen after access was paused.')
        self.assertFalse(hasattr(task, '_completion_cache'))

    def test_access_pause_while_a_code_steps_answer_is_written_does_not_publish_it(self):
        # The menu fallback's "already met" finish: verified, then its answer is written. Paused
        # during that last call, the answer must not come back as a result.
        self.verified_arrival()
        task = self.task([{'type': 'click_point', 'label': 'Arrival: Friday 19 June.', 'status': 'submitted',
                           'effect': 'nothing changed on screen'}])
        self.provider.on_answer = lambda: setattr(self.desktop, 'enabled', False)
        with self.assertRaisesRegex(ValueError, 'access was paused'):
            self.propose(task)
        self.assertEqual(self.calls(), (0, 1, 1))


class ReceiptOnlyCodeStepTests(Fixture):
    """Code steps that end on "my action was submitted": native state decides when it can, else the verifier."""
    SORT = {'type': 'choose_menu', 'label': 'View > Sort By > Name', 'status': 'submitted'}

    def finder(self, *, sort='kind column', view='list view'):
        self.bridge.value['app'] = {'name': 'Finder', 'bundle_id': 'com.apple.finder', 'pid': 11}
        self.bridge.value['finder'] = {'target': '/tmp/A11y test 918 fixture', 'view': view, 'sort': sort, 'selection': []}
        self.bridge.value['window']['title'] = 'A11y test 918 fixture'
        self.vision.lines = ['A11y test 918 fixture', 'zebra.txt', 'apple.txt']

    def sort(self, receipt=None):
        return self.propose(self.task([receipt or self.SORT], goal='Sort this Finder folder by name.'))

    def safari(self, url='https://example.invalid/start'):
        self.bridge.value['app'] = dict(SAFARI)
        self.bridge.value['window'].update(title='Start Page', url=url)
        self.bridge.value['available_apps'] = [{'name': 'Safari', 'bundle_id': 'com.apple.Safari'}]
        self.bridge.apps = [{'name': 'Safari', 'bundle_id': 'com.apple.Safari'}]
        self.vision.lines = ['Start Page', 'Search the web']

    # --- Finder sort: the list view's own sort column is a native fact ---

    def test_native_sort_column_that_matches_finishes_without_a_model_call(self):
        for shown in ('name column', 'Name'):
            with self.subTest(shown=shown):
                self.finder(sort=shown)
                self.assertEqual(self.sort(), {'status': 'done', 'action': None, 'message': 'Sorted by name.'})
        self.assertEqual(self.calls(), (0, 0, 0))

    def test_a_verifier_outage_cannot_undo_a_natively_confirmed_sort(self):
        self.finder(sort='name column')
        self.provider.verification = RuntimeError('Verifier timed out')
        self.assertEqual(self.sort()['status'], 'done')
        self.assertEqual(self.provider.observations, [])

    def test_native_sort_column_that_contradicts_cannot_be_overruled_by_the_verifier(self):
        for shown in ('kind column', 'none'):
            with self.subTest(shown=shown):
                self.finder(sort=shown)
                self.provider.verification = {'met': True, 'missing': '', 'evidence': 'zebra.txt'}
                result = self.sort()
                self.assertEqual(result['status'], 'needs_user')
                self.assertIsNone(result['action'])
                self.assertIn('not sorted by name', result['message'])
        self.assertEqual(self.calls(), (0, 0, 0))

    def test_a_sort_that_shows_a_moment_later_is_seen_on_the_second_look(self):
        self.finder(sort='kind column')
        self.slept.side_effect = lambda _: self.bridge.value['finder'].update(sort='name column')
        self.assertEqual(self.sort()['status'], 'done')
        self.assertEqual((self.slept.call_count, self.calls()), (1, (0, 0, 0)))

    def test_a_failed_receipt_is_not_a_sorted_folder(self):
        self.finder(sort='name column', view='icon view')
        self.provider.verification = {'met': True, 'missing': '', 'evidence': 'zebra.txt'}
        result = self.sort(dict(self.SORT, status='failed'))
        self.assertEqual(result['status'], 'needs_user')
        self.assertEqual(self.provider.observations, [])

    def test_where_the_sort_column_says_nothing_the_verifier_decides(self):
        # Icon view, grouping instead of sorting, and a column Finder's scripting cannot name:
        # the list view's sort column does not describe the result there.
        grouped = dict(self.SORT, native_result={'submitted': True, 'path': 'View > Group By > Name'})
        cases = [('icon view', 'kind column', self.SORT, 'Sort this Finder folder by name.'),
                 ('list view', 'kind column', grouped, 'Sort this Finder folder by name.'),
                 ('list view', 'name column', dict(self.SORT, label='View > Sort By > Date Added'), 'Sort this Finder folder by date added.')]
        for view, shown, receipt, goal in cases:
            with self.subTest(view=view, receipt=receipt['label'], verifier='no'):
                self.finder(sort=shown, view=view)
                self.provider.verification = {'met': False, 'missing': 'Finder is not sorted that way.', 'evidence': 'apple.txt'}
                checks = len(self.provider.observations)
                result = self.propose(self.task([receipt], goal=goal))
                self.assertEqual(result['status'], 'needs_user')
                self.assertIn('Finder is not sorted that way', result['message'])
                self.assertEqual(len(self.provider.observations), checks+1)
                self.assertEqual(self.provider.observations[-1]['facts']['finder']['sort'], shown)
            with self.subTest(view=view, receipt=receipt['label'], verifier='yes'):
                self.provider.verification = {'met': True, 'missing': '', 'evidence': 'apple.txt'}
                self.assertEqual(self.propose(self.task([receipt], goal=goal))['status'], 'done')
        self.assertEqual(self.provider.steps, 0)

    # --- no screen text is not an exemption; menu ---

    def test_no_screen_text_does_not_exempt_a_code_step(self):
        self.safari()
        self.vision.lines = []
        result = self.propose(self.task([{'type': 'choose_menu', 'label': 'View', 'status': 'submitted'}],
                                        goal='Show me the View menu in Safari.'))
        self.assertEqual(result['status'], 'needs_user')
        self.assertEqual(self.calls(), (0, 1, 0))
        self.finder(view='icon view')
        self.vision.lines = []
        self.assertEqual(self.sort()['status'], 'needs_user')
        self.assertEqual(self.calls(), (0, 2, 0))

    def test_noop_tab_history_and_menu_receipts_do_not_prove_navigation(self):
        self.safari()
        self.bridge.value['window'].pop('url')  # the address cannot be read: nothing native to go by
        cases = [
            ('Switch to the Wikipedia tab.', {'type': 'browse_url', 'label': 'Switch tabs', 'status': 'submitted', 'settled': True}),
            ('Go back in Safari.', {'type': 'press_key', 'key': 'cmd_[', 'label': 'Back', 'status': 'submitted'}),
            ('Show me the View menu in Safari.', {'type': 'choose_menu', 'label': 'View', 'status': 'submitted'}),
        ]
        for goal, receipt in cases:
            with self.subTest(goal=goal):
                checks = len(self.provider.observations)
                result = self.propose(self.task([receipt], goal=goal))
                self.assertEqual(result['status'], 'needs_user')
                self.assertIsNone(result['action'])
                self.assertEqual(self.provider.steps, 0)
                self.assertEqual(len(self.provider.observations), checks+1)

    def test_an_opened_menu_quoted_from_the_screen_finishes(self):
        self.safari()
        self.vision.lines = ['Start Page', 'Show Tab Overview', 'Reload Page']
        self.provider.verification = {'met': True, 'missing': '', 'evidence': 'Show Tab Overview'}
        task = self.task([{'type': 'choose_menu', 'label': 'View', 'status': 'submitted'}], goal='Show me the View menu in Safari.')
        result = self.propose(task)
        self.assertEqual((result['status'], result['message']), ('done', 'The View menu is open.'))
        self.assertEqual(self.provider.steps, 0)

    def test_access_paused_during_a_code_steps_check_does_not_publish_success(self):
        self.finder(sort='name column', view='icon view')
        self.provider.verification = {'met': True, 'missing': '', 'evidence': 'apple.txt'}
        self.provider.on_verify = lambda: setattr(self.desktop, 'enabled', False)
        with self.assertRaisesRegex(ValueError, 'access was paused'):
            self.sort()

    # --- back / forward: the page address before and after each press ---

    def back(self, *before, goal='Go back in Safari.'):
        task = self.task(goal=goal)
        for address in before:
            task.page_seen = address  # the address when the key was chosen, as propose records it
            task.pending = {'type': 'press_key', 'key': 'cmd_['}
            task.record({'type': 'press_key', 'key': 'cmd_[', 'label': 'Press ⌘[ in Safari', 'status': 'submitted'})
        return task

    def test_back_that_changed_the_page_address_finishes_without_a_model_call(self):
        self.safari('https://example.invalid/list')
        result = self.propose(self.back('https://example.invalid/list/item-918'))
        self.assertEqual(result, {'status': 'done', 'action': None, 'message': 'Went back.'})
        self.assertEqual((self.calls(), self.slept.call_count), ((0, 0, 0), 0))

    def test_back_that_left_the_page_address_unchanged_cannot_be_overruled_by_the_verifier(self):
        self.safari('https://example.invalid/list')
        self.provider.verification = dict(YES_TITLE)
        result = self.propose(self.back('https://example.invalid/list'))
        self.assertEqual(result['status'], 'needs_user')
        self.assertIsNone(result['action'])
        self.assertIn('did not change', result['message'])
        self.assertEqual(self.calls(), (0, 0, 0))
        self.assertEqual(self.slept.call_count, 3)  # looked again before giving up

    def test_a_page_that_loads_a_moment_later_is_seen_on_the_second_look(self):
        self.safari('https://example.invalid/list/item-918')
        self.slept.side_effect = lambda _: self.bridge.value['window'].update(url='https://example.invalid/list')
        result = self.propose(self.back('https://example.invalid/list/item-918'))
        self.assertEqual(result, {'status': 'done', 'action': None, 'message': 'Went back.'})
        self.assertEqual((self.slept.call_count, self.calls()), (1, (0, 0, 0)))

    def test_stop_during_the_second_look_ends_the_task(self):
        self.safari('https://example.invalid/list')
        task = self.back('https://example.invalid/list')
        self.slept.side_effect = lambda _: task.cancelled.set()
        with self.assertRaisesRegex(ValueError, 'stopped'):
            self.propose(task)
        self.assertEqual(self.vision.captures, 1, 'Nothing is captured after the stop.')

    def test_every_press_of_several_must_have_changed_the_address(self):
        goal = 'Go back two pages in Safari.'
        self.safari('https://example.invalid/a')
        result = self.propose(self.back('https://example.invalid/c', 'https://example.invalid/b', goal=goal))
        self.assertEqual((result['status'], self.calls()), ('done', (0, 0, 0)))
        self.safari('https://example.invalid/b')   # the second press changed nothing
        self.provider.verification = dict(YES_TITLE)
        result = self.propose(self.back('https://example.invalid/c', 'https://example.invalid/b', goal=goal))
        self.assertEqual((result['status'], self.calls()), ('needs_user', (0, 0, 0)))

    def test_back_with_an_unreadable_address_is_left_to_the_verifier(self):
        self.safari()
        self.bridge.value['window'].pop('url')
        task = self.back('https://example.invalid/list/item-918')
        self.assertEqual(self.propose(task)['status'], 'needs_user')
        self.provider.verification = dict(YES_TITLE)
        self.assertEqual(self.propose(task)['status'], 'done')
        self.assertEqual((self.calls(), self.slept.call_count), ((0, 2, 0), 0))

    # --- tab switch and sites: the address code compared ---

    def switch(self, bundle='com.apple.Safari'):
        task = self.task(goal='Switch to the Wikipedia tab.')
        task._catalog = [{'name': 'Safari', 'bundle_id': 'com.apple.Safari'}]
        task.pending = {'type': 'browse_url', 'bundle_id': bundle, 'url': 'https://www.wikipedia.org', 'browser_mode': 'default'}
        task.record({'type': 'browse_url', 'label': 'Switch to https://www.wikipedia.org', 'status': 'submitted'})
        return task

    def test_tab_switch_whose_address_code_compared_finishes_without_a_model_call(self):
        self.safari()
        self.bridge.value['window'].pop('url')  # only the page read knows the address
        with patch('companion.mac_apps.read_page', return_value=('Wikipedia', 'https://en.wikipedia.org/wiki/Main_Page', '')):
            result = self.propose(self.switch())
        self.assertEqual(result, {'status': 'done', 'action': None, 'message': 'Switched to the Wikipedia tab.'})
        self.assertEqual(self.calls(), (0, 0, 0))

    def test_browsers_without_a_page_read_are_confirmed_from_the_observed_address(self):
        # Windows browsers and Firefox: mac_apps.read_page cannot read them (the fixture's raises if
        # called). The observation's own window.url is on the site, and that is compared instead.
        for bundle, platform in (('browser:edge', 'Windows'), ('org.mozilla.firefox', 'macOS'), ('com.apple.Safari', 'macOS')):
            with self.subTest(bundle=bundle):
                self.safari('https://en.wikipedia.org/wiki/Main_Page')
                self.bridge.value.update(app={'name': 'Browser', 'bundle_id': bundle, 'pid': 11}, platform=platform)
                task = self.switch(bundle)
                result = self.propose(task)
                self.assertEqual(result, {'status': 'done', 'action': None, 'message': 'Switched to the Wikipedia tab.'})
                self.assertIs(task.receipts[-1]['arrived'], True)
        self.assertEqual(self.calls(), (0, 0, 0))

    def test_an_observed_address_on_another_site_confirms_nothing(self):
        self.safari('https://example.invalid/start')
        self.bridge.value.update(app={'name': 'Edge', 'bundle_id': 'browser:edge', 'pid': 11}, platform='Windows')
        task = self.switch('browser:edge')
        with patch('companion.mac_apps.run_script', side_effect=AssertionError('osascript must not run')):
            self.read_page.stop()   # the real page read: it refuses this browser without scripting anything
            try:
                result = self.propose(task)
            finally:
                self.read_page.start()
        self.assertNotIn('arrived', task.receipts[-1])
        self.assertEqual(result['status'], 'needs_user')
        self.assertEqual(self.calls(), (0, 1, 0))

    def test_a_page_read_on_another_site_is_retried_and_never_marked_arrived(self):
        self.safari('https://example.invalid/start')
        task = self.switch()
        with patch('companion.mac_apps.read_page', return_value=('Start', 'https://example.invalid/start', '')):
            step = self.propose(task)
        self.assertEqual((step['status'], step['action']['type'], step['action']['url']), ('action', 'browse_url', 'https://www.wikipedia.org'))
        self.assertNotIn('arrived', task.receipts[-1])
        self.assertEqual(self.calls(), (0, 0, 0))

    def test_tab_switch_with_an_unreadable_address_is_checked_and_not_assumed(self):
        self.safari()
        self.bridge.value['window'].pop('url')
        with patch('companion.mac_apps.read_page', return_value=('', '', '')):
            result = self.propose(self.switch())
        self.assertEqual(result['status'], 'needs_user')
        self.assertEqual(self.calls(), (0, 1, 0))

    def test_an_earlier_arrival_does_not_confirm_a_later_tab_switch(self):
        # The task once reached the site, then something else ran: that old arrival is not this result.
        self.safari()
        task = self.task([{'type': 'browse_url', 'label': 'Open wikipedia.org', 'status': 'submitted', 'settled': True, 'arrived': True,
                           'wanted': {'bundle_id': 'com.apple.Safari', 'url': 'https://www.wikipedia.org'}},
                          {'type': 'press_key', 'key': 'cmd_t', 'label': 'New tab', 'status': 'submitted'}],
                         goal='Switch to the Wikipedia tab.')
        result = self.propose(task)
        self.assertEqual(result['status'], 'needs_user')
        self.assertEqual(self.calls(), (0, 1, 0))

    def sites(self):
        task = self.task(goal='Open apple.com and duckduckgo.com in two separate tabs.')
        task._catalog = [{'name': 'Safari', 'bundle_id': 'com.apple.Safari'}]
        return task

    def test_sites_whose_addresses_code_compared_finish_without_a_model_call(self):
        self.safari()
        pages = iter([('Apple', 'https://www.apple.com/', ''), ('DuckDuckGo', 'https://duckduckgo.com/', '')])
        with patch('companion.mac_apps.read_page', side_effect=lambda bundle: next(pages)):
            result = self.run_code_steps(self.sites())
        self.assertEqual(result, {'status': 'done', 'action': None, 'message': 'Opened them in separate tabs.'})
        self.assertEqual(self.calls(), (0, 0, 0))

    def test_a_site_opening_that_never_ran_does_not_count_as_opened(self):
        self.safari()
        task = self.sites()
        with patch('companion.mac_apps.read_page', return_value=('Apple', 'https://www.apple.com/', '')):
            result = self.run_code_steps(task, refuse=('Open duckduckgo.com in Safari',))
        self.assertEqual([receipt['wanted'].get('url') for receipt in task.receipts], ['https://apple.com'])
        self.assertEqual(result['status'], 'needs_user')
        self.assertEqual(self.calls(), (0, 1, 0))

    def test_recorded_site_openings_do_not_prove_that_the_tabs_exist(self):
        self.safari()
        task = self.sites()
        task.receipts = [{'type': 'browse_url', 'status': 'submitted', 'settled': True}]
        task.sites_opened = ['https://apple.com', 'https://duckduckgo.com']
        result = self.propose(task)
        self.assertEqual(result['status'], 'needs_user')
        self.assertEqual(self.provider.steps, 0)


class CalculatorKeypadEvidenceTests(Fixture):
    """The keypad path reports the sum as entered only when Calculator accepted every key."""
    KEYS = ['All Clear', '7', 'Add', '8', 'Equals']

    def calculator(self):
        self.bridge.value['app'] = {'name': 'Calculator', 'bundle_id': 'com.apple.calculator', 'pid': 11}
        self.bridge.value['window']['title'] = 'Calculator'
        self.bridge.value['controls'] = [
            {'id': 'control-%d' % i, 'label': label, 'role': 'AXButton', 'enabled': True,
             'bounds': {'x': 10+i*30, 'y': 300, 'width': 28, 'height': 28}} for i, label in enumerate(self.KEYS)]
        return self.task(goal='Open Calculator and add 7 + 8')

    def assert_entered_by_code(self, result):
        self.assertEqual(result['status'], 'done')
        self.assertIn('The answer is 15', result['message'])
        self.assertEqual((self.vision.captures, self.calls()), (0, (0, 0, 0)))

    def assert_left_to_the_display(self, result):
        """Not reported as entered: the display is looked at, and here it does not show 15."""
        self.assertEqual(result['status'], 'needs_user')
        self.assertNotIn('The answer is', result['message'])
        self.assertEqual(self.vision.captures, 1)
        self.assertTrue(self.provider.observations)

    def wrong_display(self):
        self.vision.lines = ['Calculator', '7']
        self.provider.verification = {'met': False, 'missing': 'Calculator does not show 15.', 'evidence': '7'}

    def test_every_key_accepted_finishes_without_a_screenshot_or_model_call(self):
        task = self.calculator()
        result = self.run_code_steps(task)
        self.assertEqual([receipt['label'] for receipt in task.receipts], self.KEYS)
        self.assert_entered_by_code(result)

    def test_switching_back_to_calculator_between_keys_does_not_lose_the_presses(self):
        task = self.calculator()
        focus = {'type': 'focus_app', 'label': 'Switch to Calculator', 'status': 'submitted'}
        result = self.run_code_steps(task, after={'7': focus, 'Equals': focus})
        self.assertEqual([receipt['type'] for receipt in task.receipts].count('focus_app'), 2)
        self.assert_entered_by_code(result)

    def test_a_key_that_never_ran_is_not_reported_as_entered(self):
        task = self.calculator()
        self.wrong_display()
        result = self.run_code_steps(task, refuse=('8',))
        self.assertEqual([receipt['label'] for receipt in task.receipts], ['All Clear', '7', 'Add', 'Equals'])
        self.assert_left_to_the_display(result)

    def test_a_key_press_that_failed_is_not_reported_as_entered(self):
        task = self.calculator()
        self.wrong_display()
        result = self.run_code_steps(task, fail=('8',))
        self.assertEqual([receipt['status'] for receipt in task.receipts].count('failed'), 1)
        self.assert_left_to_the_display(result)

    def test_another_receipt_in_a_missing_keys_place_is_not_that_key(self):
        # As many receipts as keys, and as many presses as keys, but one of them is not the key asked for.
        for stand_in in ({'type': 'focus_app', 'label': '8', 'status': 'submitted'},
                         {'type': 'press_control', 'label': '7', 'status': 'submitted'}):
            with self.subTest(stand_in=stand_in['type']):
                self.vision.captures = 0
                self.provider.observations.clear()
                task = self.calculator()
                self.wrong_display()
                result = self.run_code_steps(task, refuse=('8',), after={'Add': stand_in})
                self.assertEqual(len(task.receipts), len(self.KEYS))
                self.assert_left_to_the_display(result)

    def test_after_a_missed_key_the_display_itself_can_still_finish_the_task(self):
        task = self.calculator()
        self.vision.lines = ['Calculator', '15']
        self.provider.verification = {'met': True, 'missing': '', 'evidence': '15'}
        self.provider.answer = 'The result is 15.'
        result = self.run_code_steps(task, refuse=('All Clear',))
        self.assertEqual((result['status'], result['message']), ('done', 'The result is 15.'))


if __name__ == '__main__':
    unittest.main()
