"""Scroll batch 2026-10-30 (build 2385a6e664): a Finder selection read as "nothing changed",
and a link click that reached the page was not accepted as done."""
import unittest
from unittest.mock import Mock, patch

from companion.computer_task import (ComputerTask, completion_evidence, completion_schema,
                                     screen_signature, structured_facts)


ROWS = [dict(text='item088 A11y test 27161000.txt', x=.2, y=.5, width=.3, height=.03),
        dict(text='item089 A11y test 27161000.txt', x=.2, y=.55, width=.3, height=.03)]


class FinderSelectionTests(unittest.TestCase):
    def test_finder_facts_read_selected_names_as_argv_bound_script(self):
        from companion.desktop_context import finder_facts, FINDER_FACTS_SCRIPT
        with patch('companion.desktop_context.sys.platform', 'darwin'), patch('companion.desktop_context.subprocess.run') as run:
            run.return_value = Mock(returncode=0, stdout='/tmp/fixture/\nlist view\n\nitem089 A11y test 1.txt\x1fnotes.txt\n')
            facts = finder_facts({'app': {'bundle_id': 'com.apple.finder'}})
        self.assertEqual(facts, {'target': '/tmp/fixture/', 'view': 'list view', 'sort': '',
                                 'selection': ['item089 A11y test 1.txt', 'notes.txt']})
        self.assertIn('if i > 5 then exit repeat', FINDER_FACTS_SCRIPT)
        self.assertNotIn('contents', FINDER_FACTS_SCRIPT.split('selection', 1)[1])

    def test_empty_selection_is_known_and_empty(self):
        from companion.desktop_context import finder_facts
        with patch('companion.desktop_context.sys.platform', 'darwin'), patch('companion.desktop_context.subprocess.run') as run:
            run.return_value = Mock(returncode=0, stdout='/tmp/fixture/\nlist view\nname\n\n')
            self.assertEqual(finder_facts({'app': {'bundle_id': 'com.apple.finder'}})['selection'], [])

    def test_selecting_a_row_changes_the_screen_signature(self):
        before = {'finder': {'target': '/tmp/fixture/', 'selection': []}}
        after = {'finder': {'target': '/tmp/fixture/', 'selection': ['item089 A11y test 27161000.txt']}}
        self.assertNotEqual(screen_signature(ROWS, before), screen_signature(ROWS, after))
        self.assertEqual(screen_signature(ROWS, after), screen_signature(list(reversed(ROWS)), after))

    def test_click_that_only_selected_is_not_reported_as_doing_nothing(self):
        task = ComputerTask('Scroll down this Finder list and select item089 A11y test 27161000.txt. Leave the file closed.',
                            automatic=True, goal_mode='inspect')
        before = {'finder': {'selection': []}}
        task.screen_seen = screen_signature(ROWS, before)
        task.pending = {'type': 'click_point', 'label': 'item089 A11y test 27161000.txt'}
        task.record({'type': 'click_point', 'label': 'item089 A11y test 27161000.txt', 'status': 'submitted'})
        after = {'finder': {'selection': ['item089 A11y test 27161000.txt']}}
        task.screen_seen = screen_signature(ROWS, after)
        task.check_last_click(after)
        self.assertNotIn('effect', task.receipts[-1])
        self.assertFalse(task.dead_targets)

    def test_selection_is_evidence_and_a_visible_row_name_is_not_selection(self):
        facts = structured_facts({'finder': {'target': '/tmp/fixture/', 'view': 'list view', 'sort': 'name',
                                             'selection': ['item088 A11y test 27161000.txt']}})
        self.assertEqual(facts['finder']['selection'], ['item088 A11y test 27161000.txt'])
        observation = dict(facts=facts, screen_text=[r['text'] for r in ROWS])
        self.assertIn('item088 A11y test 27161000.txt', completion_schema(observation)['properties']['evidence']['enum'])
        from companion.computer_task import MET_SYSTEM
        self.assertIn('finder.selection', MET_SYSTEM)


class LinkNavigationEvidenceTests(unittest.TestCase):
    def task_after_click(self, from_url, url):
        task = ComputerTask('Use the Planning desk link to return to the start of this local website.',
                            automatic=True, goal_mode='inspect')
        task.page_seen = from_url
        task.pending = {'type': 'click_point', 'label': 'Planning desk'}
        task.record({'type': 'click_point', 'label': 'Planning desk', 'status': 'submitted'})
        snapshot = {'app': {'name': 'Safari', 'bundle_id': 'com.apple.Safari'},
                    'window': {'title': 'Planning desk', 'url': url}}
        return task, task.completion_observation(snapshot, [dict(text='Equipment catalog', x=.1, y=.2, width=.2, height=.03)])

    def test_link_click_records_where_it_led(self):
        task, observation = self.task_after_click('http://127.0.0.1:57906/equipment/guide', 'http://127.0.0.1:57906/')
        self.assertEqual(task.receipts[-1]['from_url'], 'http://127.0.0.1:57906/equipment/guide')
        moved = observation['facts']['browser_navigation']
        self.assertTrue(moved['changed'])
        self.assertEqual(moved['url'], 'http://127.0.0.1:57906/')
        self.assertTrue(completion_evidence(dict(met=True, missing='', evidence=moved['evidence']), observation))
        self.assertIn(moved['evidence'], completion_schema(observation)['properties']['evidence']['enum'])

    def test_a_click_that_stayed_on_the_page_says_so(self):
        _, observation = self.task_after_click('http://127.0.0.1:57906/', 'http://127.0.0.1:57906/')
        self.assertFalse(observation['facts']['browser_navigation']['changed'])

    def link_task(self):
        task = ComputerTask('Click Equipment catalog on this planning desk.', automatic=True, goal_mode='inspect')
        task.page_seen = 'http://127.0.0.1:57906/'
        task.pending = {'type': 'click_point', 'label': 'Equipment catalog'}
        task.record({'type': 'click_point', 'label': 'Equipment catalog', 'status': 'submitted'})
        return task

    def page(self, url):
        return {'app': {'name': 'Safari', 'bundle_id': 'com.apple.Safari'}, 'window': {'title': 'Equipment catalog', 'url': url}}

    def test_link_click_that_changed_the_address_checks_the_full_goal(self):
        task = self.link_task()
        snapshot = self.page('http://127.0.0.1:57906/equipment')
        # The new page's heading repeats the link's name: with the address change it counts.
        provider = Mock(complete_structured=Mock(side_effect=[
            dict(met=True, missing='', evidence='Equipment catalog'), dict(message='The equipment catalog is open.')]))
        result = task.goal_met(provider, snapshot, [dict(text='Equipment catalog', x=.1, y=.1, width=.3, height=.04),
                                                    dict(text='Equipment shelf 0', x=.1, y=.2, width=.3, height=.04)], {})
        self.assertEqual(result['status'], 'done')

    def test_link_click_that_stayed_put_does_not_check_or_finish(self):
        task = self.link_task()
        provider = Mock()
        self.assertIsNone(task.goal_met(provider, self.page('http://127.0.0.1:57906/'), [], {}))
        provider.complete_structured.assert_not_called()

    def test_a_link_the_goal_does_not_name_does_not_start_the_early_check(self):
        task = ComputerTask('Start at apple.com in Safari, then take me to its Support page.', automatic=True, goal_mode='inspect')
        task.page_seen = 'https://www.google.com/search?q=apple.com'
        task.pending = {'type': 'click_point', 'label': 'https://www.apple.com'}
        task.record({'type': 'click_point', 'label': 'https://www.apple.com', 'status': 'submitted'})
        provider = Mock()
        self.assertIsNone(task.goal_met(provider, self.page('https://www.apple.com/'), [], {}))
        provider.complete_structured.assert_not_called()

    def test_navigation_does_not_override_a_missing_result(self):
        task = self.link_task()
        provider = Mock(complete_structured=Mock(return_value=dict(met=False, missing='the shelf list', evidence='Equipment catalog')))
        self.assertIsNone(task.goal_met(provider, self.page('http://127.0.0.1:57906/equipment'), [], {}))

    def test_no_address_no_navigation_fact(self):
        task = ComputerTask('Click Save in TextEdit.', automatic=True, goal_mode='inspect')
        task.pending = {'type': 'click_point', 'label': 'Save'}
        task.record({'type': 'click_point', 'label': 'Save', 'status': 'submitted'})
        self.assertNotIn('from_url', task.receipts[-1])
        observation = task.completion_observation({'app': {'name': 'TextEdit'}, 'window': {'title': 'Untitled'}}, [])
        self.assertNotIn('browser_navigation', observation['facts'])


if __name__ == '__main__':
    unittest.main()


class FinishedAnswerTests(unittest.TestCase):
    def test_answer_knows_the_steps_ran_and_not_to_narrate_the_screen(self):
        # Live (page-url batch): "The screen shows 'Equipment catalog' as a visible text line. The URL …"
        from companion.computer_task import ANSWER_SYSTEM
        self.assertIn('already run', ANSWER_SYSTEM)
        self.assertIn('Never mention the screen', ANSWER_SYSTEM)
        task = ComputerTask('Click Equipment catalog on this planning desk.', automatic=True, goal_mode='inspect')
        task.receipts = [dict(type='click_point', label='Equipment catalog', status='submitted')]
        provider = Mock(complete_structured=Mock(return_value={'message': 'The equipment catalog is open.'}))
        answer = task.screen_answer(provider, {'app': {'name': 'Safari'}, 'window': {'title': 'Catalog'}},
                                    [dict(text='Equipment catalog', x=.1, y=.1, width=.3, height=.04)], '', {})
        self.assertEqual(answer, 'The equipment catalog is open.')
        import json
        sent = json.loads(provider.complete_structured.call_args.args[1][0]['content'])
        self.assertEqual(sent['last_step'], 'Equipment catalog')


class PunctuationMissingTests(unittest.TestCase):
    def test_punctuation_only_missing_names_nothing(self):
        from companion.computer_task import completion_result
        for junk in ('}', ' } ', '"}', '.', ''):
            self.assertEqual(completion_result(dict(met=True, evidence='x', missing=junk))['missing'], '')
        self.assertEqual(completion_result(dict(met=False, evidence='x', missing='the Info window'))['missing'], 'the Info window')

    def test_selection_with_stray_brace_is_finished_only_with_real_evidence(self):
        task = ComputerTask('Scroll down this Finder list and select item089 A11y test 66780000.txt. Leave the file closed.',
                            automatic=True, goal_mode='inspect')
        snapshot = {'app': {'name': 'Finder', 'bundle_id': 'com.apple.finder'}, 'window': {'title': 'fixture'},
                    'finder': {'target': '/tmp/fixture/', 'selection': ['item089 A11y test 66780000.txt']}}
        regions = [dict(text='item089 A11y test 66780000.txt', x=.1, y=.5, width=.3, height=.03)]
        good = Mock(complete_structured=Mock(return_value=dict(met=True, missing='}', evidence='item089 A11y test 66780000.txt')))
        self.assertEqual(task.still_missing(good, snapshot, regions, {}), '')
        invented = Mock(complete_structured=Mock(return_value=dict(met=True, missing='}', evidence='item089 is selected')))
        self.assertTrue(task.still_missing(invented, snapshot, regions, {}))


class AnswerBeforeMenuFallbackTests(unittest.TestCase):
    REGIONS = [dict(text='Route guide', x=.1, y=.1, width=.2, height=.03),
               dict(text='Collection point: Ridge 69000', x=.1, y=.3, width=.3, height=.03)]

    def task(self):
        task = ComputerTask('Find the collection point again by going through the catalog and guide.', automatic=True, goal_mode='inspect')
        task.receipts = [dict(type='click_point', label='Collection point: Ridge 69000', status='submitted',
                              effect='nothing changed on screen')]
        return task

    def test_a_met_goal_finishes_instead_of_choosing_a_menu(self):
        # Live (panes batch): the model clicked the answer line, then the fallback chose History.
        task = self.task()
        provider = Mock(complete_structured=Mock(side_effect=[
            dict(met=True, missing='', evidence='Collection point: Ridge 69000'), {'message': 'The collection point is Ridge 69000.'}]))
        desktop = Mock()
        snapshot = {'app': {'name': 'Safari', 'bundle_id': 'com.apple.Safari'}, 'window': {'title': 'Route guide'}}
        result = task.menu_pick_step(desktop, snapshot, provider, {}, self.REGIONS)
        self.assertEqual(result['status'], 'done')
        desktop.bridge.menu_items.assert_not_called()

    def test_an_unmet_goal_still_gets_the_menu_and_is_checked_once(self):
        task = self.task()
        provider = Mock(complete_structured=Mock(side_effect=[
            dict(met=False, missing='the guide', evidence='Route guide'), {'command': 'none'}]))
        desktop = Mock()
        desktop.bridge.menu_items.return_value = ['View > Reload Page']
        snapshot = {'app': {'name': 'Safari', 'bundle_id': 'com.apple.Safari'}, 'window': {'title': 'Catalog'}}
        self.assertIsNone(task.menu_pick_step(desktop, snapshot, provider, {}, self.REGIONS))
        desktop.bridge.menu_items.assert_called_once()
        self.assertTrue(task.met_before_menu)
