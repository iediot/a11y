"""Fallback steps chosen from the system's own lists: a menu command from the app's menus,
a System Settings pane from the installed pane catalog. The model picks from an enum; code acts."""
import json
import re
from .models import ValidationError


PANE_SYSTEM = '''The user's goal is about System Settings on their Mac. Below are its panes. Pick the one pane
the goal needs to go to first ("turn on Night Shift" is Displays, "battery health" is Battery).
Pick "none" when no pane fits or current_pane already is the one.'''
MENU_SYSTEM = '''The user wants something done in the app in front. Below are that app's menu commands as
"Menu > Item". Pick the one command that does what the goal asks or opens the place it asks for
("the sites I visited" is History > Show All History). Pick "none" when no command does it, or when
the goal is about something inside the window (a link, a file, a message, a page's content).'''
# Menu commands never offered to the menu pick: they end, remove, send or reset something.
MENU_UNSAFE = re.compile(r'\b(?:quit|close|delete|remove|erase|clear|empty|trash|log\s*out|sign\s*out|send|print|restart|shut\s*down|recent|'
                         r'uninstall|reset|revert|discard|eject|lock|sleep|force|archive|block|report|move to|save|export|duplicate|'
                         r'rename|share|publish|upload|import|install|update|purchase|buy|subscribe)\b', re.I)
# Menus that list content, not commands: pages, windows, bookmarks, recent files.
MENU_CONTENT = re.compile(r'^(?:bookmarks|window|help)\s*>|>\s*(?:services|recently closed|recently visited|open recent|recent items|'
                          r'favou?rites|history)\s*>', re.I)


class Fallbacks:
    """ComputerTask methods; see computer_task.ComputerTask for the state they use."""

    def menu_pick_step(self, desktop, snapshot, provider, cancel_kwargs, regions=()):
        """Once per task: the menu command the goal needs, chosen by the text model from the app's own
        menu items (an enum, never free text). The model only saw menu names, so "where can I see the
        sites I visited? open it" clicked around Safari for 22 steps (live, v2)."""
        from .computer_task import FULL_SCREEN_MENU, validate_step  # at call time: computer_task imports this module
        if getattr(self, 'menu_picked', False) or self.queue or self.goal_mode == 'draft' \
                or any(r.get('type') in ('new_window', 'type_text', 'choose_menu') for r in self.receipts):
            return None  # writing has its own steps: it chose File > New after the new document opened (live)
        if not any(r.get('effect') == 'nothing changed on screen' or r.get('type') == 'wait' and 'skipped' in str(r.get('label', ''))
                   for r in self.receipts):  # a click on the app's own name, skipped, is being stuck too (live, held-out)
            return None  # a fallback once a click did nothing: as a first move it chose Chrome's History for a Commons goal (live)
        if self.goal_mode == 'inspect' and len(regions) >= 2 and self.worked() and not getattr(self, 'met_before_menu', False):
            self.met_before_menu = True
            # A click that did nothing may have been on the answer itself: the guide showed "Collection
            # point: Ridge 69000", the model clicked that line, and the menu fallback chose History,
            # leaving the site (live, three times). Finish if the goal is already met.
            if not self.still_missing(provider, snapshot, regions, cancel_kwargs):
                answer = self.screen_answer(provider, snapshot, regions, '', cancel_kwargs)
                return {'status': 'done', 'action': None, 'message': answer or 'It is on the screen.'}
        from .web_apps import named_addresses
        app = snapshot.get('app') or {}
        bridge = getattr(desktop, 'bridge', None)
        if not app.get('bundle_id') or not hasattr(bridge, 'menu_items') or named_addresses(self.goal):
            return None  # a named website's content is in the page, not the menus
        named = self.named_app(snapshot)
        if named and app['bundle_id'] not in getattr(self, 'brought', set()):
            return None  # the goal names another app: its menus, not these (Finder Services for a Safari goal, live)
        self.menu_picked = True
        try:
            items = bridge.menu_items(app['bundle_id'])
        except (RuntimeError, ValueError, OSError):
            return None
        commands = []
        for path in items if isinstance(items, list) else []:
            leaf = str(path).rsplit('>', 1)[-1].strip()
            if (isinstance(path, str) and len(path) <= 90 and not MENU_UNSAFE.search(path) and not MENU_CONTENT.search(path)
                    and not re.search(r'\b(?:find|search)\b', leaf, re.I)  # searching is search_in_app's (Edit > Search in Maps left typing nowhere, live)
                    and not FULL_SCREEN_MENU.search(path) and not re.search(r' [-–—|] |https?:|\.\w{2,4}\b|…\w', leaf)
                    and path not in commands):
                commands.append(path)
        if not commands:
            return None
        commands = commands[:250]
        observation = {'goal': self.goal, 'app': app.get('name', ''), 'commands': commands}
        schema = {'type': 'object', 'additionalProperties': False, 'required': ['command'],
                  'properties': {'command': {'enum': ['none']+commands}}}
        try:
            reply = provider.complete_structured(MENU_SYSTEM, [{'role': 'user', 'content': json.dumps(observation, ensure_ascii=False)}],
                                                 schema=schema, max_tokens=60, thinking=False, **cancel_kwargs)
        except (RuntimeError, ValueError, OSError, TypeError):
            return None
        command = reply.get('command') if isinstance(reply, dict) else None
        if command not in commands:
            return None
        try:
            action = validate_step({'status': 'action', 'message': 'Using the menu.', 'risk': 'navigation',
                                    'action': {'type': 'choose_menu', 'path': command}}, desktop, snapshot,
                                   goal_mode=self.goal_mode, goal=self.goal)['action']
        except (ValueError, ValidationError):
            return None
        self.pending = action
        return {'status': 'action', 'message': action.get('label', 'Using the menu')+'…', 'action': action, 'review': not self.automatic, 'visual_capture': None}

    def settings_pane_step(self, desktop, snapshot, provider, cancel_kwargs):
        """Once per task, with System Settings in front: open the pane the goal needs by its exact
        identifier (the text model picks a name from the system's own pane catalog, an enum). A pixel
        click on "About" inside General could not be told from a switch and waited for a confirmation
        (live, harder suite)."""
        from .settings_panes import catalog, BUNDLE_ID
        if getattr(self, 'pane_picked', False) or self.queue or (snapshot.get('app') or {}).get('bundle_id') != BUNDLE_ID:
            return None
        self.pane_picked = True
        panes = catalog()
        if not panes or not hasattr(desktop, 'authorize_action'):
            return None
        names = [item['name'] for item in panes]
        current = str((snapshot.get('window') or {}).get('title', ''))[:80]
        schema = {'type': 'object', 'additionalProperties': False, 'required': ['pane'],
                  'properties': {'pane': {'enum': ['none']+names}}}
        try:
            reply = provider.complete_structured(PANE_SYSTEM, [{'role': 'user', 'content': json.dumps(
                {'goal': self.goal, 'current_pane': current, 'panes': names}, ensure_ascii=False)}],
                schema=schema, max_tokens=40, thinking=False, **cancel_kwargs)
        except (RuntimeError, ValueError, OSError, TypeError):
            return None
        name = reply.get('pane') if isinstance(reply, dict) else None
        entry = next((item for item in panes if item['name'] == name), None)
        if entry is None or name.casefold() == current.casefold():
            return None
        try:
            action = desktop.authorize_action({'type': 'open_settings_pane', 'pane': entry['id']})
        except (ValueError, RuntimeError):
            return None
        self.pending = action
        return {'status': 'action', 'message': action['label']+'.', 'action': action, 'review': not self.automatic, 'visual_capture': None}
