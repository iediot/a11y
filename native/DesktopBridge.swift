// Read-only accessibility metadata plus confirmed app activation and exact-control AXPress.
// Never reads AXValue, selected text, clipboard, screenshots, or secure input contents.
import AppKit
import ApplicationServices
import EventKit
import Foundation

func emit(_ value: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) {
        print(String(decoding: data, as: UTF8.self))
    }
}
// Programmatic scrolling is a bounded trackpad gesture, not a physical mouse wheel.
// Wheel utilities may reverse/smooth unphased events and defeat the requested direction.
func scrollDistance(windowHeight: Double) -> Int32? {
    guard windowHeight.isFinite, windowHeight > 0 else { return nil }
    // Keep overlap between observations without spending a model step per few rows.
    return Int32(max(1, min(480, (windowHeight/2).rounded())))
}
func scrollPacket(direction: String, phase: CGScrollPhase, location: CGPoint, distance: Int32 = 60) -> CGEvent? {
    guard ["up", "down"].contains(direction), [.began, .changed, .ended].contains(phase),
          (1...480).contains(distance) else { return nil }
    let delta: Int32 = phase == .changed ? (direction == "up" ? distance : -distance) : 0
    guard let event = CGEvent(scrollWheelEvent2Source: nil, units: .pixel, wheelCount: 1,
                              wheel1: delta, wheel2: 0, wheel3: 0) else { return nil }
    // CGScrollPhase and NSEvent.Phase have DIFFERENT raw values for changed/ended.
    event.setIntegerValueField(.scrollWheelEventScrollPhase, value: Int64(phase.rawValue))
    event.location = location
    return event
}
func attr(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
    var value: CFTypeRef?
    return AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success ? value : nil
}
func string(_ element: AXUIElement, _ name: String) -> String {
    String(((attr(element, name) as? String) ?? "").prefix(160))
}
func element(_ value: CFTypeRef?) -> AXUIElement? {
    guard let value, CFGetTypeID(value) == AXUIElementGetTypeID() else { return nil }
    return (value as! AXUIElement)
}
func containerRoles(_ node: AXUIElement) -> [String] {
    var roles: [String] = []
    var ancestor = element(attr(node, "AXParent"))
    for _ in 0..<3 {
        guard let parent = ancestor else { break }
        roles.append(string(parent, "AXRole"))
        ancestor = element(attr(parent, "AXParent"))
    }
    return roles
}
func finderItem(_ node: AXUIElement) -> Bool {
    var pid: pid_t = 0
    AXUIElementGetPid(node, &pid)
    guard NSRunningApplication(processIdentifier: pid)?.bundleIdentifier == "com.apple.finder" else { return false }
    return ([string(node, "AXRole")] + containerRoles(node)).contains { ["AXRow", "AXCell"].contains($0) }
}
func bounds(_ node: AXUIElement) -> [String: Double]? {
    guard let position = attr(node, "AXPosition"), let size = attr(node, "AXSize"),
          CFGetTypeID(position) == AXValueGetTypeID(), CFGetTypeID(size) == AXValueGetTypeID() else { return nil }
    var point = CGPoint.zero
    var extent = CGSize.zero
    guard AXValueGetValue(position as! AXValue, .cgPoint, &point),
          AXValueGetValue(size as! AXValue, .cgSize, &extent), extent.width > 0, extent.height > 0 else { return nil }
    return ["x": point.x, "y": point.y, "width": extent.width, "height": extent.height]
}
let args = CommandLine.arguments
let ownPID = args.firstIndex(of: "--exclude-pid").flatMap { $0+1 < args.count ? Int32(args[$0+1]) : nil }
let workspace = NSWorkspace.shared
let deniedBundles: Set<String> = ["com.apple.Passwords", "com.apple.keychainaccess", "com.agilebits.onepassword7", "com.1password.1password", "com.bitwarden.desktop"]
let running = workspace.runningApplications.filter {
    $0.activationPolicy == .regular && $0.processIdentifier != ownPID && $0.bundleIdentifier != nil && !deniedBundles.contains($0.bundleIdentifier!)
}
func scriptLiteral(_ value: String) -> String {
    return "\"" + value.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"") + "\""
}
func musicScript(_ body: String) -> NSAppleEventDescriptor {
    var failure: NSDictionary?
    let script = NSAppleScript(source: "with timeout of 30 seconds\ntell application id \"com.apple.Music\"\n" + body + "\nend tell\nend timeout")
    guard let script else { emit(["error": "Could not prepare Music automation."]); exit(1) }
    let result = script.executeAndReturnError(&failure)
    guard failure == nil else {
        emit(["error": "Music automation failed. Check Privacy & Security > Automation for Music; no action will be retried automatically."]); exit(1)
    }
    return result
}
let musicSnapshot = """
set playbackState to player state as text
set trackInfo to {"", "", ""}
try
    set trackInfo to {name of current track, artist of current track, persistent ID of current track}
end try
"""
func mediaFields(_ result: NSAppleEventDescriptor, offset: Int = 0) -> [String: Any] {
    return ["player_state": result.atIndex(offset+1)?.stringValue ?? "unknown",
            "track": ["name": result.atIndex(offset+2)?.stringValue ?? "", "artist": result.atIndex(offset+3)?.stringValue ?? "", "id": result.atIndex(offset+4)?.stringValue ?? ""]]
}
// Built-in display brightness (system_controls.py). DisplayServices is private, so it is loaded at
// run time: a missing framework or symbol is an error reply, never a crash. --brightness-key is the
// fallback for displays it cannot drive: the brightness keys, which give no reading back.
typealias BrightnessGetter = @convention(c) (UInt32, UnsafeMutablePointer<Float>) -> Int32
typealias BrightnessSetter = @convention(c) (UInt32, Float) -> Int32
func displayServicesSymbol(_ name: String) -> UnsafeMutableRawPointer? {
    guard let handle = dlopen("/System/Library/PrivateFrameworks/DisplayServices.framework/DisplayServices", RTLD_LAZY) else { return nil }
    return dlsym(handle, name)
}
func displayBrightness() -> Float? {
    guard let symbol = displayServicesSymbol("DisplayServicesGetBrightness") else { return nil }
    var level: Float = -1
    let status = unsafeBitCast(symbol, to: BrightnessGetter.self)(CGMainDisplayID(), &level)
    return status == 0 && level >= 0 && level <= 1 ? level : nil
}
if args.contains("--brightness") {
    guard let level = displayBrightness() else { emit(["error": "This display's brightness cannot be read."]); exit(1) }
    emit(["brightness": level]); exit(0)
}
if let index = args.firstIndex(of: "--set-brightness"), index+1 < args.count {
    guard let wanted = Float(args[index+1]), wanted >= 0, wanted <= 1 else { emit(["error": "Brightness must be between 0 and 1."]); exit(1) }
    guard displayBrightness() != nil, let symbol = displayServicesSymbol("DisplayServicesSetBrightness"),
          unsafeBitCast(symbol, to: BrightnessSetter.self)(CGMainDisplayID(), wanted) == 0 else {
        emit(["error": "This display's brightness cannot be set."]); exit(1)
    }
    usleep(200_000)
    guard let level = displayBrightness() else { emit(["error": "This display's brightness cannot be read."]); exit(1) }
    emit(["brightness": level]); exit(0)
}
if let index = args.firstIndex(of: "--brightness-key"), index+2 < args.count {
    let up = args[index+1] == "up"
    guard up || args[index+1] == "down", let presses = Int(args[index+2]), presses >= 1, presses <= 16 else {
        emit(["error": "Name up or down and 1 to 16 presses."]); exit(1)
    }
    let key = up ? 2 : 3  // NX_KEYTYPE_BRIGHTNESS_UP, NX_KEYTYPE_BRIGHTNESS_DOWN
    for _ in 0..<presses {
        for pressed in [true, false] {
            guard let event = NSEvent.otherEvent(with: .systemDefined, location: .zero,
                    modifierFlags: NSEvent.ModifierFlags(rawValue: pressed ? 0xa00 : 0xb00), timestamp: 0, windowNumber: 0,
                    context: nil, subtype: 8, data1: (key << 16) | ((pressed ? 0xa : 0xb) << 8), data2: -1),
                  let cgEvent = event.cgEvent else {
                emit(["error": "Could not prepare the brightness key."]); exit(1)
            }
            cgEvent.post(tap: .cghidEventTap)
        }
        usleep(60_000)
    }
    emit(["submitted": true, "presses": presses]); exit(0)
}
if args.contains("--music-state") {
    guard workspace.runningApplications.contains(where: { $0.bundleIdentifier == "com.apple.Music" && !$0.isTerminated }) else {
        emit(["player_state": "not_running"]); exit(0)
    }
    let result = musicScript(musicSnapshot + "\nreturn {playbackState, item 1 of trackInfo, item 2 of trackInfo, item 3 of trackInfo}")
    emit(mediaFields(result)); exit(0)
}
if let index = args.firstIndex(of: "--music-play"), index+2 < args.count {
    let query = args[index+1], artist = args[index+2]
    guard !query.trimmingCharacters(in: .whitespaces).isEmpty, query.count <= 500, artist.count <= 200,
          (query+artist).unicodeScalars.allSatisfy({ $0.value >= 32 }) else {
        emit(["error": "Name a song title and optional artist."]); exit(1)
    }
    let script = """
    set songName to \(scriptLiteral(query))
    set artistName to \(scriptLiteral(artist))
    set foundTracks to search library playlist 1 for songName only names
    set candidates to {}
    set exactTracks to {}
    set outcome to "not_found"
    set didPlay to false
    set requestedID to ""
    if (count of foundTracks) > 100 then
        set outcome to "too_many"
    else
        repeat with candidate in foundTracks
            if (count of candidates) < 20 then set end of candidates to {name of candidate, artist of candidate}
            ignoring case
                if (name of candidate is songName) and (artistName is "" or artist of candidate contains artistName) then set end of exactTracks to contents of candidate
            end ignoring
        end repeat
        if (count of exactTracks) is 1 then
            set chosen to item 1 of exactTracks
            set requestedID to persistent ID of chosen
            play chosen
            set didPlay to true
            set outcome to "play_submitted"
        else if (count of exactTracks) > 1 then
            set outcome to "ambiguous"
        end if
    end if
    \(musicSnapshot)
    return {outcome, didPlay, requestedID, playbackState, item 1 of trackInfo, item 2 of trackInfo, item 3 of trackInfo, candidates}
    """
    let result = musicScript(script)
    var output = mediaFields(result, offset: 3)
    var candidates: [[String: String]] = []
    if let list = result.atIndex(8), list.numberOfItems > 0 {
        for i in 1...min(20, list.numberOfItems) {
            candidates.append(["name": list.atIndex(i)?.atIndex(1)?.stringValue ?? "", "artist": list.atIndex(i)?.atIndex(2)?.stringValue ?? ""])
        }
    }
    output["submitted"] = true
    output["outcome"] = result.atIndex(1)?.stringValue ?? "unknown"
    output["play_submitted"] = result.atIndex(2)?.booleanValue ?? false
    output["requested_id"] = result.atIndex(3)?.stringValue ?? ""
    output["candidates"] = candidates
    emit(output); exit(0)
}
if let index = args.firstIndex(of: "--input"), index+1 < args.count {
    guard AXIsProcessTrusted(), let data = args[index+1].data(using: .utf8),
          let action = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
          let bundle = action["bundle_id"] as? String,
          !deniedBundles.contains(bundle), !["com.apple.Terminal", "com.googlecode.iterm2", "dev.warp.Warp-Stable"].contains(bundle),
          let owner = running.first(where: { $0.bundleIdentifier == bundle }),
          let expected = action["bounds"] as? [String: Double] else {
        emit(["error": "Input needs an observed non-protected app and Accessibility permission."]); exit(1)
    }
    let front = workspace.frontmostApplication
    guard front?.bundleIdentifier == bundle || front?.processIdentifier == ownPID else {
        emit(["error": "Another app became active. Input was cancelled."]); exit(1)
    }
    _ = owner.activate(options: [])
    let deadline = Date().addingTimeInterval(0.5)
    while workspace.frontmostApplication?.bundleIdentifier != bundle && Date() < deadline { RunLoop.current.run(until: Date().addingTimeInterval(0.02)) }
    let app = AXUIElementCreateApplication(owner.processIdentifier)
    AXUIElementSetMessagingTimeout(app, 0.25)
    guard workspace.frontmostApplication?.bundleIdentifier == bundle,
          let window = element(attr(app, "AXFocusedWindow")), bounds(window) == expected,
          string(window, "AXTitle") == action["window_title"] as? String,
          let focused = element(attr(app, "AXFocusedUIElement")), string(focused, "AXSubrole") != "AXSecureTextField" else {
        emit(["error": "The input window or focus changed. Nothing was sent."]); exit(1)
    }
    let kind = action["type"] as? String ?? ""
    if kind == "type_text" {
        guard let text = action["text"] as? String, !text.isEmpty, text.count <= 2000,
              text.unicodeScalars.allSatisfy({ $0.value >= 32 || ($0 == "\n" && string(focused, "AXRole") == "AXTextArea") }),
              ["AXTextField", "AXTextArea", "AXComboBox"].contains(string(focused, "AXRole")),
              string(focused, "AXRole") == action["focused_role"] as? String,
              bounds(focused) == action["focused_bounds"] as? [String: Double] else {
            emit(["error": "Focus the same non-password text field before typing."]); exit(1)
        }
        // A one-line field (address bar, search box) is replaced, never typed into the middle: a second
        // click put the cursor inside the old address and the new one went in there (owner, live).
        if string(focused, "AXRole") != "AXTextArea" {
            let existing = string(focused, "AXValue")
            if !existing.isEmpty {
                var whole = CFRange(location: 0, length: (existing as NSString).length)
                if let range = AXValueCreate(.cfRange, &whole) {
                    AXUIElementSetAttributeValue(focused, "AXSelectedTextRange" as CFString, range)
                    RunLoop.current.run(until: Date().addingTimeInterval(0.05))
                }
            }
        }
        var chunks: [[UInt16]] = []
        var chunk: [UInt16] = []
        for character in text {
            if character == "\n" {
                // A new line: Shift+Return (an empty chunk), never a plain Return that sends in chat apps.
                if !chunk.isEmpty { chunks.append(chunk); chunk = [] }
                chunks.append([])
                continue
            }
            let units = Array(String(character).utf16)
            guard units.count <= 20 else { emit(["error": "That text contains an unsupported long grapheme."]); exit(1) }
            if chunk.count+units.count > 20 { chunks.append(chunk); chunk = [] }
            chunk.append(contentsOf: units)
        }
        if !chunk.isEmpty { chunks.append(chunk) }
        for characters in chunks {
            guard workspace.frontmostApplication?.bundleIdentifier == bundle,
                  let current = element(attr(app, "AXFocusedUIElement")), CFEqual(current, focused),
                  let down = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: true),
                  let up = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: false) else {
                emit(["error": "Typing stopped because focus changed. Some text may already have been entered; check before retrying."]); exit(1)
            }
            if characters.isEmpty {
                guard let returnDown = CGEvent(keyboardEventSource: nil, virtualKey: 36, keyDown: true),
                      let returnUp = CGEvent(keyboardEventSource: nil, virtualKey: 36, keyDown: false) else { continue }
                returnDown.flags = .maskShift; returnUp.flags = .maskShift
                returnDown.post(tap: .cghidEventTap); returnUp.post(tap: .cghidEventTap)
                usleep(20_000)
                continue
            }
            characters.withUnsafeBufferPointer { buffer in
                down.keyboardSetUnicodeString(stringLength: buffer.count, unicodeString: buffer.baseAddress!)
                up.keyboardSetUnicodeString(stringLength: buffer.count, unicodeString: buffer.baseAddress!)
            }
            down.post(tap: .cghidEventTap); up.post(tap: .cghidEventTap)
        }
        // Did it arrive? The field's own text must now hold the last line typed (keystrokes were lost
        // right after a new TextEdit document opened, live). A field that reports no text is not judged.
        let lastLine = text.split(separator: "\n").last.map { $0.trimmingCharacters(in: .whitespaces) } ?? ""
        // A field that counts its characters is judged even when empty: an open menu (Notes' search scope)
        // swallowed every keystroke and the field stayed empty (live).
        let judged = attr(focused, "AXNumberOfCharacters") != nil
        var arrived = lastLine.isEmpty
        let deadline = Date().addingTimeInterval(0.8)
        while !arrived && Date() < deadline {
            RunLoop.current.run(until: Date().addingTimeInterval(0.1))
            let value = string(focused, "AXValue")
            arrived = (value.isEmpty && !judged) || value.contains(lastLine)
        }
        if !arrived {
            emit(["error": "The typed text did not arrive in the field. Look again before retrying."]); exit(1)
        }
    } else if kind == "press_key" {
        let keys: [String: CGKeyCode] = ["enter": 36, "tab": 48, "escape": 53, "space": 49, "backspace": 51,
            "left": 123, "right": 124, "up": 126, "down": 125, "cmd_l": 37, "cmd_f": 3, "cmd_t": 17,
            "cmd_n": 45, "cmd_w": 13, "cmd_a": 0, "cmd_r": 15, "cmd_[": 33, "cmd_]": 30, "cmd_s": 1]
        guard let name = action["key"] as? String, let key = keys[name],
              let down = CGEvent(keyboardEventSource: nil, virtualKey: key, keyDown: true),
              let up = CGEvent(keyboardEventSource: nil, virtualKey: key, keyDown: false) else {
            emit(["error": "Unsupported keyboard command."]); exit(1)
        }
        if name.hasPrefix("cmd_") { down.flags = .maskCommand; up.flags = .maskCommand }
        down.post(tap: .cghidEventTap); up.post(tap: .cghidEventTap)
    } else if kind == "scroll" {
        guard let direction = action["direction"] as? String, ["up", "down"].contains(direction),
              let rectangle = bounds(window), let x = rectangle["x"], let y = rectangle["y"], let width = rectangle["width"], let height = rectangle["height"],
              let distance = scrollDistance(windowHeight: height) else {
            emit(["error": "Unsupported scroll destination."]); exit(1)
        }
        for phase in [CGScrollPhase.began, .changed, .ended] {
            guard workspace.frontmostApplication?.bundleIdentifier == bundle,
                  let currentWindow = element(attr(app, "AXFocusedWindow")), CFEqual(currentWindow, window),
                  bounds(currentWindow) == expected,
                  string(currentWindow, "AXTitle") == action["window_title"] as? String,
                  let current = element(attr(app, "AXFocusedUIElement")),
                  string(current, "AXSubrole") != "AXSecureTextField" else {
                emit(["error": "Scroll focus changed; check the app before retrying."]); exit(1)
            }
            guard let event = scrollPacket(direction: direction, phase: phase,
                                           location: CGPoint(x: x+width/2, y: y+height/2), distance: distance) else {
                emit(["error": "Could not construct scroll event."]); exit(1)
            }
            event.post(tap: .cghidEventTap)
            RunLoop.current.run(until: Date().addingTimeInterval(0.02))
        }
    } else { emit(["error": "Unsupported input operation."]); exit(1) }
    emit(["submitted": true, "verification_required": true]); exit(0)
}
// The app whose normal window is frontmost at a point, ignoring A11y's own windows.
func windowOwner(at point: CGPoint) -> pid_t? {
    guard let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] else { return nil }
    for info in list {
        guard (info[kCGWindowLayer as String] as? Int) == 0,
              let pid = info[kCGWindowOwnerPID as String] as? pid_t, pid != ownPID,
              let frame = info[kCGWindowBounds as String] as? NSDictionary,
              let rect = CGRect(dictionaryRepresentation: frame as CFDictionary), rect.contains(point) else { continue }
        return pid
    }
    return nil
}
if let index = args.firstIndex(of: "--hit"), index+2 < args.count {
    // What a click at this point would land on, by accessibility: so a click on an app's Dock icon
    // or its name in the menu bar can become switching to that app (or be refused when it is already
    // in front) instead of a click that changes nothing. Read only: nothing is pressed.
    guard AXIsProcessTrusted(), let x = Double(args[index+1]), let y = Double(args[index+2]), x.isFinite, y.isFinite else {
        emit(["error": "Accessibility permission is required."]); exit(1)
    }
    var hit: AXUIElement?
    guard AXUIElementCopyElementAtPosition(AXUIElementCreateSystemWide(), Float(x), Float(y), &hit) == .success, let found = hit else {
        emit(["role": ""]); exit(0)
    }
    var pid: pid_t = 0
    AXUIElementGetPid(found, &pid)
    let owner = NSRunningApplication(processIdentifier: pid)
    var result: [String: Any] = ["role": string(found, "AXRole"), "subrole": string(found, "AXSubrole"),
                                 "title": String((string(found, "AXTitle").isEmpty ? string(found, "AXDescription") : string(found, "AXTitle")).prefix(100)),
                                 "owner": owner?.bundleIdentifier ?? "", "owner_name": owner?.localizedName ?? ""]
    if finderItem(found) {
        result["finder_item"] = true
        // Finder's filename label is a field value. Do not read arbitrary
        // values in other apps, or a secure field.
        if (result["title"] as? String ?? "").isEmpty && string(found, "AXSubrole") != "AXSecureTextField" {
            result["title"] = string(found, "AXValue")
        }
    }
    // Structural roles only: distinguish navigation in a settings sidebar/toolbar
    // from controls that change settings. Never export parent titles or values.
    var ancestorRoles: [String] = []
    var ancestor = element(attr(found, "AXParent"))
    for _ in 0..<10 {
        guard let current = ancestor else { break }
        ancestorRoles.append(string(current, "AXRole"))
        ancestor = element(attr(current, "AXParent"))
    }
    result["ancestor_roles"] = ancestorRoles
    if string(found, "AXRole") == "AXDockItem", let url = attr(found, "AXURL") as? URL, let bundle = Bundle(url: url)?.bundleIdentifier {
        result["app"] = bundle  // the app this Dock icon opens
    }
    if string(found, "AXRole") == "AXMenuBarItem", let bar = element(attr(found, "AXParent")),
       let items = attr(bar, "AXChildren") as? [AXUIElement], items.count > 1, CFEqual(items[1], found) {
        result["app"] = owner?.bundleIdentifier ?? ""  // the app's own name, next to the Apple menu
        result["app_name_item"] = true
    }
    emit(result); exit(0)
}
if let index = args.firstIndex(of: "--click"), index+4 < args.count {
    guard AXIsProcessTrusted(), let x = Double(args[index+1]), let y = Double(args[index+2]),
          x.isFinite, y.isFinite, let displayID = UInt32(args[index+3]), CGDisplayIsActive(displayID) != 0,
          let data = args[index+4].data(using: .utf8),
          let expected = (try? JSONSerialization.jsonObject(with: data)) as? [String: Double] else {
        emit(["error": "A current display and macOS Accessibility permission are required for clicking."]); exit(1)
    }
    let buttonName = index+5 < args.count ? args[index+5] : "left"
    let count = index+6 < args.count ? Int(args[index+6]) ?? 0 : 1
    guard ["left", "right"].contains(buttonName), [1, 2].contains(count), buttonName != "right" || count == 1 else {
        emit(["error": "Unsupported mouse click."]); exit(1)
    }
    let point = CGPoint(x: x, y: y)
    let actual = CGDisplayBounds(displayID)
    guard actual.contains(point), expected["x"] == actual.origin.x,
          expected["y"] == actual.origin.y, expected["width"] == actual.width, expected["height"] == actual.height else {
        emit(["error": "The display changed. No click was sent."]); exit(1)
    }
    // A11y's own window (her, or her reply beside her) over the point would take the click, and screenshots
    // leave her out, so nothing would seem to happen (Battery, Calculator's sidebar, live). Refuse it.
    // Her windows float above normal ones, so any level counts for hers; another app's normal window in
    // front of the point ends the search (the target is on top).
    if let ownPID, let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] {
        for info in list {
            guard let frame = info[kCGWindowBounds as String] as? NSDictionary,
                  let rect = CGRect(dictionaryRepresentation: frame as CFDictionary), rect.contains(point) else { continue }
            let pid = info[kCGWindowOwnerPID as String] as? pid_t
            if pid == ownPID && ((info[kCGWindowAlpha as String] as? Double) ?? 1) > 0.05 && rect.width <= 700 {  // not the click-through overlay
                emit(["error": "The companion's window covers that point. No click was sent."]); exit(1)
            }
            if (info[kCGWindowLayer as String] as? Int) == 0 { break }
        }
    }
    // A click on a window of an app that is not active is often swallowed: the app only comes
    // forward (web pages, Teams and other Electron apps). Bring the target's app forward first.
    var activated = false
    if let pid = windowOwner(at: point), let target = NSRunningApplication(processIdentifier: pid), !target.isActive {
        target.activate(options: [.activateIgnoringOtherApps])
        let deadline = Date().addingTimeInterval(1.0)
        while !target.isActive && Date() < deadline { RunLoop.current.run(until: Date().addingTimeInterval(0.03)) }
        RunLoop.current.run(until: Date().addingTimeInterval(0.15))
        activated = target.isActive
    }
    // 1. Without the user's pointer: press the element at the point through accessibility (buttons, links,
    //    tabs, menus) or focus it (text fields). Single left clicks only; the element must belong to the
    //    app under the point, never to A11y. Walks up from a label to the control that holds it.
    if buttonName == "left" && count == 1, let pid = windowOwner(at: point) {
        var found: AXUIElement?
        if AXUIElementCopyElementAtPosition(AXUIElementCreateSystemWide(), Float(point.x), Float(point.y), &found) == .success,
           var node = found {
            var owner: pid_t = 0
            AXUIElementGetPid(node, &owner)
            let pressable: Set<String> = ["AXButton", "AXLink", "AXRadioButton", "AXTab", "AXCheckBox", "AXMenuButton",
                                          "AXPopUpButton", "AXMenuBarItem", "AXMenuItem", "AXDisclosureTriangle", "AXCell", "AXRow"]
            let fields: Set<String> = ["AXTextField", "AXSearchField", "AXTextArea", "AXComboBox"]
            for _ in 0..<4 where owner == pid {
                // Focusing a filename field enters rename mode. File rows need
                // a real mouse selection/opening, never generic field focus.
                if let found, finderItem(found) { break }
                let role = string(node, "AXRole")
                var names: CFArray?
                AXUIElementCopyActionNames(node, &names)
                let actions = (names as? [String]) ?? []
                AXUIElementSetMessagingTimeout(node, 1)
                if pressable.contains(role) && actions.contains(kAXPressAction as String) {
                    if AXUIElementPerformAction(node, kAXPressAction as CFString) == .success {
                        emit(["submitted": true, "verification_required": true, "method": "press", "activated_target_app": activated]); exit(0)
                    }
                    break
                }
                if fields.contains(role) {
                    if AXUIElementSetAttributeValue(node, kAXFocusedAttribute as CFString, kCFBooleanTrue) == .success,
                       (attr(node, kAXFocusedAttribute as String) as? Bool) == true {
                        emit(["submitted": true, "verification_required": true, "method": "focus", "activated_target_app": activated]); exit(0)
                    }
                    break
                }
                guard let parent = element(attr(node, "AXParent")) else { break }
                node = parent
            }
        }
    }
    // 2. The pointer, borrowed for a moment: hidden, a quick click at the point, and put back where the user
    //    left it (unless they moved it meanwhile, then it stays with them).
    let saved = CGEvent(source: nil)?.location
    CGDisplayHideCursor(CGMainDisplayID())
    defer { CGDisplayShowCursor(CGMainDisplayID()) }
    // A real left (or right) click: move there, press, hold briefly, release, as a mouse does.
    let source = CGEventSource(stateID: .hidSystemState)
    let button: CGMouseButton = buttonName == "left" ? .left : .right
    let downType: CGEventType = buttonName == "left" ? .leftMouseDown : .rightMouseDown
    let upType: CGEventType = buttonName == "left" ? .leftMouseUp : .rightMouseUp
    guard let move = CGEvent(mouseEventSource: source, mouseType: .mouseMoved, mouseCursorPosition: point, mouseButton: button),
          let down = CGEvent(mouseEventSource: source, mouseType: downType, mouseCursorPosition: point, mouseButton: button),
          let up = CGEvent(mouseEventSource: source, mouseType: upType, mouseCursorPosition: point, mouseButton: button) else {
        emit(["error": "The mouse events could not be created. No click was sent."]); exit(1)
    }
    move.post(tap: .cghidEventTap)
    RunLoop.current.run(until: Date().addingTimeInterval(0.025))
    for click in 1...count {
        down.setIntegerValueField(.mouseEventClickState, value: Int64(click))
        up.setIntegerValueField(.mouseEventClickState, value: Int64(click))
        down.post(tap: .cghidEventTap)
        RunLoop.current.run(until: Date().addingTimeInterval(0.03))
        up.post(tap: .cghidEventTap)
        if click < count { RunLoop.current.run(until: Date().addingTimeInterval(0.08)) }
    }
    RunLoop.current.run(until: Date().addingTimeInterval(0.05))  // let the events reach the app before exiting
    if let saved, let now = CGEvent(source: nil)?.location, hypot(now.x-point.x, now.y-point.y) < 3 {
        CGWarpMouseCursorPosition(saved)
        CGAssociateMouseAndMouseCursorPosition(1)
    }
    CGDisplayShowCursor(CGMainDisplayID())
    emit(["submitted": true, "verification_required": true, "method": "pointer", "activated_target_app": activated]); exit(0)
}
if let index = args.firstIndex(of: "--drag"), index+6 < args.count {
    // Press at one point, move to another, release (select text, move an item). The user's pointer is hidden,
    // borrowed for it and put back, as for a click.
    guard AXIsProcessTrusted(), let x = Double(args[index+1]), let y = Double(args[index+2]),
          let x2 = Double(args[index+3]), let y2 = Double(args[index+4]), [x, y, x2, y2].allSatisfy({ $0.isFinite }),
          let displayID = UInt32(args[index+5]), CGDisplayIsActive(displayID) != 0,
          let data = args[index+6].data(using: .utf8),
          let expected = (try? JSONSerialization.jsonObject(with: data)) as? [String: Double] else {
        emit(["error": "A current display and macOS Accessibility permission are required for dragging."]); exit(1)
    }
    let from = CGPoint(x: x, y: y), to = CGPoint(x: x2, y: y2)
    let actual = CGDisplayBounds(displayID)
    guard actual.contains(from), actual.contains(to), expected["x"] == actual.origin.x, expected["y"] == actual.origin.y,
          expected["width"] == actual.width, expected["height"] == actual.height else {
        emit(["error": "The display changed. Nothing was dragged."]); exit(1)
    }
    if let ownPID, let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] {
        for info in list {
            guard let frame = info[kCGWindowBounds as String] as? NSDictionary,
                  let rect = CGRect(dictionaryRepresentation: frame as CFDictionary), rect.contains(from) else { continue }
            if (info[kCGWindowOwnerPID as String] as? pid_t) == ownPID && rect.width <= 700 {
                emit(["error": "The companion's window covers that point. Nothing was dragged."]); exit(1)
            }
            if (info[kCGWindowLayer as String] as? Int) == 0 { break }
        }
    }
    if let pid = windowOwner(at: from), let target = NSRunningApplication(processIdentifier: pid), !target.isActive {
        target.activate(options: [.activateIgnoringOtherApps])
        let deadline = Date().addingTimeInterval(1.0)
        while !target.isActive && Date() < deadline { RunLoop.current.run(until: Date().addingTimeInterval(0.03)) }
    }
    let saved = CGEvent(source: nil)?.location
    CGDisplayHideCursor(CGMainDisplayID())
    let source = CGEventSource(stateID: .hidSystemState)
    func post(_ type: CGEventType, _ point: CGPoint) {
        CGEvent(mouseEventSource: source, mouseType: type, mouseCursorPosition: point, mouseButton: .left)?.post(tap: .cghidEventTap)
    }
    post(.mouseMoved, from)
    RunLoop.current.run(until: Date().addingTimeInterval(0.03))
    post(.leftMouseDown, from)
    RunLoop.current.run(until: Date().addingTimeInterval(0.06))
    for step in 1...12 {  // a short, even glide so apps see a drag, not a jump
        let t = Double(step)/12
        post(.leftMouseDragged, CGPoint(x: from.x+(to.x-from.x)*t, y: from.y+(to.y-from.y)*t))
        RunLoop.current.run(until: Date().addingTimeInterval(0.015))
    }
    post(.leftMouseUp, to)
    RunLoop.current.run(until: Date().addingTimeInterval(0.05))
    if let saved, let now = CGEvent(source: nil)?.location, hypot(now.x-to.x, now.y-to.y) < 3 {
        CGWarpMouseCursorPosition(saved)
        CGAssociateMouseAndMouseCursorPosition(1)
    }
    CGDisplayShowCursor(CGMainDisplayID())
    emit(["submitted": true, "verification_required": true, "method": "drag"]); exit(0)
}
if let index = args.firstIndex(of: "--activate"), index+1 < args.count {
    let bundle = args[index+1]
    guard let app = running.first(where: { $0.bundleIdentifier == bundle }) else {
        emit(["error": "The app is no longer available."]); exit(1)
    }
    emit(["activated": app.activate(options: []), "bundle_id": bundle]); exit(0)
}
func installedApps() -> [[String: String]] {
    let roots = ["/Applications", "/Applications/Utilities", "/System/Applications", "/System/Applications/Utilities"]
    var result: [[String: String]] = []
    var seen = Set<String>()
    for root in roots {
        let urls = ((try? FileManager.default.contentsOfDirectory(at: URL(fileURLWithPath: root), includingPropertiesForKeys: nil)) ?? []) + (root == "/System/Applications" ? [URL(fileURLWithPath: "/System/Library/CoreServices/Finder.app")] : [])
        for url in urls {
            guard url.pathExtension == "app", let bundle = Bundle(url: url), let id = bundle.bundleIdentifier,
                  !deniedBundles.contains(id), (bundle.object(forInfoDictionaryKey: "LSUIElement") as? Bool) != true,
                  (bundle.object(forInfoDictionaryKey: "LSBackgroundOnly") as? Bool) != true,
                  seen.insert(id).inserted else { continue }
            result.append(["bundle_id": id, "name": String(((bundle.object(forInfoDictionaryKey: "CFBundleDisplayName") as? String) ?? url.deletingPathExtension().lastPathComponent).prefix(100))])
        }
    }
    return result.sorted { $0["name"]! < $1["name"]! }
}
// Calendar and Reminders through EventKit (recurring events included, unlike AppleScript).
// Reading needs the user's Calendars/Reminders permission; adding is only ever run after the
// user confirmed that exact item in A11y.
func eventStore(_ type: EKEntityType) -> EKEventStore? {
    let store = EKEventStore()
    let done = DispatchSemaphore(value: 0)
    var granted = false
    if #available(macOS 14.0, *) {
        if type == .event {
            store.requestFullAccessToEvents { ok, _ in granted = ok; done.signal() }
        } else {
            store.requestFullAccessToReminders { ok, _ in granted = ok; done.signal() }
        }
    } else {
        store.requestAccess(to: type) { ok, _ in granted = ok; done.signal() }
    }
    _ = done.wait(timeout: .now() + 60)
    return granted ? store : nil
}
func accessError(_ type: EKEntityType) -> [String: Any] {
    let pane = type == .event ? "Calendars" : "Reminders"
    return ["error": "Allow A11y to use " + pane + " in System Settings > Privacy & Security > " + pane + ", then ask again."]
}
if let index = args.firstIndex(of: "--calendar-events"), index+2 < args.count {
    guard let from = Double(args[index+1]), let to = Double(args[index+2]), to > from, to - from <= 62 * 86400 else {
        emit(["error": "Invalid calendar range."]); exit(1)
    }
    guard let store = eventStore(.event) else { emit(accessError(.event)); exit(1) }
    let predicate = store.predicateForEvents(withStart: Date(timeIntervalSince1970: from), end: Date(timeIntervalSince1970: to), calendars: nil)
    let events = store.events(matching: predicate).sorted { $0.startDate < $1.startDate }.prefix(40)
    emit(["events": events.map { event -> [String: Any] in
        ["title": event.title ?? "", "start": event.startDate.timeIntervalSince1970, "end": event.endDate.timeIntervalSince1970,
         "all_day": event.isAllDay, "location": event.location ?? "", "calendar": event.calendar?.title ?? ""]
    }]); exit(0)
}
if args.contains("--reminders") {
    guard let store = eventStore(.reminder) else { emit(accessError(.reminder)); exit(1) }
    let predicate = store.predicateForIncompleteReminders(withDueDateStarting: nil, ending: nil, calendars: nil)
    let done = DispatchSemaphore(value: 0)
    var found: [EKReminder] = []
    store.fetchReminders(matching: predicate) { reminders in found = reminders ?? []; done.signal() }
    _ = done.wait(timeout: .now() + 30)
    emit(["reminders": found.prefix(60).map { reminder -> [String: Any] in
        var item: [String: Any] = ["title": reminder.title ?? "", "list": reminder.calendar?.title ?? "", "id": reminder.calendarItemIdentifier]
        if let due = reminder.dueDateComponents, let date = Calendar.current.date(from: due) { item["due"] = date.timeIntervalSince1970 }
        return item
    }]); exit(0)
}
if let index = args.firstIndex(of: "--add-event"), index+3 < args.count {
    guard let start = Double(args[index+2]), let end = Double(args[index+3]), end > start, !args[index+1].isEmpty else {
        emit(["error": "Invalid event."]); exit(1)
    }
    guard let store = eventStore(.event) else { emit(accessError(.event)); exit(1) }
    let event = EKEvent(eventStore: store)
    event.title = String(args[index+1].prefix(200))
    event.startDate = Date(timeIntervalSince1970: start)
    event.endDate = Date(timeIntervalSince1970: end)
    event.calendar = store.defaultCalendarForNewEvents
    do { try store.save(event, span: .thisEvent, commit: true) } catch {
        emit(["error": "Calendar did not save the event."]); exit(1)
    }
    emit(["submitted": true, "calendar": event.calendar?.title ?? ""]); exit(0)
}
if let index = args.firstIndex(of: "--add-reminder"), index+2 < args.count {
    guard !args[index+1].isEmpty else { emit(["error": "Invalid reminder."]); exit(1) }
    guard let store = eventStore(.reminder) else { emit(accessError(.reminder)); exit(1) }
    let reminder = EKReminder(eventStore: store)
    reminder.title = String(args[index+1].prefix(200))
    reminder.calendar = store.defaultCalendarForNewReminders()
    if let due = Double(args[index+2]), due > 0 {
        reminder.dueDateComponents = Calendar.current.dateComponents([.year, .month, .day, .hour, .minute], from: Date(timeIntervalSince1970: due))
    }
    do { try store.save(reminder, commit: true) } catch {
        emit(["error": "Reminders did not save the reminder."]); exit(1)
    }
    emit(["submitted": true, "list": reminder.calendar?.title ?? ""]); exit(0)
}
if let index = args.firstIndex(of: "--complete-reminder"), index+1 < args.count {
    guard let store = eventStore(.reminder) else { emit(accessError(.reminder)); exit(1) }
    guard let reminder = store.calendarItem(withIdentifier: args[index+1]) as? EKReminder else {
        emit(["error": "That reminder no longer exists."]); exit(1)
    }
    reminder.isCompleted = true
    do { try store.save(reminder, commit: true) } catch {
        emit(["error": "Reminders did not save the change."]); exit(1)
    }
    emit(["submitted": true]); exit(0)
}
if let index = args.firstIndex(of: "--delete-reminder"), index+1 < args.count {
    guard let store = eventStore(.reminder) else { emit(accessError(.reminder)); exit(1) }
    guard let reminder = store.calendarItem(withIdentifier: args[index+1]) as? EKReminder else {
        emit(["error": "That reminder no longer exists."]); exit(1)
    }
    do { try store.remove(reminder, commit: true) } catch {
        emit(["error": "Reminders did not delete the reminder."]); exit(1)
    }
    emit(["submitted": true]); exit(0)
}
if args.contains("--catalog") { emit(["installed_apps": installedApps()]); exit(0) }
if let index = args.firstIndex(of: "--app-state"), index+1 < args.count {
    let bundleID = args[index+1]
    let deadline = Date().addingTimeInterval(1.5)
    var matches: [NSRunningApplication] = []
    repeat {
        // A newly launched app can initially be accessory/background, so do not
        // reuse the regular-app-only guidance inventory as process evidence.
        matches = workspace.runningApplications.filter { $0.bundleIdentifier == bundleID && !$0.isTerminated }
        if !matches.isEmpty { break }
        RunLoop.current.run(until: Date().addingTimeInterval(0.05))
    } while Date() < deadline
    emit(["bundle_id": bundleID, "running": !matches.isEmpty, "active": matches.contains(where: { $0.isActive }),
          "observation": matches.isEmpty ? "not_observed" : "process_observed"]); exit(0)
}
if let index = args.firstIndex(of: "--browse"), index+2 < args.count {
    let bundleID = args[index+1]
    let browsers = ["com.apple.Safari", "com.google.Chrome", "org.mozilla.firefox", "com.microsoft.edgemac"]
    guard browsers.contains(bundleID), installedApps().contains(where: { $0["bundle_id"] == bundleID }),
          let appURL = workspace.urlForApplication(withBundleIdentifier: bundleID),
          let url = URL(string: args[index+2]), ["https", "http"].contains(url.scheme?.lowercased() ?? ""),
          url.host != nil, url.user == nil, url.password == nil else {
        emit(["error": "Only an installed supported browser and a public HTTP(S) address are allowed."]); exit(1)
    }
    let mode = index+3 < args.count ? args[index+3] : "default"
    // "current": the tab the user is looking at (or the window whose title holds the hint), not a new one.
    let hint = index+4 < args.count ? args[index+4] : ""
    // Other hosts an open tab of this site may show (redirects, the www. twin); hostnames only.
    let alsoHosts = (index+5 < args.count ? args[index+5] : "").split(separator: ",").map(String.init).filter {
        !$0.isEmpty && $0.count <= 100 && $0.allSatisfy { $0.isLetter || $0.isNumber || $0 == "." || $0 == "-" }
    }
    guard ["default", "window", "tab", "current"].contains(mode) else {
        emit(["error": "Invalid browser placement."]); exit(1)
    }
    // Reuse an already-open tab for the same site instead of opening another window: the
    // same host for a plain site address, the exact address when it carries a query (a search).
    // Fixed application commands only; addresses are quoted data literals.
    func quoted(_ value: String) -> String {
        return "\"" + value.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"") + "\""
    }
    let exact = url.query != nil
    let sitePrefix = (url.scheme ?? "https") + "://" + (url.host ?? "")
    let chromium = ["com.google.Chrome", "com.microsoft.edgemac"]
    if mode == "current", bundleID == "com.apple.Safari" || chromium.contains(bundleID) {
        let literal = quoted(url.absoluteString)
        let pick = hint.isEmpty ? "" : "repeat with w in windows\nif (name of w) contains " + quoted(hint) + " then\nset index of w to 1\nexit repeat\nend if\nend repeat\n"
        let navigate = bundleID == "com.apple.Safari"
            ? "if (count of windows) is 0 then\nmake new document with properties {URL:" + literal + "}\nelse\n" + pick + "set URL of current tab of front window to " + literal + "\nend if\nactivate\nreturn {URL of current tab of front window, count of windows, count of tabs of front window}"
            : "if (count of windows) is 0 then\nmake new window\nend if\n" + pick + "set URL of active tab of front window to " + literal + "\nactivate\nreturn {URL of active tab of front window, count of windows, count of tabs of front window}"
        var failure: NSDictionary?
        let observation = NSAppleScript(source: "with timeout of 30 seconds\ntell application id \"" + bundleID + "\"\n" + navigate + "\nend tell\nend timeout")?.executeAndReturnError(&failure)
        guard failure == nil, let observation else {
            emit(["error": "The browser did not take the address in its current tab. Nothing else was opened."]); exit(1)
        }
        emit(["submitted": true, "verification_required": true, "tab_placement": "current",
              "browser_observation": ["url": observation.atIndex(1)?.stringValue ?? "", "window_count": observation.atIndex(2)?.int32Value ?? 0,
                                      "tab_count": observation.atIndex(3)?.int32Value ?? 0]]); exit(0)
    }
    if mode == "default", (bundleID == "com.apple.Safari" || chromium.contains(bundleID)),
       !NSRunningApplication.runningApplications(withBundleIdentifier: bundleID).isEmpty {
        let prefixes = [sitePrefix] + alsoHosts.map { (url.scheme ?? "https") + "://" + $0 }
        let test = exact ? "u is " + quoted(url.absoluteString) : prefixes.map { "u starts with " + quoted($0) }.joined(separator: " or ")
        let select = bundleID == "com.apple.Safari" ? "set current tab of w to t" : "set active tab index of w to i"
        let source = "with timeout of 10 seconds\ntell application id \"" + bundleID + "\"\nrepeat with w in windows\nset i to 0\nrepeat with t in tabs of w\nset i to i + 1\nset u to \"\"\ntry\nset u to URL of t\nend try\nif u is missing value then set u to \"\"\nif " + test + " then\n" + select + "\nset index of w to 1\nactivate\nreturn {u, count of windows, count of tabs of w}\nend if\nend repeat\nend repeat\nend tell\nreturn {\"\", 0, 0}\nend timeout"
        var failure: NSDictionary?
        if let script = NSAppleScript(source: source) {
            let found = script.executeAndReturnError(&failure)
            if failure == nil, let existing = found.atIndex(1)?.stringValue, !existing.isEmpty {
                emit(["submitted": true, "verification_required": true, "tab_placement": "existing_tab", "reused_tab": true,
                      "browser_observation": ["url": existing, "window_count": found.atIndex(2)?.int32Value ?? 0,
                                              "tab_count": found.atIndex(3)?.int32Value ?? 0]]); exit(0)
            }
        }
    }
    if bundleID == "com.apple.Safari" {
        // Fixed application commands only. The destination is a quoted data literal,
        // never an LLM-supplied script. No System Events or simulated keystrokes.
        let literal = quoted(url.absoluteString)
        // A new window only when asked for (or none is open); otherwise a tab in the front window.
        let commands = mode == "window"
            ? "make new document with properties {URL:" + literal + "}"
            : "if (count of windows) is 0 then\nmake new document with properties {URL:" + literal + "}\nelse\nset current tab of front window to (make new tab at end of tabs of front window with properties {URL:" + literal + "})\nend if"
        let source = "with timeout of 30 seconds\ntell application id \"com.apple.Safari\"\n" + commands + "\nactivate\nreturn {URL of current tab of front window, count of windows, count of tabs of front window}\nend tell\nend timeout"
        var failure: NSDictionary?
        guard let script = NSAppleScript(source: source) else {
            emit(["error": "Could not prepare Safari navigation."]); exit(1)
        }
        let observation = script.executeAndReturnError(&failure)
        guard failure == nil else {
            let number = failure?[NSAppleScript.errorNumber] as? Int ?? 0
            emit(["error": number == -1743
                ? "Allow Safari control in System Settings > Privacy & Security > Automation, then ask again. No fallback tab was opened."
                : "Safari did not confirm the requested window/tab. Check Safari before retrying; no fallback was attempted."]); exit(1)
        }
        emit(["submitted": true, "verification_required": true, "tab_placement": mode == "tab" ? "tab" : "window",
              "browser_observation": ["url": observation.atIndex(1)?.stringValue ?? "",
                  "window_count": observation.atIndex(2)?.int32Value ?? 0, "tab_count": observation.atIndex(3)?.int32Value ?? 0]]); exit(0)
    }
    guard mode == "default" else {
        emit(["error": "Explicit window/tab placement currently supports Safari. No fallback navigation was sent."]); exit(1)
    }
    var finished = false
    var submitted = false
    workspace.open([url], withApplicationAt: appURL, configuration: NSWorkspace.OpenConfiguration()) { apps, error in
        submitted = error == nil && apps != nil
        finished = true
    }
    let deadline = Date().addingTimeInterval(3)
    while !finished && Date() < deadline { RunLoop.current.run(until: Date().addingTimeInterval(0.02)) }
    emit(["submitted": submitted, "verification_required": true, "tab_placement": "browser_preference"]); exit(submitted ? 0 : 1)
}
if let index = args.firstIndex(of: "--launch"), index+1 < args.count {
    let bundleID = args[index+1]
    guard installedApps().contains(where: { $0["bundle_id"] == bundleID }),
          let url = workspace.urlForApplication(withBundleIdentifier: bundleID) else {
        emit(["error": "That installed application is no longer available."]); exit(1)
    }
    var finished = false
    var opened = false
    workspace.openApplication(at: url, configuration: NSWorkspace.OpenConfiguration()) { app, error in
        opened = app != nil && error == nil
        finished = true
    }
    let deadline = Date().addingTimeInterval(3)
    while !finished && Date() < deadline { RunLoop.current.run(until: Date().addingTimeInterval(0.02)) }
    emit(["submitted": opened, "verification_required": true]); exit(opened ? 0 : 1)
}
if let index = args.firstIndex(of: "--quit"), index+1 < args.count {
    guard let app = running.first(where: { $0.bundleIdentifier == args[index+1] }) else {
        emit(["error": "That application is no longer running."]); exit(1)
    }
    emit(["submitted": app.terminate(), "verification_required": true]); exit(0)
}
if args.contains("--request-access") {
    let trusted = AXIsProcessTrustedWithOptions([kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary)
    emit(["accessibility": trusted ? "granted" : "permission_required"]); exit(0)
}
var output: [String: Any] = [
    "platform": "macOS", "accessibility": AXIsProcessTrusted() ? "granted" : "permission_required",
    "installed_apps": installedApps(),
    "available_apps": running.prefix(25).map { ["name": $0.localizedName ?? "Application", "bundle_id": $0.bundleIdentifier!] }
]
// Window-server inventory includes unfocused windows. This is geometry, not pixels:
// windows may overlap, and absence from this bounded list proves nothing.
if let windows = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] {
    var candidates: [[String: Any]] = []
    for window in windows {
        guard candidates.count < 40,
              let pid = window[kCGWindowOwnerPID as String] as? Int32,
              let owner = running.first(where: { $0.processIdentifier == pid }),
              (window[kCGWindowLayer as String] as? Int) == 0,
              let rect = window[kCGWindowBounds as String] as? [String: Any],
              let x = rect["X"] as? Double, let y = rect["Y"] as? Double,
              let width = rect["Width"] as? Double, let height = rect["Height"] as? Double,
              width > 1, height > 1 else { continue }
        candidates.append(["app": owner.localizedName ?? "Application", "bundle_id": owner.bundleIdentifier!,
            "bounds": ["x": x, "y": y, "width": width, "height": height]])
    }
    output["onscreen_window_candidates"] = candidates
}
// Every normal window, including unfocused ones, gets its own close target.
var appWindows: [[String: Any]] = []
if AXIsProcessTrusted() {
    let started = CFAbsoluteTimeGetCurrent()
    for owner in running {
        if appWindows.count >= 35 || CFAbsoluteTimeGetCurrent()-started > 1 { break }
        let node = AXUIElementCreateApplication(owner.processIdentifier)
        AXUIElementSetMessagingTimeout(node, 0.1)
        for (index, window) in ((attr(node, "AXWindows") as? [AXUIElement]) ?? []).prefix(12).enumerated() {
            guard let rect = bounds(window) else { continue }
            let close = element(attr(window, "AXCloseButton"))
            appWindows.append(["window_id": "\(owner.bundleIdentifier!):\(index)",
                "bundle_id": owner.bundleIdentifier!, "name": owner.localizedName ?? "Application",
                "title": string(window, "AXTitle"), "bounds": rect,
                "can_close": close != nil && ((close.flatMap { attr($0, "AXEnabled") } as? Bool) ?? true)])
        }
    }
}
output["app_windows"] = appWindows
if let index = args.firstIndex(of: "--close-window"), index+4 < args.count {
    guard AXIsProcessTrusted(),
          let owner = running.first(where: { $0.bundleIdentifier == args[index+1] }),
          let ordinal = Int(args[index+2]), ordinal >= 0,
          let data = args[index+4].data(using: .utf8),
          let expected = (try? JSONSerialization.jsonObject(with: data)) as? [String: Double] else {
        emit(["error": "Accessibility permission and an observed window are required."]); exit(1)
    }
    let app = AXUIElementCreateApplication(owner.processIdentifier)
    AXUIElementSetMessagingTimeout(app, 0.2)
    let windows = (attr(app, "AXWindows") as? [AXUIElement]) ?? []
    guard ordinal < windows.count, let actual = bounds(windows[ordinal]),
          string(windows[ordinal], "AXTitle") == args[index+3],
          ["x", "y", "width", "height"].allSatisfy({
              guard let a = actual[$0], let e = expected[$0], e.isFinite else { return false }
              return abs(a-e) <= 1
          }), let close = element(attr(windows[ordinal], "AXCloseButton")),
          (attr(close, "AXEnabled") as? Bool) != false else {
        emit(["error": "That window changed or cannot be closed. Request a fresh observation."]); exit(1)
    }
    AXUIElementSetMessagingTimeout(close, 1)
    let status = AXUIElementPerformAction(close, kAXPressAction as CFString)
    emit(["submitted": status == .success, "verification_required": true]); exit(0)
}
if let index = args.firstIndex(of: "--menu-items"), index+1 < args.count {
    // Every enabled menu item of a running app as "Menu > Item" (one submenu level more), names only:
    // so "where can I see the sites I visited" can find History > Show All History. Read only.
    guard AXIsProcessTrusted(), let owner = running.first(where: { $0.bundleIdentifier == args[index+1] }) else {
        emit(["error": "That app is not open."]); exit(1)
    }
    let app = AXUIElementCreateApplication(owner.processIdentifier)
    AXUIElementSetMessagingTimeout(app, 0.5)
    guard let bar = element(attr(app, "AXMenuBar")) else { emit(["items": []]); exit(0) }
    func entries(_ menuTitle: AXUIElement) -> [AXUIElement] {
        ((attr(menuTitle, "AXChildren") as? [AXUIElement])?.first).flatMap { attr($0, "AXChildren") as? [AXUIElement] } ?? []
    }
    var paths: [String] = []
    let started = CFAbsoluteTimeGetCurrent()
    for top in ((attr(bar, "AXChildren") as? [AXUIElement]) ?? []) {  // the Apple menu too: About This Mac, System Settings…
        let menu = string(top, "AXTitle")
        guard !menu.isEmpty else { continue }
        for item in entries(top) where paths.count < 400 && CFAbsoluteTimeGetCurrent()-started < 2 {
            let title = string(item, "AXTitle")
            guard !title.isEmpty, (attr(item, "AXEnabled") as? Bool) != false else { continue }
            let sub = entries(item)
            if sub.isEmpty {
                paths.append(menu + " > " + title)
            } else {
                for leaf in sub.prefix(40) where !string(leaf, "AXTitle").isEmpty && (attr(leaf, "AXEnabled") as? Bool) != false {
                    paths.append(menu + " > " + title + " > " + string(leaf, "AXTitle"))
                }
            }
        }
    }
    emit(["items": paths]); exit(0)
}
if let index = args.firstIndex(of: "--menu"), index+2 < args.count {
    // choose_menu: press a menu item by its titles ("View > as List"), through accessibility.
    guard AXIsProcessTrusted(), let owner = running.first(where: { $0.bundleIdentifier == args[index+1] }) else {
        emit(["error": "That app is not open."]); exit(1)
    }
    let app = AXUIElementCreateApplication(owner.processIdentifier)
    AXUIElementSetMessagingTimeout(app, 1)
    func clean(_ title: String) -> String {
        title.lowercased().replacingOccurrences(of: "…", with: "").replacingOccurrences(of: "...", with: "").trimmingCharacters(in: .whitespaces)
    }
    guard let bar = element(attr(app, "AXMenuBar")) else { emit(["error": "That app has no menu bar."]); exit(1) }
    // "View > Sort By > Date Added | View > Group By > Date Added": the first path that exists wins
    // (Finder has no "Sort By" while it groups items).
    func resolve(_ text: String) -> (AXUIElement, [String], Int)? {
        let path = text.components(separatedBy: ">").map { clean($0) }.filter { !$0.isEmpty }
        guard !path.isEmpty else { return nil }
        var level = bar
        var titles: [String] = []
        for (depth, wanted) in path.enumerated() {
            let items = depth == 0 ? ((attr(level, "AXChildren") as? [AXUIElement]) ?? [])
                : (((attr(level, "AXChildren") as? [AXUIElement])?.first).flatMap { attr($0, "AXChildren") as? [AXUIElement] } ?? [])
            guard let match = items.first(where: { !string($0, "AXTitle").isEmpty && clean(string($0, "AXTitle")) == wanted }),
                  (attr(match, "AXEnabled") as? Bool) != false else { return nil }
            titles.append(string(match, "AXTitle"))
            level = match
        }
        return (level, titles, path.count)
    }
    let choices = args[index+2].components(separatedBy: "|")
    guard let (found, titles, depth) = choices.lazy.compactMap({ resolve($0) }).first else {
        let first = choices[0].components(separatedBy: ">").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        var level = bar
        var named: [String] = []
        for (depth, wanted) in first.enumerated() {  // say which item is missing, as before
            let items = depth == 0 ? ((attr(level, "AXChildren") as? [AXUIElement]) ?? [])
                : (((attr(level, "AXChildren") as? [AXUIElement])?.first).flatMap { attr($0, "AXChildren") as? [AXUIElement] } ?? [])
            named = items.map { string($0, "AXTitle") }.filter { !$0.isEmpty }
            guard let next = items.first(where: { clean(string($0, "AXTitle")) == clean(wanted) }) else {
                emit(["error": "No menu item “" + wanted + "”. Available: " + named.prefix(30).joined(separator: ", ")]); exit(1)
            }
            level = next
        }
        emit(["error": "That menu item is not available right now."]); exit(1)
    }
    let level = found
    let path = Array(repeating: "", count: depth)
    owner.activate(options: [])
    AXUIElementSetMessagingTimeout(level, 1)
    if path.count == 1 {
        // Just the menu: open it so its items show.
        let status = AXUIElementPerformAction(level, kAXPressAction as CFString)
        emit(["submitted": status == .success, "path": titles.joined(separator: " > ")]); exit(0)
    }
    let status = AXUIElementPerformAction(level, kAXPressAction as CFString)
    emit(["submitted": status == .success, "path": titles.joined(separator: " > ")]); exit(0)
}
if let index = args.firstIndex(of: "--close-tab"), index+1 < args.count {
    // The tab in front, not its whole window. Fixed commands; no page content is read.
    let bundleID = args[index+1]
    let command = ["com.apple.Safari": "close current tab of front window", "com.google.Chrome": "close active tab of front window",
                   "com.microsoft.edgemac": "close active tab of front window"][bundleID]
    guard let command, !NSRunningApplication.runningApplications(withBundleIdentifier: bundleID).isEmpty else {
        emit(["error": "That browser is not open."]); exit(1)
    }
    var failure: NSDictionary?
    _ = NSAppleScript(source: "with timeout of 10 seconds\ntell application id \"" + bundleID + "\"\nif (count of windows) > 0 then " + command + "\nend tell\nend timeout")?.executeAndReturnError(&failure)
    emit(failure == nil ? ["submitted": true, "verification_required": true] : ["error": "The browser did not close the tab."]); exit(failure == nil ? 0 : 1)
}
if let index = args.firstIndex(of: "--new-window"), index+1 < args.count {
    if args[index+1] == "com.apple.Safari" {
        var failure: NSDictionary?
        let script = NSAppleScript(source: "with timeout of 30 seconds\ntell application id \"com.apple.Safari\"\nmake new document\nactivate\nend tell\nend timeout")
        _ = script?.executeAndReturnError(&failure)
        guard script != nil, failure == nil else {
            emit(["error": "Could not open a Safari window. Check macOS Automation permission for Safari before retrying."]); exit(1)
        }
        emit(["submitted": true, "verification_required": true]); exit(0)
    }
    // Any running app's one enabled ⌘N item (New Window, New Document, New Note): an empty window, no
    // data changed. "In a new TextEdit document" wrote into the open one when only Terminal had this (live).
    guard AXIsProcessTrusted(), let owner = running.first(where: { $0.bundleIdentifier == args[index+1] }) else {
        emit(["error": "New-window automation needs the app running and Accessibility permission."]); exit(1)
    }
    let app = AXUIElementCreateApplication(owner.processIdentifier)
    AXUIElementSetMessagingTimeout(app, 0.1)
    var queue: [(AXUIElement, Int)] = element(attr(app, "AXMenuBar")).map { [($0, 0)] } ?? []
    var matches: [AXUIElement] = []
    var visited = 0
    let started = CFAbsoluteTimeGetCurrent()
    while !queue.isEmpty && visited < 300 && CFAbsoluteTimeGetCurrent()-started < 1.5 {
        let (node, depth) = queue.removeFirst()
        visited += 1
        if string(node, "AXRole") == "AXMenuItem", string(node, "AXMenuItemCmdChar").lowercased() == "n",
           (attr(node, "AXMenuItemCmdModifiers") as? Int) == 0, (attr(node, "AXEnabled") as? Bool) != false {
            matches.append(node)
        }
        if depth < 5, let children = attr(node, "AXChildren") as? [AXUIElement] {
            queue.append(contentsOf: children.prefix(50).map { ($0, depth+1) })
        }
    }
    guard matches.count == 1 else {
        emit(["error": "The app did not expose one unambiguous Command-N menu action. Open its New Window menu manually."]); exit(1)
    }
    guard owner.activate(options: []) else { emit(["error": "The app could not be activated."]); exit(1) }
    AXUIElementSetMessagingTimeout(matches[0], 1)
    let status = AXUIElementPerformAction(matches[0], kAXPressAction as CFString)
    emit(["submitted": status == .success, "verification_required": true]); exit(0)
}
let pressIndex = args.firstIndex(of: "--press")
var observedApp = workspace.frontmostApplication
if let index = args.firstIndex(of: "--observe-app"), index+1 < args.count {
    observedApp = running.first(where: { $0.bundleIdentifier == args[index+1] })
}
if let index = pressIndex {
    guard index+5 < args.count,
          let app = running.first(where: { $0.bundleIdentifier == args[index+1] }) else {
        emit(["error": "The requested application is no longer available."]); exit(1)
    }
    observedApp = app
}
guard let front = observedApp else {
    output["status"] = "unavailable"; emit(output); exit(0)
}
if front.processIdentifier == ownPID {
    output["status"] = "companion_focused"; emit(output); exit(0)
}
if deniedBundles.contains(front.bundleIdentifier ?? "") {
    output["status"] = "private_app"; emit(output); exit(0)
}
output["status"] = "available"
output["app"] = ["name": front.localizedName ?? "Application", "bundle_id": front.bundleIdentifier ?? "", "pid": front.processIdentifier]
guard AXIsProcessTrusted() else { emit(output); exit(0) }
let app = AXUIElementCreateApplication(front.processIdentifier)
AXUIElementSetMessagingTimeout(app, 0.25)
let window = element(attr(app, "AXFocusedWindow"))
if let window {
    var info: [String: Any] = ["title": string(window, "AXTitle"), "bounds": bounds(window) ?? [:]]
    for key in ["AXDocument", "AXURL"] {
        let value = attr(window, key)
        let text = (value as? URL)?.absoluteString ?? (value as? String ?? "")
        if !text.isEmpty { info[key == "AXDocument" ? "document" : "url"] = String(text.prefix(1024)) }
    }
    output["window"] = info
}
let actionable: Set<String> = ["AXButton", "AXCheckBox", "AXRadioButton", "AXPopUpButton", "AXMenuButton", "AXTab", "AXLink", "AXDisclosureTriangle", "AXMenuItem"]
func descriptor(_ node: AXUIElement, index: Int) -> [String: Any]? {
    let role = string(node, "AXRole")
    let subrole = string(node, "AXSubrole")
    if subrole == "AXSecureTextField" || role == "AXTextField" || role == "AXTextArea" || role == "AXStaticText" { return nil }
    guard actionable.contains(role) else { return nil }
    let title = string(node, "AXTitle")
    let label = title.isEmpty ? string(node, "AXDescription") : title
    guard !label.isEmpty, let rect = bounds(node) else { return nil }
    return ["id": "control-\(index)", "role": role, "label": label, "bounds": rect,
            "enabled": (attr(node, "AXEnabled") as? Bool) ?? true]
}
if let focused = element(attr(app, "AXFocusedUIElement")) {
    // Read only the focused element and its selected children; never secure input.
    let role = string(focused, "AXRole")
    output["focused_role"] = (string(focused, "AXSubrole") == "AXSecureTextField" || role == "AXSecureTextField") ? "secure_input" : role
    if string(focused, "AXSubrole") != "AXSecureTextField" && role != "AXSecureTextField" {
        output["focused_bounds"] = bounds(focused) ?? [:]
        output["focused_container_roles"] = containerRoles(focused)
        func readable(_ node: AXUIElement) -> [String: String]? {
            if string(node, "AXSubrole") == "AXSecureTextField" || string(node, "AXRole") == "AXSecureTextField" { return nil }
            var result = ["role": string(node, "AXRole")]
            for (key, attribute) in [("name", "AXTitle"), ("value", "AXValue"), ("text", "AXSelectedText")] {
                let value = string(node, attribute)
                if !value.isEmpty { result[key] = String(value.prefix(512)) }
            }
            if result["name"] == nil {
                let label = string(node, "AXDescription")
                if !label.isEmpty { result["name"] = String(label.prefix(512)) }
            }
            return result.count > 1 ? result : nil
        }
        if let value = readable(focused) { output["focused"] = value }
        let selected = (attr(focused, "AXSelectedChildren") as? [AXUIElement] ?? []).prefix(3).compactMap { readable($0) }
        if !selected.isEmpty { output["selected"] = selected }
    }
}
var queue: [(AXUIElement, Int)] = window.map { [($0, 0)] } ?? []
var controls: [[String: Any]] = []
var controlNodes: [AXUIElement] = []
var visited = 0
let traversalStarted = CFAbsoluteTimeGetCurrent()
while !queue.isEmpty && visited < 220 && controls.count < 45 && CFAbsoluteTimeGetCurrent()-traversalStarted < 1.5 {
    let (node, depth) = queue.removeFirst()
    visited += 1
    if let description = descriptor(node, index: controls.count) {
        controls.append(description)
        controlNodes.append(node)
    }
    if depth >= 7 || string(node, "AXSubrole") == "AXSecureTextField" { continue }
    if let children = attr(node, "AXChildren") as? [AXUIElement] {
        queue.append(contentsOf: children.prefix(40).map { ($0, depth+1) })
    }
}
output["controls"] = controls
// Lists and tables (Mail's messages, Finder's files): where their visible rows are, so "click the
// first email" is a row position rather than a guess at pixels. Row contents are not included.
if pressIndex == nil, let window {
    var lists: [[String: Any]] = []
    var pending: [(AXUIElement, Int)] = [(window, 0)]
    var seen = 0
    let listStarted = CFAbsoluteTimeGetCurrent()
    while !pending.isEmpty && seen < 300 && lists.count < 3 && CFAbsoluteTimeGetCurrent()-listStarted < 0.5 {
        let (node, depth) = pending.removeFirst()
        seen += 1
        let role = string(node, "AXRole")
        if ["AXTable", "AXOutline", "AXList"].contains(role) {
            let rows = (attr(node, "AXVisibleRows") as? [AXUIElement]) ?? []
            let frames = rows.prefix(12).compactMap { bounds($0) }
            if let frame = bounds(node), !frames.isEmpty {
                lists.append(["role": role, "label": string(node, "AXDescription"), "bounds": frame, "rows": frames])
            }
            continue  // rows are not walked: a long list would use up the budget
        }
        if depth < 6, let children = attr(node, "AXChildren") as? [AXUIElement] {
            pending.append(contentsOf: children.prefix(40).map { ($0, depth+1) })
        }
    }
    if !lists.isEmpty { output["lists"] = lists }
}
// --targets (computer-task steps): everything in the window a person could click, web pages
// included, named as assistive technology reads it (title, description, help, a field's
// placeholder; never its value). Depth-first, so targets come in reading order.
if pressIndex == nil, args.contains("--targets"), let window, let frame = bounds(window) {
    // Electron apps (Teams, Slack, VS Code) build their accessibility tree only when asked.
    AXUIElementSetAttributeValue(app, "AXManualAccessibility" as CFString, kCFBooleanTrue)
    let windowRect = CGRect(x: frame["x"]!, y: frame["y"]!, width: frame["width"]!, height: frame["height"]!)
    let clickable: Set<String> = ["AXButton", "AXLink", "AXCheckBox", "AXRadioButton", "AXPopUpButton", "AXMenuButton", "AXTab",
        "AXDisclosureTriangle", "AXMenuItem", "AXTextField", "AXTextArea", "AXSearchField", "AXComboBox", "AXSlider", "AXSwitch", "AXCell", "AXRow"]
    let fields: Set<String> = ["AXTextField", "AXTextArea", "AXSearchField", "AXComboBox"]
    var targets: [[String: Any]] = []
    var pageURL: String? = nil  // Safari's window has no AXURL; its web area does
    var stack: [(AXUIElement, Int, Bool)] = [(window, 0, false)]  // the Bool: inside a web page
    var seen = 0
    let started = CFAbsoluteTimeGetCurrent()
    while !stack.isEmpty && seen < 3000 && targets.count < 150 && CFAbsoluteTimeGetCurrent()-started < 0.6 {
        let (node, depth, inPage) = stack.removeLast()
        seen += 1
        let box = bounds(node).map { CGRect(x: $0["x"]!, y: $0["y"]!, width: $0["width"]!, height: $0["height"]!) }
        if let box, !box.intersects(windowRect) { continue }  // the off-screen part of a page
        if string(node, "AXSubrole") == "AXSecureTextField" { continue }
        let role = string(node, "AXRole")
        if role == "AXWebArea", pageURL == nil, let url = attr(node, "AXURL") as? URL { pageURL = String(url.absoluteString.prefix(1024)) }
        // Greyed-out controls are not offered: the model could not tell them from ones that work (owner).
        let filename = front.bundleIdentifier == "com.apple.finder" && (role == "AXTextField" || role == "AXStaticText") && finderItem(node)
        if (clickable.contains(role) || filename), let box, box.width >= 4, box.height >= 4, windowRect.contains(CGPoint(x: box.midX, y: box.midY)),
           (attr(node, "AXEnabled") as? Bool) != false {
            var label = string(node, "AXTitle")
            if label.isEmpty { label = string(node, "AXDescription") }
            if label.isEmpty { label = string(node, "AXHelp") }
            if label.isEmpty && fields.contains(role) { label = string(node, "AXPlaceholderValue") }
            if label.isEmpty && filename { label = string(node, "AXValue") }  // exact Finder item name only
            if !label.isEmpty {
                var target: [String: Any] = ["role": role, "label": String(label.prefix(80)), "page": inPage,
                                             "bounds": ["x": box.minX, "y": box.minY, "width": box.width, "height": box.height]]
                if filename { target["file_item"] = true }
                // Which tab or option is chosen (a state, not a value): Activity Monitor's Memory tab was
                // clicked and nothing could tell it had switched (live).
                if role == "AXRadioButton" || role == "AXTab", let chosen = attr(node, "AXValue") as? Int { target["selected"] = chosen == 1 }
                targets.append(target)
                if role != "AXRow" && role != "AXCell" { continue }  // a named button's parts are not separate targets
            }
        }
        if depth < 60, let children = attr(node, "AXChildren") as? [AXUIElement] {
            let page = inPage || role == "AXWebArea"
            stack.append(contentsOf: children.prefix(300).reversed().map { ($0, depth+1, page) })
        }
    }
    output["targets"] = targets
    if let pageURL, var info = output["window"] as? [String: Any], info["url"] == nil {
        info["url"] = pageURL
        output["window"] = info
    }
    // The app's menu bar titles (File, Edit, View…), for choose_menu.
    if let bar = element(attr(app, "AXMenuBar")) {
        output["menus"] = ((attr(bar, "AXChildren") as? [AXUIElement]) ?? []).map { string($0, "AXTitle") }.filter { !$0.isEmpty }.dropFirst().map { $0 }
    }
}
if let index = pressIndex {
    let controlID = args[index+2]
    let expectedLabel = args[index+3]
    let expectedTitle = args[index+5]
    guard let position = controls.firstIndex(where: { ($0["id"] as? String) == controlID }),
          let actualBounds = controls[position]["bounds"] as? [String: Double],
          let expectedData = args[index+4].data(using: .utf8),
          let expectedBounds = (try? JSONSerialization.jsonObject(with: expectedData)) as? [String: Double],
          controls[position]["label"] as? String == expectedLabel,
          controls[position]["enabled"] as? Bool == true,
          let window, string(window, "AXTitle") == expectedTitle,
          ["x", "y", "width", "height"].allSatisfy({
              guard let actual = actualBounds[$0], let expected = expectedBounds[$0], expected.isFinite else { return false }
              return abs(actual-expected) <= 1
          }) else {
        emit(["error": "The control changed. Request a fresh observation before pressing it."]); exit(1)
    }
    guard front.activate(options: []) else {
        emit(["error": "The application could not be brought forward."]); exit(1)
    }
    AXUIElementSetMessagingTimeout(controlNodes[position], 1)
    let status = AXUIElementPerformAction(controlNodes[position], kAXPressAction as CFString)
    // A timeout may occur after an app starts handling an action. Never retry it.
    guard status == .success else {
        emit(["error": "The press result is uncertain. Check the app before trying again."]); exit(1)
    }
    emit(["submitted": true, "verification_required": true]); exit(0)
}
emit(output)
