import unittest
from unittest.mock import Mock,patch
from companion.ollama import Planner,OllamaProvider
from companion.provider import AIConfig,ProviderError
from companion.request_plan import semantic_plan


class MotionScopeTests(unittest.TestCase):
    def provider(self,effect):
        p=OllamaProvider(AIConfig(model='local'))
        p.complete_structured=Mock(return_value={'effect':effect})
        return p
    def test_misrouted_app_work_cannot_move_mascot_or_keep_wrong_scope(self):
        base=dict(operation='move_object',target='Timer',app_name='Clock',referent='companion',goal_mode='inspect')
        p=self.provider('computer_task')
        with patch.object(Planner,'interpret_request',return_value=base):result=p.interpret_request('Begin a countdown in Clock.')
        self.assertEqual(result['operation'],'computer_task');self.assertEqual(result['target'],'Begin a countdown in Clock.')
        self.assertNotIn('goal_mode',result);self.assertEqual(semantic_plan(result,'Begin a countdown in Clock.')['operation'],'computer_task')
    def test_auxiliary_referent_cannot_create_movement_from_a_task(self):
        p=self.provider('computer_task')
        with patch.object(Planner,'interpret_request',return_value=dict(operation='computer_task',target='Appearance',app_name='System Settings',referent='companion')):
            result=p.interpret_request('Navigate to the Appearance pane.')
        self.assertEqual(result['referent'],'destination')
    def test_requested_relocation_and_pointing_keep_distinct_effects(self):
        for effect,operation in [('relocate_companion','move_object'),('point','highlight')]:
            p=self.provider(effect)
            with patch.object(Planner,'interpret_request',return_value=dict(operation='move_object',target='button',app_name='',referent='other')):
                result=p.interpret_request('Point out this button.')
            self.assertEqual(result['operation'],operation)
            if effect=='point':self.assertEqual(result['referent'],'visible_target')
    def test_conversation_uncertainty_and_invalid_checks_never_propose_actions(self):
        for effect in ('conversation','uncertain'):
            p=self.provider(effect)
            with patch.object(Planner,'interpret_request',return_value=dict(operation='move_geometry',app_name='',target='top left')):
                self.assertEqual(p.interpret_request('hello')['operation'],'answer')
        p=self.provider('send')
        with patch.object(Planner,'interpret_request',return_value=dict(operation='move_object',app_name='',target='button')),self.assertRaises(ProviderError):p.interpret_request('hello')
    def test_ordinary_tasks_and_explicit_chat_do_not_add_a_call(self):
        for base in [dict(operation='computer_task',app_name='Finder',target='open folder',referent='destination'),
                     dict(operation='answer',app_name='',target='',referent='companion',acts_on_computer=False)]:
            p=self.provider('uncertain')
            with patch.object(Planner,'interpret_request',return_value=base):self.assertEqual(p.interpret_request('request'),base)
            p.complete_structured.assert_not_called()

    def test_pointing_and_chat_cannot_inherit_a_stale_app_opening(self):
        for effect in ('point','conversation','uncertain'):
            p=self.provider(effect)
            base=dict(operation='move_object',app_name='Clock',target='button',referent='companion',opening={'operation':'launch_app','app_name':'Clock','target':''})
            with patch.object(Planner,'interpret_request',return_value=base):result=p.interpret_request('request')
            self.assertNotIn('opening',result)

    def test_reading_and_pointing_keep_their_operation_without_reclassification(self):
        for operation in ('highlight', 'inspect_screen'):
            base = dict(operation=operation, app_name='', target='close button', referent='companion')
            p = self.provider('computer_task')
            with patch.object(Planner, 'interpret_request', return_value=base):
                result = p.interpret_request('request')
            self.assertEqual(result['operation'], operation)
            self.assertEqual(semantic_plan(result, 'request')['operation'], operation)
            if operation == 'inspect_screen':
                p.complete_structured.assert_not_called()
            else:
                p.complete_structured.assert_called_once()

    def test_relocation_mislabelled_as_highlight_still_moves_only_after_effect_check(self):
        p = self.provider('relocate_companion')
        base = dict(operation='highlight', app_name='', target='upper left', referent='companion')
        with patch.object(Planner,'interpret_request',return_value=base):
            result = p.interpret_request('Please tuck yourself into the upper-left corner.')
        self.assertEqual(semantic_plan(result,'Please tuck yourself into the upper-left corner.')['operation'],'move_geometry')
