import json
import socket
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QSettings
from pythonosc.osc_message import OscMessage
import serial

from head_tracker_monitor.app import MainWindow
from head_tracker_monitor.network import Bridge
from head_tracker_monitor.orientation import Quaternion
from head_tracker_monitor.protocol import Pose, READY, decode_usb_pose
from head_tracker_monitor.usb import JsonLines, UsbStreamWorker
from test_bridge import pump


def message(sequence=0, **changes):
    return {"type": "pose", "session": "0123456789abcdef", "sequence": sequence,
            "state": READY, "recenter": 0, "quaternion": [1, 0, 0, 0], **changes}


class FakeDevice:
    """A serial peer with interleaved replies/poses and a removable cable."""

    def __init__(self):
        self.connections = []
        self.writes = []
        self.unplug = False

    def serial(self, **kwargs):
        device = self

        class Connection:
            def __init__(self):
                self.buffer = bytearray()
                self.streaming = False
                self.sequence = 0
                self.next_pose = 0
                self.closed = False
                device.connections.append(self)

            def __enter__(self): return self
            def __exit__(self, *args): self.closed = True
            def open(self):
                assert self.dtr is True and self.rts is False
            def reset_input_buffer(self): self.buffer.clear()

            @property
            def in_waiting(self): return len(self.buffer)

            def write(self, data):
                request = json.loads(data)
                device.writes.append((request["cmd"], request.get("enabled")))
                if request["cmd"] == "stream":
                    self.streaming = request["enabled"]
                # Malformed IDs must be ignored without stopping the worker.
                self.buffer.extend(b'{"id":[]}\n')
                self.buffer.extend((json.dumps({"id": request["id"], "ok": True,
                    "usb_stream": self.streaming, "password": "must-not-reach-gui",
                    "receiver_ip": "192.168.1.10"})+"\n").encode())

            def read(self, size):
                if device.unplug:
                    device.unplug = False
                    raise serial.SerialException("test cable removal")
                if self.streaming and time.monotonic() >= self.next_pose:
                    self.buffer.extend((json.dumps(message(self.sequence))+"\n").encode())
                    self.sequence += 1
                    self.next_pose = time.monotonic()+.02
                if not self.buffer:
                    time.sleep(.005)
                result = bytes(self.buffer[:size])
                del self.buffer[:size]
                return result

        return Connection()


class UsbTests(unittest.TestCase):
    def test_json_lines_split_batched_boot_and_oversize(self):
        parser = JsonLines()
        line = json.dumps(message()).encode()
        self.assertEqual(parser.feed(b"ESP-ROM boot\nwarning "+line[:31]), [])
        self.assertEqual(parser.feed(line[31:]+b"\r\n{}\n"), [message(), {}])
        self.assertEqual(parser.feed(b"x"*1100+b' {"bad":true}\n'+line+b"\n"), [message()])
        self.assertEqual(parser.feed(b"[1,2]\ninvalid\n{bad}\n\xff\n"), [])

    def test_usb_pose_validation_uses_same_schema(self):
        self.assertEqual(decode_usb_pose(message()).orientation, Quaternion())
        for changes in ({"type": "status"}, {"session": "bad"}, {"sequence": True},
                        {"sequence": -1}, {"state": 3}, {"recenter": None},
                        {"quaternion": [0, 0, 0, 0]}, {"quaternion": [True, 0, 0, 0]},
                        {"quaternion": [float("nan"), 0, 0, 0]}, {"quaternion": "1000"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                decode_usb_pose(message(**changes))

    def test_usb_relay_inverse_and_input_isolation(self):
        bridge = Bridge()
        bridge.use_usb()
        bridge.enabled = True
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as output:
            output.bind(("127.0.0.1", 0))
            output.settimeout(.1)
            bridge.destination_port = output.getsockname()[1]
            reference = Quaternion.from_ypr(33, -12, 8)
            delta = Quaternion.from_ypr(-15, 25, 40)
            bridge.feed_usb(Pose("0123456789abcdef", 0, READY, 0, reference), "COM4")
            output.recv(512)
            bridge.feed_usb(Pose("0123456789abcdef", 1, READY, 0, reference*delta), "COM4")
            outgoing = Quaternion(*OscMessage(output.recv(512)).params)
            for value, expected in zip((outgoing*delta).values(), Quaternion().values()):
                self.assertAlmostEqual(value, expected, places=5)
            count = bridge.sent
            duplicate = Pose("0123456789abcdef", 1, READY, 0, reference)
            bridge.feed_usb(duplicate, "COM4")
            bridge.feed_usb(Pose("0123456789abcdef", 2, READY, 0, reference), "COM3")
            self.assertEqual(bridge.sent, count)
            bridge.tracker.last_ready = time.monotonic()-1
            bridge.forward()
            self.assertEqual(bridge.sent, count)
            self.assertTrue(bridge.listen(0))
            bridge.feed_usb(duplicate, "COM4")
            self.assertIsNone(bridge.tracker.session)
            self.assertTrue(bridge.accept_pose(duplicate, "127.0.0.1", "wifi"))
            bridge.use_usb()
            self.assertFalse(bridge.accept_pose(duplicate, "127.0.0.1", "wifi"))
            bridge.feed_usb(duplicate, "COM4")
            for actual, expected in zip(bridge.tracker.relative().values(), Quaternion().values()):
                self.assertAlmostEqual(actual, expected)
        bridge.stop_input()

    def test_stream_shared_commands_reconnect_and_stop(self):
        device = FakeDevice()
        worker = UsbStreamWorker("COM4")
        poses, results, connections = [], [], []
        worker.pose.connect(poses.append)
        worker.result.connect(results.append)
        worker.connected.connect(connections.append)
        command = {"cmd": "configure", "password": "test-secret"}
        with patch("head_tracker_monitor.usb.serial.Serial", device.serial):
            worker.start()
            try:
                pump(.2)
                self.assertIn(True, connections)
                self.assertGreater(len(poses), 3)
                worker.submit(command)
                pump(.1)
                self.assertEqual(command, {})
                self.assertEqual(len(device.connections), 1)
                self.assertEqual(results, [{"ok": True, "usb_stream": True,
                                            "receiver_ip": "192.168.1.10"}])
                device.unplug = True
                pump(1.3)
                self.assertGreaterEqual(connections.count(True), 2)
                self.assertIn(False, connections)
            finally:
                worker.requestInterruption()
                self.assertTrue(worker.wait(2000))
                pump()
        self.assertTrue(all(connection.closed for connection in device.connections))
        self.assertEqual(device.writes[-1], ("stream", False))

    def test_gui_switches_transport_without_stale_poses_or_port_conflict(self):
        settings = QSettings(".cache/head-tracker-tests/usb-settings.ini", QSettings.Format.IniFormat)
        settings.setValue("receive_port", 19092)
        settings.setValue("input_mode", "wifi")
        device = FakeDevice()
        port = SimpleNamespace(device="COM4", description="M5StickS3", vid=0x303A)
        with patch("head_tracker_monitor.app.QSettings", return_value=settings), \
             patch("head_tracker_monitor.app.list_ports.comports", return_value=[port]), \
             patch("head_tracker_monitor.usb.serial.Serial", device.serial):
            window = MainWindow()
            try:
                window.input_mode.setCurrentIndex(1)
                pump(.2)
                self.assertTrue(window.usb_connected)
                self.assertEqual(window.bridge.source, "USB COM4")
                self.assertFalse(window.receive_port.isEnabled())
                owner = window.stream
                window.start_usb({"cmd": "status"})
                pump(.1)
                self.assertTrue(all(button.isEnabled() for button in window.usb_actions))
                self.assertEqual(len(device.connections), 1)
                window.input_mode.setCurrentIndex(0)
                window.usb_pose(owner, decode_usb_pose(message(100)))
                pump(.2)
                self.assertIsNone(window.stream)
                self.assertEqual(window.bridge.transport, "wifi")
                self.assertTrue(window.receive_port.isEnabled())
                self.assertIsNone(window.bridge.tracker.session)
                window.input_mode.setCurrentIndex(1)
                pump(.2)
                self.assertTrue(window.usb_connected)
                self.assertNotEqual(window.stream, owner)
                self.assertEqual(len(device.connections), 2)
            finally:
                window.close()
                pump(.2)
                self.assertIsNone(window.stream)


if __name__ == "__main__":
    unittest.main()
