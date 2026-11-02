"""macOS-only features and their Windows paths (docs/ai/PLATFORM_PARITY.md).

Each feature either uses a Windows equivalent with the same strict validation (Settings pages from a
fixed ms-settings: list, File Explorer's folder and selected names, a browser's page address) or steps
aside with no exception and no model call. Win32, UI Automation and the shell are doubled, so these run
on any OS; they do not prove behaviour on a real Windows desktop."""
import json
import re
import sys
import unittest
from unittest import mock

from companion import settings_panes
from companion.computer_task import ComputerTask, PANE_SYSTEM, PANE_SYSTEM_WINDOWS, structured_facts, screen_signature
from companion.desktop_context import DesktopContext
from companion.windows_desktop import WindowsDesktopBridge, page_address
from companion.windows_input import SendInput, ShellWindows, UIAutomation, is_file_path, MOUSEEVENTF_WHEEL, WHEEL_DELTA
from test_windows_input import windows_bridge, NOTEPAD

BOUNDS = {'x': 10, 'y': 20, 'width': 600, 'height': 400}
EXPLORER = (300, 'C:\\Windows\\explorer.exe', 'Documents', BOUNDS)
CHROME = (400, 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', 'Example - Google Chrome', BOUNDS)
SETTINGS_WINDOW = (500, 'C:\\Windows\\System32\\ApplicationFrameHost.exe', 'Settings', BOUNDS)


class on_windows:
    """sys.platform reads 'win32' and the pane catalog is rebuilt for it."""
    def __enter__(self):
        settings_panes.catalog.cache_clear()
        self.patch = mock.patch.object(sys, 'platform', 'win32')
        self.patch.start()

    def __exit__(self, *exc):
        self.patch.stop()
        settings_panes.catalog.cache_clear()


class WindowsSettingsCatalogTests(unittest.TestCase):
    def test_windows_catalog_is_the_fixed_list_with_exact_unique_ids(self):
        with on_windows():
            panes = settings_panes.catalog()
            self.assertEqual(settings_panes.settings_app(), ('uwp:settings', 'Settings'))
        self.assertEqual([(p['id'], p['name']) for p in panes], list(settings_panes.WINDOWS_PANES))
        self.assertEqual(len({p['id'] for p in panes}), len(panes))
        self.assertEqual(len({p['name'].casefold() for p in panes}), len(panes))
        for item in panes:
            self.assertRegex(item['id'], r'^[a-z][a-z-]*$')
        self.assertIn({'id': 'display', 'name': 'Display'}, panes)

    def test_other_platforms_have_no_catalog(self):
        settings_panes.catalog.cache_clear()
        with mock.patch.object(settings_panes.sys, 'platform', 'linux'):
            self.assertEqual(settings_panes.catalog(), [])
            self.assertEqual(settings_panes.settings_app(), (settings_panes.BUNDLE_ID, 'System Settings'))
        settings_panes.catalog.cache_clear()

    def test_uri_only_for_listed_pages(self):
        self.assertEqual(settings_panes.windows_uri('display'), 'ms-settings:display')
        for bad in ('Display', 'ms-settings:display', 'display?x=1', 'display/../privacy', '', 'windowsupdate-action',
                    'com.apple.SystemProfiler.AboutExtension'):
            with self.subTest(bad=bad):
                self.assertIsNone(settings_panes.windows_uri(bad))

    def test_settings_app_ids_never_overlap_between_platforms(self):
        self.assertTrue(settings_panes.is_settings_app('com.apple.systempreferences'))
        self.assertTrue(settings_panes.is_settings_app('uwp:settings'))
        for other in ('uwp:calculator', 'explorer.exe', 'com.apple.finder', None, ''):
            self.assertFalse(settings_panes.is_settings_app(other))


class WindowsSettingsActionTests(unittest.TestCase):
    def test_authorization_takes_only_listed_page_ids_and_names_windows_settings(self):
        desktop = DesktopContext(mock.Mock(), clock=lambda: 100)
        with on_windows():
            action = desktop.authorize_action({'type': 'open_settings_pane', 'pane': 'nightlight'})
            self.assertEqual(action, {'type': 'open_settings_pane', 'pane': 'nightlight', 'bundle_id': 'uwp:settings',
                                      'label': 'Open Night light in Settings'})
            for bad in ({'type': 'open_settings_pane', 'pane': 'Night light'},
                        {'type': 'open_settings_pane', 'pane': 'ms-settings:nightlight'},
                        {'type': 'open_settings_pane', 'pane': 'nightlight', 'uri': 'ms-settings:privacy'},
                        {'type': 'open_settings_pane', 'pane': 'com.apple.Keyboard-Settings.extension'}):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    desktop.authorize_action(bad)

    def test_execution_goes_through_the_windows_bridge_never_the_mac_open_command(self):
        bridge = mock.Mock()
        bridge.application_action.return_value = {'submitted': True, 'verification_required': True}
        desktop = DesktopContext(bridge, clock=lambda: 100)
        with on_windows(), mock.patch('companion.desktop_context.subprocess.run') as run, \
                mock.patch.object(desktop, 'collect_once'):
            result = desktop.application_action({'type': 'open_settings_pane', 'pane': 'display'})
        self.assertTrue(result['submitted'])
        run.assert_not_called()
        bridge.application_action.assert_called_once_with(
            {'type': 'open_settings_pane', 'pane': 'display', 'bundle_id': 'uwp:settings', 'label': 'Open Display in Settings'})

    def test_bridge_opens_the_ms_settings_uri_as_one_value(self):
        b = windows_bridge({1: NOTEPAD}, 1)
        opened = []

        def startfile(uri):
            opened.append(uri)
            b.win32.records[5] = SETTINGS_WINDOW  # Settings shows its window

        with mock.patch('companion.windows_desktop.os.startfile', side_effect=startfile, create=True):
            result = b.application_action({'type': 'open_settings_pane', 'pane': 'storagesense', 'bundle_id': 'uwp:settings'})
        self.assertEqual(opened, ['ms-settings:storagesense'])
        self.assertEqual(result, {'submitted': True, 'verification_required': True})
        self.assertTrue(b.app_state('uwp:settings')['running'])

    def test_bridge_refuses_anything_off_the_list_before_windows_sees_it(self):
        b = windows_bridge({1: NOTEPAD}, 1)
        with mock.patch('companion.windows_desktop.os.startfile', create=True) as startfile:
            for bad in ('Display', 'ms-settings:display', 'display&privacy', None, 7):
                with self.subTest(bad=bad), self.assertRaises(RuntimeError):
                    b.application_action({'type': 'open_settings_pane', 'pane': bad})
        startfile.assert_not_called()


WINDOWS_SETTINGS = {'app': {'name': 'Settings', 'bundle_id': 'uwp:settings'}, 'window': {'title': 'Settings'}}


class WindowsPaneStepTests(unittest.TestCase):
    def step(self, snapshot, reply='Night light'):
        task = ComputerTask('Turn on night light so the screen is warmer in the evening.', automatic=True, goal_mode='act')
        provider = mock.Mock(complete_structured=mock.Mock(return_value={'pane': reply}))
        desktop = mock.Mock()
        desktop.authorize_action.side_effect = lambda a: dict(a, bundle_id='uwp:settings', label='Open Night light in Settings')
        return task, provider, desktop, task.settings_pane_step(desktop, snapshot, provider, {})

    def test_windows_settings_in_front_offers_the_windows_pages_with_their_own_prompt(self):
        with on_windows():
            _, provider, desktop, step = self.step(WINDOWS_SETTINGS)
        self.assertEqual(step['action']['pane'], 'nightlight')
        call = provider.complete_structured.call_args
        self.assertEqual(call.args[0], PANE_SYSTEM_WINDOWS)
        self.assertEqual(call.kwargs['schema']['properties']['pane']['enum'],
                         ['none']+[name for _, name in settings_panes.WINDOWS_PANES])
        desktop.authorize_action.assert_called_once_with({'type': 'open_settings_pane', 'pane': 'nightlight'})

    def test_without_a_catalog_the_step_asks_no_model(self):
        settings_panes.catalog.cache_clear()
        with mock.patch.object(settings_panes.sys, 'platform', 'linux'):
            _, provider, desktop, step = self.step(WINDOWS_SETTINGS)
        settings_panes.catalog.cache_clear()
        self.assertIsNone(step)
        provider.complete_structured.assert_not_called()
        desktop.authorize_action.assert_not_called()

    def test_a_reply_off_the_enum_opens_nothing(self):
        with on_windows():
            _, _, desktop, step = self.step(WINDOWS_SETTINGS, reply='ms-settings:privacy')
        self.assertIsNone(step)
        desktop.authorize_action.assert_not_called()

    def test_other_windows_apps_never_reach_the_pane_step(self):
        with on_windows():
            _, provider, _, step = self.step({'app': {'bundle_id': 'uwp:calculator'}, 'window': {'title': 'Calculator'}})
        self.assertIsNone(step)
        provider.complete_structured.assert_not_called()

    def test_prompts_are_separate_and_stable(self):
        self.assertNotEqual(PANE_SYSTEM, PANE_SYSTEM_WINDOWS)
        self.assertIn('Mac', PANE_SYSTEM)
        self.assertNotIn('Mac', PANE_SYSTEM_WINDOWS)


class FakeShell:
    def __init__(self, facts=None, error=None):
        self.value, self.error, self.calls = facts or {}, error, []

    def facts(self, hwnd):
        self.calls.append(hwnd)
        if self.error:
            raise self.error
        return self.value


class ExplorerSelectionTests(unittest.TestCase):
    def bridge(self, shell, records=None, foreground=3):
        b = windows_bridge(records or {3: EXPLORER}, foreground)
        b._shell = shell
        return b

    def test_explorer_in_front_reports_folder_and_selected_names_only(self):
        shell = FakeShell({'target': 'C:\\Users\\ana\\Documents', 'selection': ['Report.pdf', 'Notes.txt']})
        sample = self.bridge(shell).sample_targets()
        self.assertEqual(sample['finder'], {'target': 'C:\\Users\\ana\\Documents', 'selection': ['Report.pdf', 'Notes.txt']})
        self.assertEqual(shell.calls, [3])

    def test_selection_is_bounded_and_bad_values_dropped(self):
        shell = FakeShell({'target': 'C:\\x', 'selection': ['a'*400, 7, '', 'b', 'c', 'd', 'e', 'f'], 'contents': 'secret'})
        facts = self.bridge(shell).sample_targets()['finder']
        self.assertEqual(set(facts), {'target', 'selection'})
        self.assertEqual(facts['selection'], ['a'*255, 'b', 'c', 'd', 'e'])

    def test_other_apps_unreadable_shells_and_missing_comtypes_step_aside(self):
        shell = FakeShell({'target': 'C:\\x', 'selection': ['a']})
        self.assertNotIn('finder', self.bridge(shell, {1: NOTEPAD}, 1).sample_targets())
        self.assertEqual(shell.calls, [])
        self.assertNotIn('finder', self.bridge(FakeShell(error=OSError('busy'))).sample_targets())
        self.assertNotIn('finder', self.bridge(False).sample_targets())
        self.assertNotIn('finder', self.bridge(shell).sample())  # plain samples stay as small as before

    def test_desktop_context_carries_explorer_facts_into_task_facts(self):
        b = self.bridge(FakeShell({'target': 'C:\\Users\\ana\\Documents', 'selection': ['Report.pdf']}))
        context = DesktopContext(b, clock=lambda: 5)
        context.enabled = True
        snapshot = context.collect_once(targets=True)
        self.assertEqual(snapshot['finder']['selection'], ['Report.pdf'])
        facts = structured_facts(snapshot)['finder']
        self.assertEqual((facts['folder_name'], facts['parent_folder_name']), ('Documents', 'ana'))
        # A selection is a screen change on Windows too.
        self.assertNotEqual(screen_signature([], snapshot), screen_signature([], dict(snapshot, finder={'target': 'C:\\x', 'selection': []})))

    def test_mac_finder_paths_keep_posix_names(self):
        facts = structured_facts({'finder': {'target': '/Users/ana/Documents/', 'selection': []}})['finder']
        self.assertEqual((facts['folder_name'], facts['parent_folder_name']), ('Documents', 'ana'))
        unc = structured_facts({'finder': {'target': '\\\\server\\share\\Team\\Plans'}})['finder']
        self.assertEqual((unc['folder_name'], unc['parent_folder_name']), ('Plans', 'Team'))


class Item:
    def __init__(self, path, name):
        self.Path, self.Name = path, name


class Items:
    def __init__(self, items):
        self.items, self.Count = items, len(items)

    def Item(self, index):
        return self.items[index]


class ShellAdapterTests(unittest.TestCase):
    def adapter(self, windows):
        shell = mock.Mock()
        shell.Windows.return_value = Items(windows)
        adapter = object.__new__(ShellWindows)
        adapter.local = mock.Mock(shell=shell)
        return adapter

    def window(self, hwnd, folder, selected):
        view = mock.Mock(Folder=mock.Mock(Self=mock.Mock(Path=folder)))
        view.SelectedItems.return_value = Items(selected)
        return mock.Mock(HWND=hwnd, Document=view)

    def test_names_come_from_file_paths_with_extensions_and_at_most_five(self):
        selected = [Item('C:\\Users\\ana\\Report.pdf', 'Report'), Item('::{645FF040}', 'Recycle Bin')] + \
                   [Item('C:\\x\\%d.txt' % n, str(n)) for n in range(6)]
        adapter = self.adapter([None, self.window(9, 'C:\\Other', []), self.window(3, 'C:\\Users\\ana', selected)])
        self.assertEqual(adapter.facts(3), {'target': 'C:\\Users\\ana', 'selection': ['Report.pdf', 'Recycle Bin', '0.txt', '1.txt', '2.txt']})

    def test_virtual_folders_have_no_target_and_unknown_windows_nothing(self):
        adapter = self.adapter([self.window(3, '::{20D04FE0-3AEA-1069-A2D8-08002B30309D}', [])])
        self.assertEqual(adapter.facts(3), {'selection': []})
        self.assertEqual(adapter.facts(4), {})

    def test_file_paths(self):
        for good in ('C:\\Users', 'd:/data', '\\\\server\\share'):
            self.assertTrue(is_file_path(good), good)
        for bad in ('::{GUID}', 'shell:Downloads', 'C:', '', None, 'é:\\x', 'C:\\'+'x'*1100):
            self.assertFalse(is_file_path(bad), bad)


class FakeAddressUIA:
    def __init__(self, url='', error=None):
        self.url, self.error, self.calls = url, error, []

    def focused(self):
        return None

    def controls(self, hwnd):
        return []

    def page_url(self, hwnd):
        self.calls.append(hwnd)
        if self.error:
            raise self.error
        return self.url


class BrowserAddressTests(unittest.TestCase):
    def sample(self, uia, records=None, foreground=4):
        b = windows_bridge(records or {4: CHROME}, foreground)
        b._uia = uia
        b._focus = mock.Mock(focused=mock.Mock(return_value=None))
        return b.sample_targets()

    def test_browser_in_front_reports_its_page_address(self):
        uia = FakeAddressUIA('https://support.example.com/en/help?q=1')
        sample = self.sample(uia)
        self.assertEqual(sample['window']['url'], 'https://support.example.com/en/help?q=1')
        self.assertEqual(uia.calls, [4])

    def test_only_public_http_addresses_are_kept(self):
        for bad in ('edge://settings', 'file:///C:/x.html', 'about:blank', 'https://user:pw@example.com/',
                    'https://exa mple.com/\n', 'https://'+'a'*1100+'.com', 'javascript:alert(1)', 'https:///nohost', ''):
            with self.subTest(bad=bad):
                self.assertNotIn('url', self.sample(FakeAddressUIA(bad))['window'])
                self.assertEqual(page_address(bad), '')

    def test_non_browsers_errors_and_missing_ui_automation_step_aside(self):
        uia = FakeAddressUIA('https://example.com/')
        self.assertNotIn('url', self.sample(uia, {1: NOTEPAD}, 1)['window'])
        self.assertEqual(uia.calls, [])
        self.assertNotIn('url', self.sample(FakeAddressUIA(error=OSError('timeout')))['window'])
        b = windows_bridge({4: CHROME}, 4)
        b._uia = False
        b._focus = mock.Mock(focused=mock.Mock(return_value=None))
        self.assertNotIn('url', b.sample_targets()['window'])

    def test_a_link_click_records_where_it_led_from_the_windows_address(self):
        task = ComputerTask('Click Pricing on this page', automatic=True, goal_mode='inspect')
        task.receipts = [dict(type='click_point', label='Pricing', status='submitted', from_url='https://example.com/')]
        snapshot = dict(self.sample(FakeAddressUIA('https://example.com/pricing')), app={'name': 'Google Chrome', 'bundle_id': 'chrome.exe'})
        navigation = task.completion_observation(snapshot, [])['facts']['browser_navigation']
        self.assertEqual((navigation['from_url'], navigation['url'], navigation['changed']),
                         ('https://example.com/', 'https://example.com/pricing', True))

    def test_uia_adapter_reads_the_document_value_never_an_edit_field(self):
        document = mock.Mock()
        document.GetCurrentPropertyValue.side_effect = lambda prop: {30045: '', 30093: 'https://example.com/a'}[prop]
        root = mock.Mock()
        root.FindFirst.return_value = document
        automation = mock.Mock()
        automation.ElementFromHandle.return_value = root
        adapter = object.__new__(UIAutomation)
        adapter.automation = lambda: automation
        self.assertEqual(adapter.page_url(4), 'https://example.com/a')
        automation.CreatePropertyCondition.assert_called_once_with(30003, 50030)  # control type: Document
        root.FindFirst.return_value = None
        self.assertEqual(adapter.page_url(4), '')


class WindowsScrollTests(unittest.TestCase):
    def test_windows_scroll_is_one_wheel_event_at_the_point_then_the_pointer_returns(self):
        sender = object.__new__(SendInput)
        sender.user32 = mock.Mock()
        sent, moves = [], []
        sender.send = lambda events: sent.extend(events)
        sender.cursor = lambda: (5, 6)
        sender.move = lambda x, y: moves.append((x, y))
        sender.scroll(620, 440, 'down')
        sender.scroll(620, 440, 'up')
        self.assertEqual(moves, [(620, 440), (620, 440)])
        self.assertEqual([(e.mi.dwFlags, e.mi.mouseData) for e in sent],
                         [(MOUSEEVENTF_WHEEL, (-3*WHEEL_DELTA) & 0xFFFFFFFF), (MOUSEEVENTF_WHEEL, 3*WHEEL_DELTA)])
        self.assertEqual(sender.user32.SetCursorPos.call_args_list, [mock.call(5, 6), mock.call(5, 6)])

    def test_windows_scroll_refuses_a_changed_window(self):
        b = windows_bridge({1: NOTEPAD, 2: NOTEPAD[:2]+('Other - Notepad',)+NOTEPAD[3:]}, 1,
                           focus={'role': 'control', 'bounds': None, 'pid': 100, 'key': (1,)})
        context = DesktopContext(b, clock=lambda: 5)
        context.enabled = True
        context.collect_once()
        action = context.authorize_action({'type': 'scroll', 'direction': 'down'})
        b.win32.foreground = 2
        with self.assertRaises(RuntimeError):
            b.application_action(action)
        self.assertEqual(b._sender.events, [])


class ParityDocTests(unittest.TestCase):
    def test_parity_table_is_linked_and_names_each_feature(self):
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        table = (root/'docs'/'ai'/'PLATFORM_PARITY.md').read_text(encoding='utf-8')
        for feature in ('System Settings panes', 'Finder selection', 'Browser page address', 'Scroll'):
            self.assertIn(feature, table)
        self.assertIn('PLATFORM_PARITY.md', (root/'docs'/'ai'/'README.md').read_text(encoding='utf-8'))
        self.assertTrue(re.search(r'^\|.*\|.*\|.*\|$', table, re.M))


if __name__ == '__main__':
    unittest.main()
