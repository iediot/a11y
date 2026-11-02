"""System Settings panes open by their exact identifier (harder suite: "Go to About" waited on a
confirmation for a pixel click inside General)."""
import json
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from companion import settings_panes
from companion.computer_task import ComputerTask, PANE_SYSTEM

PANES = [{'id': 'com.apple.SystemProfiler.AboutExtension', 'name': 'About'},
         {'id': 'com.apple.Keyboard-Settings.extension', 'name': 'Keyboard'},
         {'id': 'com.apple.Sound-Settings.extension', 'name': 'Sound'}]
SETTINGS = {'app': {'name': 'System Settings', 'bundle_id': 'com.apple.systempreferences'}, 'window': {'title': 'General'}}


class CatalogTests(unittest.TestCase):
    def write(self, root, name, identifier, display, point=settings_panes.SETTINGS_POINT):
        info = root/(name+'.appex')/'Contents'
        info.mkdir(parents=True)
        with open(info/'Info.plist', 'wb') as handle:
            plistlib.dump({'CFBundleIdentifier': identifier, 'CFBundleDisplayName': display,
                           'EXAppExtensionAttributes': {'EXExtensionPointIdentifier': point}}, handle)

    def test_catalog_reads_settings_extensions_only_with_readable_names(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.write(root, 'About', 'com.apple.SystemProfiler.AboutExtension', 'About')
            self.write(root, 'Wifi', 'com.apple.wifi-settings-extension', 'WiFiSettings')
            self.write(root, 'Date', 'com.apple.Date-Time-Settings.extension', 'DateAndTime Extension')
            self.write(root, 'Pay', 'com.apple.WalletSettingsExtension', 'Wallet & Apple\xa0Pay')
            self.write(root, 'Widget', 'com.apple.weather.widget', 'Weather', point='com.apple.widgetkit-extension')
            (root/'Broken.appex'/'Contents').mkdir(parents=True)
            (root/'Broken.appex'/'Contents'/'Info.plist').write_text('<plist><dict><bad')
            settings_panes.catalog.cache_clear()
            with patch.object(settings_panes, 'EXTENSIONS', root), patch.object(settings_panes.sys, 'platform', 'darwin'):
                names = {item['name']: item['id'] for item in settings_panes.catalog()}
            settings_panes.catalog.cache_clear()
        self.assertEqual(names, {'About': 'com.apple.SystemProfiler.AboutExtension', 'wifi': 'com.apple.wifi-settings-extension',
                                 'Date Time': 'com.apple.Date-Time-Settings.extension', 'Wallet & Apple Pay': 'com.apple.WalletSettingsExtension'})

    def test_no_installed_catalog_off_macos(self):
        # Windows gets its fixed ms-settings: list instead (tests/test_platform_parity.py); nothing else has one.
        settings_panes.catalog.cache_clear()
        with patch.object(settings_panes.sys, 'platform', 'linux'):
            self.assertEqual(settings_panes.catalog(), [])
        with patch.object(settings_panes.sys, 'platform', 'win32'), patch.object(settings_panes.Path, 'glob') as glob:
            settings_panes.catalog.cache_clear()
            self.assertNotIn('com.apple', repr(settings_panes.catalog()))
            glob.assert_not_called()
        settings_panes.catalog.cache_clear()


class OpenPaneActionTests(unittest.TestCase):
    def desktop(self):
        from companion.desktop_context import DesktopContext
        desktop = DesktopContext(Mock(), clock=lambda: 100)
        return desktop

    def test_only_catalog_identifiers_are_accepted(self):
        desktop = self.desktop()
        with patch('companion.settings_panes.catalog', return_value=PANES):
            action = desktop.authorize_action({'type': 'open_settings_pane', 'pane': 'com.apple.SystemProfiler.AboutExtension'})
            self.assertEqual(action, {'type': 'open_settings_pane', 'pane': 'com.apple.SystemProfiler.AboutExtension',
                                      'bundle_id': 'com.apple.systempreferences', 'label': 'Open About in System Settings'})
            for bad in ({'type': 'open_settings_pane', 'pane': 'com.apple.preference.security?Privacy_Camera'},
                        {'type': 'open_settings_pane', 'pane': 'About'},
                        {'type': 'open_settings_pane', 'pane': 'com.apple.SystemProfiler.AboutExtension', 'url': 'https://x'}):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    desktop.authorize_action(bad)

    def test_execution_opens_the_validated_pane_as_one_argument(self):
        desktop = self.desktop()
        with patch('companion.settings_panes.catalog', return_value=PANES), \
                patch('companion.desktop_context.subprocess.run', return_value=Mock(returncode=0)) as run, \
                patch.object(desktop, 'collect_once'):
            result = desktop.application_action({'type': 'open_settings_pane', 'pane': 'com.apple.Keyboard-Settings.extension'})
        self.assertTrue(result['submitted'])
        self.assertEqual(run.call_args.args[0], ['open', 'x-apple.systempreferences:com.apple.Keyboard-Settings.extension'])


class PaneStepTests(unittest.TestCase):
    def step(self, goal, reply, snapshot=SETTINGS):
        task = ComputerTask(goal, automatic=True, goal_mode='inspect')
        provider = Mock(complete_structured=Mock(return_value={'pane': reply}))
        desktop = Mock()
        desktop.authorize_action.side_effect = lambda a: dict(a, bundle_id='com.apple.systempreferences', label='Open X in System Settings')
        with patch('companion.settings_panes.catalog', return_value=PANES):
            return task, provider, desktop, task.settings_pane_step(desktop, snapshot, provider, {})

    def test_the_model_picks_a_pane_name_and_code_opens_its_identifier(self):
        task, provider, desktop, step = self.step('Go to About in System Settings and read out the installed memory.', 'About')
        self.assertEqual(step['action']['pane'], 'com.apple.SystemProfiler.AboutExtension')
        call = provider.complete_structured.call_args
        self.assertEqual(call.args[0], PANE_SYSTEM)
        self.assertEqual(call.kwargs['schema']['properties']['pane']['enum'], ['none', 'About', 'Keyboard', 'Sound'])
        self.assertEqual(json.loads(call.args[1][0]['content'])['current_pane'], 'General')
        self.assertIsNone(task.settings_pane_step(desktop, SETTINGS, provider, {}))  # once per task
        self.assertEqual(provider.complete_structured.call_count, 1)

    def test_none_or_the_current_pane_leaves_it_to_the_model(self):
        self.assertIsNone(self.step('close this window', 'none')[3])
        self.assertIsNone(self.step('Turn the volume down a bit', 'Sound',
                                    dict(SETTINGS, window={'title': 'Sound'}))[3])

    def test_only_with_system_settings_in_front(self):
        task, provider, _, step = self.step('Show me the About pane', 'About', {'app': {'bundle_id': 'com.apple.Safari'}})
        self.assertIsNone(step)
        provider.complete_structured.assert_not_called()

    def test_prompt_examples_are_not_the_live_test_wordings(self):
        for wording in ('installed memory', 'keyboard behaves'):
            self.assertNotIn(wording, PANE_SYSTEM)


if __name__ == '__main__':
    unittest.main()


class AfterPaneTests(unittest.TestCase):
    def task(self):
        task = ComputerTask('Go to About in System Settings and read out the installed memory in GB, without changing anything.',
                            automatic=True, goal_mode='inspect')
        task.receipts = [dict(type='open_settings_pane', label='Open About in System Settings', status='submitted', screen='a')]
        task.screen_seen = 'a'
        return task

    REGIONS = [dict(text='About', x=.4, y=.1, width=.1, height=.03), dict(text='Memory', x=.4, y=.4, width=.1, height=.03),
               dict(text='16 GB', x=.6, y=.4, width=.1, height=.03)]

    def test_an_opened_pane_checks_the_full_goal_and_answers(self):
        provider = Mock(complete_structured=Mock(side_effect=[
            dict(met=True, missing='', evidence='16 GB'), dict(message='Your Mac has 16 GB of memory.')]))
        result = self.task().goal_met(provider, dict(SETTINGS, window={'title': 'About'}), self.REGIONS, {})
        self.assertEqual(result, {'status': 'done', 'action': None, 'message': 'Your Mac has 16 GB of memory.'})

    def test_the_pane_name_alone_does_not_answer_a_question(self):
        provider = Mock(complete_structured=Mock(return_value=dict(met=False, missing='the memory size', evidence='About')))
        self.assertIsNone(self.task().goal_met(provider, dict(SETTINGS, window={'title': 'About'}), self.REGIONS, {}))
