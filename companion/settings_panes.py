"""Settings panes: macOS System Settings from the system's own extension bundles, Windows
Settings pages from a fixed list in code.

On macOS each pane is an ExtensionKit bundle with an exact identifier that
``x-apple.systempreferences:<id>`` opens. On Windows each page is an
``ms-settings:<id>`` URI from ``WINDOWS_PANES`` below; nothing else is ever opened.
Code opens the pane; the model only picks one of these names. "Go to About"
clicked a content row that cannot be told apart from a switch, so it waited for a
confirmation (live, harder suite).
"""
from functools import lru_cache
from pathlib import Path
import plistlib
import re
import sys

EXTENSIONS = Path('/System/Library/ExtensionKit/Extensions')
SETTINGS_POINT = 'com.apple.Settings.extension.ui'
BUNDLE_ID = 'com.apple.systempreferences'
# The Settings app as the Windows bridge reports it in front: a Store app hosted by
# ApplicationFrameHost is 'uwp:' plus its title (windows_desktop.WindowsDesktopBridge.windows).
WINDOWS_APP_ID = 'uwp:settings'
WINDOWS_APP_IDS = frozenset({WINDOWS_APP_ID, 'systemsettings.exe'})
# Windows Settings pages: (ms-settings: page, name as Settings shows it). A fixed list, validated
# in code; a model reply, a request or a page's text can never add a URI.
WINDOWS_PANES = (
    ('about', 'About'), ('activation', 'Activation'), ('appsfeatures', 'Installed apps'),
    ('backup', 'Windows Backup'), ('batterysaver', 'Battery saver'), ('bluetooth', 'Bluetooth & devices'),
    ('clipboard', 'Clipboard'), ('colors', 'Colors'), ('dateandtime', 'Date & time'),
    ('defaultapps', 'Default apps'), ('display', 'Display'), ('easeofaccess', 'Accessibility'),
    ('easeofaccess-closedcaptioning', 'Captions'), ('easeofaccess-colorfilter', 'Color filters'),
    ('easeofaccess-cursor', 'Text cursor'), ('easeofaccess-display', 'Text size'),
    ('easeofaccess-highcontrast', 'Contrast themes'), ('easeofaccess-keyboard', 'Accessibility keyboard'),
    ('easeofaccess-magnifier', 'Magnifier'), ('easeofaccess-mousepointer', 'Mouse pointer and touch'),
    ('easeofaccess-narrator', 'Narrator'), ('easeofaccess-speechrecognition', 'Speech recognition'),
    ('easeofaccess-visualeffects', 'Visual effects'), ('emailandaccounts', 'Email & accounts'),
    ('gaming-gamebar', 'Game Bar'), ('keyboard', 'Keyboard'), ('lockscreen', 'Lock screen'),
    ('mousetouchpad', 'Mouse'), ('devices-touchpad', 'Touchpad'), ('multitasking', 'Multitasking'),
    ('network-status', 'Network & internet'), ('network-wifi', 'Wi-Fi'), ('network-ethernet', 'Ethernet'),
    ('network-vpn', 'VPN'), ('network-airplanemode', 'Airplane mode'), ('network-proxy', 'Proxy'),
    ('nightlight', 'Night light'), ('notifications', 'Notifications'), ('personalization', 'Personalization'),
    ('personalization-background', 'Background'), ('personalization-start', 'Start'),
    ('powersleep', 'Power & battery'), ('printers', 'Printers & scanners'), ('privacy', 'Privacy & security'),
    ('privacy-location', 'Location'), ('privacy-microphone', 'Microphone'), ('privacy-webcam', 'Camera'),
    ('quiethours', 'Focus'), ('recovery', 'Recovery'), ('regionlanguage', 'Language & region'),
    ('signinoptions', 'Sign-in options'), ('sound', 'Sound'), ('speech', 'Speech'),
    ('startupapps', 'Startup apps'), ('storagesense', 'Storage'), ('taskbar', 'Taskbar'),
    ('themes', 'Themes'), ('troubleshoot', 'Troubleshoot'), ('typing', 'Typing'),
    ('windowsdefender', 'Windows Security'), ('windowsupdate', 'Windows Update'), ('yourinfo', 'Your info'),
)


def readable(bundle_id, display):
    """The pane's name as the sidebar says it. Display names are sometimes internal
    ("WiFiSettings", "AccessibilitySettingsExtension"); then the identifier reads better."""
    display = ' '.join((display or '').split())
    if display and not re.search(r'Extension|Settings|Pane', display) and (' ' in display or not re.search(r'[a-z][A-Z]', display)):
        return display
    stem = re.sub(r'^com\.apple\.(?:systempreferences\.|settings\.)?', '', bundle_id)
    stem = re.sub(r'(?:[-.]?Settings)?(?:[-.]extension)?$', '', stem, flags=re.I).split('.')[0]
    stem = re.sub(r'(?:Settings|Extension)$', '', stem)
    words = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', stem.replace('-', ' ')).split()
    return ' '.join(words)


@lru_cache(maxsize=1)
def catalog():
    """[{'id', 'name'}] for every Settings pane: installed ones on macOS, the fixed list on
    Windows, [] elsewhere or when unreadable."""
    if sys.platform == 'win32':
        return [{'id': identifier, 'name': name} for identifier, name in WINDOWS_PANES]
    if sys.platform != 'darwin' or not EXTENSIONS.is_dir():
        return []
    panes, seen = [], set()
    for bundle in sorted(EXTENSIONS.glob('*.appex')):
        try:
            with open(bundle/'Contents'/'Info.plist', 'rb') as handle:
                info = plistlib.load(handle)
        except Exception:  # noqa: BLE001 - one unreadable bundle never hides the rest
            continue
        point = (info.get('EXAppExtensionAttributes') or {}).get('EXExtensionPointIdentifier')
        identifier = info.get('CFBundleIdentifier')
        if point != SETTINGS_POINT or not isinstance(identifier, str) or not re.fullmatch(r'[\w.-]{3,120}', identifier):
            continue
        name = readable(identifier, info.get('CFBundleDisplayName') if isinstance(info.get('CFBundleDisplayName'), str) else '')
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            panes.append({'id': identifier, 'name': name[:60]})
    return panes


def pane(identifier):
    """The catalog entry with this exact identifier, or None."""
    return next((item for item in catalog() if item['id'] == identifier), None)


def settings_app():
    """(app id, name) of this platform's Settings app, as the desktop bridge reports it."""
    return (WINDOWS_APP_ID, 'Settings') if sys.platform == 'win32' else (BUNDLE_ID, 'System Settings')


def is_settings_app(bundle_id):
    """Whether this app id is System Settings (macOS) or Settings (Windows). The ids never overlap."""
    return bundle_id == BUNDLE_ID or bundle_id in WINDOWS_APP_IDS


def windows_uri(identifier):
    """The ms-settings: URI of a page in the fixed Windows list, or None for anything else."""
    return 'ms-settings:'+identifier if any(identifier == known for known, _ in WINDOWS_PANES) else None
