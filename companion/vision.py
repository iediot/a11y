"""Session consent, local-only request-time screen vision. Never records or saves frames."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from .timing import timing
from .ollama import OllamaProvider


class ScreenVision:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        from .paths import bundled_helper
        self.prebuilt = bundled_helper('screen-bridge')  # packaged app: compiled at build time
        self.binary = self.prebuilt or self.directory/'screen-bridge'
        self.source = Path(__file__).resolve().parent.parent/'native'/'ScreenBridge.swift'
        self.enabled = False
        self.cloud_approved = False  # set only after consent to send screen images to Claude
        self.generation = 0
        self.lock = threading.Lock()

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        self.generation += 1

    def prepare(self):
        if sys.platform != 'darwin':
            raise RuntimeError('Native screen vision currently requires macOS 14 or later.')
        if self.prebuilt:
            return
        with self.lock:
            if self.binary.exists() and self.binary.stat().st_mtime >= self.source.stat().st_mtime:
                return
            self.directory.mkdir(parents=True, exist_ok=True)
            try:
                result = subprocess.run(['xcrun', 'swiftc', '-parse-as-library', '-O', '-module-cache-path',
                    str(self.directory/'swift-cache'), str(self.source), '-o', str(self.binary)],
                    capture_output=True, timeout=60)
            except (OSError, subprocess.TimeoutExpired):
                raise RuntimeError('Screen vision needs Xcode Command Line Tools. Run xcode-select --install.') from None
            if result.returncode:
                raise RuntimeError('Could not build screen vision. Check Xcode Command Line Tools.')

    def call(self, *args):
        if sys.platform == 'win32':
            return self.windows_call(*args)
        self.prepare()
        try:
            result = subprocess.run([str(self.binary), '--exclude-pid', str(os.getpid()), *args],
                                    capture_output=True, timeout=15)
            if len(result.stdout) > 12_000_000:
                raise RuntimeError('Screen image exceeded the size limit.')
            data = json.loads(result.stdout)
            if data.get('error'):
                raise RuntimeError(str(data['error']))
            return data
        except (OSError, subprocess.TimeoutExpired, ValueError):
            raise RuntimeError('Screen vision could not capture a frame. Check Screen Recording permission.') from None

    def windows_call(self, *args):
        from .windows_screen import WindowsScreenBridge
        try:
            if not hasattr(self, 'windows_bridge'):
                self.windows_bridge = WindowsScreenBridge()
            data = self.windows_bridge.call(*args)
        except (OSError, AttributeError, ValueError):
            raise RuntimeError('Screen vision could not capture a frame on this version of Windows.') from None
        if data.get('error'):
            raise RuntimeError(str(data['error']))
        return data

    def request_access(self):
        return self.call('--request-access')

    def check_access(self):
        return self.call('--check-access')

    def capture(self, provider, *, x=0, y=0, desktop=None, fast_text=False):
        if not self.enabled:
            raise RuntimeError('Screen vision is paused. Enable it from the character’s right-click menu.')
        if getattr(provider, 'cloud', False):
            # Screen images leave this computer only with consent given for that cloud model.
            if not self.cloud_approved:
                raise RuntimeError('Screen images go to Claude only after you allow it. Turn screen vision off and on again to review.')
        elif not isinstance(provider, OllamaProvider):
            raise RuntimeError('Automatic screen vision is restricted to local Ollama and Claude. Use previewed capture for other providers.')
        else:
            model = provider.config.vision_model or provider.config.model
            if 'vision' not in provider.model_info(model).get('capabilities', []):
                raise RuntimeError('Choose an installed image-capable Ollama model to use screen vision.')
        if desktop and (desktop.get('status') == 'private_app' or desktop.get('focused_role') == 'secure_input'):
            raise RuntimeError('Screen vision paused for a protected app or password field.')
        generation = self.generation
        args = ('--x', str(x), '--y', str(y))
        data = self.call(*args, *(('--ocr',) if fast_text else ()))
        if not self.enabled or generation != self.generation:
            raise RuntimeError('Screen vision was paused before capture completed. The frame was discarded.')
        if not isinstance(data.get('image_data'), str) or not data['image_data'].startswith('data:image/png;base64,'):
            raise RuntimeError('No screen image was captured. I cannot infer what is visible.')
        data['captured_monotonic'] = time.monotonic()
        return data

    @staticmethod
    def model_image(image_data):
        """A copy of a frame sized for the model only.

        Ollama's Qwen-VL runner starts with --image-min-tokens 1024 and upscales any
        smaller image to about 1M pixels, so shrinking further saves no tokens and
        only blurs it (measured live: a 768-px copy still cost ~1,040 tokens).
        1280x800 is at that minimum. Click guards and OCR keep the full frame;
        coordinates are fractions, so they are unchanged. A11Y_MODEL_IMAGE_WIDTH
        (512-2048) tunes it for other runtimes."""
        import base64
        try:
            width = int(os.getenv('A11Y_MODEL_IMAGE_WIDTH', '1280'))
        except ValueError:
            width = 1280
        width = max(512, min(width, 2048))
        try:
            from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt
            from PyQt6.QtGui import QImage
        except ImportError:
            return image_data
        image = QImage.fromData(base64.b64decode(image_data.split(',', 1)[1]))
        if image.isNull() or image.width() <= width:
            return image_data
        scaled = image.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        scaled.save(buffer, 'PNG')
        return 'data:image/png;base64,'+base64.b64encode(bytes(data)).decode()

    @staticmethod
    def click_guard(image_data, visual, action):
        """Keep hashes/geometry only, not screenshot pixels, while awaiting approval."""
        import base64
        import hashlib
        from PyQt6.QtGui import QImage
        screen = visual.get('screen', {})
        if not isinstance(visual.get('display_id'), int) or visual['display_id'] <= 0:
            raise ValueError('A screen click needs a capture bound to a native display.')
        image = QImage.fromData(base64.b64decode(image_data.split(',', 1)[1]))
        if image.isNull():
            raise ValueError('The screen capture could not be checked for clicking.')
        px, py = int(action['x']*image.width()), int(action['y']*image.height())
        radius = max(6, round(image.width()*.009))
        colors = bytearray()
        for row in range(8):
            for column in range(8):
                x = max(0, min(image.width()-1, px-radius+round(column*2*radius/7)))
                y = max(0, min(image.height()-1, py-radius+round(row*2*radius/7)))
                color = image.pixelColor(x, y)
                colors.extend((color.red()//16, color.green()//16, color.blue()//16))
        desktop_x = screen['x']+action['x']*screen['width']
        desktop_y = screen['y']+action['y']*screen['height']
        windows = []
        for window in visual.get('windows', []):
            bounds = window.get('bounds', {})
            if all(key in bounds for key in ('x', 'y', 'width', 'height')) and bounds['x'] <= desktop_x < bounds['x']+bounds['width'] and bounds['y'] <= desktop_y < bounds['y']+bounds['height']:
                windows.append((window.get('window_id'), tuple(bounds[key] for key in ('x', 'y', 'width', 'height'))))
        return {'screen': dict(screen), 'display_id': visual['display_id'],
                'captured_monotonic': visual['captured_monotonic'],
                'region_hash': hashlib.sha256(colors).hexdigest(), 'windows': sorted(windows)}

    @staticmethod
    def same_target(desktop, proposal, screen):
        """Pixels around the point changed, but is the same named element still there? Animated content
        (Weather's sky) changed every frame and each click on "Conditions" was refused (live). Only a
        named element whose name matches counts; anything else stays refused."""
        label = ' '.join(str(proposal.get('label', '')).casefold().split())
        bridge = getattr(desktop, 'bridge', None)
        if len(label) < 2 or not hasattr(bridge, 'hit'):
            return False
        try:
            hit = bridge.hit(screen['x']+proposal['x']*screen['width'], screen['y']+proposal['y']*screen['height'])
        except (RuntimeError, ValueError, OSError, KeyError, TypeError):
            return False
        title = ' '.join(str((hit or {}).get('title', '')).casefold().split())
        return bool(title) and (title == label or len(title) >= 3 and (title in label or label in title))

    def execute_click(self, provider, desktop, proposal):
        guard = proposal.get('capture_guard')
        if not self.enabled or not guard or time.monotonic()-guard['captured_monotonic'] > 90:
            raise ValueError('That click observation expired or screen vision is off. Ask for a fresh target.')
        screen = guard['screen']
        generation = self.generation
        current = self.capture(provider, x=screen['x'], y=screen['y'], desktop=desktop.context())
        check = self.click_guard(current['image_data'], current, proposal)
        if any(check[key] != guard[key] for key in ('screen', 'display_id', 'windows')) or (
                check['region_hash'] != guard['region_hash'] and not self.same_target(desktop, proposal, screen)):
            raise ValueError('The target or its window changed. No click was sent; ask for a fresh target.')
        if not self.enabled or self.generation != generation:
            raise ValueError('Screen vision was paused. No click was sent.')
        if proposal.get('type') != 'drag' and hasattr(desktop.bridge,'hit'):
            hit = desktop.bridge.hit(screen['x']+proposal['x']*screen['width'], screen['y']+proposal['y']*screen['height'])
            from .eval_control import guard_native_test_call
            guard_native_test_call(((hit or {}).get('app', ''), (hit or {}).get('owner', '')))
            if self.named_hit_conflict(proposal,hit):
                if hit.get('role') == 'AXLink':
                    raise ValueError('A different named link is under that point. No click was sent; ask for a fresh target.')
                raise ValueError('A different file or menu command is under that point. No click was sent; ask for a fresh target.')
        result = desktop.bridge.drag(proposal, guard) if proposal.get('type') == 'drag' else desktop.bridge.click(proposal, guard)
        # How it clicked: "press" (accessibility, the user's pointer untouched), "focus", or "pointer" (borrowed, put back).
        timing('click.method', time.monotonic(), method=str(result.get('method', 'pointer'))[:10])
        if not result.get('submitted'):
            raise RuntimeError('The click result is uncertain. Check the app before trying again.')
        desktop.collect_once()
        return result

    @staticmethod
    def named_hit_conflict(proposal, hit):
        if not isinstance(hit,dict) or not (hit.get('finder_item') or hit.get('role') in ('AXMenuItem', 'AXLink')):
            return False
        import unicodedata
        actual=unicodedata.normalize('NFC',str(hit.get('title',''))).casefold().strip()
        wanted=unicodedata.normalize('NFC',str(proposal.get('label',''))).casefold().strip()
        return bool(actual and wanted and actual != wanted)
