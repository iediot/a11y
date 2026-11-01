"""System Settings panes on this Mac, read from the system's own extension bundles.

Each pane is an ExtensionKit bundle with an exact identifier that
``x-apple.systempreferences:<id>`` opens. Code opens the pane; the model only
picks one of these names. "Go to About" clicked a content row that cannot be
told apart from a switch, so it waited for a confirmation (live, harder suite).
"""
from functools import lru_cache
from pathlib import Path
import plistlib
import re
import sys

EXTENSIONS = Path('/System/Library/ExtensionKit/Extensions')
SETTINGS_POINT = 'com.apple.Settings.extension.ui'
BUNDLE_ID = 'com.apple.systempreferences'


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
    """[{'id', 'name'}] for every Settings pane installed; [] off macOS or when unreadable."""
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
