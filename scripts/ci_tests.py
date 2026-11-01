"""Run the test suite; on failure, print each failing test as a GitHub annotation (readable on the run
page without opening the log). The suite runs in a child process that names each test as it starts, so a
silent crash (Windows aborts the process when a QThread outlives its window) still names the test."""
import io
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def child():
    import faulthandler
    faulthandler.enable(all_threads=True)  # an abort still prints where every thread was
    sys.path.insert(0, str(ROOT))  # as "python -m unittest" from the project root
    slot_errors = []

    def hook(kind, error, trace):
        # An exception in a Qt slot aborts the process under the default hook (0xC0000409 on Windows,
        # with only the last line printed). With a hook, PyQt calls it and carries on: print it whole.
        import traceback
        text = ''.join(traceback.format_exception(kind, error, trace))
        slot_errors.append(text)
        sys.stderr.write('Exception in a Qt slot:\n'+text)
        print('::error title=Exception in a Qt slot::%s' % ' | '.join(text.strip().splitlines()[-6:]).replace('%', '%25')[:900], flush=True)
    sys.excepthook = hook

    class Named(unittest.TextTestResult):
        def startTest(self, test):
            print('START', test, flush=True)
            super().startTest(test)

    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=1, resultclass=Named).run(unittest.defaultTestLoader.discover(str(ROOT/'tests')))
    sys.stderr.write(stream.getvalue())
    for test, trace in result.failures+result.errors:
        last = [line for line in trace.strip().splitlines() if line.strip()][-6:]
        message = ' | '.join(last).replace('%', '%25').replace('\r', '').replace('\n', ' ')[:900]
        print('::error title=%s::%s' % (str(test).replace(':', ' ').replace(',', ' ')[:200], message), flush=True)
    sys.exit(0 if result.wasSuccessful() and not slot_errors else 1)


def main():
    # The child writes UTF-8 and this process prints it as UTF-8: test names and failures can hold
    # characters (›, …) that Windows' cp1252 console encoding cannot, which used to crash this runner.
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    process = subprocess.Popen([sys.executable, __file__, '--child'], stdout=subprocess.PIPE, text=True, encoding='utf-8', errors='replace', env=env)
    last = ''
    for line in process.stdout:
        if line.startswith('START '):
            last = line[6:].strip()
        else:
            sys.stdout.write(line)
            sys.stdout.flush()
    code = process.wait()
    if code not in (0, 1):
        print('::error title=Test process crashed (exit %s)::The last test started was %s' % (code, last.replace(':', ' ')))
    sys.exit(0 if code == 0 else 1)


if __name__ == '__main__':
    child() if '--child' in sys.argv else main()
