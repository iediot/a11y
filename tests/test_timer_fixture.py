import sys
from pathlib import Path
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import live_broad


class TimerFixtureTests(unittest.TestCase):
    def test_preflight_requires_positive_idle_evidence(self):
        for values in ('', 'Clock Timers', 'missing value', 'Add Cancel', 'Add Pause', 'Add Resume'):
            self.assertFalse(live_broad.timer_fixture_is_idle(values))
        self.assertTrue(live_broad.timer_fixture_is_idle('Clock Timers Add Start'))

    def test_ax_read_includes_accessibility_descriptions(self):
        with patch.object(live_broad,'osa',return_value='Resume') as native:
            self.assertEqual(live_broad.ax_values('Clock'),'Resume')
        self.assertIn('description of element',native.call_args.args[0])
