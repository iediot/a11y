"""Offline diagnostic scoring must not reward blind clicks or repeated failed scrolls."""
import unittest

from scripts.policy_choice_eval import score


def proposal(kind, **fields):
    return {'status': 'action', 'risk': 'navigation', 'action': dict(type=kind, **fields)}


class PolicyChoiceScoringTests(unittest.TestCase):
    def test_visible_target_requires_actual_point_and_single_left_click(self):
        row = {'expected': 'visible', 'box': [.1, .2, .3, .4]}
        self.assertTrue(score(proposal('click_point', x=.2, y=.3), row))
        for fields in ({'x': .5, 'y': .3}, {'x': .2, 'y': .3, 'click_count': 2},
                       {'x': .2, 'y': .3, 'button': 'right'}, {'x': float('nan'), 'y': .3},
                       {'x': True, 'y': .3}, {'x': .3, 'y': .3}):
            with self.subTest(fields=fields):
                self.assertFalse(score(proposal('click_point', **fields), row))

    def test_missing_target_does_not_reward_guessed_click_or_giving_up(self):
        row = {'expected': 'reveal', 'target': 'item089.txt'}
        self.assertFalse(score(proposal('click_point', x=.2, y=.4, label='item089.txt'), row))
        self.assertFalse(score({'status': 'needs_user', 'action': None}, row))
        self.assertTrue(score(proposal('scroll', direction='down'), row))
        self.assertTrue(score(proposal('scroll_to', text='item089.txt'), row))
        self.assertFalse(score(proposal('scroll_to', text='item008.txt'), row))

    def test_recovery_requires_changed_method_and_relevant_query(self):
        row = {'expected': 'recover', 'target': 'item089.txt'}
        self.assertFalse(score(proposal('scroll', direction='down'), row))
        self.assertTrue(score(proposal('press_key', key='cmd_f'), row))
        self.assertTrue(score(proposal('search_in_app', query='item089.txt'), row))
        self.assertFalse(score(proposal('search_in_app', query='anything'), row))

    def test_menu_return_accepts_back_chevron_or_escape(self):
        row = {'expected': 'back'}
        self.assertTrue(score(proposal('press_key', key='escape'), row))
        self.assertTrue(score(proposal('click_point', x=.03, y=.26), row))
        self.assertFalse(score(proposal('click_point', x=.97, y=.26), row))

    def test_support_url_requires_expected_host_and_path(self):
        row = {'expected': 'reveal', 'support_urls': True, 'target': 'Support'}
        for url in ('https://support.apple.com', 'https://www.apple.com/support/'):
            self.assertTrue(score(proposal('browse_url', url=url), row))
        for url in ('https://support.apple.com.evil.test', 'https://www.apple.com/store',
                    'https://apple.com@evil.test/support', 'file:///support', None, 'http://['):
            with self.subTest(url=url):
                self.assertFalse(score(proposal('browse_url', url=url), row))

    def test_malformed_or_confirmation_requiring_proposals_fail(self):
        row = {'expected': 'visible', 'box': [0, 0, 1, 1]}
        for value in (None, [], {}, {'status': 'action', 'action': 'click'},
                      {'status': 'action', 'risk': 'change', 'action': {'type': 'click_point', 'x': .2, 'y': .2}}):
            with self.subTest(value=value):
                self.assertFalse(score(value, row))


if __name__ == '__main__':
    unittest.main()
