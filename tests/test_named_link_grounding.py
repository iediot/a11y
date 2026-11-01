"""A model-supplied link label cannot rename a different observed link."""
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from companion.computer_task import ComputerTask
from companion.models import ValidationError
from companion.vision import ScreenVision


class NamedLinkGroundingTests(unittest.TestCase):
    def test_support_label_cannot_click_store(self):
        self.assertTrue(ScreenVision.named_hit_conflict({'label': 'Support'}, {'role': 'AXLink', 'title': 'Store'}))

    def test_exact_link_names_allow_unicode_and_case_normalization(self):
        for label, title in (('Support', 'Support'), ('SUPPORT', 'Support'), ('Café', 'Cafe\u0301')):
            with self.subTest(label=label):
                self.assertFalse(ScreenVision.named_hit_conflict({'label': label}, {'role': 'AXLink', 'title': title}))
        self.assertTrue(ScreenVision.named_hit_conflict({'label': 'Support'}, {'role': 'AXLink', 'title': 'Support plans'}))

    def test_unnamed_targets_and_container_titles_are_not_assumed_to_be_link_names(self):
        for hit in (None, {'role': 'AXLink', 'title': ''}, {'role': 'AXWebArea', 'title': 'Fixture page'},
                    {'role': 'AXGroup', 'title': 'Navigation'}, {'role': 'AXImage', 'title': 'Site logo'}):
            with self.subTest(hit=hit):
                self.assertFalse(ScreenVision.named_hit_conflict({'label': 'Support'}, hit))

    def test_planning_rejection_returns_observed_target_for_another_choice(self):
        task = ComputerTask('Reach the Support page', automatic=True)
        desktop = SimpleNamespace(bridge=SimpleNamespace(hit=Mock(return_value={'role': 'AXLink', 'title': 'Store'})))
        step = {'status': 'action', 'risk': 'navigation', 'message': 'Open Support',
                'action': {'type': 'click_point', 'label': 'Support', 'x': .2, 'y': .3}}
        with self.assertRaisesRegex(ValidationError, 'observed link "Store"'):
            task.app_click(step, desktop, {}, {'screen': {'x': 0, 'y': 0, 'width': 1000, 'height': 800}})
        self.assertIsNone(task.pending)
        self.assertEqual(task.receipts, [])

    def test_execution_revalidates_a_link_that_changed_after_planning(self):
        vision = ScreenVision('/tmp/a11y-named-link-unit')
        vision.set_enabled(True)
        guard = {'captured_monotonic': time.monotonic(), 'screen': {'x': 0, 'y': 0, 'width': 1000, 'height': 800},
                 'display_id': 1, 'windows': [], 'region_hash': 'same'}
        proposal = {'type': 'click_point', 'label': 'Support', 'x': .2, 'y': .3, 'capture_guard': guard}
        vision.capture = Mock(return_value={'image_data': 'pixels'})
        vision.click_guard = Mock(return_value=dict(guard))
        desktop = SimpleNamespace(context=Mock(return_value={}), collect_once=Mock(),
                                  bridge=SimpleNamespace(hit=Mock(return_value={'role': 'AXLink', 'title': 'Store'}), click=Mock()))
        with self.assertRaisesRegex(ValueError, 'different named link'):
            vision.execute_click(object(), desktop, proposal)
        desktop.bridge.click.assert_not_called()
        desktop.collect_once.assert_not_called()

    def test_matching_link_still_executes_with_normal_capture_guards(self):
        vision = ScreenVision('/tmp/a11y-named-link-unit')
        vision.set_enabled(True)
        guard = {'captured_monotonic': time.monotonic(), 'screen': {'x': 0, 'y': 0, 'width': 1000, 'height': 800},
                 'display_id': 1, 'windows': [], 'region_hash': 'same'}
        proposal = {'type': 'click_point', 'label': 'Support', 'x': .2, 'y': .3, 'capture_guard': guard}
        vision.capture = Mock(return_value={'image_data': 'pixels'})
        vision.click_guard = Mock(return_value=dict(guard))
        desktop = SimpleNamespace(context=Mock(return_value={}), collect_once=Mock(),
                                  bridge=SimpleNamespace(hit=Mock(return_value={'role': 'AXLink', 'title': 'Support'}),
                                                         click=Mock(return_value={'submitted': True})))
        self.assertTrue(vision.execute_click(object(), desktop, proposal)['submitted'])
        desktop.bridge.click.assert_called_once_with(proposal, guard)
        desktop.collect_once.assert_called_once()


if __name__ == '__main__':
    unittest.main()
