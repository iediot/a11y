"""Where A11y finds bundled resources and keeps its data, from source or as a packaged app."""
import json
import os
from pathlib import Path
import sys


def frozen():
    """True inside a packaged app (PyInstaller)."""
    return bool(getattr(sys, 'frozen', False))


def resource_dir():
    """Read-only files shipped with the app: native helpers, icons."""
    if frozen() and hasattr(sys, '_MEIPASS'):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def data_dir():
    """A11Y_DATA_DIR, else the platform's per-user app data folder for the packaged app.

    Source checkouts keep the historical .data folder so developer setups don't move."""
    configured = os.getenv('A11Y_DATA_DIR')
    if configured:
        return Path(configured)
    if not frozen():
        return Path('.data')
    if sys.platform == 'darwin':
        return Path.home()/'Library'/'Application Support'/'A11y'
    if sys.platform == 'win32':
        return Path(os.getenv('LOCALAPPDATA', str(Path.home()/'AppData'/'Local')))/'A11y'
    return Path(os.getenv('XDG_DATA_HOME', str(Path.home()/'.local'/'share')))/'a11y'


def bundled_helper(name):
    """A prebuilt native helper shipped with the app (no Xcode tools needed), or None."""
    path = resource_dir()/'native'/'bin'/name
    return path if path.is_file() and os.access(path, os.X_OK) else None


SETTINGS = ('model', 'vision_model', 'gui_profile', 'gui_model', 'provider',  # provider: ollama (default) or claude
            'web_lookups',  # 'on' (default) or 'off': weather and facts looked up in the background
            'home_place')   # where "the weather" means when no place is named


def saved_ai_settings():
    """Model choices saved by the setup window; environment variables still take precedence."""
    try:
        value = json.loads((data_dir()/'ai.json').read_text())
    except (OSError, ValueError):
        return {}
    return {key: value[key] for key in SETTINGS if isinstance(value, dict) and isinstance(value.get(key), str) and len(value[key]) <= 200}


def save_ai_settings(**values):
    current = saved_ai_settings()
    current.update({key: value for key, value in values.items() if key in SETTINGS and isinstance(value, str)})
    directory = data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory/'ai.json').write_text(json.dumps(current, indent=2))
    return current


def use_bundled_certificates():
    """Point HTTPS checks at certifi's CA list inside a packaged app.

    PyInstaller's Python does not read the macOS keychain or Windows certificate store, so
    every urllib download (Ollama, the natural voice) failed with CERTIFICATE_VERIFY_FAILED.
    Verification stays on; an SSL_CERT_FILE the user set (e.g. a company proxy CA) wins."""
    if not frozen() or os.getenv('SSL_CERT_FILE'):
        return None
    try:
        import certifi
    except ImportError:
        return None
    os.environ['SSL_CERT_FILE'] = certifi.where()
    return os.environ['SSL_CERT_FILE']
