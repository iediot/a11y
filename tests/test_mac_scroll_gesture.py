"""Exercise production scroll packets through CoreGraphics/AppKit without posting input."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('xcrun'),
                     'CoreGraphics event inspection is macOS-only')
class MacScrollGestureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        folder = Path(cls.temporary.name)
        native = (Path(__file__).resolve().parents[1]/'native'/'DesktopBridge.swift').read_text()
        # Compile the production packet builder alone: the desktop helper's top-level
        # workspace enumeration and input execution must never run in these tests.
        packet = 'func scrollDistance('+native.split('func scrollDistance(', 1)[1].split('\nfunc attr(', 1)[0]
        harness = '''
var rows: [[String: Any]] = []
for direction in ["up", "down"] {
    for phase in [CGScrollPhase.began, .changed, .ended] {
        guard let event = scrollPacket(direction: direction, phase: phase, location: CGPoint(x: 321, y: 234)),
              let appEvent = NSEvent(cgEvent: event) else { fatalError("Missing packet") }
        rows.append(["direction": direction,
                     "cg_phase": event.getIntegerValueField(.scrollWheelEventScrollPhase),
                     "appkit_phase": appEvent.phase.rawValue,
                     "continuous": event.getIntegerValueField(.scrollWheelEventIsContinuous),
                     "vertical": event.getIntegerValueField(.scrollWheelEventPointDeltaAxis1),
                     "horizontal": event.getIntegerValueField(.scrollWheelEventPointDeltaAxis2),
                     "x": event.location.x, "y": event.location.y])
    }
}
let invalidDirection = scrollPacket(direction: "sideways", phase: .changed, location: .zero) == nil
let invalidPhase = scrollPacket(direction: "down", phase: .cancelled, location: .zero) == nil
let invalidHeights = [0.0, -1.0, Double.nan, Double.infinity].allSatisfy { scrollDistance(windowHeight: $0) == nil }
let invalidDistances: [Int32] = [0, -1, 481, Int32.max]
let rejectedDistances = invalidDistances.allSatisfy { scrollPacket(direction: "down", phase: .changed, location: .zero, distance: $0) == nil }
var sizes: [[String: Any]] = []
for height in [120.0, 460.0, 800.0, 2160.0] {
    let distance = scrollDistance(windowHeight: height)!
    let event = scrollPacket(direction: "down", phase: .changed, location: .zero, distance: distance)!
    sizes.append(["height": height, "distance": distance,
                  "vertical": event.getIntegerValueField(.scrollWheelEventPointDeltaAxis1)])
}
let data = try JSONSerialization.data(withJSONObject: ["rows": rows,
    "invalid_direction_rejected": invalidDirection, "invalid_phase_rejected": invalidPhase,
    "invalid_heights_rejected": invalidHeights, "invalid_distances_rejected": rejectedDistances,
    "sizes": sizes])
print(String(decoding: data, as: UTF8.self))
'''
        source = folder/'packets.swift'
        source.write_text('import AppKit\nimport CoreGraphics\nimport Foundation\n'+packet+harness)
        result = subprocess.run(['xcrun', 'swiftc', '-module-cache-path', str(folder/'cache'),
                                 str(source), '-o', str(folder/'packets')],
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise RuntimeError(result.stderr[-3000:])
        result = subprocess.run([str(folder/'packets')], capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise RuntimeError(result.stderr[-3000:])
        cls.report = json.loads(result.stdout)

    def test_actual_appkit_phases_are_began_changed_ended(self):
        for direction in ('up', 'down'):
            rows = [r for r in self.report['rows'] if r['direction'] == direction]
            self.assertEqual([r['cg_phase'] for r in rows], [1, 2, 4])
            self.assertEqual([r['appkit_phase'] for r in rows], [1, 4, 8])

    def test_only_changed_packet_moves_and_directions_are_opposite(self):
        for direction, delta in (('up', 60), ('down', -60)):
            rows = [r for r in self.report['rows'] if r['direction'] == direction]
            self.assertEqual([r['vertical'] for r in rows], [0, delta, 0])

    def test_packets_are_continuous_with_no_horizontal_scroll(self):
        for row in self.report['rows']:
            self.assertEqual(row['continuous'], 1)
            self.assertEqual(row['horizontal'], 0)

    def test_each_phase_keeps_the_validated_destination(self):
        for row in self.report['rows']:
            self.assertEqual((row['x'], row['y']), (321, 234))

    def test_invalid_direction_or_phase_cannot_create_input(self):
        self.assertTrue(self.report['invalid_direction_rejected'])
        self.assertTrue(self.report['invalid_phase_rejected'])
        self.assertTrue(self.report['invalid_heights_rejected'])
        self.assertTrue(self.report['invalid_distances_rejected'])

    def test_distance_scales_with_window_and_preserves_overlap(self):
        sizes = self.report['sizes']
        for row in sizes:
            self.assertGreater(row['distance'], 0)
            self.assertLess(row['distance'], row['height'])
            self.assertLessEqual(row['distance'], 480)
        self.assertLess(sizes[0]['distance'], sizes[1]['distance'])
        self.assertLess(sizes[1]['distance'], sizes[2]['distance'])

    def test_viewport_distance_reaches_actual_pixel_event(self):
        for row in self.report['sizes']:
            self.assertEqual(row['vertical'], -row['distance'])


if __name__ == '__main__':
    unittest.main()
