"""The code-first step (task_choices): code lists what can be done, the text model only chooses.

Through the real loop with scripted model replies and a fake bridge; nothing reaches an app or a
model. Each test names the outcome it holds:
- a chosen option is acted on through the validated paths (a target's own centre with the capture
  guard, press_control by id, choose_menu by path), with one small text call and no image call;
- a commit is still confirmed, a look-only goal still refuses changes, Stop still ends the step;
- "done" from the chooser needs the verifier's proof, and the image step stays for empty lists,
  "none", and options that were all tried;
- Windows snapshots (controls, no targets, no menus) get options too.
"""
from copy import deepcopy
import json
import unittest

from companion.computer_task import CHOOSE_SYSTEM, ComputerTask, SYSTEM
from companion.task_choices import goal_parts, menu_commands, relevance, words
from tests.test_task_loop_outcomes import Loop, Scripted, check, done, target, verdict, FORMS


def choose(step, note='Next.', text=''):
    return {'note': note, 'step': step, 'text': text}


class Chooser(Scripted):
    """Scripted, plus the chooser's replies; plan_task on the class marks a planning transport."""
    def __init__(self):
        super().__init__()
        self.choices, self.choice_inputs, self.images = [], [], []

    def plan_task(self, observation, *, cancel=None):
        raise AssertionError('the tests plan nothing')

    def complete_structured(self, system, messages, **kwargs):
        if kwargs.get('image_data'):
            self.images.append(system[:30])
        if system == CHOOSE_SYSTEM:
            self.choice_inputs.append({'messages': messages, 'schema': kwargs['schema'], 'max_tokens': kwargs.get('max_tokens')})
            reply = self.choices.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply(messages) if callable(reply) else deepcopy(reply)
        return super().complete_structured(system, messages, **kwargs)


class MenuBridge:
    def __init__(self, value, items):
        self.value, self.items, self.asked = value, items, []

    def sample(self):
        return deepcopy(self.value)

    def menu_items(self, bundle_id):
        self.asked.append(bundle_id)
        return list(self.items)


class ChooseLoop(Loop):
    def setUp(self):
        super().setUp()
        self.provider = Chooser()

    def options(self, call=0):
        """The options the chooser was shown, as {name: id}."""
        current = json.loads(self.provider.choice_inputs[call]['messages'][1]['content'])
        return {line.split('|', 2)[2]: line.split('|', 1)[0] for line in current['options']}

    def site(self):
        self.show(title='Tarnby Leisure Trust', url='https://tarnby.example/', lines=['Tarnby Leisure Trust', 'Pools and gyms across the district.'],
                  targets=[target('AXLink', 'Home', 100), target('AXLink', 'Membership', 140), target('AXLink', 'Penfield pool', 200),
                           target('AXLink', 'Yarcombe gym', 240), target('AXLink', 'Yarcombe pool', 280)])


class ChosenOptionsAct(ChooseLoop):
    def test_the_chosen_link_is_clicked_at_its_own_centre_with_one_text_call(self):
        self.site()
        task = self.task('Open the Yarcombe pool page and tell me what lane hire costs.')
        self.provider.choices = [lambda messages: choose(self.options()['Yarcombe pool'], 'The pool page.')]
        step = self.propose(task)
        action = step['action']
        self.assertEqual((action['type'], action['label']), ('click_point', 'Yarcombe pool'))
        self.assertAlmostEqual(action['x'], .22, places=3)      # target x 100+240/2 of a 1000 wide screen
        self.assertAlmostEqual(action['y'], 293/700, places=3)
        self.assertEqual(action['capture_guard'], {'region_hash': 'fixture'})
        self.assertEqual(self.provider.step_messages, [])        # no image step
        self.assertEqual(self.provider.images, [])
        self.assertEqual(len(self.provider.choice_inputs), 1)
        self.assertLessEqual(self.provider.choice_inputs[0]['max_tokens'], 120)
        self.assertIs(step['review'], False)

    def test_options_that_share_the_goal_s_words_come_first(self):
        self.site()
        task = self.task('Open the Yarcombe pool page.')
        self.provider.choices = [choose('none')]
        self.provider.steps = [done()]*2
        self.propose(task)
        listed = list(self.options())
        self.assertEqual(listed[0], 'Yarcombe pool')
        self.assertLess(listed.index('Yarcombe gym'), listed.index('Home'))

    def test_a_field_with_text_is_clicked_then_typed_into_and_a_search_field_also_searches(self):
        for role, label, then in (('AXTextField', 'Display name', ['type_text']), ('AXSearchField', 'Search staff', ['type_text', 'press_key'])):
            with self.subTest(role=role):
                self.show(app=FORMS, title='Form', lines=['Form'], targets=[target(role, label, 200)])
                task = self.task('Put Ignace Penford in the '+label.casefold()+'.', mode='change')
                self.provider.choices = [lambda messages: choose(self.options(-1)[label], 'Typing.', 'Ignace Penford')]
                step = self.propose(task)
                self.assertEqual((step['action']['type'], step['action']['label']), ('click_point', label))
                self.assertEqual([item['type'] for item in task.queue], then)
                self.assertEqual(task.queue[0]['text'], 'Ignace Penford')

    def test_a_relevant_menu_command_is_offered_and_chosen_by_its_path(self):
        value = deepcopy(self.bridge.value)
        value.update(app={'name': 'Finder', 'bundle_id': 'com.apple.finder', 'pid': 4}, targets=[target('AXButton', 'Back', 60)])
        value['window']['title'] = 'Documents'
        self.desktop.bridge = MenuBridge(value, ['View > as List', 'View > as Icons', 'File > Move to Trash', 'Go > Recents', 'File > Rename'])
        self.lines = ['Documents']
        task = self.task('Show this folder as a list.', mode='change')
        self.provider.choices = [lambda messages: choose(self.options()['View > as List'], 'List view.')]
        step = self.propose(task)
        self.assertEqual((step['action']['type'], step['action']['path']), ('choose_menu', 'View > as List'))
        offered = self.options()
        self.assertNotIn('File > Move to Trash', offered)   # unsafe commands are never offered
        self.assertNotIn('File > Rename', offered)
        self.assertNotIn('Go > Recents', offered)           # shares no word with the goal

    def test_a_typed_step_needs_a_focused_field(self):
        self.site()
        task = self.task('Search for swimming lessons.')
        self.provider.choices = [choose('none')]
        self.provider.steps = [done()]*2
        self.propose(task)
        self.assertNotIn('type', self.provider.choice_inputs[0]['schema']['properties']['step']['enum'])


class SafetyHolds(ChooseLoop):
    def checkout(self, label='Place order'):
        self.show(title='Checkout', url='https://shop.example/checkout', lines=['Checkout', 'Kettle £20'],
                  targets=[target('AXButton', label, 300), target('AXLink', 'Delivery information', 400)])

    def test_a_chosen_commit_button_waits_for_the_user(self):
        self.checkout()
        task = self.task('Order the kettle.', mode='change')
        self.provider.choices = [lambda messages: choose(self.options()['Place order'], 'Ordering.')]
        self.provider.commit = {'effect': 'commit'}
        step = self.propose(task)
        self.assertEqual(step['action']['label'], 'Place order')
        self.assertIs(step['review'], True)

    def test_a_look_only_goal_refuses_a_change_and_the_image_step_decides(self):
        self.checkout('Send message')
        task = self.task('What does delivery cost?')
        pick = lambda messages: choose(self.options(-1)['Send message'], 'Sending.')
        self.provider.choices = [pick, pick]
        self.provider.steps = [{'status': 'needs_user', 'message': 'It is not shown.', 'risk': 'navigation', 'action': None}]*2
        step = self.propose(task)
        self.assertIsNone(step['action'])           # nothing was sent
        self.assertEqual(len(self.provider.choice_inputs), 2)
        refusal = self.provider.choice_inputs[1]['messages'][-1]['content']
        self.assertIn('refused', refusal)
        self.assertEqual(len(self.provider.step_messages), 2)

    def test_stop_during_the_choice_submits_nothing(self):
        self.site()
        task = self.task('Open the Yarcombe pool page.')
        def stop(messages):
            task.stop()
            return choose(self.options()['Yarcombe pool'])
        self.provider.choices = [stop]
        with self.assertRaises(ValueError):
            self.propose(task)
        self.assertIsNone(task.pending)

    def test_a_click_that_did_nothing_is_not_offered_again_on_the_same_screen(self):
        self.site()
        task = self.task('Open the Yarcombe pool page.')
        self.provider.choices = [lambda messages: choose(self.options(-1)['Yarcombe pool'])]
        self.run_step(task)
        self.provider.choices = [choose('none')]
        self.provider.steps = [done()]*2
        self.propose(task)                          # the screen reads the same: the click did nothing
        self.assertNotIn('Yarcombe pool', self.options(-1))


class OptionsLeftOut(ChooseLoop):
    def test_a_box_this_task_ticked_is_not_offered_again(self):
        self.show(app=FORMS, title='Form', lines=['Form'], targets=[target('AXCheckBox', 'Remind me about due dates', 200),
                                                                   target('AXTextField', 'Display name', 260)])
        task = self.task('Tick the box about due dates and put Ann as the display name.', mode='change')
        task.receipts = [dict(type='click_point', label='Remind me about due dates', status='submitted', state_changed=True, screen='x')]
        self.provider.choices = [choose('none')]
        self.provider.steps = [done()]*2
        self.propose(task)
        self.assertNotIn('Remind me about due dates', self.options())
        self.assertIn('Display name', self.options())

    def test_in_a_web_page_the_browser_s_address_field_is_not_a_typing_option(self):
        page = dict(page=True)
        self.show(title='Staff directory', url='https://staff.example/', lines=['Staff directory'],
                  targets=[target('AXTextField', 'Smart Search Field', 20), target('AXSearchField', 'Search staff', 200, **page),
                           target('AXLink', 'Contact', 260, **page)])
        task = self.task('Look up Soren in this staff directory.')
        self.provider.choices = [choose('none')]
        self.provider.steps = [done()]*2
        self.propose(task)
        self.assertNotIn('Smart Search Field', self.options())
        self.assertIn('Search staff', self.options())


class DoneAndFallbacks(ChooseLoop):
    def test_done_needs_the_verifier_s_proof(self):
        self.show(title='Pool', lines=['Yarcombe pool', 'Lane hire: £14.45 per hour'])
        task = self.task('How much is lane hire?')
        self.provider.choices = [choose('done', 'Lane hire is £14.45 per hour.')]
        self.provider.verification = verdict(check('lane hire price', 'find_out', 'Lane hire: £14.45 per hour'))
        step = self.propose(task)
        self.assertEqual(step['status'], 'done')
        self.assertEqual(self.provider.step_messages, [])

    def test_an_unproven_done_is_asked_again_then_left_to_the_image_step(self):
        self.show(title='Pool', lines=['Yarcombe pool', 'Opening times'], targets=[target('AXLink', 'Lane hire prices', 300)])
        task = self.task('How much is lane hire?')
        self.provider.choices = [choose('done', 'It is £14.'), choose('done', 'It is £14.')]
        self.provider.steps = [{'status': 'needs_user', 'message': 'Not shown.', 'risk': 'navigation', 'action': None}]*2
        step = self.propose(task)
        self.assertEqual(step['status'], 'needs_user')       # never done on the chooser's word
        self.assertIn('Not done yet', self.provider.choice_inputs[1]['messages'][-1]['content'])
        self.assertEqual(len(self.provider.step_messages), 2)

    def test_when_nothing_fits_the_verifier_is_asked_once_before_guessing(self):
        self.show(app={'name': 'Finder', 'bundle_id': 'com.apple.finder', 'pid': 42}, title='Scans', lines=['Scans', 'page1.pdf'],
                  targets=[target('AXCell', 'page1.pdf', 200, file_item=True), target('AXButton', 'Downloads', 60)],
                  finder={'target': '/Users/ally/Ashhaven archive/Scans'})
        task = self.task('Open the Ashhaven archive folder, then Scans inside it.', mode='change')
        self.provider.verification = verdict(check('Open the Ashhaven archive folder', 'go_to', '/Users/ally/Ashhaven archive/Scans'),
                                             check('then Scans inside it', 'go_to', '/Users/ally/Ashhaven archive/Scans'))
        self.provider.answer = 'Scans in Ashhaven archive is open.'
        step = self.propose(task)
        self.assertEqual((step['status'], step['message']), ('done', 'Scans in Ashhaven archive is open.'))
        self.assertEqual(self.provider.choice_inputs, [])       # no guess was needed
        # Not met: the chooser decides, and the verifier is not asked again before the next guess.
        task = self.task('Open the Ashhaven archive folder, then Scans inside it.', mode='change')
        self.provider.verification = verdict(check('the Ashhaven archive folder', 'go_to', '', False), met=False, missing='x')
        self.provider.choices = [choose('none')]
        self.provider.steps = [done()]*2
        before = len(self.provider.observations)
        self.propose(task)
        self.assertEqual(len(self.provider.choice_inputs), 1)
        self.assertGreaterEqual(len(self.provider.observations)-before, 1)

    def test_none_and_an_empty_screen_go_to_the_image_step(self):
        self.site()
        task = self.task('Open the Yarcombe pool page.')
        self.provider.choices = [choose('none')]
        self.provider.steps = [done()]*2
        self.propose(task)
        self.assertTrue(self.provider.step_messages)
        self.show(targets=[], lines=[])
        self.provider.choice_inputs.clear()
        self.provider.steps = [done()]*2
        self.propose(self.task('Open the Yarcombe pool page.'))
        self.assertEqual(self.provider.choice_inputs, [])     # nothing to choose from: no chooser call

    def test_when_every_fitting_option_was_tried_the_image_step_decides(self):
        self.site()
        task = self.task('Open the Yarcombe pool page.')
        task.choose_tried = {'open the yarcombe pool page': {'yarcombe pool', 'yarcombe gym', 'penfield pool', 'pools and gyms across the district.'}}
        self.provider.steps = [done()]*2
        self.propose(task)
        self.assertEqual(self.provider.choice_inputs, [])
        self.assertTrue(self.provider.step_messages)

    def test_an_ask_is_left_to_the_image_step_and_two_equal_fits_still_get_a_question(self):
        self.show(app={'name': 'Finder', 'bundle_id': 'com.apple.finder', 'pid': 42}, title='Clubs', lines=['Clubs'],
                  targets=[target('AXCell', 'Marlcombe minutes', 200, file_item=True), target('AXCell', 'Varnhaven minutes', 240, file_item=True)])
        task = self.task('Open the minutes folder.', mode='change')
        self.provider.choices = [choose('ask', 'Which one?')]
        self.provider.steps = [{'status': 'needs_user', 'message': 'Which minutes folder?', 'risk': 'navigation', 'action': None}]*2
        self.assertEqual(self.propose(task)['status'], 'needs_user')
        self.assertEqual(len(self.provider.step_messages), 2)     # the image step asked, with its recheck
        # The chooser picking one of two equal fits is caught by the same check as an image-step click.
        task = self.task('Open the minutes folder.', mode='change')
        self.provider.choices = [lambda messages: choose(self.options(-1)['Marlcombe minutes'])]
        step = self.propose(task)
        self.assertEqual(step['status'], 'needs_user')
        self.assertIn('Varnhaven minutes', step['message'])


class WindowsSnapshots(ChooseLoop):
    def test_controls_are_offered_without_targets_or_menus_and_pressed_by_id(self):
        value = self.bridge.value
        value.update(platform='Windows', app={'name': 'Contoso Viewer', 'bundle_id': 'viewer.exe', 'pid': 9}, targets=None,
                     controls=[{'id': 'uia-1', 'role': 'AXButton', 'label': 'Display', 'bounds': {'x': 20, 'y': 100, 'width': 200, 'height': 30}},
                               {'id': 'uia-2', 'role': 'AXButton', 'label': 'Sound', 'bounds': {'x': 20, 'y': 140, 'width': 200, 'height': 30}}])
        value.pop('targets')
        self.lines = ['Settings']
        task = self.task('Open the display page.', mode='inspect')
        self.provider.choices = [lambda messages: choose(self.options()['Display'], 'Display page.')]
        step = self.propose(task)
        self.assertEqual((step['action']['type'], step['action']['control_id']), ('press_control', 'uia-1'))
        self.assertNotIn('back', self.provider.choice_inputs[0]['schema']['properties']['step']['enum'])


class Similarity(unittest.TestCase):
    def test_word_similarity_is_general(self):
        self.assertGreater(relevance('Delivery', words('what does next-day delivering cost')), 0)
        self.assertGreater(relevance('Organize items', words('organise the list')), 0)
        self.assertEqual(relevance('Membership', words('lane hire price')), 0)
        self.assertGreater(relevance('Yarcombe pool', words('Yarcombe pool page')), relevance('Yarcombe gym', words('Yarcombe pool page')))

    def test_goal_parts_follow_the_grammar(self):
        self.assertEqual(goal_parts('Open Reports, then select budget.xlsx and tell me its size.'),
                         ['Open Reports', 'select budget.xlsx', 'tell me its size'])

    def test_menu_commands_leave_out_unsafe_and_content_items(self):
        self.assertEqual(menu_commands(['File > Delete', 'History > Recently Closed > Page', 'View > as List', 'Edit > Find…', 'View > Enter Full Screen']),
                         ['View > as List'])




class TypedTextIsNotAPlace(unittest.TestCase):
    def test_a_search_window_titled_with_the_typed_query_does_not_prove_the_folder_open(self):
        from companion.task_completion import check_proven
        observation = {'goal': 'Open the Receipts 2027 folder.', 'window': 'Receipts 2027', 'screen_text': ['No results'],
                       'facts': {'finder': {'target': '/Users/ally/Documents'}, 'typed': ['Receipts 2027']}}
        part = check('Open the Receipts 2027 folder', 'go_to', 'Receipts 2027')
        self.assertFalse(check_proven(part, observation))
        observation['facts'].pop('typed')
        self.assertTrue(check_proven(part, observation))   # without the typing, the title alone is the old rule's case


class CommitsWaitWhateverTheChooserSays(ChooseLoop):
    def test_a_chosen_menu_command_that_commits_waits_for_the_user(self):
        value = deepcopy(self.bridge.value)
        value.update(app={'name': 'Shop', 'bundle_id': 'org.example.shop', 'pid': 5}, targets=[target('AXLink', 'Help', 60)])
        value['window']['title'] = 'Basket'
        self.desktop.bridge = MenuBridge(value, ['Order > Place Order', 'View > Show Basket'])
        self.lines = ['Basket', 'Kettle £20']
        task = self.task('Place the order for the kettle.', mode='change')
        self.provider.choices = [lambda messages: choose(self.options()['Order > Place Order'], 'Ordering.')]
        self.provider.commit = {'effect': 'commit'}
        step = self.propose(task)
        self.assertEqual((step['action']['type'], step['action']['path']), ('choose_menu', 'Order > Place Order'))
        self.assertIs(step['review'], True)
        self.assertEqual((self.provider.commit_inputs[0]['button'], self.provider.commit_inputs[0]['kind']), ('Order > Place Order', 'menu item'))

    def test_a_menu_command_from_the_menu_fallback_is_checked_too(self):
        from unittest.mock import Mock, patch
        task = ComputerTask('place the order', automatic=True)
        task.receipts = [{'type': 'click_point', 'label': 'Basket', 'effect': 'nothing changed on screen'}]
        desktop = Mock(bridge=Mock(menu_items=Mock(return_value=['Order > Place Order'])))
        provider = Mock()
        provider.complete_structured.side_effect = lambda system, *a, **k: {'effect': 'unsure'} if 'about to be pressed' in system else {'command': 'Order > Place Order'}
        with patch('companion.computer_task.validate_step', side_effect=lambda raw, *a, **k: {'action': raw['action']}):
            step = task.menu_pick_step(desktop, {'app': {'bundle_id': 'org.example.shop', 'name': 'Shop'}}, provider, {})
        self.assertIs(step['review'], True)

    def test_a_link_and_an_agreement_box_chosen_as_navigation_wait(self):
        for role, label in (('AXLink', 'Place order'), ('AXCheckBox', 'I agree to the terms and conditions')):
            with self.subTest(label=label):
                self.show(title='Checkout', lines=['Checkout'], targets=[target(role, label, 300), target('AXLink', 'Help', 400)])
                task = self.task('Finish the checkout.', mode='change')
                self.provider.choices = [lambda messages: choose(self.options(-1)[label])]
                self.provider.commit = {'effect': 'commit'}
                self.assertIs(self.propose(task)['review'], True)

    def test_the_chooser_can_mark_a_step_as_a_change(self):
        self.show(app=FORMS, title='Form', lines=['Form'], targets=[target('AXButton', 'Next page', 300)])
        task = self.task('Go on with the form.', mode='change')
        self.provider.choices = [lambda messages: dict(choose(self.options()['Next page']), risk='change')]
        self.assertIs(self.propose(task)['review'], True)
        self.assertEqual(self.provider.commit_inputs, [])     # already a change: no extra call
        self.assertIn('risk', self.provider.choice_inputs[0]['schema']['required'])

    def test_enter_in_a_composer_is_confirmed_and_a_new_line_in_a_document_is_not(self):
        for document, review in ((None, True), ('file:///Users/ally/Notes.txt', False)):
            with self.subTest(document=document):
                self.show(app=FORMS, title='New message', lines=['New message'], targets=[target('AXTextArea', 'Message', 300)])
                self.bridge.value['focused_role'] = 'AXTextArea'
                self.bridge.value['window'].pop('document', None)
                if document:
                    self.bridge.value['window']['document'] = document
                task = self.task('Draft a note saying hello.', mode='draft')
                self.provider.choices = [choose('enter', 'New line.')]
                self.assertIs(self.propose(task)['review'], review)


class SurvivorsKilled(ChooseLoop):
    def test_a_chosen_option_is_recorded_as_tried_for_the_open_part(self):
        self.site()
        task = self.task('Open the Yarcombe pool page.')
        self.provider.choices = [lambda messages: choose(self.options()['Yarcombe pool'])]
        self.propose(task)
        self.assertEqual(task.choose_tried, {'open the yarcombe pool page': {'yarcombe pool'}})

    def test_the_open_part_of_the_goal_outranks_the_rest(self):
        self.show(title='Trust', url='https://trust.example/', lines=['Trust'],
                  targets=[target('AXLink', 'Tickets', 100), target('AXLink', 'Parking', 140)])
        task = self.task('Open the tickets page, then the parking page.')
        task.receipts = [dict(type='click_point', label='Home', status='submitted', screen='x')]
        task.open_parts = ['the parking page']
        self.provider.choices = [choose('none')]
        self.provider.steps = [done()]*2
        self.propose(task)
        self.assertEqual(list(self.options())[0], 'Parking')
        self.assertIn('the parking page', json.loads(self.provider.choice_inputs[0]['messages'][1]['content'])['open_part_of_goal'])

    def test_the_verifier_s_unproven_parts_become_the_open_part(self):
        self.show(title='Tickets', lines=['Adult ticket: £11.60'])
        task = self.task('Tell me the ticket price and the car park charge.')
        self.provider.verification = verdict(check('the ticket price', 'find_out', 'Adult ticket: £11.60'),
                                             check('the car park charge', 'find_out', '', False), met=False)
        task.still_missing(self.provider, self.bridge.sample(), [{'text': 'Adult ticket: £11.60', 'x': .1, 'y': .1, 'width': .3, 'height': .04}], {})
        self.assertEqual(task.open_parts, ['the car park charge'])

    def test_a_switch_shows_its_state_from_its_own_row(self):
        self.show(app=FORMS, title='Settings', lines=['Automatic backups', 'On'],
                  targets=[target('AXCheckBox', 'Automatic backups', int(.1*700), x=500, width=300, height=28)])
        self.lines = []
        self.vision.capture.side_effect = lambda *args, **kwargs: {
            'image_data': self.image, 'display_id': 7, 'screen': {'x': 0, 'y': 0, 'width': 1000, 'height': 700},
            'text_regions': [{'text': 'Automatic backups', 'x': .51, 'y': .102, 'width': .15, 'height': .03},
                             {'text': 'On', 'x': .76, 'y': .102, 'width': .03, 'height': .03}]}
        task = self.task('Make sure automatic backups are on.', mode='change')
        self.provider.choices = [choose('none')]
        self.provider.steps = [done()]*2
        self.propose(task)
        current = json.loads(self.provider.choice_inputs[0]['messages'][1]['content'])
        self.assertIn('c1|checkbox, shows On|Automatic backups', current['options'])


if __name__ == '__main__':
    unittest.main()
