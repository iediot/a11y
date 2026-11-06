"""'Open X' without a model: installed app first, then the web.

Resolution order for X:
1. a browser is named ("in Safari", "in a new Chrome tab"): the web, below;
2. exactly one installed app matches X (same rules as launch requests);
3. a known web app (web_apps.WEB_APPS) or a typed address: open it;
4. X is a near miss of exactly one installed app's name (app_names.closest_app): that app;
5. otherwise: a web search for X.
Anything after "and"/"then" stays for the computer task. Requests this parser
does not clearly understand return None and go to the model as before."""
import re
from .app_names import ALIASES, closest_app, normalized, resolve_app
from .web_apps import BROWSER_WORDS, WEB_APPS, site_search

VERB = r'(?:please\s+)?(?:can you\s+|could you\s+)?(?:open(?:\s+up)?|launch|start|go to|bring up)'
PLACEMENT = re.compile(r'\s+(?:in|on|using|with)\s+(?:a\s+|the\s+)?(new\s+)?(?:(safari|chrome|edge|firefox|browser)\s*)?(window|tab)?\s*$', re.I)
FOLLOW_UP = re.compile(r'\s*(?:,\s*)?\b(?:and(?:\s+then)?|then)\b\s+', re.I)
DOMAIN = re.compile(r'(?:https?://)?(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}(?::\d+)?(?:[/?#]\S*)?', re.I)
LOCAL_THINGS = {'folder', 'folders', 'file', 'files', 'document', 'documents', 'email', 'emails', 'message',
                'messages', 'chat', 'tab', 'tabs', 'window', 'windows', 'page', 'link', 'button', 'menu', 'photo', 'photos'}
FILLER = re.compile(r'(?:\s+(?:yourself|please|for me|now|right now|quickly|real quick|instead|then))+\s*$', re.I)
# Leading words that make X a description rather than a name ("open the second email",
# "open a new window"); those go to the model.
NOT_A_NAME = {'the', 'a', 'an', 'my', 'this', 'that', 'it', 'new', 'another', 'some', 'your', 'our', 'up'}


def parse(request):
    """{'name', 'browser', 'mode', 'rest'} for 'open X [in <browser> window/tab] [and ...]', else None."""
    text = request.strip().rstrip('.!?')
    match = re.match(VERB+r'\s+(.+)$', text, re.I)
    if not match:
        return None
    head, rest = match.group(1), ''
    split = FOLLOW_UP.search(head)
    if split:
        head, rest = head[:split.start()], head[split.end():].strip()
    head = FILLER.sub('', head)
    browser, mode = None, 'default'
    placement = PLACEMENT.search(head)
    if placement and (placement.group(2) or placement.group(3)):
        new, named, kind = placement.groups()
        head = FILLER.sub('', head[:placement.start()])
        browser = BROWSER_WORDS.get((named or '').casefold(), '')
        kind = (kind or '').casefold()
        mode = 'tab' if kind == 'tab' else 'window' if kind == 'window' and new else 'default'
    name = head.strip(' "\'“”')
    words = name.split()
    if not words or len(words) > 5 or words[0].casefold() in NOT_A_NAME or len(name) > 80:
        return None
    return {'name': name, 'browser': browser, 'mode': mode, 'rest': rest}


def plan(request, catalog):
    """A request plan for a parsed 'open X', or None to let the model interpret it."""
    parsed = parse(request)
    if parsed is None:
        return None
    name, browser, rest = parsed['name'], parsed['browser'], parsed['rest']
    if browser is None and re.search(r'\s(?:in|within)\s', name, re.I):
        return None  # A destination inside an app needs semantic interpretation, not web fallback.
    if re.search(r'\b(play|pause|resume|skip|shuffle)\b', rest, re.I):
        return None  # "open Music and play X" uses the dedicated playback path.
    base = {'app_name': browser or '', 'browser_mode': parsed['mode'], 'remaining_request': rest}
    query = re.fullmatch(r'(?:then\s+)?search(?:\s+the\s+web|\s+online)?\s+for\s+(.+)', rest, re.I) if rest else None
    if browser is None and name.casefold() in BROWSER_WORDS and query and not FOLLOW_UP.search(query.group(1)):
        # "Open Safari and search for lasagna recipe" is one web search in that browser; as an
        # app launch plus a task it took ten seconds of clicking (live).
        return dict(base, operation='browser_search', app_name=BROWSER_WORDS[name.casefold()],
                    target=query.group(1).strip(' "\'“”'), remaining_request='')
    if browser is None:
        apps = resolve_app(name, catalog)
        if len(apps) == 1:
            return {'operation': 'launch_app', 'app_name': apps[0]['name'], 'target': '',
                    'browser_mode': 'default', 'remaining_request': rest}
        if len(apps) > 1:
            return None  # Ambiguous installed apps: let the model ask which one.
    # Only an unambiguous "search for X": with another "and/then" or a comma ("rock and roll",
    # "cats, click the first one") the query's end is unclear, so the task gets the whole request.
    found = site_search(name, rest) if rest and not FOLLOW_UP.search(rest) and not re.search(r'[,;:]', rest) else None
    if found:
        return dict(base, operation='browser_open', target=found, remaining_request='')
    if name.casefold() in WEB_APPS:
        return dict(base, operation='browser_open', target=WEB_APPS[name.casefold()])
    if DOMAIN.fullmatch(name):
        return dict(base, operation='browser_open', target=name)
    if LOCAL_THINGS & {word.casefold() for word in name.split()}:
        return None  # "open downloads folder", "open last email": not a web search.
    from pathlib import Path
    if browser is None and not rest and '/' not in name and (Path.home()/name).is_dir() and not name.startswith('.'):
        # "Can you open Movies?": a folder in the home folder (it became a web search, live).
        return {'operation': 'open_file', 'app_name': '', 'target': name, 'browser_mode': 'default', 'remaining_request': rest}
    near = closest_app(name, catalog) if browser is None else None
    if near is not None:
        # "Open safarie" / "open calender" became a web search (request eval): a near miss of one
        # installed app's name, when nothing else has that name, is that app.
        return {'operation': 'launch_app', 'app_name': near['name'], 'target': '', 'browser_mode': 'default', 'remaining_request': rest}
    if browser is None and closest_app(name, catalog, allow_words=True) is not None:
        return None  # "open calender": a real word, but near an app's name; the model tells which was meant
    if browser is None and re.match(r'^\W*(?:please\s+)?(?:can|could)\s+you\b', request, re.I):
        # A question with no resolved app, site, address or folder may ask about
        # capabilities. Let the interpreter distinguish that from a polite action
        # request instead of silently searching the web for its object.
        return None
    if not rest and browser is None and len(name.split()) <= 3:
        # "Open Canva": not installed and not a known site. Search, then the task opens its website
        # (a bare search page was the end of it, live).
        return dict(base, operation='browser_search', target=name, remaining_request='open the '+name+' website from the results')
    return dict(base, operation='browser_search', target=name)


VENDORS = ('microsoft ', 'google ', 'adobe ', 'apple ')
LEAD_IN = re.compile(r"^\W*(?:(?:um+|uh+|ok(?:ay)?|so|please|hey|ally)\b\W*)*", re.I)


def bare_name(request, catalog):
    """'Microsoft Teams.' said or typed on its own names an app: open it. Only an exact name
    (or the name without its vendor, "Teams" for Microsoft Teams) counts, then a known web
    app; "Activity" never becomes Activity Monitor. Anything else returns None for the model."""
    name = LEAD_IN.sub('', request.strip()).rstrip(' .!?…').strip(' "\'“”')
    words = name.split()
    if not words or len(words) > 4 or not re.fullmatch(r"[\w .&+'’-]+", name):
        return None
    wanted = normalized(name)
    def matches(app):
        full = app['name'].casefold()
        short = next((full[len(vendor):] for vendor in VENDORS if full.startswith(vendor)), full)
        return wanted in (normalized(full), normalized(short))
    apps = [app for app in catalog if matches(app)] or (resolve_app(name, catalog) if wanted in ALIASES else [])
    if len(apps) == 1:
        return {'operation': 'launch_app', 'app_name': apps[0]['name'], 'target': '', 'browser_mode': 'default', 'remaining_request': ''}
    if not apps and name.casefold() in WEB_APPS:
        return {'operation': 'browser_open', 'app_name': '', 'target': WEB_APPS[name.casefold()], 'browser_mode': 'default', 'remaining_request': ''}
    return None
