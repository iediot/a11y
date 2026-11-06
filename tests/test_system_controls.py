"""Direct system controls (companion/system_controls.py): a fake runner stands in for osascript,
networksetup and system_profiler; nothing on this computer is read or changed."""
import os
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # test_desktop_context, without discover
from companion import system_controls as sc
from companion.request_plan import INTENT_WIRE_SCHEMA, OPERATIONS, enforce_operation, preserve_desktop_goal

PANES = {pane_id: {'id': pane_id, 'name': name} for pane_id, name in (
    ('com.apple.Sound-Settings.extension', 'Sound'), ('com.apple.Displays-Settings.extension', 'Displays'),
    ('com.apple.BluetoothSettings', 'Bluetooth'), ('com.apple.Focus-Settings.extension', 'Focus'),
    ('com.apple.Accessibility-Settings.extension', 'Accessibility'))}


class FakeMac:
    """A Mac's settings behind the fixed commands system_controls runs. Every call is recorded."""

    def __init__(self, level=50, muted=False, dark=False, wifi=True, bluetooth=True, fail=(), volume_text=None, sticky=(), broken=()):
        self.broken = set(broken)
        self.level, self.muted, self.dark, self.wifi, self.bluetooth = level, muted, dark, wifi, bluetooth
        self.fail, self.volume_text, self.sticky, self.calls = set(fail), volume_text, set(sticky), []

    def __call__(self, command, **options):
        assert isinstance(command, list) and all(isinstance(part, str) for part in command), command
        assert not options.get('shell')
        self.calls.append(command)
        program, rest = command[0], command[1:]

        def done(stdout='', code=0, stderr=''):
            return subprocess.CompletedProcess(command, code, stdout, stderr)
        if program in self.fail:
            return done('', 1, 'execution error: Not authorized to send Apple events to System Events. (-1743)'
                        if program == 'osascript' else 'error')
        if program == 'osascript':
            source, args = rest[1], rest[2:]
            if source == sc.VOLUME_GET:
                return done(self.volume_text or '%d,%s\n' % (self.level, 'true' if self.muted else 'false'))
            if source == sc.VOLUME_SET:
                if 'volume' not in self.sticky:
                    self.level = int(args[0])
                return done()
            if source == sc.MUTE_SET:
                if 'mute' not in self.sticky:
                    self.muted = args[0] == 'true'
                return done()
            if source == sc.APPEARANCE_SET:
                if 'appearance' not in self.sticky:
                    self.dark = args[0] == 'true'
                return done()
            raise AssertionError('unknown script')
        if program == 'defaults':
            if 'defaults' in self.broken:
                return done('', 1, 'unexpected failure')
            return done('Dark\n') if self.dark else done('', 1, 'The domain/default pair of (kCFPreferencesAnyApplication, AppleInterfaceStyle) does not exist')
        if command == ['networksetup', '-listallhardwareports']:
            return done('Hardware Port: Wi-Fi\nDevice: en1\nEthernet Address: x\n')
        if command == ['networksetup', '-getairportpower', 'en1']:
            return done('Wi-Fi Power (en1): %s\n' % ('On' if self.wifi else 'Off'))
        if command[:3] == ['networksetup', '-setairportpower', 'en1']:
            if 'wifi' not in self.sticky:
                self.wifi = command[3] == 'on'
            return done()
        if command == ['system_profiler', 'SPBluetoothDataType']:
            return done('Bluetooth:\n  Bluetooth Controller:\n    State: %s\n' % ('On' if self.bluetooth else 'Off'))
        if program == 'open':
            return done('', 1, 'cannot open') if 'open' in self.broken else done()
        raise AssertionError('unexpected command %r' % command)

    def scripts(self):
        return [call for call in self.calls if call[0] == 'osascript']

    def opened(self):
        return [call[1] for call in self.calls if call[0] == 'open']


def run(target, request='', mac=None, **options):
    mac = mac or FakeMac()
    with patch('companion.settings_panes.pane', side_effect=PANES.get):
        return sc.control(target, request, runner=mac, platform='darwin', **options), mac


class ParseTests(unittest.TestCase):
    def test_router_targets_map_to_one_setting_and_change(self):
        cases = {
            ('volume | down a bit', ''): ('volume', {'by': -sc.SMALL_STEP}),
            ('volume | down', 'turn the sound down a little'): ('volume', {'by': -sc.SMALL_STEP}),
            ('volume | up', ''): ('volume', {'by': sc.STEP}),
            ('volume | way down', ''): ('volume', {'by': -sc.LARGE_STEP}),
            ('volume | 30', ''): ('volume', {'set': 30}),
            ('volume | 30%', ''): ('volume', {'set': 30}),
            ('volume | up by 5', ''): ('volume', {'by': 5}),
            ('volume | max', ''): ('volume', {'set': 100}),
            ('sound | louder', ''): ('volume', {'by': sc.STEP}),
            ('mute | on', ''): ('mute', {'on': True}),
            ('mute', 'mute'): ('mute', {'on': True}),
            ('unmute', 'unmute please'): ('mute', {'on': False}),
            ('mute |', 'is the sound muted?'): ('mute', {'read': True}),
            ('appearance | on', ''): ('appearance', {'on': True}),
            ('appearance | dark', ''): ('appearance', {'on': True}),
            ('appearance | light', ''): ('appearance', {'on': False}),
            ('dark mode', 'switch to dark mode'): ('appearance', {'on': True}),
            ('appearance |', 'turn on dark mode'): ('appearance', {'on': True}),
            ('wifi |', 'is wifi on?'): ('wifi', {'read': True}),
            ('Wi-Fi | off', ''): ('wifi', {'on': False}),
            ('wifi | switch off', ''): ('wifi', {'on': False}),
            ('bluetooth', 'is bluetooth on'): ('bluetooth', {'read': True}),
            ('text_size | up', ''): ('text_size', {'by': sc.STEP}),
            ('focus | on', ''): ('focus', {'on': True}),
            ('do not disturb | on', ''): ('focus', {'on': True}),
            ('brightness | down a bit', ''): ('brightness', {'by': -sc.SMALL_STEP}),
        }
        for (target, request), expected in cases.items():
            self.assertEqual(sc.parse(target, request), expected, target)

    def test_an_unknown_setting_asks_and_runs_nothing(self):
        mac = FakeMac()
        result = sc.control('printer | on', 'turn on the printer', runner=mac, platform='darwin')
        self.assertIsNone(result['setting'])
        self.assertIn('Which setting', result['message'])
        self.assertEqual(mac.calls, [])


class VolumeTests(unittest.TestCase):
    def test_relative_and_absolute_changes_report_the_observed_level(self):
        for target, start, expected in (('volume | down a bit', 50, 40), ('volume | up', 50, 70), ('volume | way down', 50, 10),
                                        ('volume | up', 95, 100), ('volume | down', 5, 0), ('volume | 30', 80, 30)):
            result, mac = run(target, mac=FakeMac(level=start))
            self.assertEqual(mac.level, expected, target)
            self.assertEqual(result['message'], 'Volume is now %d%%.' % expected)
            self.assertEqual((result['before']['level'], result['after']['level']), (start, expected))
            self.assertTrue(result['changed'])

    def test_already_at_the_limit_changes_nothing(self):
        result, mac = run('volume | up', mac=FakeMac(level=100))
        self.assertEqual(result['message'], 'Volume is already at 100%.')
        self.assertFalse(result['changed'])
        self.assertEqual([call for call in mac.scripts() if call[2] != sc.VOLUME_GET], [])

    def test_reading_the_volume_changes_nothing(self):
        result, mac = run('volume |', 'what is the volume?', mac=FakeMac(level=35, muted=True))
        self.assertEqual(result['message'], 'Volume is at 35%, and sound is muted.')
        self.assertEqual([call[2] for call in mac.scripts()], [sc.VOLUME_GET])

    def test_turning_up_while_muted_unmutes(self):
        result, mac = run('volume | up a bit', mac=FakeMac(level=30, muted=True))
        self.assertEqual((mac.level, mac.muted), (40, False))
        self.assertEqual(result['message'], 'Volume is now 40%.')

    def test_mute_and_unmute(self):
        result, mac = run('mute | on', mac=FakeMac(level=40))
        self.assertTrue(mac.muted)
        self.assertEqual(result['message'], 'Sound is muted.')
        result, mac = run('mute | off', mac=FakeMac(level=40, muted=True))
        self.assertEqual(result['message'], 'Sound is on again, at 40%.')
        result, mac = run('volume | off', 'turn the sound off', mac=FakeMac(level=40))
        self.assertTrue(mac.muted)

    def test_values_go_as_argv_never_into_the_script(self):
        hostile = 'volume | 30" & do shell script "touch /tmp/pwned'
        result, mac = run(hostile, hostile)
        sources = {call[2] for call in mac.scripts()}
        self.assertLessEqual(sources, {sc.VOLUME_GET, sc.VOLUME_SET, sc.MUTE_SET, sc.APPEARANCE_SET})
        for call in mac.scripts():
            self.assertEqual(call[:2], ['osascript', '-e'])
            for argument in call[3:]:
                self.assertRegex(argument, r'^(?:\d{1,3}|true|false)$')
        self.assertEqual(mac.level, 30)
        for source in (sc.VOLUME_SET, sc.MUTE_SET, sc.APPEARANCE_SET):
            self.assertIn('argv', source)


class HonestFailureTests(unittest.TestCase):
    def test_a_failed_command_never_claims_a_change(self):
        result, mac = run('volume | down', mac=FakeMac(fail={'osascript'}))
        self.assertNotIn('now', result['message'])
        self.assertFalse(result['changed'])

    def test_a_setting_that_did_not_take_reports_what_is_observed(self):
        result, _ = run('volume | 20', mac=FakeMac(level=60, sticky={'volume'}))
        self.assertEqual(result['message'], 'I set the volume to 20%, but it now reads 60%.')
        result, _ = run('appearance | dark', mac=FakeMac(sticky={'appearance'}))
        self.assertEqual(result['message'], 'I asked for dark mode, but the Mac still reports light mode.')

    def test_an_output_without_volume_control(self):
        result, _ = run('volume | up', mac=FakeMac(volume_text='missing value,missing value'))
        self.assertEqual(result['message'], 'This output device has no volume control I can read.')

    def test_missing_automation_permission_is_explained(self):
        result, _ = run('appearance | dark', mac=FakeMac(fail={'osascript'}))
        self.assertIn('Automation', result['message'])
        self.assertFalse(result['changed'])

    def test_unrunnable_tool(self):
        def broken(command, **options):
            raise OSError('missing')
        result = sc.control('wifi | off', '', runner=broken, platform='darwin')
        self.assertIn('couldn’t', result['message'])
        self.assertFalse(result['changed'])


class OtherSettingsTests(unittest.TestCase):
    def test_dark_mode(self):
        result, mac = run('appearance | dark')
        self.assertTrue(mac.dark)
        self.assertEqual(result['message'], 'Dark mode is now on.')
        result, mac = run('appearance | dark', mac=FakeMac(dark=True))
        self.assertEqual(result['message'], 'Dark mode is already on.')
        self.assertEqual(mac.scripts(), [])
        result, mac = run('appearance | toggle', mac=FakeMac(dark=True))
        self.assertEqual((mac.dark, result['message']), (False, 'Light mode is now on.'))

    def test_wifi_state_and_switching_use_the_wifi_device(self):
        result, _ = run('wifi |', 'is wifi on?')
        self.assertEqual(result['message'], 'Wi-Fi is on.')
        result, mac = run('wifi | off')
        self.assertFalse(mac.wifi)
        self.assertIn(['networksetup', '-setairportpower', 'en1', 'off'], mac.calls)
        self.assertTrue(result['message'].startswith('Wi-Fi is now off.'))

    def test_bluetooth_is_read_and_its_pane_opened_for_a_change(self):
        result, mac = run('bluetooth |', 'is bluetooth on?')
        self.assertEqual((result['message'], mac.opened()), ('Bluetooth is on.', []))
        result, mac = run('bluetooth | off')
        self.assertEqual(mac.opened(), ['x-apple.systempreferences:com.apple.BluetoothSettings'])
        self.assertIn('Bluetooth is on.', result['message'])
        self.assertIn('opened Bluetooth settings', result['message'])
        self.assertFalse(result['changed'])
        self.assertTrue(mac.bluetooth)

    def test_settings_without_a_command_open_their_pane_and_say_so(self):
        for target, pane, words in (('text_size | up', 'com.apple.Accessibility-Settings.extension', 'Text size'),
                                    ('brightness | down', 'com.apple.Displays-Settings.extension', 'Displays'),
                                    ('focus | on', 'com.apple.Focus-Settings.extension', 'Focus')):
            result, mac = run(target)
            self.assertEqual(mac.opened(), ['x-apple.systempreferences:'+pane], target)
            self.assertIn('can’t', result['message'])
            self.assertIn(words, result['message'])

    def test_a_pane_missing_from_the_catalog_is_not_opened(self):
        mac = FakeMac()
        with patch('companion.settings_panes.pane', return_value=None):
            result = sc.control('text_size | up', '', runner=mac, platform='darwin')
        self.assertEqual(mac.opened(), [])
        self.assertIn('System Settings', result['message'])


class ReviewFixTests(unittest.TestCase):
    """Wording the first version got wrong (review, 2026-11-06)."""

    def test_negations_follow_the_mode_or_setting_named(self):
        for target, request, expected in (('appearance |', 'disable dark mode', {'on': False}),
                                          ('appearance | deactivate dark mode', '', {'on': False}),
                                          ('appearance |', 'turn off light mode', {'on': True}),
                                          ('appearance | no more dark mode', '', {'on': False}),
                                          ('mute |', 'stop muting', {'on': False}),
                                          ('mute | stop', 'stop muting the sound', {'on': False}),
                                          ('wifi |', 'switch the wifi off', {'on': False})):
            self.assertEqual(sc.parse(target, request)[1], expected, request)
        result, mac = run('appearance |', 'disable dark mode', mac=FakeMac(dark=True))
        self.assertEqual((mac.dark, result['message']), (False, 'Light mode is now on.'))

    def test_to_n_is_a_level_and_by_n_a_step(self):
        for target, request, start, expected in (('volume | up to 80', 'turn the volume up to 80', 60, 80),
                                                 ('volume | down to 20', 'lower the volume to 20', 60, 20),
                                                 ('volume |', 'set the volume to 30', 60, 30),
                                                 ('volume |', 'switch the volume to 30', 60, 30),
                                                 ('volume | up by 10', 'turn the volume up by 10', 60, 70)):
            result, mac = run(target, request, mac=FakeMac(level=start))
            self.assertEqual(mac.level, expected, request)

    def test_a_polite_question_with_an_action_is_a_command(self):
        result, mac = run('wifi |', 'could you turn off wifi?')
        self.assertFalse(mac.wifi)
        result, mac = run('volume |', 'can you make it louder?')
        self.assertEqual(mac.level, 70)
        result, mac = run('wifi |', 'can you tell me if wifi is on?')
        self.assertEqual((mac.wifi, result['message']), (True, 'Wi-Fi is on.'))

    def test_a_target_that_names_no_setting_in_the_request_changes_nothing(self):
        for target, request in (('volume | down', 'turn down the offer'), ('appearance | dark', 'dark chocolate recipe'),
                                ('| down', 'turn it down')):
            result, mac = run(target, request)
            self.assertIsNone(result['setting'], request)
            self.assertEqual(mac.calls, [], request)

    def test_dark_mode_unreadable_is_unknown_not_light(self):
        result, mac = run('appearance | dark', mac=FakeMac(broken={'defaults'}))
        self.assertEqual(mac.scripts(), [])
        self.assertNotIn('now', result['message'])
        self.assertFalse(result['changed'])

    def test_questions_about_unreadable_settings_open_nothing(self):
        for target, request in (('text_size |', 'how big is my text?'), ('focus |', 'is do not disturb on?'),
                                ('brightness |', 'how bright is my screen?')):
            result, mac = run(target, request)
            self.assertEqual(mac.opened(), [], request)
            self.assertIn('can’t read', result['message'])

    def test_settings_that_do_not_take_are_reported(self):
        result, _ = run('mute | on', mac=FakeMac(sticky={'mute'}))
        self.assertEqual(result['message'], 'I tried, but sound is still on.')
        result, _ = run('wifi | off', mac=FakeMac(sticky={'wifi'}))
        self.assertEqual(result['message'], 'I tried to turn Wi-Fi off, but it is still on.')

    def test_max_volume_while_muted_unmutes(self):
        result, mac = run('volume | max', mac=FakeMac(level=100, muted=True))
        self.assertFalse(mac.muted)
        self.assertEqual(result['message'], 'Volume is now 100%.')

    def test_a_failed_pane_open_is_not_reported_as_opened(self):
        result, mac = run('focus | on', mac=FakeMac(broken={'open'}))
        self.assertNotIn('opened', result['message'])
        self.assertIn('System Settings → Focus', result['message'])

    def test_leftover_work(self):
        self.assertEqual(sc.leftover('a bit', 'volume'), 'same')
        self.assertEqual(sc.leftover('the volume down a bit', 'volume'), 'same')
        self.assertEqual(sc.leftover('turn on dark mode', 'mute'), ('setting', 'appearance | turn on dark mode'))
        self.assertEqual(sc.leftover('open Safari', 'mute'), 'task')


class FakeHelper:
    """The desktop helper's brightness flags (native/DesktopBridge.swift) on a fake display."""

    def __init__(self, level=0.5, readable=True, settable=True):
        self.level, self.readable, self.settable, self.calls = level, readable, settable, []

    def __call__(self, *args):
        self.calls.append(args)
        if args == ('--brightness',):
            if not self.readable:
                raise RuntimeError('This display’s brightness cannot be read.')
            return {'brightness': self.level}
        if args[0] == '--set-brightness':
            if not self.settable:
                raise RuntimeError('cannot be set')
            self.level = float(args[1])
            return {'brightness': self.level}
        if args[0] == '--brightness-key':
            return {'submitted': True, 'presses': int(args[2])}
        raise AssertionError(args)


class BrightnessTests(unittest.TestCase):
    def test_read_and_set_report_the_level_read_back(self):
        helper = FakeHelper(level=0.5)
        result, mac = run('brightness |', 'how bright is my screen?', native=helper)
        self.assertEqual(result['message'], 'Screen brightness is at 50%.')
        result, mac = run('brightness | down a bit', native=helper)
        self.assertEqual(result['message'], 'Brightness is now 40%.')
        self.assertIn(('--set-brightness', '0.400'), helper.calls)
        result, mac = run('brightness | 75', native=helper)
        self.assertEqual((result['message'], mac.opened()), ('Brightness is now 75%.', []))

    def test_without_a_reading_the_keys_are_pressed_and_the_reply_says_unconfirmed(self):
        helper = FakeHelper(readable=False)
        result, _ = run('brightness | down a bit', native=helper)
        self.assertIn(('--brightness-key', 'down', '2'), helper.calls)
        self.assertIn('can’t read', result['message'])
        self.assertNotIn('now', result['message'])
        result, _ = run('brightness | 40', native=helper)
        self.assertIn('exact level', result['message'])
        self.assertFalse([call for call in helper.calls if call[0] == '--brightness-key' and call[2] != '2'])

    def test_a_display_that_cannot_be_set_falls_back_to_the_keys(self):
        helper = FakeHelper(level=0.5, settable=False)
        result, _ = run('brightness | up', native=helper)
        self.assertIn(('--brightness-key', 'up', '3'), helper.calls)
        self.assertIn('confirm', result['message'])

    def test_helper_values_are_arguments_in_range(self):
        helper = FakeHelper(level=0.95)
        run('brightness | up a lot', native=helper)
        value = next(call[1] for call in helper.calls if call[0] == '--set-brightness')
        self.assertEqual(value, '1.000')


class WindowsTests(unittest.TestCase):
    def test_windows_refuses_honestly_and_runs_nothing(self):
        runner = Mock(side_effect=AssertionError('no commands on Windows'))
        for target, words in (('volume | down a bit', 'can’t change the volume on Windows yet'),
                              ('wifi |', 'can’t check Wi-Fi on Windows yet')):
            result = sc.control(target, '', runner=runner, platform='win32')
            self.assertIn(words, result['message'])
            self.assertFalse(result['changed'])
        runner.assert_not_called()

    def test_other_platforms_refuse(self):
        runner = Mock()
        result = sc.control('volume | up', '', runner=runner, platform='linux')
        self.assertIn('macOS and Windows only', result['message'])
        runner.assert_not_called()


class RoutingTests(unittest.TestCase):
    """The router's typed operation reaches system_controls through the service; no screen task."""

    def setUp(self):
        from companion.desktop_context import DesktopContext
        from companion.models import Identity
        from companion.service import Companion
        from companion.storage import Store
        from test_desktop_context import FakeBridge
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        bridge = FakeBridge()
        bridge.catalog = Mock(return_value=[{'name': 'Safari', 'bundle_id': 'com.apple.Safari'}])
        self.provider = Mock()
        self.app = Companion(Store(self.temp.name), Identity('company', 'employee'), self.provider)
        self.app.desktop_context = DesktopContext(bridge, clock=lambda: 100)
        self.mac = FakeMac()
        # Only system_controls sees the fake Mac; the rest of the service keeps the real modules.
        commands = SimpleNamespace(run=self.mac, SubprocessError=subprocess.SubprocessError)
        for patcher in (patch('companion.system_controls.subprocess', commands), patch('companion.system_controls.sys', SimpleNamespace(platform='darwin')),
                        patch('companion.settings_panes.pane', side_effect=PANES.get)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def route(self, target, **extra):
        self.provider.interpret_request.return_value = dict({'operation': 'system_control', 'app_name': '', 'target': target, 'reply': '',
                                                             'browser_mode': 'default', 'remaining_request': '', 'goal_mode': 'change',
                                                             'acts_on_computer': True, 'referent': 'other'}, **extra)

    def test_varied_wording_is_one_typed_operation_executed_in_code(self):
        for request, target, expected in (
                ('turn the volume down a bit', 'volume | down a bit', 'Volume is now 40%.'),
                ('a little quieter please', 'volume | down', 'Volume is now 30%.'),
                ('set volume to 75', 'volume | 75', 'Volume is now 75%.'),
                ('mute', 'mute | on', 'Sound is muted.'),
                ('turn on dark mode', 'appearance | on', 'Dark mode is now on.'),
                ('is wifi on', 'wifi |', 'Wi-Fi is on.')):
            self.route(target)
            response = self.app.ask(request)
            self.assertEqual(response['message'], expected, request)
            self.assertIsNone(response['desktop_action'], request)
        from companion.request_plan import GROUNDING_SYSTEM
        # Follow-up rewriting may ask the model about the wording; nothing is grounded on the screen.
        self.assertFalse([call for call in self.provider.complete_structured.call_args_list if call.args[0] == GROUNDING_SYSTEM])

    def test_remaining_work_carries_on_as_a_task(self):
        self.route('appearance | dark', remaining_request='open Safari')
        response = self.app.ask('turn on dark mode and then open Safari')
        self.assertEqual(response['message'], 'Dark mode is now on.')
        self.assertEqual(response['desktop_action']['type'], 'start_task')
        self.assertEqual(response['desktop_action']['goal'], 'open Safari')

    def test_a_setting_in_another_app_is_that_apps_task(self):
        self.route('mute | on', app_name='Slack')
        plan = self.app.prepare_request('mute the conversation in Slack')
        self.assertEqual(plan['operation'], 'computer_task')
        self.assertFalse(self.mac.muted)

    def test_two_settings_both_run_in_code(self):
        self.route('mute | on', remaining_request='turn on dark mode')
        response = self.app.ask('mute and turn on dark mode')
        self.assertEqual(response['message'], 'Sound is muted. Dark mode is now on.')
        self.assertIsNone(response['desktop_action'])
        self.assertTrue(self.mac.muted and self.mac.dark)

    def test_a_restated_amount_is_not_more_work(self):
        self.route('volume | down', remaining_request='a bit')
        response = self.app.ask('turn the volume down a bit')
        self.assertEqual((response['message'], response['desktop_action']), ('Volume is now 40%.', None))

    def test_settings_words_do_not_turn_it_into_a_screen_task(self):
        # system_info about "settings" becomes a task (to show the pane); a setting change does not.
        self.route('volume | up')
        plan = self.app.prepare_request('turn up the volume setting')
        self.assertEqual(plan['operation'], 'system_control')

    def test_wire_schema_and_plan_contract(self):
        self.assertIn('system_control', OPERATIONS)
        general = INTENT_WIRE_SCHEMA['anyOf'][-1]
        self.assertIn('system_control', general['properties']['operation']['enum'])
        self.assertNotIn('when', general['properties'])
        plan = {'operation': 'system_control', 'app_name': '', 'target': 'volume | up', 'browser_mode': 'default', 'remaining_request': ''}
        self.assertEqual(preserve_desktop_goal(plan, 'volume up')['operation'], 'system_control')
        enforce_operation(plan, {'desktop_action': {'type': 'start_task', 'goal': 'open Safari'}})
        with self.assertRaises(Exception):
            enforce_operation(plan, {'desktop_action': {'type': 'click_point'}})


if __name__ == '__main__':
    unittest.main()
