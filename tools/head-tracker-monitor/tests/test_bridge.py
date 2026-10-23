import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import socket
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtGui import QFont, QFontDatabase, QImage
from PySide6.QtNetwork import QHostAddress
from PySide6.QtWidgets import QApplication
from pythonosc.osc_message import OscMessage

from head_tracker_monitor.app import MainWindow, lan_addresses
from head_tracker_monitor.network import Bridge
from head_tracker_monitor.orientation import Quaternion
from head_tracker_monitor.protocol import READY, Pose, encode_pose
from head_tracker_monitor.usb import UsbWorker
from head_tracker_monitor.view import HeadView

APP = QApplication.instance() or QApplication([])
# The Windows offscreen platform has no system font discovery.
if os.name == "nt" and not QFontDatabase.families():
    from pathlib import Path
    font = Path(os.environ.get("WINDIR", "C:/Windows"))/"Fonts"/"YuGothR.ttc"
    if font.exists():
        font_id = QFontDatabase.addApplicationFont(str(font))
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            APP.setFont(QFont(families[0], 10))


def pump(duration=.05):
    deadline = time.monotonic()+duration
    while time.monotonic() < deadline:
        APP.processEvents()
        time.sleep(.001)


class BridgeTests(unittest.TestCase):
    def test_camera_is_right_handed_and_rear_view_matches_head_motion(self):
        view = HeadView()
        view.resize(500, 400)
        for viewpoint in ("rear", "front"):
            view.viewpoint = viewpoint
            right, up, normal = view.camera()
            cross = (right[1]*up[2]-right[2]*up[1], right[2]*up[0]-right[0]*up[2],
                     right[0]*up[1]-right[1]*up[0])
            for actual, expected in zip(cross, normal):
                self.assertAlmostEqual(actual, expected)
            for basis in (right, up, normal):
                self.assertAlmostEqual(sum(v*v for v in basis), 1., places=6)
        view.viewpoint = "rear"
        neutral_face = view.project((1., 0., 0.))
        neutral_left = view.project((0., 1., 0.))
        face_left = view.project(Quaternion.from_ypr(30, 0, 0).rotate((1., 0., 0.)))
        face_down = view.project(Quaternion.from_ypr(0, 30, 0).rotate((1., 0., 0.)))
        ear_up = view.project(Quaternion.from_ypr(0, 0, 30).rotate((0., 1., 0.)))
        self.assertLess(face_left.x(), neutral_face.x())
        self.assertGreater(face_down.y(), neutral_face.y())
        self.assertLess(ear_up.y(), neutral_left.y())

    def test_real_udp_forwarding_and_timeout(self):
        bridge = Bridge()
        self.assertTrue(bridge.listen(0))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as output, \
             socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            output.bind(("127.0.0.1", 0))
            output.settimeout(.1)
            bridge.destination = QHostAddress("127.0.0.1")
            bridge.destination_port = output.getsockname()[1]
            bridge.enabled = True
            destination = ("127.0.0.1", bridge.listening_port)
            def send(sequence, q):
                sender.sendto(encode_pose(Pose("0123456789abcdef", sequence, READY, 0, q)), destination)
                pump()
            send(0, Quaternion())
            output.recv(512)
            send(1, Quaternion.from_ypr(90, 0, 0))
            message = OscMessage(output.recv(512))
            self.assertEqual(message.address, "/SceneRotator/quaternions")
            self.assertAlmostEqual(Quaternion(*message.params).ypr()[0], -90, places=4)
            count = bridge.sent
            send(0, Quaternion())
            self.assertEqual(bridge.sent, count)
            bridge.tracker.last_ready = time.monotonic()-1
            bridge.forward()
            self.assertEqual(bridge.sent, count)
            send(2, Quaternion.from_ypr(45, 0, 0))
            self.assertAlmostEqual(Quaternion(*OscMessage(output.recv(512)).params).ypr()[0], -45, places=4)
            bridge.enabled = False
            send(3, Quaternion())
            self.assertEqual(bridge.sent, count+1)
        bridge.receiver.close()

    def test_gui_render_and_port_conflict(self):
        # Keep test preferences in an artifact directory, never the user's registry.
        from pathlib import Path
        artifacts = Path(__file__).resolve().parents[3]/".cache"/"head-tracker-tests"
        artifacts.mkdir(parents=True, exist_ok=True)
        settings = QSettings(str(artifacts/"test-settings.ini"), QSettings.Format.IniFormat)
        settings.setValue("receive_port", 19090)
        settings.setValue("input_mode", "wifi")
        with patch("head_tracker_monitor.app.QSettings", return_value=settings), \
             patch("head_tracker_monitor.app.list_ports.comports", return_value=[]):
            window = MainWindow()
            window.show()
            pump()
            self.assertIsNotNone(window.bridge.listening_port)
            second = Bridge()
            self.assertFalse(second.listen(19090))
            window.head.orientation = Quaternion.from_ypr(35, -20, 10)
            original = window.head.orientation
            window.viewpoint.setCurrentIndex(1)
            self.assertEqual(window.head.viewpoint, "front")
            self.assertEqual(window.head.orientation, original)
            window.viewpoint.setCurrentIndex(0)
            self.assertEqual(window.head.viewpoint, "rear")
            self.assertEqual(window.head.orientation, original)
            image = QImage(window.size(), QImage.Format.Format_ARGB32)
            window.render(image)
            self.assertTrue(image.save(str(artifacts/"monitor.png")))
            window.close()
            second.receiver.close()

    def test_usb_filters_response_and_clears_credentials(self):
        class Connection:
            def __init__(self, **kwargs):
                self.response = b""
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def open(self):
                assert self.dtr is True and self.rts is False
            def reset_input_buffer(self): pass
            def write(self, data):
                request = json.loads(data)
                self.response = b"ESP-ROM boot line\nstartup warning " + (json.dumps(
                    {"id": request["id"], "ok": True, "ssid": "test", "password": "never-display"})+"\n").encode()
            def read(self, size):
                byte, self.response = self.response[:size], self.response[size:]
                return byte
        command = {"cmd": "configure", "password": "secret"}
        worker = UsbWorker("COM1", command)
        results = []
        worker.result.connect(results.append)
        with patch("head_tracker_monitor.usb.serial.Serial", Connection):
            worker.run()
        self.assertEqual(command, {})
        self.assertEqual(results, [{"ok": True, "ssid": "test"}])

    def test_receiver_update_does_not_transmit_wifi_credentials(self):
        settings = QSettings(".cache/head-tracker-tests/receiver-settings.ini", QSettings.Format.IniFormat)
        settings.setValue("receive_port", 19091)
        settings.setValue("input_mode", "wifi")
        with patch("head_tracker_monitor.app.QSettings", return_value=settings), \
             patch("head_tracker_monitor.app.list_ports.comports", return_value=[]):
            window = MainWindow()
            window.device_target.setCurrentText("192.168.1.12")
            window.ssid.setText("do-not-send")
            window.password.setText("do-not-send")
            with patch.object(window, "start_usb") as send:
                window.configure_receiver()
                send.assert_called_once_with({"cmd": "receiver", "receiver_ip": "192.168.1.12",
                                              "receiver_port": 19091})
            window.close()

    def test_physical_adapter_before_virtual(self):
        from types import SimpleNamespace
        from PySide6.QtNetwork import QNetworkInterface
        flags = QNetworkInterface.InterfaceFlag.IsUp | QNetworkInterface.InterfaceFlag.IsRunning
        def interface(label, address):
            return SimpleNamespace(flags=lambda: flags, humanReadableName=lambda: label,
                                   addressEntries=lambda: [SimpleNamespace(ip=lambda: QHostAddress(address))])
        interfaces = [interface("vEthernet (WSL)", "172.21.80.1"),
                      interface("Ethernet", "192.168.10.101"),
                      interface("Unused", "169.254.1.1"), interface("Loopback", "127.0.0.1")]
        with patch("head_tracker_monitor.app.QNetworkInterface.allInterfaces", return_value=interfaces):
            self.assertEqual(lan_addresses(), [("192.168.10.101", "Ethernet"),
                                               ("172.21.80.1", "vEthernet (WSL)")])


if __name__ == "__main__":
    unittest.main()
