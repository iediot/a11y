"""Conversation outcome and inference budgets through the service + real planner.

The transport is scripted, never the routing/service path. These establish a
call budget, memory/privacy behavior and lack of unintended desktop actions;
they do not establish real-model language quality or latency.
"""
import json
import tempfile
import unittest
from unittest.mock import patch

from companion.desktop_context import DesktopContext
from companion.request_plan import INTENT_WIRE_SCHEMA
from companion.models import Identity
from companion.ollama import Planner
from companion.service import Companion
from companion.storage import Store


class EmptyDesktop:
    def catalog(self):
        return []


class ScriptedModel(Planner):
    cloud = False

    def __init__(self):
        self.calls = []
        self.router_reply = 'Hello from the fixture.'
        self.chat_reply = 'Your expedition code is amber-6741.'
        self.acts_on_computer = False

    def complete(self, system, messages, *, response_schema=None, **kwargs):
        self.calls.append((system, messages))
        if response_schema is INTENT_WIRE_SCHEMA:  # the router call: one shape per group of operations
            return dict(operation='answer', app_name='', target='', browser_mode='default',
                        remaining_request='', reply=self.router_reply,
                        acts_on_computer=self.acts_on_computer, referent='other')
        return {'message': self.chat_reply, 'target': None, 'desktop_action': None}

    def complete_structured(self, system, messages, *, schema, **kwargs):
        return self.complete(system, messages, response_schema=schema, **kwargs)


class ChatInferenceBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for name in ('companion.mail_reader.available', 'companion.mac_apps.available'):
            patcher = patch(name, return_value=False)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.model = ScriptedModel()
        self.app = Companion(Store(self.temp.name, persist_history=False),
                             Identity('fixture', 'user'), self.model)
        self.app.desktop_context = DesktopContext(EmptyDesktop())

    def assert_conversation_only(self, result):
        self.assertIsNone(result['desktop_action'])
        self.assertIsNone(result['target'])

    def assert_reply_within_budget(self, result):
        # A first-turn reply is written by the reply call, which never carries the front app or
        # window titles the router call sees (test_routing_sees_the_front_app_but_replies_do_not).
        # Taking the router's own reply instead was reverted for that reason, so one model call is
        # not pinned here: at most the router call and the reply call, and the message is the
        # reply call's when it ran.
        self.assertLessEqual(len(self.model.calls), 2)
        self.assertEqual(result['message'], self.model.chat_reply if len(self.model.calls) == 2 else self.model.router_reply)

    def test_first_turn_is_a_conversational_reply_within_two_inferences(self):
        result = self.app.ask('Please greet me.')
        self.assert_reply_within_budget(result)
        self.assert_conversation_only(result)

    def test_unresolved_capability_question_reaches_interpreter_without_search(self):
        self.model.router_reply = 'Yes, I can open installed apps when you ask.'
        result = self.app.ask('Can you open apps on my Mac?')
        self.assertIn('operation', json.dumps(self.model.calls[0][0]))  # the interpreter was asked, not a web search
        self.assert_reply_within_budget(result)
        self.assert_conversation_only(result)

    def test_empty_conversation_plan_cannot_authorize_a_task_through_auxiliary_flag(self):
        self.model.acts_on_computer = True
        self.model.router_reply = 'Yes, I can open apps when you request one.'
        result = self.app.ask('Can you open apps on my Mac?')
        self.assert_reply_within_budget(result)
        self.assert_conversation_only(result)

    def test_explicit_destination_remains_an_action_despite_an_answer_operation(self):
        from companion.request_plan import semantic_plan
        result = semantic_plan(dict(operation='answer', app_name='Finder', target='Trips', reply='Here is Trips.',
                                    acts_on_computer=True, referent='destination'), 'Show the Trips folder.')
        self.assertEqual(result['operation'], 'computer_task')

    def test_polite_named_app_request_keeps_the_deterministic_open_path(self):
        from companion.quick_open import plan
        catalog = [{'name': 'Safari', 'bundle_id': 'com.apple.Safari'}]
        result = plan('Can you open Safari?', catalog)
        self.assertEqual(result['operation'], 'launch_app')
        self.assertEqual(result['app_name'], 'Safari')
        self.assertIsNone(plan('Can you open apps on my Mac?', catalog))

    def test_explicit_memory_question_uses_memory_without_an_intent_call(self):
        self.app.remember('user', 'My expedition code is amber-6741.')
        self.app.remember('assistant', 'Got it.')
        result = self.app.ask('What code did I just say?')
        self.assertEqual(result['message'], self.model.chat_reply)
        self.assertEqual(len(self.model.calls), 1)
        self.assertIn('amber-6741', json.dumps(self.model.calls))
        self.assert_conversation_only(result)

    def test_implicit_context_still_gets_a_contextual_answer(self):
        self.app.remember('user', 'My expedition code is amber-6741.')
        result = self.app.ask('Could you repeat the expedition code?')
        self.assertEqual(result['message'], self.model.chat_reply)
        self.assertIn('amber-6741', json.dumps(self.model.calls))
        self.assert_conversation_only(result)

    def test_cloud_private_only_memory_never_leaks(self):
        self.model.cloud = True
        self.app.remember('assistant', 'PRIVATE_SCREEN_96531', private=True)
        result = self.app.ask('Please greet me.')
        self.assert_reply_within_budget(result)
        self.assertNotIn('PRIVATE_SCREEN_96531', json.dumps(self.model.calls))
        self.assert_conversation_only(result)

    def test_paused_access_discards_private_context_before_reply(self):
        self.app.remember('assistant', 'PRIVATE_SCREEN_96531', private=True)
        self.app.forget_desktop()
        result = self.app.ask('Please greet me.')
        self.assert_reply_within_budget(result)
        self.assertNotIn('PRIVATE_SCREEN_96531', json.dumps(self.model.calls))
        self.assert_conversation_only(result)


if __name__ == '__main__':
    unittest.main()
