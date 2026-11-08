# PyInstaller spec for the A11y desktop companion (macOS .app, Windows folder build).
# Build with packaging/build_macos.sh or packaging/build_windows.ps1.
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, copy_metadata

ROOT = Path(SPECPATH).parent
ICONS = ROOT/'packaging'/'icons'
datas = [(str(ROOT/'native'/'DesktopBridge.swift'), 'native'), (str(ROOT/'native'/'ScreenBridge.swift'), 'native')]
datas.append((str(ICONS/'A11y.png'), 'packaging/icons'))
binaries = []
# Stamp the commit so a running packaged app reports which version it is (see instance.build_id).
import subprocess
try:
    commit = subprocess.run(['git', 'rev-parse', '--short=10', 'HEAD'], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
except OSError:
    commit = ''
(ROOT/'build').mkdir(exist_ok=True)
(ROOT/'build'/'build-id.txt').write_text(commit or 'unknown')
datas.append((str(ROOT/'build'/'build-id.txt'), '.'))
# Swift helpers compiled at build time (macOS), so users need no Xcode tools.
helpers = ROOT/'build'/'native-bin'
for name in ('desktop-bridge', 'screen-bridge'):
    if (helpers/name).exists():
        binaries.append((str(helpers/name), 'native/bin'))
hiddenimports = []
# Of onnxruntime, A11y runs only the inference core. Its other subpackages (datasets,
# quantization, transformers, tools...) crash PyInstaller's import check on Windows,
# so only the runtime parts are bundled. Test packages are skipped everywhere.
import re
RUNTIME_ONNX = re.compile(r'^onnxruntime(\.(capi|backend|experimental)(\.|$)|$)')
TESTS = re.compile(r'(^|\.)tests?(\.|$)')
UNUSED_MODULES = ['onnxruntime.datasets', 'onnxruntime.quantization', 'onnxruntime.transformers', 'onnxruntime.tools']

def wanted(name):
    if name.startswith('onnxruntime') and not RUNTIME_ONNX.match(name):
        return False
    return not TESTS.search(name)
# onnxruntime crashes on import on Windows once Qt's older MSVC runtime is loaded, so the
# Windows app leaves it (and the Kokoro voice built on it) out: speech output uses the
# system voice and transcription runs without the optional speech filter.
ONNX = ['kokoro_onnx', 'onnxruntime']
if sys.platform == 'win32':
    UNUSED_MODULES += ONNX
# Optional voice stack: bundled when installed in the build environment.
# language_tags (phonemizer -> csvw, under kokoro_onnx) reads JSON data files at import.
for package in ['faster_whisper', 'ctranslate2', 'espeakng_loader', 'sounddevice', 'tokenizers']+([] if sys.platform == 'win32' else ONNX+['language_tags']):
    try:
        package_datas, package_binaries, package_imports = collect_all(package, filter_submodules=wanted)
    except Exception:
        continue
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_imports
# CA certificates for HTTPS downloads (see paths.use_bundled_certificates).
cert_datas, cert_binaries, cert_imports = collect_all('certifi')
datas += cert_datas
hiddenimports += cert_imports+['certifi']
# Claude provider: the SDK imports opentelemetry, which finds its runtime context through
# package entry points, so their metadata must ship too (the self-test sends a request).
for package in ('anthropic', 'opentelemetry-api'):
    try:
        datas += copy_metadata(package)
    except Exception:
        pass
hiddenimports += ['anthropic', 'companion.claude']
if sys.platform == 'win32':
    hiddenimports += ['comtypes', 'comtypes.client', 'winrt.windows.media.ocr', 'winrt.windows.graphics.imaging',
                      'winrt.windows.storage.streams', 'winrt.windows.foundation']

analysis = Analysis([str(ROOT/'a11y_desktop.py')], pathex=[str(ROOT)], binaries=binaries, datas=datas,
                    hiddenimports=hiddenimports, excludes=['tkinter']+UNUSED_MODULES, noarchive=False)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name='A11y', console=False,
          icon=str(ICONS/('A11y.ico' if sys.platform == 'win32' else 'A11y.icns')),
          codesign_identity=None, entitlements_file=None)
collected = COLLECT(exe, analysis.binaries, analysis.datas, name='A11y')

if sys.platform == 'darwin':
    app = BUNDLE(collected, name='A11y.app', icon=str(ICONS/'A11y.icns'), bundle_identifier='com.a11y.companion',
                 info_plist={
                     'CFBundleName': 'A11y', 'CFBundleDisplayName': 'A11y',
                     'CFBundleShortVersionString': '0.11.1', 'CFBundleVersion': '0.11.1',
                     'LSMinimumSystemVersion': '14.0', 'NSHighResolutionCapable': True,
                     'NSMicrophoneUsageDescription': 'A11y listens for “Hey Ally” and your spoken requests when you turn voice on. Audio is processed on this Mac and never saved.',
                     'NSAppleEventsUsageDescription': 'A11y reads or changes Mail, Notes, Contacts, Music and your browser only when you ask it to, for example to read your last email or open a page.',
                     'NSCalendarsFullAccessUsageDescription': 'A11y reads your events when you ask what is on your calendar, and adds an event only after you confirm it.',
                     'NSCalendarsUsageDescription': 'A11y reads your events when you ask what is on your calendar, and adds an event only after you confirm it.',
                     'NSRemindersFullAccessUsageDescription': 'A11y reads your reminders when you ask, and adds or completes one only after you confirm it.',
                     'NSRemindersUsageDescription': 'A11y reads your reminders when you ask, and adds or completes one only after you confirm it.',
                     'NSDownloadsFolderUsageDescription': 'A11y lists your newest downloads when you ask for them.',
                     'NSDesktopFolderUsageDescription': 'A11y lists recent files on your Desktop when you ask for them.',
                     'NSDocumentsFolderUsageDescription': 'A11y lists recent documents when you ask for them.',
                     'NSScreenCaptureUsageDescription': 'A11y looks at your screen only when you ask for help with what is on it. Images go only to the AI you chose: the local model, or Claude if you allowed screen vision for it.',
                 })
