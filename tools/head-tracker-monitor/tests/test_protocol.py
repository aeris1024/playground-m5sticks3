import math
import unittest

from pythonosc.osc_message import OscMessage
from pythonosc.osc_message_builder import OscMessageBuilder

from head_tracker_monitor.orientation import Quaternion
from head_tracker_monitor.protocol import (CALIBRATING, IMU_ERROR, MAX_COUNTER, READY,
                                          Pose, decode_pose, encode_pose, encode_scene)
from head_tracker_monitor.tracker import Tracker


def pose(sequence=0, q=Quaternion(), session="0123456789abcdef", recenter=0, state=READY):
    return Pose(session, sequence, state, recenter, q)


class OrientationTests(unittest.TestCase):
    def assertRotation(self, first, second):
        for vector in ((1., 0., 0.), (0., 1., 0.), (0., 0., 1.)):
            for actual, expected in zip(first.rotate(vector), second.rotate(vector)):
                self.assertAlmostEqual(actual, expected, places=5)

    def test_axes(self):
        for angles, vector, expected in (
            ((90, 0, 0), (1, 0, 0), (0, 1, 0)),
            ((0, 90, 0), (1, 0, 0), (0, 0, -1)),
            ((0, 0, 90), (0, 1, 0), (0, 0, 1)),
        ):
            for actual, value in zip(Quaternion.from_ypr(*angles).rotate(vector), expected):
                self.assertAlmostEqual(actual, value)

    def test_composite_and_inverse(self):
        q = Quaternion.from_ypr(80, 35, -20)
        self.assertRotation(q.inverse()*q, Quaternion())
        self.assertRotation(q, Quaternion(*(-v for v in q.values())))
        for actual, expected in zip(q.ypr(), (80, 35, -20)):
            self.assertAlmostEqual(actual, expected)

    def test_relative_and_recenter(self):
        tracker = Tracker()
        reference = Quaternion.from_ypr(75, -20, 13)
        delta = Quaternion.from_ypr(-35, 15, 45)
        tracker.accept(pose(0, reference), 0)
        tracker.accept(pose(1, reference*delta), .02)
        self.assertRotation(tracker.relative(), delta)
        outgoing = OscMessage(encode_scene(tracker.relative().inverse()))
        self.assertIn(b",ffff\0", encode_scene(tracker.relative().inverse()))
        self.assertEqual(outgoing.address, "/SceneRotator/quaternions")
        self.assertTrue(all(type(v) is float for v in outgoing.params))
        self.assertRotation(Quaternion(*outgoing.params)*delta, Quaternion())
        tracker.recenter()
        self.assertRotation(tracker.relative(), Quaternion())
        tracker.accept(pose(2, reference, recenter=1), .04)
        self.assertRotation(tracker.relative(), Quaternion())


class ProtocolTests(unittest.TestCase):
    def test_roundtrip_and_types(self):
        original = pose(123, Quaternion.from_ypr(45, 20, 15), recenter=4)
        decoded = decode_pose(encode_pose(original))
        self.assertEqual((decoded.session, decoded.sequence, decoded.state, decoded.recenter),
                         (original.session, 123, READY, 4))
        self.assertIn(b",siiiffff\0", encode_pose(original))

    def test_invalid_packets(self):
        invalid = [b"", b"invalid", b"#bundle\0"+b"\0"*20,
                   encode_pose(pose())[:-1], b"x"*513]
        for q in (Quaternion(0, 0, 0, 0), Quaternion(float("nan")), Quaternion(float("inf"))):
            invalid.append(encode_pose(pose(q=q)))
        invalid.extend((encode_pose(pose(session="bad")), encode_pose(pose(state=9)),
                        encode_pose(pose(sequence=-1))))
        message = OscMessageBuilder("/headtracker/pose")
        for value in ("0123456789abcdef", 1, READY, 0, 1, 0, 0, 0):
            message.add_arg(value)
        invalid.append(message.build().dgram)
        for data in invalid:
            with self.subTest(data=data[:20]), self.assertRaises(ValueError):
                decode_pose(data)

    def test_reorder_wrap_and_sessions(self):
        tracker = Tracker()
        self.assertTrue(tracker.accept(pose(MAX_COUNTER), 0))
        self.assertTrue(tracker.accept(pose(0), .02))
        self.assertFalse(tracker.accept(pose(MAX_COUNTER), .03))
        self.assertFalse(tracker.accept(pose(0), .04))
        self.assertTrue(tracker.accept(pose(1, session="fedcba9876543210"), .05))
        self.assertFalse(tracker.accept(pose(2), .06))
        self.assertEqual(tracker.session, "fedcba9876543210")

    def test_timeout_resume_and_calibration(self):
        tracker = Tracker()
        tracker.accept(pose(0), 0)
        tracker.accept(pose(1), .02)
        self.assertAlmostEqual(tracker.rate(.02), 50)
        self.assertTrue(tracker.ready(.519))
        self.assertFalse(tracker.ready(.52))
        tracker.accept(pose(2, Quaternion.from_ypr(30, 0, 0)), 1.)
        self.assertTrue(tracker.ready(1.))
        self.assertAlmostEqual(tracker.relative().ypr()[0], 30)
        tracker.accept(pose(3, state=CALIBRATING), 1.02)
        self.assertTrue(tracker.connected(1.02))
        self.assertFalse(tracker.ready(1.02))
        tracker.accept(pose(4, Quaternion.from_ypr(30, 0, 0)), 1.04)
        self.assertAlmostEqual(tracker.relative().ypr()[0], 0)
        tracker.accept(pose(5, state=IMU_ERROR), 1.06)
        self.assertFalse(tracker.ready(1.06))


if __name__ == "__main__":
    unittest.main()
