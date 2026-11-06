"""Task-loop outcomes found by the simulated-desktop evaluation, through the real loop.

Scripted model replies and a fake bridge; nothing here reaches an app or a model. Each test
names the mechanism it holds:
- every listed part of a goal needs its own proof, of the kind that can prove it;
- a fact read on an earlier screen still counts later in the same task;
- a step that worked is not simply repeated; a tick is not undone;
- a state-only control is judged by its own look, not by the words around it;
- a button press that commits something is confirmed whatever risk the step model gave it;
- Stop reaches the planning and classification requests.
"""
import base64
from copy import deepcopy
import importlib.util
import json
import os
import threading
import time
import unittest
from unittest.mock import Mock, patch

from companion.computer_task import (ComputerTask, SYSTEM, CHOOSE_SYSTEM, MET_SYSTEM, ANSWER_SYSTEM, COMMIT_SYSTEM, FILE_INTERACTION_SYSTEM,
                                     FILE_INTERACTION_SCHEMA, check_proven, completion_evidence, completion_result, completion_schema,
                                     region_look, screen_signature, unproven_parts)
from companion.desktop_context import DesktopContext
from companion.ollama import Planner, RequestCancelled


SCREEN = {'x': 0, 'y': 0, 'width': 1000, 'height': 700}
FINDER = {'name': 'Finder', 'bundle_id': 'com.apple.finder', 'pid': 42}
SAFARI = {'name': 'Safari', 'bundle_id': 'com.apple.Safari', 'pid': 11}
FORMS = {'name': 'Formbook', 'bundle_id': 'org.a11y.fixture.forms', 'pid': 12}


def check(part, kind, quote, shown=True):
    return {'part': part, 'part_kind': kind, 'quote': quote, 'shown': shown}


def verdict(*checks, met=True, missing=''):
    """A verifier reply as the model writes it. The summary fields are what the loop used to
    trust on their own; the listed checks now decide."""
    shown = [item['quote'] for item in checks if item['shown']]
    return {'checks': list(checks), 'evidence': shown[0] if shown else '', 'met': met, 'missing': missing}


def target(role, label, y, *, x=100, width=240, height=26, **extra):
    return {'role': role, 'label': label, 'page': False, 'bounds': {'x': x, 'y': y, 'width': width, 'height': height}, **extra}


def click(target_id, risk='navigation'):
    return {'status': 'action', 'message': 'Clicking it.', 'risk': risk, 'action': {'type': 'click_target', 'target_id': target_id}}


def done(message='It is done.'):
    return {'status': 'done', 'message': message, 'risk': 'navigation', 'action': None}


class Bridge:
    def __init__(self, value):
        self.value = value

    def sample(self):
        return deepcopy(self.value)


class Scripted:
    """Step replies in order; the verifier, answer writer and commit check answer by their own prompt."""
    def __init__(self):
        self.steps, self.step_messages = [], []
        self.verification, self.observations = verdict(check('the goal', 'find_out', '', False), met=False, missing='the goal'), []
        self.answer, self.answer_inputs = 'Here it is.', []
        self.commit, self.commit_inputs = {'effect': 'navigate'}, []

    def complete_structured(self, system, messages, **kwargs):
        if system == SYSTEM:
            self.step_messages.append(messages)
            return deepcopy(self.steps.pop(0))
        content = json.loads(messages[-1]['content'])
        if system == MET_SYSTEM:
            self.observations.append(content)
            reply = self.verification(content) if callable(self.verification) else self.verification
            return deepcopy(reply)
        if system == ANSWER_SYSTEM:
            self.answer_inputs.append(content)
            return {'message': self.answer}
        if system == COMMIT_SYSTEM:
            self.commit_inputs.append(content)
            if isinstance(self.commit, Exception):
                raise self.commit
            return deepcopy(self.commit)
        if system == FILE_INTERACTION_SYSTEM:
            return {'goal_verb': '', 'interaction': 'other'}
        if system == CHOOSE_SYSTEM:
            # A provider with plan_task also gets the choose step (task_choices); "none" leaves the step
            # to the scripted image replies these tests are about.
            return {'note': '', 'step': 'none', 'text': ''}
        raise AssertionError('unexpected model stage: '+system[:50])


class Loop(unittest.TestCase):
    def setUp(self):
        settings = patch.dict(os.environ, {'A11Y_ROUTES': '0', 'A11Y_WEB_ANSWERS': '0'})
        settings.start()
        self.addCleanup(settings.stop)
        for name in ('companion.mac_apps.read_page',):   # no test here may reach a real browser
            patcher = patch(name, side_effect=AssertionError('no real app in tests'))
            patcher.start()
            self.addCleanup(patcher.stop)
        sleeping = patch('companion.computer_task.time.sleep')
        sleeping.start()
        self.addCleanup(sleeping.stop)
        self.bridge = Bridge({'status': 'available', 'platform': 'macOS', 'accessibility': 'granted', 'app': dict(SAFARI),
                              'window': {'title': 'Start', 'bounds': {'x': 0, 'y': 0, 'width': 1000, 'height': 700}},
                              'controls': [], 'targets': [], 'available_apps': [], 'focused_role': 'AXWindow'})
        self.desktop = DesktopContext(self.bridge, clock=lambda: 100)
        self.desktop.enabled = True
        self.lines, self.image = [], 'data:image/png;base64,fixture'
        self.vision = Mock(enabled=True, generation=0)
        self.vision.capture.side_effect = lambda *args, **kwargs: {
            'image_data': self.image, 'display_id': 7, 'screen': dict(SCREEN), 'captured_monotonic': time.monotonic(),
            'text_regions': [{'text': line, 'x': .5, 'y': .1+i*.06, 'width': .3, 'height': .04} for i, line in enumerate(self.lines)]}
        self.vision.click_guard.return_value = {'region_hash': 'fixture'}
        self.provider = Scripted()

    def show(self, *, app=None, title=None, url=None, targets=None, lines=None, finder=None):
        value = self.bridge.value
        if app:
            value['app'] = dict(app)
        if title is not None:
            value['window']['title'] = title
        if url is not None:
            value['window']['url'] = url
        if targets is not None:
            value['targets'] = targets
        if finder is not None:
            value['finder'] = finder
        if lines is not None:
            self.lines = list(lines)

    def task(self, goal, mode='inspect'):
        task = ComputerTask(goal, automatic=True, goal_mode=mode)
        task.planned = True
        return task

    def propose(self, task):
        return task.propose(self.desktop, self.vision, self.provider, {'x': 0, 'y': 0})

    def run_step(self, task, **receipt):
        """Propose, and record the proposed action as the app accepted it."""
        step = self.propose(task)
        if step['status'] == 'action':
            task.record(dict({'type': step['action']['type'], 'label': step['action'].get('label', ''), 'status': 'submitted'}, **receipt))
        return step


class EveryPartOfTheGoal(Loop):
    def test_one_proven_part_is_not_the_goal(self):
        # sim, two facts: "done" with the ticket price while the reply itself said the car park charge was missing.
        self.show(title='Tickets', lines=['Tickets', 'Adult ticket: £11.60'])
        self.provider.steps = [done('Adult ticket £11.60. The car park charge is not on this page.')]*2
        self.provider.verification = verdict(check('the price of an adult ticket', 'find_out', 'Adult ticket: £11.60'),
                                             check('the car park charge', 'find_out', '', False), met=True)
        task = self.task('I need two things from this site: the price of an adult ticket and the car park charge.')
        task.receipts = [dict(type='click_point', label='Tickets', status='submitted', screen='before')]
        step = self.propose(task)
        self.assertEqual(step['status'], 'needs_user')
        self.assertIn('the car park charge', step['message'])
        # The model was told which part is outstanding before the task gave up.
        self.assertIn('the car park charge', self.provider.step_messages[1][-1]['content'])

    def test_the_list_decides_even_when_the_summary_says_met(self):
        reply = completion_result(verdict(check('open the folder', 'go_to', 'Invoices'), check('then 2024 inside it', 'go_to', '', False), met=True))
        self.assertIs(reply['met'], False)
        self.assertEqual(reply['missing'], 'then 2024 inside it')
        self.assertEqual(reply['evidence'], 'Invoices')

    def finder_observation(self, task, folder='/Users/a/Documents/Invoices/', selection=()):
        self.show(app=FINDER, title=folder.rstrip('/').rsplit('/', 1)[-1], lines=['2023', '2024', '2025'],
                  finder={'target': folder, 'view': 'list view', 'selection': list(selection)})
        snapshot = self.desktop.collect_once(targets=True)
        return task.completion_observation(snapshot, [{'text': line, 'x': .2, 'y': .2+i*.05, 'width': .2, 'height': .03} for i, line in enumerate(self.lines)])

    def test_a_folder_listed_in_finder_is_not_the_folder_that_is_open(self):
        # sim, folder navigation: inside "Invoices" with "2024" in the list, the task said both were open.
        task = self.task('In Finder, open the Invoices folder and then the 2024 folder inside it.')
        observation = self.finder_observation(task)
        reply = completion_result(verdict(check('open the Invoices folder', 'go_to', 'Invoices'), check('then the 2024 folder inside it', 'go_to', '2024')))
        self.assertFalse(completion_evidence(reply, observation))
        self.assertEqual(unproven_parts(reply, observation), ['then the 2024 folder inside it'])
        inside = self.finder_observation(task, '/Users/a/Documents/Invoices/2024/')
        reply = completion_result(verdict(check('open the Invoices folder', 'go_to', '/Users/a/Documents/Invoices/2024/'),
                                          check('then the 2024 folder inside it', 'go_to', '2024')))
        self.assertTrue(completion_evidence(reply, inside))
        # sim, long folder: the folder that is open was quoted as proof of opening another one.
        elsewhere = completion_result(verdict(check('Open the Zelcroft project folder.', 'go_to', 'Invoices')))
        self.assertFalse(completion_evidence(elsewhere, observation))

    def test_the_early_check_does_not_finish_a_goal_with_a_later_folder_outstanding(self):
        task = self.task('In Finder, open the Invoices folder and then the 2024 folder inside it.')
        self.show(app=FINDER, title='Invoices', lines=['2023', '2024', '2025'],
                  finder={'target': '/Users/a/Documents/Invoices/', 'view': 'list view', 'selection': []},
                  targets=[target('AXTextField', '2024', 200, file_item=True)])
        task.receipts = [dict(type='click_point', label='Invoices', status='submitted', screen='before', click_count=2,
                              wanted={'file_parent': '/Users/a/Documents', 'file_name': 'Invoices'}, settled=True)]
        self.provider.verification = verdict(check('open the Invoices folder', 'go_to', 'Invoices'), check('then the 2024 folder inside it', 'go_to', '2024'))
        self.provider.steps = [{'status': 'action', 'message': 'Opening 2024.', 'risk': 'navigation', 'action': {'type': 'open_target', 'target_id': 't1'}}]
        step = self.propose(task)
        self.assertEqual(step['status'], 'action')   # old: done, "the 2024 folder is visible within it"
        self.assertEqual((step['action']['label'], step['action']['click_count']), ('2024', 2))
        self.assertEqual(self.provider.answer_inputs, [])

    def test_selecting_needs_a_selection_fact_not_a_visible_or_opened_name(self):
        task = self.task('Open the Handover folder and select Rota.pdf in it.')
        reply = completion_result(verdict(check('Open the Handover folder', 'go_to', 'Handover'), check('select Rota.pdf in it', 'select', 'Rota.pdf')))
        self.lines = ['Rota.pdf']
        listed = self.finder_observation(task, '/Users/a/Documents/Handover/')
        listed['screen_text'] = ['Rota.pdf']
        self.assertFalse(completion_evidence(reply, listed))                    # visible in the list
        selected = self.finder_observation(task, '/Users/a/Documents/Handover/', selection=['Rota.pdf'])
        self.assertTrue(completion_evidence(reply, selected))                   # Finder reports it selected
        opened = {'goal': task.goal, 'app': 'Preview', 'window': 'Rota.pdf', 'last_click': 'Rota.pdf', 'screen_text': ['Rota.pdf'],
                  'facts': {'document': 'file:///Users/a/Documents/Handover/Rota.pdf', 'document_name': 'Rota.pdf'}}
        wrong = completion_result(verdict(check('Open the Handover folder', 'go_to', 'file:///Users/a/Documents/Handover/Rota.pdf'),
                                          check('select Rota.pdf in it', 'select', 'Rota.pdf')))
        self.assertFalse(completion_evidence(wrong, opened))                    # opened in Preview instead (sim, folder and file)
        # Neither part: the document's address names the folder, but only the document itself is open.
        self.assertEqual(unproven_parts(wrong, opened), ['Open the Handover folder', 'select Rota.pdf in it'])
        self.assertTrue(check_proven(check('Open Rota.pdf', 'go_to', 'file:///Users/a/Documents/Handover/Rota.pdf'), opened))

    def test_what_the_user_said_not_to_do_is_not_a_result_to_prove(self):
        observation = {'goal': 'Put Ada in the Name box. Don’t submit the form.', 'window': 'Form', 'screen_text': ['Name', 'Ada'], 'facts': {}}
        reply = completion_result(verdict(check('Put Ada in the Name box', 'change', 'Ada'), check('Don’t submit the form', 'avoid', '', False)))
        self.assertTrue(completion_evidence(reply, observation))
        only_avoid = completion_result(verdict(check('Don’t submit the form', 'avoid', '', False)))
        self.assertFalse(completion_evidence(only_avoid, observation))
        # sim, moved page: "Try the price list" was listed as a destination and the answer on the Rates page refused.
        rates = {'goal': 'What’s the day hire rate? Try the price list.', 'window': 'Rates', 'screen_text': ['Rates', 'Day hire: £18.10'], 'facts': {},
                 '_tried': ['Price list', 'Rates']}
        reply = completion_result(verdict(check('What’s the day hire rate?', 'find_out', 'Day hire: £18.10'), check('Try the price list.', 'hint', '', False)))
        self.assertTrue(completion_evidence(reply, rates))

    def test_the_verifier_grammar_lists_parts_and_bounds_its_free_text(self):
        schema = completion_schema({'window': 'Tickets', 'screen_text': ['Adult ticket: £11.60'], 'facts': {}})
        item = schema['properties']['checks']['items']
        self.assertEqual(set(item['required']), {'part', 'part_kind', 'quote', 'shown'})
        self.assertEqual(item['properties']['quote']['enum'], ['Tickets', 'Adult ticket: £11.60', ''])
        self.assertIn('checks', schema['required'])
        # A 450-token explanation in "missing" cost 14 s and was then cut off (sim, back).
        self.assertLessEqual(schema['properties']['missing']['maxLength'], 256)
        self.assertLessEqual(item['properties']['part']['maxLength'], 256)
        # Ollama writes properties in name order: the parts must be decided before the summary.
        self.assertLess('checks', 'met')
        self.assertEqual(sorted(item['properties']), ['part', 'part_kind', 'quote', 'shown'])

    def test_a_single_part_goal_still_finishes_on_one_check_and_one_answer(self):
        self.show(title='Tickets', url='https://museum.example/tickets', lines=['Tickets', 'Adult ticket: £11.60'])
        task = self.task('Open the Tickets page.')
        task.receipts = [dict(type='click_point', label='Tickets', status='submitted', screen='before', from_url='https://museum.example/')]
        self.provider.verification = verdict(check('Open the Tickets page.', 'go_to', 'After clicking Tickets: https://museum.example/ -> https://museum.example/tickets'))
        step = self.propose(task)
        self.assertEqual(step['status'], 'done')
        self.assertEqual((len(self.provider.observations), len(self.provider.answer_inputs), len(self.provider.step_messages)), (1, 1, 0))


class FactsFromEarlierScreens(Loop):
    def test_a_fact_read_on_an_earlier_page_still_counts_and_reaches_the_answer(self):
        goal = 'I need two things from this site: the price of an adult ticket and the car park charge.'
        task = self.task(goal)
        self.show(title='Tickets', url='https://museum.example/tickets', lines=['Tickets', 'Adult ticket: £11.60'],
                  targets=[target('AXLink', 'Visiting', 60)])
        def verify(observation):
            price = 'Adult ticket: £11.60' if 'Adult ticket: £11.60' in observation['screen_text']+observation.get('found_earlier', []) else ''
            parking = 'Car park: £2.90 per day' if 'Car park: £2.90 per day' in observation['screen_text'] else ''
            return verdict(check('the price of an adult ticket', 'find_out', price, bool(price)),
                           check('the car park charge', 'find_out', parking, bool(parking)), met=bool(price and parking))
        self.provider.verification = verify
        task.receipts = [dict(type='click_point', label='Tickets', status='submitted', screen='before')]
        self.provider.steps = [done('Adult ticket £11.60.'), click('t1'), done('Both found.')]
        first = self.run_step(task)
        self.assertEqual((first['status'], first['action']['label']), ('action', 'Visiting'))
        self.assertEqual(task.findings, ['Adult ticket: £11.60'])
        self.show(title='Visiting', url='https://museum.example/visiting', lines=['Visiting', 'Car park: £2.90 per day'])
        self.provider.answer = 'An adult ticket is £11.60 and the car park is £2.90 per day.'
        second = self.propose(task)
        self.assertEqual(second['status'], 'done')   # old: the price was no longer on screen, so never done
        self.assertEqual(self.provider.observations[-1]['found_earlier'], ['Adult ticket: £11.60'])
        self.assertIn('Adult ticket: £11.60', self.provider.answer_inputs[-1]['found_earlier'])
        self.assertIn('11.60', second['message'])
        task.stop('done')
        self.assertEqual(task.findings, [])   # nothing read from the screen outlives the task

    def test_only_a_proven_screen_line_is_kept(self):
        task = self.task('What is the price and the charge?')
        observation = {'goal': task.goal, 'window': 'Tickets', 'screen_text': ['Adult ticket: £11.60'], 'facts': {}}
        task.keep_findings(completion_result(verdict(check('price', 'find_out', 'Tickets'), check('charge', 'find_out', 'Car park: free'),
                                                     check('page', 'go_to', 'Adult ticket: £11.60'))), observation)
        self.assertEqual(getattr(task, 'findings', []), [])   # a window title, an unobserved line, a navigation part


class AStepThatWorked(Loop):
    def back_task(self):
        task = self.task('Go back to the page I was on before this one.')
        self.show(title='Route A', url='https://buses.example/a', lines=['Route A', 'Timetable'], targets=[target('AXButton', 'Back', 40, width=40)])
        task.receipts = [dict(type='click_point', label='Back', status='submitted', screen='earlier', from_url='https://buses.example/b')]
        return task

    def test_the_same_step_again_is_questioned_once_and_done_is_then_verified(self):
        # sim, back: Back reached the right page, Back was pressed again, and done was said on the wrong page.
        task = self.back_task()
        answers = [verdict(check('go back', 'go_to', '', False), met=False), verdict(check('go back', 'go_to', 'Route A'))]
        self.provider.verification = lambda observation: answers.pop(0) if len(answers) > 1 else answers[0]
        self.provider.steps = [click('t1'), done('This is the page from before.')]
        task._completion_cache = None
        with patch.object(ComputerTask, 'goal_met', return_value=None):   # the early check missed it, as in the run
            step = self.propose(task)
        self.assertEqual(len(self.provider.step_messages), 2)
        correction = self.provider.step_messages[1][-1]['content']
        self.assertIn('already ran and it worked', correction)
        self.assertIn('https://buses.example/b to https://buses.example/a', correction)
        self.assertNotEqual(step['status'], 'action')   # old: a second Back

    def test_a_second_identical_proposal_runs(self):
        task = self.back_task()
        self.provider.steps = [click('t1'), click('t1')]
        step = self.propose(task)
        self.assertEqual((step['status'], step['action']['label']), ('action', 'Back'))
        task.record({'type': 'click_point', 'label': 'Back', 'status': 'submitted'})
        self.show(title='Home', url='https://buses.example/', lines=['Home', 'Welcome'])
        self.provider.steps = [click('t1')]
        self.assertEqual(self.propose(task)['status'], 'action')   # asked once per target, not on every repeat
        self.assertEqual(len(self.provider.step_messages), 3)

    def test_a_step_that_changed_nothing_is_left_to_the_existing_limits(self):
        task = self.back_task()
        task.receipts[-1]['from_url'] = 'https://buses.example/a'
        self.propose_seen = None
        self.provider.steps = [click('t1')]
        with patch.object(ComputerTask, 'check_last_click'):
            task.receipts[-1]['screen'] = screen_signature(
                [{'text': line} for line in self.lines], self.bridge.value)   # reads the same as when it was chosen
            step = self.propose(task)
        self.assertEqual(step['status'], 'action')
        self.assertEqual(len(self.provider.step_messages), 1)


@unittest.skipUnless(importlib.util.find_spec('PyQt6'), 'Qt is required to compare how a control looks')
class StateOnlyControls(Loop):
    BOX = target('AXCheckBox', 'Remind me about due dates', 300)

    @staticmethod
    def frame(ticked):
        from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
        from PyQt6.QtGui import QColor, QImage, QPainter
        image = QImage(1000, 700, QImage.Format.Format_RGB32)
        image.fill(QColor('#ffffff'))
        painter = QPainter(image)
        painter.fillRect(104, 305, 16, 16, QColor('#0a64d6' if ticked else '#d0d0d0'))
        painter.end()
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, 'PNG')
        return 'data:image/png;base64,'+base64.b64encode(bytes(data)).decode()

    def form(self, goal='Tick “Remind me about due dates”.'):
        task = self.task(goal, mode='draft')
        self.show(app=FORMS, title='Library card', lines=['Library card', 'Remind me about due dates', 'Submit'], targets=[deepcopy(self.BOX)])
        self.image = self.frame(False)
        self.provider.steps = [click('t1')]
        first = self.run_step(task)
        self.assertEqual(first['action']['label'], 'Remind me about due dates')
        return task

    def test_a_tick_that_adds_no_text_is_a_change_and_is_not_undone(self):
        task = self.form()
        self.image = self.frame(True)   # the same words; only the box looks different
        self.provider.steps = [click('t1'), done('The box is ticked.')]
        self.provider.verification = lambda observation: verdict(check(
            'Tick “Remind me about due dates”', 'change', (observation['facts'].get('controls_changed') or [{'evidence': ''}])[0]['evidence'],
            bool(observation['facts'].get('controls_changed'))), met=bool(observation['facts'].get('controls_changed')))
        step = self.propose(task)
        last = task.receipts[-1]
        self.assertIs(last['state_changed'], True)
        self.assertNotEqual(last.get('effect'), 'nothing changed on screen')   # old: this, and then a second click unticked it
        self.assertFalse(task.dead_targets)
        self.assertIn('undo', self.provider.step_messages[-1][-1]['content'])   # the repeated click was refused
        self.assertEqual(step['status'], 'done')
        self.assertEqual(self.provider.observations[-1]['facts']['controls_changed'][0]['control'], 'Remind me about due dates')

    def test_a_switch_that_also_changes_a_word_is_a_state_change_while_it_is_still_there(self):
        task = self.form('Switch on reminders.')
        self.image = self.frame(True)
        self.lines = self.lines+['On']           # the switch now reads "On" as well
        self.provider.steps = [done('It is on.')]*2
        self.propose(task)
        self.assertIs(task.receipts[-1]['state_changed'], True)
        self.assertEqual(self.provider.observations[-1]['facts']['controls_changed'][0]['control'], 'Remind me about due dates')
        task = self.form('Switch on reminders.')
        self.image = self.frame(True)
        self.show(title='Thank you', lines=['Thank you'], targets=[])   # another screen altogether: that rectangle is not the control
        self.provider.steps = [done('Done.')]*2
        self.propose(task)
        self.assertIs(task.receipts[-1]['state_changed'], False)

    def test_a_click_that_left_the_box_as_it_was_is_still_nothing_changed(self):
        task = self.form()
        self.provider.steps = [done('Nothing more to do.')]*2
        self.propose(task)
        last = task.receipts[-1]
        self.assertIs(last['state_changed'], False)
        self.assertEqual(last['effect'], 'nothing changed on screen')
        self.assertNotIn('controls_changed', self.provider.observations[-1]['facts'])

    def test_only_a_state_control_is_judged_by_its_look(self):
        task = self.task('Open the help page.')
        self.show(title='Start', lines=['Start', 'Help'], targets=[target('AXButton', 'Help', 300)])
        self.image = self.frame(False)
        self.provider.steps = [click('t1')]
        self.run_step(task)
        self.assertNotIn('look', task.receipts[-1])
        self.image = self.frame(True)   # a hover highlight on a button is not a result
        self.provider.steps = [done()]*2
        self.propose(task)
        self.assertEqual(task.receipts[-1]['effect'], 'nothing changed on screen')

    def test_the_fingerprint_sees_the_box_and_ignores_the_rest(self):
        rect = {'x': .1, 'y': 300/700, 'width': .24, 'height': 26/700}
        self.assertNotEqual(region_look(self.frame(False), rect), region_look(self.frame(True), rect))
        elsewhere = {'x': .5, 'y': .1, 'width': .2, 'height': .05}
        self.assertEqual(region_look(self.frame(False), elsewhere), region_look(self.frame(True), elsewhere))
        self.assertIsNone(region_look('data:image/png;base64,fixture', rect))

    def test_the_chosen_tab_is_part_of_what_the_screen_shows(self):
        regions = [{'text': 'CPU'}, {'text': 'Memory'}]
        before = {'targets': [target('AXRadioButton', 'CPU', 40, selected=True), target('AXRadioButton', 'Memory', 40, x=400, selected=False)]}
        after = {'targets': [target('AXRadioButton', 'CPU', 40, selected=False), target('AXRadioButton', 'Memory', 40, x=400, selected=True)]}
        self.assertNotEqual(screen_signature(regions, before), screen_signature(regions, after))   # old: equal, "nothing changed"
        task = self.task('Select the Memory tab.')
        self.show(title='Monitor', targets=after['targets'], lines=['CPU', 'Memory'])
        observation = task.completion_observation(self.desktop.collect_once(targets=True), [])
        self.assertEqual(observation['facts']['selected_controls'], ['Memory'])
        reply = completion_result(verdict(check('Select the Memory tab', 'select', 'Memory')))
        self.assertTrue(completion_evidence(reply, observation))


class TargetsOutOfView(Loop):
    def folder(self, goal='Open the Zelcroft project folder.'):
        task = self.task(goal)
        names = ['Ashfield project', 'Brenfield project', 'Caldwell project']
        self.show(app=FINDER, title='Projects', lines=['Projects']+names,
                  finder={'target': '/Users/a/Documents/Projects/', 'view': 'list view', 'selection': []},
                  targets=[target('AXTextField', name, 200+i*30, file_item=True) for i, name in enumerate(names)])
        self.bridge.hit = Mock(return_value={'role': 'AXGroup', 'title': ''})   # the window's title bar: nothing to press
        return task

    @staticmethod
    def guess(label, x=.5, y=.07):
        return {'status': 'action', 'message': 'Looking for it.', 'risk': 'navigation', 'action': {'type': 'click_point', 'x': x, 'y': y, 'label': label}}

    def test_a_named_target_that_is_not_on_screen_is_scrolled_to_not_clicked_blind(self):
        # sim, long folder: the click landed on the title bar, did nothing, and a menu command took over.
        task = self.folder()
        self.provider.steps = [self.guess('Zelcroft')]
        first = self.run_step(task)
        self.assertEqual((first['action']['type'], first['action'].get('direction')), ('scroll', 'down'))   # old: click_point
        second = self.run_step(task)                         # still not in view: code scrolls on, the model is not asked
        self.assertEqual(second['action']['type'], 'scroll')
        self.assertEqual(len(self.provider.step_messages), 1)
        self.show(lines=['Projects', 'Yarrow project', 'Zelcroft project'], targets=[target('AXTextField', 'Zelcroft project', 230, file_item=True)])
        self.provider.steps = [{'status': 'action', 'message': 'Opening it.', 'risk': 'navigation', 'action': {'type': 'open_target', 'target_id': 't1'}}]
        third = self.propose(task)                           # in view now: the model picks the listed target
        self.assertEqual((third['action']['type'], third['action']['label'], third['action']['click_count']), ('click_point', 'Zelcroft project', 2))
        self.assertFalse(task.queue)

    def test_it_is_tried_once_and_then_the_model_decides(self):
        task = self.folder()
        task.scrolled_for = {('zelcroft',)}
        self.provider.steps = [self.guess('Zelcroft')]
        self.assertEqual(self.propose(task)['action']['type'], 'click_point')

    def test_a_visible_name_a_description_or_an_unnamed_control_is_still_clicked(self):
        for label, hit in (('Caldwell project', None),            # on screen
                           ('Sidebar toggle', None),              # the model's own description, not a name from the goal
                           ('Zelcroft', {'role': 'AXButton', 'title': ''}),             # an unnamed button is under the point
                           ('Zelcroft', {'role': 'AXStaticText', 'title': 'Zelcroft project'}),   # it is there; text recognition missed it
                           ('Zelcroft', 'unknown')):              # nothing can say what is under the point
            with self.subTest(label=label, hit=hit):
                task = self.folder()
                if hit == 'unknown':
                    del self.bridge.hit
                elif hit:
                    self.bridge.hit = Mock(return_value=hit)
                self.provider.steps = [self.guess(label, x=.3, y=.5)]
                step = self.propose(task)
                self.assertEqual(step['action']['type'], 'click_point')
                self.assertFalse(task.queue)


class CommitsAreConfirmed(Loop):
    def checkout(self, label='Place order', role='AXButton'):
        task = self.task('Order the kettle.', mode='change')
        self.show(title='Checkout', url='https://shop.example/checkout', lines=['Checkout', 'Kettle £20', label],
                  targets=[target(role, label, 300), target('AXLink', 'Delivery information', 400)])
        return task

    def test_a_commit_button_labelled_navigation_waits_for_the_user(self):
        task = self.checkout()
        self.provider.steps = [click('t1', risk='navigation')]
        self.provider.commit = {'effect': 'commit'}
        step = self.propose(task)
        self.assertEqual(step['action']['label'], 'Place order')
        self.assertIs(step['review'], True)   # old: False, it ran unconfirmed
        asked = self.provider.commit_inputs[0]
        self.assertEqual((asked['button'], asked['window']), ('Place order', 'Checkout'))
        self.assertNotIn('goal', asked)       # being asked for does not make a commit safe

    def test_unsure_and_a_failed_check_wait_too(self):
        for answer in ({'effect': 'unsure'}, RuntimeError('model unavailable')):
            with self.subTest(answer=str(answer)):
                task = self.checkout('Continue')
                self.provider.steps = [click('t1')]
                self.provider.commit = answer
                self.assertIs(self.propose(task)['review'], True)

    def test_a_navigation_button_runs_and_is_asked_about_once(self):
        task = self.checkout('Show more')
        self.provider.steps = [click('t1')]
        step = self.run_step(task)
        self.assertIs(step['review'], False)
        self.assertEqual(len(self.provider.commit_inputs), 1)
        self.lines = self.lines+['More kettles']   # the press worked; the button is still there
        self.provider.steps = [click('t1'), click('t1')]
        self.assertIs(self.propose(task)['review'], False)
        self.assertEqual(len(self.provider.commit_inputs), 1)   # the answer for this button is kept

    def test_fields_back_and_decline_buttons_are_never_asked_about(self):
        # Changed 2026-11-06 (Q2 review): links and checkboxes left this list. A "Place order" link and an
        # "I agree to the terms" box commit as much as a button, so they get the same check (next test).
        self.provider.commit = {'effect': 'commit'}
        for label, role in (('Back', 'AXButton'), ('Not now', 'AXButton'), ('Gift note', 'AXTextField')):
            with self.subTest(label=label):
                task = self.checkout(label, role)
                self.provider.steps = [click('t1')]
                step = self.propose(task)
                self.assertEqual(step['action']['label'], label)
                self.assertIs(step['review'], False)
        self.assertEqual(self.provider.commit_inputs, [])

    def test_links_and_boxes_get_the_commit_check(self):
        for label, role, target_id, effect, review in (('Delivery information', 'AXLink', 't2', 'navigate', False),
                                                       ('Place order', 'AXLink', 't1', 'commit', True),
                                                       ('Gift wrap', 'AXCheckBox', 't1', 'navigate', False),
                                                       ('I agree to the terms and conditions', 'AXCheckBox', 't1', 'commit', True)):
            with self.subTest(label=label):
                self.provider.commit, self.provider.commit_inputs = {'effect': effect}, []
                task = self.checkout(label if target_id == 't1' else 'Place order', role if target_id == 't1' else 'AXButton')
                self.provider.steps = [click(target_id)]
                step = self.propose(task)
                self.assertEqual(step['action']['label'], label)
                self.assertIs(step['review'], review)
                self.assertEqual(self.provider.commit_inputs[0]['button'], label)

    def test_a_step_already_marked_as_a_change_costs_no_extra_call(self):
        task = self.checkout()
        self.provider.steps = [click('t1', risk='change')]
        self.assertIs(self.propose(task)['review'], True)
        self.assertEqual(self.provider.commit_inputs, [])

    def test_a_click_goal_naming_a_commit_button_is_confirmed(self):
        task = ComputerTask('Click Place order', automatic=True, goal_mode='change')
        task.planned = True
        self.show(title='Checkout', lines=['Checkout', 'Kettle £20'], targets=[target('AXButton', 'Place order', 300)])
        self.provider.commit = {'effect': 'commit'}
        step = self.propose(task)
        self.assertEqual(step['action']['label'].casefold(), 'place order')
        self.assertIs(step['review'], True)   # old: False
        self.assertEqual(self.provider.step_messages, [])


class StopReachesEveryRequest(Loop):
    def test_planning_and_classification_get_the_stop_event(self):
        seen = {}
        class Provider(Scripted):
            def plan_task(self, observation, *, cancel=None):
                seen['plan'] = cancel
                return {'start': 'current', 'app': '', 'url': '', 'query': '', 'steps': [], 'done_when': ''}

            def classify_task_goal(self, goal, *, cancel=None):
                seen['classify'] = cancel
                return 'inspect'
        self.provider = Provider()
        self.provider.steps = [done()]*2
        task = ComputerTask('What does this page say about parking?', automatic=True)
        self.show(lines=['Parking is free.'])
        self.propose(task)
        self.assertIs(seen['plan'], task.cancelled)       # old: plan_task took no cancel
        self.assertIs(seen['classify'], task.cancelled)

    def test_a_provider_without_the_parameter_is_called_as_before(self):
        calls = []
        class Provider(Scripted):
            def plan_task(self, observation):
                calls.append('plan')
                return {'start': 'current', 'app': '', 'url': '', 'query': '', 'steps': [], 'done_when': ''}

            def classify_task_goal(self, goal):
                calls.append('classify')
                return 'inspect'
        self.provider = Provider()
        self.provider.steps = [done()]*2
        self.show(lines=['Parking is free.'])
        self.propose(ComputerTask('What does this page say about parking?', automatic=True))
        self.assertEqual(calls, ['plan', 'classify'])

    def test_stop_during_planning_ends_the_step_before_anything_else_is_observed(self):
        task = ComputerTask('What does this page say about parking?', automatic=True)
        class Provider(Scripted):
            def plan_task(self, observation, *, cancel=None):
                task.stop()
                raise RequestCancelled()
        self.provider = Provider()
        with self.assertRaises(RuntimeError):
            self.propose(task)
        self.vision.capture.assert_not_called()   # old: the screen was still captured after Stop
        self.assertEqual(self.provider.step_messages, [])

    def test_the_shared_planner_passes_cancel_to_the_transport_only_when_given(self):
        calls = []
        class Transport(Planner):
            def complete(self, system, messages, **kwargs):
                calls.append(kwargs)
                return {'mode': 'inspect'} if 'Classify' in system else {'start': 'current'}
        stop = threading.Event()
        transport = Transport()
        transport.plan_task({'goal': 'x'}, cancel=stop)
        self.assertEqual(transport.classify_task_goal('x', cancel=stop), 'inspect')
        self.assertTrue(all(call['cancel'] is stop for call in calls))
        calls.clear()
        transport.plan_task({'goal': 'x'})
        transport.classify_task_goal('x')
        self.assertTrue(all('cancel' not in call for call in calls))


class ProofByKind(Loop):
    """Review round: what may prove each kind of part, and what ties the list to the goal."""
    def finder(self, task, folder, *, title=None, items=(), selection=()):
        self.show(app=FINDER, title=title or folder.rstrip('/').rsplit('/', 1)[-1], lines=list(items),
                  finder={'target': folder, 'view': 'list view', 'selection': list(selection)},
                  targets=[target('AXTextField', name, 200+i*30, file_item=True) for i, name in enumerate(items)])
        return task.completion_observation(self.desktop.collect_once(targets=True), [])

    def accepted(self, observation, *checks):
        return completion_evidence(completion_result(verdict(*checks)), observation)

    def test_only_the_open_folder_proves_a_folder_part(self):
        task = self.task('Show me the Documents folder')
        pictures = self.finder(task, '/Users/me/Pictures/')
        self.assertFalse(self.accepted(pictures, check('Show me the Documents folder', 'go_to', '/Users/me/Pictures/')))   # "me" is on the path
        task = self.task('Open the 2024 folder in Invoices')
        parent = self.finder(task, '/Users/a/Invoices/', items=['2023', '2024'])
        self.assertFalse(self.accepted(parent, check('Open the 2024 folder in Invoices', 'go_to', 'Invoices')))   # 2024 is only listed here
        child = self.finder(task, '/Users/a/Invoices/2024/')
        self.assertTrue(self.accepted(child, check('Open the 2024 folder in Invoices', 'go_to', '2024')))
        unlisted = self.finder(task, '/Users/a/Invoices/')   # 2024 is not even listed: the part still names a folder beyond the open one
        for quote in ('Invoices', '/Users/a/Invoices/'):
            self.assertFalse(self.accepted(unlisted, check('Open the 2024 folder in Invoices', 'go_to', quote)), quote)
        self.assertTrue(self.accepted(self.finder(self.task('In Finder, show me the Invoices folder'), '/Users/a/Invoices/'),
                                      check('In Finder, show me the Invoices folder', 'go_to', 'Invoices')))   # the app is not a folder
        # A part cut off in the middle of a word of the goal means that word (the verifier wrote "the 2" for "the 2024").
        task = self.task('In Finder, open the Invoices folder and then the 2024 one inside it.')
        child = self.finder(task, '/Users/a/Invoices/2024/')
        self.assertTrue(self.accepted(child, check('open the Invoices folder', 'go_to', 'Invoices'), check('and then the 2', 'go_to', '2024')))
        self.assertFalse(self.accepted(child, check('open the Invoices folder', 'go_to', 'Invoices'), check('and then the', 'go_to', '2024')))
        # A folder on the way counts beside the part the open folder proves, never alone.
        self.assertTrue(self.accepted(child, check('Open Invoices', 'go_to', 'Invoices'), check('then 2024 inside', 'go_to', '2024')))
        self.assertFalse(self.accepted(child, check('Open Invoices', 'go_to', 'Invoices')))

    def test_another_finder_window_is_proven_by_its_own_title(self):
        task = self.task('Show the Info window for Rota.pdf')
        info = self.finder(task, '/Users/a/Documents/', title='Rota.pdf Info')
        self.assertTrue(self.accepted(info, check('Show the Info window for Rota.pdf', 'go_to', 'Rota.pdf Info')))
        self.assertFalse(self.accepted(info, check('Show the Info window for Notes.txt', 'go_to', 'Rota.pdf Info')))

    def test_a_goal_name_that_no_part_mentions_is_a_missing_part(self):
        task = self.task('Open the Reports folder and select budget.xlsx in it.')
        reports = self.finder(task, '/Users/a/Reports/', items=['budget.xlsx'])
        reply = completion_result(verdict(check('Open the Reports folder', 'go_to', 'Reports')))
        self.assertFalse(completion_evidence(reply, reports))
        self.assertIn('budget.xlsx', ' '.join(unproven_parts(reply, reports)))
        # The app, window and address in front are context: leaving them out of the parts is not an omission.
        page = {'goal': 'In Safari, what does the Fees page say about court hire?', 'app': 'Safari', 'window': 'Fees', 'screen_text': ['Court hire: £8.90'], 'facts': {}}
        self.assertTrue(self.accepted(page, check('what does the page say about court hire', 'find_out', 'Court hire: £8.90')))
        chrome = dict(page, app='Google Chrome')
        self.assertFalse(self.accepted(chrome, check('what does the page say about court hire', 'find_out', 'Court hire: £8.90')))   # the goal said Safari

    def test_an_unshown_part_is_never_dropped_and_a_shown_repeat_is(self):
        task = self.task('Open the Reports folder and select budget.xlsx in it.')
        reports = self.finder(task, '/Users/a/Reports/', items=['budget.xlsx'])
        lumped = completion_result(verdict(check('Open the Reports folder and select budget.xlsx in it', 'find_out', 'Reports'),
                                           check('select budget.xlsx in it', 'select', '', False)))
        self.assertEqual(lumped['missing'], 'select budget.xlsx in it')
        self.assertFalse(completion_evidence(lumped, reports))
        page = {'goal': 'Go back to the page I was on before this one.', 'app': 'Safari', 'window': 'Route A', 'screen_text': ['Route A'], 'facts': {'url': 'https://x/a'}}
        again = verdict(check('Go back to the page I was on before this one.', 'go_to', 'Route A'),
                        check('the page I was on before this one', 'select', 'Route A'))   # the same part once more, as a kind nothing can prove
        self.assertTrue(completion_evidence(completion_result(again), page))

    def test_a_hint_or_an_avoid_label_does_not_waive_a_result(self):
        tickets = {'goal': 'the price of an adult ticket and the car park charge', 'window': 'Tickets', 'screen_text': ['Adult ticket: £11.60'], 'facts': {},
                   '_tried': ['Tickets']}
        price = check('the price of an adult ticket', 'find_out', 'Adult ticket: £11.60')
        self.assertFalse(self.accepted(tickets, price, check('the car park charge', 'hint', '', False)))    # nothing named "car park" was tried
        self.assertFalse(self.accepted(tickets, price, check('the car park charge', 'avoid', '', False)))   # it does not say "not"
        self.assertTrue(self.accepted(tickets, price, check('but do not buy the car park pass', 'avoid', '', False)))

    def test_a_change_needs_a_state_fact_or_new_words(self):
        form = {'goal': 'Tick Remind me about due dates', 'window': 'Library card', 'screen_text': ['Remind me about due dates', 'Submit'], 'facts': {}}
        tick = check('Tick Remind me about due dates', 'change', 'Remind me about due dates')
        self.assertFalse(self.accepted(form, tick))   # the box's label is there ticked or not
        changed = dict(form, facts={'controls_changed': [{'control': 'Remind me about due dates', 'evidence': 'Clicked “Remind me about due dates”: its own state changed once.'}]})
        self.assertTrue(self.accepted(changed, tick))
        name = {'goal': 'Put Ada Lovelace in the Name box', 'window': 'Form', 'screen_text': ['Name', 'Ada Lovelace'], 'facts': {}}
        put = check('Put Ada Lovelace in the Name box', 'change', 'Ada Lovelace')
        self.assertFalse(self.accepted(name, put))                                      # the request read back
        self.assertTrue(self.accepted(dict(name, facts={'typed': ['Ada Lovelace']}), put))   # this task typed it
        gone = {'goal': 'Delete this draft claim.', 'window': 'Claims', 'screen_text': ['Draft deleted.'], 'facts': {}}
        self.assertTrue(self.accepted(gone, check('Delete this draft claim.', 'change', 'Draft deleted.')))   # new words on screen
        switch = {'goal': 'Make sure backups are switched on.', 'window': 'Backup', 'screen_text': ['Backups', 'On'], 'facts': {}, '_control_labels': ['Backups']}
        self.assertTrue(self.accepted(switch, check('Make sure backups are switched on.', 'change', 'On')))
        self.assertFalse(self.accepted(switch, check('Make sure backups are switched on.', 'change', 'Backups')))

    def test_a_browser_page_is_proven_by_its_title_or_address_not_by_a_visible_link(self):
        home = {'goal': 'Go to the Pricing page', 'app': 'Safari', 'window': 'Home', 'last_click': 'Pricing', 'screen_text': ['Home', 'Pricing', 'About'],
                'facts': {'url': 'https://x/', 'browser_navigation': {'clicked': 'Pricing', 'from_url': 'https://x/', 'url': 'https://x/', 'changed': False,
                                                                   'evidence': 'After clicking Pricing: https://x/ -> https://x/'}}}
        self.assertFalse(self.accepted(home, check('Go to the Pricing page', 'go_to', 'Pricing')))
        self.assertFalse(self.accepted(home, check('Go to the Pricing page', 'go_to', 'After clicking Pricing: https://x/ -> https://x/')))   # the click led nowhere
        there = {'goal': 'Go to the Pricing page', 'app': 'Safari', 'window': 'Pricing', 'screen_text': ['Pricing', 'Plans'],
                 'facts': {'url': 'https://x/pricing', 'browser_navigation': {'clicked': 'Pricing', 'from_url': 'https://x/', 'url': 'https://x/pricing', 'changed': True,
                                                                           'evidence': 'After clicking Pricing: https://x/ -> https://x/pricing'}}}
        for quote in ('Pricing', 'https://x/pricing', 'After clicking Pricing: https://x/ -> https://x/pricing'):
            self.assertTrue(self.accepted(there, check('Go to the Pricing page', 'go_to', quote)), quote)
        self.assertFalse(self.accepted(there, check('Go to the Pricing page', 'go_to', 'Plans')))

    def test_a_line_from_an_earlier_screen_proves_only_information(self):
        later = {'goal': 'Open the fees page and tell me the court fee', 'app': 'Safari', 'window': 'Home', 'screen_text': ['Home'], 'facts': {'url': 'https://x/'},
                 'found_earlier': ['Court hire: £8.90']}
        self.assertTrue(check_proven(check('tell me the court fee', 'find_out', 'Court hire: £8.90'), later))
        self.assertFalse(check_proven(check('Open the fees page', 'go_to', 'Court hire: £8.90'), later))
        self.assertFalse(check_proven(check('set the fee', 'change', 'Court hire: £8.90'), later))

    def test_an_opening_the_system_confirms_can_be_quoted_when_nothing_else_can(self):
        monitor = {'name': 'Activity Monitor', 'bundle_id': 'com.apple.ActivityMonitor', 'pid': 3}
        task = self.task('Open Activity Monitor')
        self.show(app=monitor, title='', lines=[])
        task.receipts = [dict(type='launch_app', label='Open Activity Monitor', status='submitted', screen='x', wanted={'bundle_id': monitor['bundle_id']}, settled=True)]
        self.provider.steps = [done('Activity Monitor is open.')]*2
        self.provider.verification = verdict(check('Open Activity Monitor', 'go_to', 'Activity Monitor is open and in front.'))
        step = self.propose(task)
        self.assertEqual((step['status'], len(self.provider.observations)), ('done', 1))   # old: needs_user, "fresh observed evidence", no check at all
        observation = self.provider.observations[-1]
        self.assertFalse(any(name.startswith('_') for name in observation))   # code-only keys are not sent
        self.assertFalse(check_proven(check('show me the CPU tab', 'go_to', 'Activity Monitor is open and in front.'), observation))   # names no app
        task.record({'type': 'click_point', 'label': 'CPU', 'status': 'submitted'})   # still said after later steps in that app
        self.assertEqual(task.completion_observation(self.desktop.collect_once(targets=True), [])['facts']['opened_app']['name'], 'Activity Monitor')
        task = self.task('Open Activity Monitor')   # another app is in front: no such fact
        self.show(app=dict(SAFARI), title='', lines=[])
        task.receipts = [dict(type='launch_app', label='Open Activity Monitor', status='submitted', screen='x', wanted={'bundle_id': monitor['bundle_id']}, settled=True)]
        self.assertNotIn('opened_app', task.completion_observation(self.desktop.collect_once(targets=True), [])['facts'])


class ReviewedEdges(Loop):
    def test_the_commit_answer_is_not_reused_once_a_form_is_on_screen(self):
        task = self.task('Order the kettle.', mode='change')
        self.show(title='Shop', lines=['Shop', 'Apply'], targets=[target('AXButton', 'Apply', 300)])
        self.provider.steps = [click('t1')]
        self.assertIs(self.run_step(task)['review'], False)
        self.show(title='Shop', lines=['Shop', 'Card number', 'Apply'], targets=[target('AXTextField', 'Card number', 200), target('AXButton', 'Apply', 300)])
        self.provider.steps = [click('t2'), click('t2')]
        self.provider.commit = {'effect': 'commit'}
        self.assertIs(self.propose(task)['review'], True)   # same window title and label, now with a field: asked again
        self.assertEqual([asked['form_fields_on_screen'] for asked in self.provider.commit_inputs], [False, True])

    def test_a_commit_reply_that_is_no_answer_waits_for_the_user(self):
        task = self.task('Order the kettle.', mode='change')
        self.show(title='Shop', lines=['Shop', 'Apply'], targets=[target('AXButton', 'Apply', 300)])
        self.provider.steps = [click('t1')]
        self.provider.commit = {'status': 'action'}
        self.assertIs(self.propose(task)['review'], True)

    def test_a_twice_refused_undo_ends_from_the_observed_state_not_in_an_error(self):
        for verification, status in ((verdict(check('Tick the box', 'change', 'Clicked “Box”: its own state changed once.')), 'done'),
                                     (verdict(check('Tick the box', 'change', '', False)), 'needs_user')):
            with self.subTest(status=status):
                task = self.task('Tick the box', mode='draft')
                self.show(app=FORMS, title='Form', lines=['Form', 'Box'], targets=[target('AXCheckBox', 'Box', 300)])
                task.receipts = [dict(type='click_point', label='Box', status='submitted', screen='x', state_changed=True)]
                self.provider.steps = [click('t1'), click('t1')]
                self.provider.verification = verification
                step = self.propose(task)   # old: ValidationError "I could not find a safe next step"
                self.assertEqual(step['status'], status)
                self.assertIn('Box', step['message'])
                self.assertIsNone(task.pending)

    def test_a_failed_step_is_not_taken_for_one_that_worked(self):
        task = self.task('Go back to the page I was on before this one.')
        self.show(title='Route A', url='https://buses.example/a', lines=['Route A', 'Timetable'], targets=[target('AXButton', 'Back', 40, width=40)])
        task.receipts = [dict(type='click_point', label='Back', status='failed', screen='earlier', from_url='https://buses.example/b')]
        self.provider.steps = [click('t1')]
        self.assertEqual(self.propose(task)['status'], 'action')
        self.assertEqual(len(self.provider.step_messages), 1)   # not questioned: the last Back never ran

    def click_goal(self):
        task = ComputerTask('Click Help', automatic=True, goal_mode='inspect')
        task.planned = True
        self.show(title='Start', lines=['Start', 'Welcome'], targets=[target('AXButton', 'Help', 300)])
        return task

    def test_stop_during_the_commit_check_of_a_code_click_offers_nothing(self):
        task = self.click_goal()
        answer = self.provider.complete_structured
        def stopping(system, messages, **kwargs):
            if system == COMMIT_SYSTEM:
                task.stop()
            return answer(system, messages, **kwargs)
        self.provider.complete_structured = stopping
        with self.assertRaises(ValueError):
            self.propose(task)

    def test_stop_while_a_code_click_is_judged_does_not_report_done(self):
        task = self.click_goal()
        self.assertEqual(self.run_step(task)['action']['label'].casefold(), 'help')
        self.show(title='Help', lines=['Help', 'Topics: 3'])
        def guard(*args, **kwargs):
            task.stop()
            return {'region_hash': 'fixture'}
        self.vision.click_guard.side_effect = guard
        with self.assertRaises(ValueError):
            self.propose(task)

    def test_facts_from_a_step_that_was_stopped_are_not_kept(self):
        task = self.task('What is the price and the charge?')
        self.show(title='Tickets', lines=['Tickets', 'Adult ticket: £11.60'])
        task.receipts = [dict(type='click_point', label='Tickets', status='submitted', screen='before')]
        def verify(observation):
            task.stop()
            return verdict(check('price', 'find_out', 'Adult ticket: £11.60'), check('charge', 'find_out', '', False))
        self.provider.verification = verify
        self.provider.steps = [done()]*2
        with self.assertRaises(ValueError):
            self.propose(task)
        self.assertEqual(task.findings, [])
        self.assertNotIn('_found', task.__dict__)


@unittest.skipUnless(importlib.util.find_spec('PyQt6'), 'Qt is required to compare how a control looks')
class GoalsMetOrUnclear(Loop):
    """Second round of sim defects: a goal already met is not undone, a named browser is used,
    two equal matches are asked about, a joined clause stays required, a confirmed change and
    a value read in passing count."""
    def backups(self, state='On'):
        # "On" is the line at y .22-.26; the switch's own row covers exactly that line.
        self.show(app=FORMS, title='Backup settings', lines=['Backup settings', 'Automatic backups', state],
                  targets=[target('AXCheckBox', 'Automatic backups', 154, width=600, height=28)])
        self.provider.steps = [click('t1')]

    def test_a_switch_already_as_asked_is_left_alone(self):
        # sim, pre-satisfied: "make sure automatic backups are switched on" with them on; the click switched them off.
        task = self.task('Make sure automatic backups are switched on.', mode='change')
        self.backups('On')
        self.provider.verification = verdict(check('automatic backups are switched on', 'find_out', 'On'))
        step = self.propose(task)
        self.assertEqual(step['status'], 'done')   # old: the click on the switch was proposed
        self.assertIsNone(step['action'])
        self.assertIn('“On”', step['message'])
        self.assertIsNone(task.pending)

    def test_the_switch_is_clicked_when_the_goal_is_not_met_or_its_state_is_not_seen(self):
        task = self.task('Make sure automatic backups are switched on.', mode='change')
        self.backups('Off')
        self.provider.verification = verdict(check('automatic backups are switched on', 'change', '', False), met=False)
        self.assertEqual(self.propose(task)['action']['label'], 'Automatic backups')
        # Its name is no state: met with only the name on its row is not "already on".
        task = self.task('Make sure automatic backups are switched on.', mode='change')
        self.show(app=FORMS, title='Backup settings', lines=['Backup settings', 'Automatic backups', 'Saved nightly'],
                  targets=[target('AXCheckBox', 'Automatic backups', 112, width=600, height=28)])   # its row holds only its name
        self.provider.steps = [click('t1')]
        self.provider.verification = verdict(check('automatic backups are switched on', 'find_out', 'Saved nightly'))
        self.assertEqual(self.propose(task)['status'], 'action')

    def minutes(self):
        names = ['Letters', 'Marlcombe minutes', 'Varnhaven minutes']
        self.show(app=FINDER, title='Documents', lines=['Documents']+names,
                  finder={'target': '/Users/a/Documents/', 'view': 'list view', 'selection': []},
                  targets=[target('AXTextField', name, 200+i*30, file_item=True) for i, name in enumerate(names)])

    def test_two_equal_matches_are_asked_about_before_anything_runs(self):
        # sim, ambiguous: "open the minutes folder" opened "Marlcombe minutes" and ended without asking.
        task = self.task('Open the minutes folder.', mode='change')
        self.minutes()
        self.provider.steps = [click('t2')]
        step = self.propose(task)
        self.assertEqual(step['status'], 'needs_user')
        self.assertIsNone(step['action'])
        self.assertIsNone(task.pending)
        self.assertIn('Marlcombe minutes', step['message'])
        self.assertIn('Varnhaven minutes', step['message'])
        self.assertTrue(step['message'].endswith('?'))

    def test_a_goal_that_says_which_one_is_not_asked_about(self):
        for goal in ('Open the Varnhaven minutes folder.', 'Open the Letters folder.', 'Open the Quarry minutes folder.'):
            with self.subTest(goal=goal):
                task = self.task(goal, mode='change')
                self.minutes()
                self.provider.steps = [click('t3' if 'Varnhaven' in goal else 't1' if 'Letters' in goal else 't2')]
                # (Quarry is nowhere on screen: the one meant may be out of view, no question then)
                self.assertEqual(self.propose(task)['status'], 'action')

    def test_the_named_browser_comes_forward_first(self):
        # sim, named browser: "In Safari, open the Fees page…" with Chrome in front; the task worked in Chrome.
        chrome = {'name': 'Google Chrome', 'bundle_id': 'com.google.Chrome', 'pid': 13}
        self.show(app=chrome, title='Inverwell Tennis Club', lines=['Inverwell Tennis Club', 'Fees'])
        self.bridge.value['available_apps'] = [dict(chrome), dict(SAFARI)]
        self.bridge.value['installed_apps'] = [{'name': 'Google Chrome', 'bundle_id': 'com.google.Chrome'}, {'name': 'Safari', 'bundle_id': 'com.apple.Safari'}]
        task = ComputerTask('In Safari, open the Fees page of the site that’s open there and tell me what court hire costs.',
                            automatic=True, goal_mode='inspect')
        step = self.propose(task)
        self.assertEqual((step['action']['type'], step['action']['bundle_id']), ('focus_app', 'com.apple.Safari'))
        self.assertEqual(self.provider.step_messages, [])   # no model step, no plan call

    def test_a_joined_clause_no_part_mentions_stays_required(self):
        observation = {'goal': 'Put Ignace Penford as the display name and tick the box about due dates.', 'app': 'Formbook', 'window': 'Card',
                       'screen_text': ['Display name', 'Ignace Penford'], 'facts': {'typed': ['Ignace Penford']}}
        only_first = completion_result(verdict(check('Put Ignace Penford as the display name', 'change', 'Ignace Penford')))
        self.assertFalse(completion_evidence(only_first, observation))   # old: proven by the first clause alone
        self.assertIn('tick the box about due dates', unproven_parts(only_first, observation))
        both = completion_result(verdict(check('Put Ignace Penford as the display name', 'change', 'Ignace Penford'),
                                         check('tick the due date box', 'change', 'Ignace Penford')))
        self.assertNotIn('tick the box about due dates', unproven_parts(both, observation))
        # A clause that says "not", and words in their own sentence (a hint), are not required parts.
        hint = dict(observation, goal='Put Ignace Penford as the display name and do not submit it. It should be on the card page.')
        self.assertTrue(completion_evidence(only_first, hint))

    def deleted(self, confirmed):
        task = self.task('Get rid of this draft claim.', mode='change')
        self.show(app=FORMS, title='Expense claim draft', lines=['Expense claim draft', 'Draft deleted.'], targets=[])
        task.receipts = [dict(type='click_point', label='Delete draft', status='submitted', screen='before',
                              lines_before=['Expense claim draft', 'Amount', 'Delete draft'], **({'confirmed': True} if confirmed else {}))]
        self.provider.verification = verdict(check('Get rid of this draft claim', 'find_out', '', False), met=False)
        return task, self.desktop.collect_once(targets=True)

    def test_a_confirmed_change_that_took_effect_is_its_proof(self):
        # sim, delete: the user confirmed "Delete draft", "Draft deleted." appeared, and the task said it could not finish.
        task, snapshot = self.deleted(True)
        regions = [{'text': line, 'x': .5, 'y': .1+i*.06, 'width': .3, 'height': .04} for i, line in enumerate(self.lines)]
        self.assertEqual(task.still_missing(self.provider, snapshot, regions, {}), '')
        task, snapshot = self.deleted(False)   # not a step the user confirmed: the verifier's word stands
        self.assertNotEqual(task.still_missing(self.provider, snapshot, regions, {}), '')
        task, snapshot = self.deleted(True)
        self.provider.verification = verdict(check('what the claim amount was', 'find_out', '', False), met=False)
        self.assertNotEqual(task.still_missing(self.provider, snapshot, regions, {}), '')   # a question is not answered by a press

    def test_a_value_the_step_model_reads_in_passing_is_kept(self):
        # sim, two facts: "The adult ticket is £11.60. Now the car park." was never checked on its page and was lost.
        task = self.task('I need two things from this site: the price of an adult ticket and what the car park costs.')
        self.show(title='Tickets', url='https://museum.example/tickets', lines=['Tickets', 'Child ticket: £5.20', 'Adult ticket: £11.60'],
                  targets=[target('AXLink', 'Visiting', 60)])
        self.provider.steps = [dict(click('t1'), message='The adult ticket is £11.60. Now the car park.')]
        self.run_step(task)
        self.assertEqual(task.findings, ['Adult ticket: £11.60'])   # old: []


class UnreadableCapture(StateOnlyControls):
    def test_a_capture_that_cannot_be_read_is_not_a_state_change(self):
        task = self.form()
        self.image = 'data:image/png;base64,fixture'
        self.provider.steps = [done('Nothing more to do.')]*2
        self.propose(task)
        self.assertIs(task.receipts[-1]['state_changed'], False)
        self.assertEqual(task.receipts[-1]['effect'], 'nothing changed on screen')


class FileInteraction(unittest.TestCase):
    def test_the_goal_verb_for_this_item_is_named_before_the_interaction(self):
        self.assertEqual(FILE_INTERACTION_SCHEMA['required'], ['goal_verb', 'interaction'])
        self.assertLess('goal_verb', 'interaction')   # Ollama writes properties in name order
        self.assertEqual(FILE_INTERACTION_SCHEMA['properties']['interaction']['enum'], ['open', 'select', 'other'])


if __name__ == '__main__':
    unittest.main()
