"""One confirmed desktop action followed by observation; never an open-ended executor."""
from copy import deepcopy
import time
from .timing import timing


class ComputerUse:
    def __init__(self, desktop, *, vision=None, provider=None):
        self.desktop = desktop
        self.vision, self.provider = vision, provider

    def execute_confirmed(self, proposal):
        started = time.monotonic()
        try:
            return self._execute(proposal)
        finally:
            timing('action.executed', started, type=str(proposal.get('type', ''))[:20])

    def _execute(self, proposal):
        generation = self.desktop.generation
        before = self.desktop.context()
        kind = proposal.get('type')
        native_result = {}
        if kind == 'wait':
            import time
            milliseconds = proposal.get('milliseconds')
            if type(milliseconds) is not int or milliseconds not in (250, 500, 1000):
                raise ValueError('Unsupported wait duration.')
            time.sleep(milliseconds/1000)
            self.desktop.collect_once()
        elif kind == 'click_point':
            if self.vision is None:
                raise ValueError('Screen vision must be enabled for a confirmed screen click.')
            self.vision.execute_click(self.provider, self.desktop, proposal)
        elif kind == 'drag':
            if self.vision is None:
                raise ValueError('Screen vision must be enabled for a drag.')
            self.vision.execute_click(self.provider, self.desktop, proposal)
        elif kind == 'press_control':
            self.desktop.press(proposal)
        elif kind in ('launch_app', 'quit_app', 'new_window', 'close_window', 'close_tab', 'choose_menu', 'browse_url', 'play_media', 'type_text', 'press_key', 'scroll', 'open_settings_pane'):
            native_result = self.desktop.application_action(proposal) or {}
        elif kind == 'focus_app':
            self.desktop.activate(proposal['bundle_id'])
            self.desktop.collect_once()
        else:
            raise ValueError('That desktop action is not supported by the confirmed executor.')
        if (kind not in ('launch_app', 'browse_url', 'open_settings_pane', 'click_point', 'drag', 'play_media', 'close_tab') and not (kind == 'new_window' and proposal.get('bundle_id') == 'com.apple.Safari') and not self.desktop.enabled) or self.desktop.generation != generation:
            raise RuntimeError('The action may have been submitted, but app awareness was paused. Check the app before trying again.')
        after = self.desktop.context()
        return {'type': kind, 'native_result': native_result, 'remaining_request': proposal.get('remaining_request', ''), 'requested_url': proposal.get('url'),
                **({'query': proposal.get('query', ''), 'artist': proposal.get('artist', '')} if kind == 'play_media' else {}), 'bundle_id': proposal.get('bundle_id'), 'label': proposal.get('label', ''), 'status': 'submitted',
                'before': self.summary(before, proposal.get('bundle_id')), 'after': self.summary(after, proposal.get('bundle_id')),
                'verification': 'Observe the result; API acceptance does not prove task completion.',
                'application_state': self.desktop.app_state(proposal['bundle_id']) if proposal.get('bundle_id') else {},
                'task_completed': False}

    @staticmethod
    def summary(observation, bundle_id=None):
        # Full fresh controls are already in desktop_context; do not triple their
        # prompt footprint or overwhelm a local model with duplicate observations.
        windows = [item for item in observation.get('app_windows', []) if bundle_id is None or item['bundle_id'] == bundle_id]
        return {'window_count': len(windows), 'windows': [{key: deepcopy(item.get(key)) for key in ('window_id', 'title', 'bounds')} for item in windows[:8]],
                **{key: deepcopy(observation.get(key)) for key in ('app', 'window')},
                'control_labels': [item['label'] for item in observation.get('controls', [])[:15]]}
