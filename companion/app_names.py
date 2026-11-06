"""Resolve platform names to observed installed apps; never manufacture a bundle ID."""
def normalized(value):
    return ''.join(character for character in value.casefold() if character.isalnum())


# Platform vocabulary, independent of LLM command interpretation.
ALIASES = {
    'settings': ('System Settings', 'System Preferences'),
    'macsettings': ('System Settings', 'System Preferences'),
    'macossettings': ('System Settings', 'System Preferences'),
    'preferences': ('System Settings', 'System Preferences'),
    'macssettings': ('System Settings', 'System Preferences'),
    'systempreferences': ('System Settings', 'System Preferences'),
    'systemsettings': ('System Settings', 'System Preferences'),
    'notepad': ('TextEdit',),
    'texteditor': ('TextEdit',),
    'macnotes': ('Notes',),
    'macosnotes': ('Notes',),
    'browser': ('Safari',),
}


def resolve_app(name, catalog):
    wanted = normalized(name)
    exact = [app for app in catalog if normalized(app['name']) == wanted]
    if exact:
        return exact
    aliases = {normalized(alias) for alias in ALIASES.get(wanted, ())}
    known_ids = {'systemsettings': 'com.apple.systempreferences', 'systempreferences': 'com.apple.systempreferences', 'textedit': 'com.apple.TextEdit', 'notes': 'com.apple.Notes', 'safari': 'com.apple.Safari'}
    bundle_ids = {known_ids[alias] for alias in aliases if alias in known_ids}
    matches = [app for app in catalog if normalized(app['name']) in aliases or app['bundle_id'] in bundle_ids]
    if matches:
        return matches
    # PR #2: "Teams" can match "Microsoft Teams". Preserve ambiguity rather
    # than choosing a random app, and never use arbitrary substring matches.
    words = lambda value: {normalized(word) for word in value.split()} - {''}
    requested = words(name)
    return [app for app in catalog if requested and requested <= words(app['name'])]


def edits(first, second, limit):
    """Edit distance (a wrong, missing, extra or swapped letter each count one), or limit+1 when larger."""
    if abs(len(first)-len(second)) > limit:
        return limit+1
    rows = [list(range(len(second)+1))]
    for i, a in enumerate(first, 1):
        row = [i]+[0]*len(second)
        for j, b in enumerate(second, 1):
            row[j] = min(rows[-1][j]+1, row[j-1]+1, rows[-1][j-1]+(a != b))
            if i > 1 and j > 1 and a == second[j-2] and first[i-2] == b:
                row[j] = min(row[j], rows[-2][j-2]+1)
        rows.append(row)
    return min(rows[-1][-1], limit+1)


def closest_app(name, catalog):
    """The one installed app whose name is a near miss of `name` ("safarie", "calender"), else None.

    For opening an app only, after exact names, aliases and known sites found nothing. One edit for
    a name of six letters or more, two from ten; shorter names are not guessed ("mail"/"maps",
    "canva"). The app's whole name counts, or one word of it ("settigns" for System Settings).
    When two apps are equally near, neither is chosen."""
    wanted = normalized(name)
    limit = 2 if len(wanted) >= 10 else 1 if len(wanted) >= 6 else 0
    if not limit:
        return None
    found = {}
    for app in catalog:
        keys = {normalized(app['name'])} | {normalized(word) for word in app['name'].split()}
        distance = min(edits(wanted, key, limit) for key in keys if key)
        if distance <= limit:
            found[app['bundle_id']] = (distance, app)
    if not found:
        return None
    best = min(distance for distance, _ in found.values())
    nearest = [app for distance, app in found.values() if distance == best]
    return nearest[0] if len(nearest) == 1 else None


def launchable(name, catalog):
    """Apps to open for a name: the exact/alias/word matches, else the one near miss."""
    matches = resolve_app(name, catalog)
    if not matches:
        near = closest_app(name, catalog)
        matches = [near] if near else []
    return matches
