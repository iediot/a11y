"""The router writes one object shape per group of operations (request_plan.INTENT_WIRE_SCHEMA);
code expands it into the plan the rest of the app has always consumed."""
import json
import unittest
from unittest.mock import patch

from companion.ollama import OllamaProvider, grammar_schema, reply_confidence
from companion.provider import AIConfig
from companion.request_plan import (INTENT_OMITTED, INTENT_SCHEMA, INTENT_WIRE_SCHEMA, OPERATIONS, TIMED_OPERATIONS,
                                    expand_intent, validate_plan, wire_intent)

PLAN_KEYS = {'operation', 'app_name', 'target', 'browser_mode', 'remaining_request', 'goal_mode', 'reply', 'when',
             'acts_on_computer', 'referent'}


def shapes_for(operation):
    return [shape for shape in INTENT_WIRE_SCHEMA['anyOf'] if operation in shape['properties']['operation']['enum']]


def tokens(text, logprob=-0.05):
    """A token stream as Ollama reports it, cut at JSON punctuation."""
    pieces, piece = [], ''
    for character in text:
        piece += character
        if character in ' ",:{}':
            pieces.append(piece)
            piece = ''
    return [{'token': item, 'logprob': logprob} for item in pieces + ([piece] if piece else [])]


class WireSchemaTests(unittest.TestCase):
    def test_every_operation_has_exactly_one_shape_made_of_plan_fields(self):
        for operation in OPERATIONS:
            self.assertEqual(len(shapes_for(operation)), 1, operation)
        for shape in INTENT_WIRE_SCHEMA['anyOf']:
            self.assertEqual((shape['type'], shape['additionalProperties']), ('object', False))
            self.assertLessEqual(set(shape['required']), set(shape['properties']))
            for key, definition in shape['properties'].items():
                if key not in ('operation', 'reply'):
                    self.assertEqual(definition, INTENT_SCHEMA['properties'][key], key)

    def test_each_shape_is_the_old_key_sequence_cut_short_at_the_end(self):
        # Ollama writes required keys sorted by name, then optional ones, and each value is decided
        # with everything before it in view. A shape may therefore only drop keys from the END of
        # the sequence the full schema produced: dropping or moving one in the middle changes later
        # decisions (measured: targets and times changed when the empty reply before them went).
        before = sorted(INTENT_SCHEMA['required']) + ['media_artist']
        for shape in INTENT_WIRE_SCHEMA['anyOf']:
            optional = sorted(set(shape['properties']) - set(shape['required']))
            written = sorted(shape['required']) + optional
            self.assertEqual(written, before[:len(written)], shape['properties']['operation']['enum'])
            self.assertLessEqual(set(optional), {'media_artist'})  # as before: only the artist may be left out

    def test_no_operation_loses_a_field_that_is_read_for_it(self):
        for operation in OPERATIONS:
            shape = shapes_for(operation)[0]
            for key in ('acts_on_computer', 'app_name', 'browser_mode', 'goal_mode', 'referent', 'remaining_request', 'target'):
                self.assertIn(key, shape['required'], operation)
            self.assertEqual('when' in shape['properties'], operation in TIMED_OPERATIONS + ('play_media',), operation)
            self.assertEqual('media_artist' in shape['properties'], operation == 'play_media', operation)

    def test_only_an_answer_can_carry_a_reply(self):
        for operation in OPERATIONS:
            reply = shapes_for(operation)[0]['properties']['reply']
            self.assertEqual(reply, {'type': 'string'} if operation == 'answer' else {'const': ''}, operation)

    def test_ollama_and_claude_accept_the_schema_as_written(self):
        from companion.claude import wire_schema, wrapped
        self.assertEqual(grammar_schema(INTENT_WIRE_SCHEMA), INTENT_WIRE_SCHEMA)  # nothing the grammar drops
        schema, unwrap = wrapped(INTENT_WIRE_SCHEMA)
        self.assertTrue(unwrap)  # an anyOf root travels inside "result", like the task step
        self.assertEqual(wire_schema(schema)['properties']['result'], INTENT_WIRE_SCHEMA)


class ExpansionTests(unittest.TestCase):
    def test_left_out_fields_get_their_only_meaning_and_written_ones_are_kept(self):
        written = {'acts_on_computer': True, 'app_name': 'Safari', 'browser_mode': 'default', 'goal_mode': 'inspect',
                   'operation': 'computer_task', 'referent': 'destination', 'remaining_request': '', 'reply': '',
                   'target': 'show me my Bluetooth settings'}
        plan = validate_plan(expand_intent(written))
        self.assertEqual(set(plan), PLAN_KEYS)
        self.assertEqual(plan, dict(written, **INTENT_OMITTED))
        timed = expand_intent(dict(written, operation='add_reminder', remaining_request='then tell me', when='2026-11-07T15:00'))
        self.assertEqual((timed['remaining_request'], timed['when'], timed['reply']), ('then tell me', '2026-11-07T15:00', ''))

    def test_invalid_output_is_still_rejected(self):
        from companion.models import ValidationError
        for bad in ('launch_app', None, {'app_name': 'Safari', 'target': ''},
                    {'operation': 'answer', 'app_name': '', 'target': '', 'reply': 5},
                    {'operation': 'launch_app', 'app_name': 'Safari', 'target': '', 'plan': 'x'}):
            with self.assertRaises(ValidationError):
                validate_plan(expand_intent(bad))

    def test_wire_intent_is_what_the_grammar_writes_and_round_trips(self):
        full = {'operation': 'weather', 'app_name': '', 'target': 'Porto', 'browser_mode': 'default', 'remaining_request': '',
                'goal_mode': 'inspect', 'reply': '', 'when': 'tomorrow', 'acts_on_computer': False, 'referent': 'other'}
        self.assertEqual(list(wire_intent(full)), ['acts_on_computer', 'app_name', 'browser_mode', 'goal_mode', 'operation',
                                                   'referent', 'remaining_request', 'reply', 'target', 'when'])
        self.assertEqual(expand_intent(wire_intent(full)), full)
        answer = dict(full, operation='answer', target='', when='', reply='Hello.')
        self.assertEqual(list(wire_intent(answer))[-2:], ['reply', 'target'])
        self.assertEqual(expand_intent(wire_intent(answer)), answer)
        launch = dict(full, operation='launch_app', app_name='Safari', target='', when='', reply='Opening Safari.')
        self.assertEqual(wire_intent(launch)['reply'], '')  # the shape cannot say it
        self.assertNotIn('when', wire_intent(launch))
        song = dict(full, operation='play_media', target='Hey Jude', when='', media_artist='The Beatles', acts_on_computer=True)
        self.assertEqual(list(wire_intent(song))[-3:], ['target', 'when', 'media_artist'])
        self.assertNotIn('media_artist', wire_intent(dict(full, operation='play_media')))


class PlannerTests(unittest.TestCase):
    def provider(self):
        return OllamaProvider(AIConfig(base_url='http://127.0.0.1:11434', model='local-model'))

    def test_a_compact_answer_becomes_the_full_plan_with_its_reply_confidence(self):
        provider = self.provider()
        written = {'acts_on_computer': False, 'app_name': '', 'browser_mode': 'default', 'goal_mode': 'inspect',
                   'operation': 'answer', 'referent': 'other', 'remaining_request': '',
                   'reply': 'The capital of France is Paris.', 'target': ''}
        stream = tokens(json.dumps(written), logprob=-0.02)
        with patch.object(provider, 'complete', return_value=(written, stream)) as complete:
            plan = provider.interpret_request("what's the capital of France?", context={'now': 'Friday 2026-11-06 10:12'})
        self.assertIs(complete.call_args.kwargs['response_schema'], INTENT_WIRE_SCHEMA)
        self.assertIs(complete.call_args.kwargs['logprobs'], True)
        self.assertEqual(complete.call_count, 1)  # conversation needs no coverage check
        self.assertEqual(set(plan), PLAN_KEYS | {'confidence'})
        self.assertEqual((plan['reply'], plan['when']), (written['reply'], ''))
        self.assertAlmostEqual(plan['confidence'], 0.98, places=2)
        unsure = [dict(token, logprob=-1.2) if token['token'].startswith(('Paris', 'France', 'capital')) else token for token in stream]
        self.assertLess(reply_confidence(unsure), 0.7)

    def test_an_operation_without_a_reply_has_no_confidence_and_keeps_its_coverage_check(self):
        provider = self.provider()
        written = {'acts_on_computer': True, 'app_name': 'Safari', 'browser_mode': 'window', 'goal_mode': 'inspect',
                   'operation': 'browser_open', 'referent': 'destination', 'remaining_request': '', 'reply': '',
                   'target': 'https://teams.microsoft.com'}
        goal = 'open teams in a new safari window and look if i have any tasks i need to be doing'
        with patch.object(provider, 'complete', side_effect=[(written, tokens(json.dumps(written))),
                                                             {'requires_followup_actions': True}]) as complete:
            plan = provider.interpret_request(goal)
        self.assertEqual(complete.call_count, 2)
        self.assertNotIn('confidence', plan)
        self.assertEqual((plan['operation'], plan['target'], plan['browser_mode']), ('computer_task', goal, 'window'))
        self.assertEqual(plan['opening'], {'operation': 'browser_open', 'app_name': 'Safari',
                                           'target': 'https://teams.microsoft.com', 'browser_mode': 'window'})
        self.assertEqual((plan['reply'], plan['when']), ('', ''))


if __name__ == '__main__':
    unittest.main()
