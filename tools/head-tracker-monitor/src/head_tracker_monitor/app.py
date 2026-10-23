"""Desktop OSC receiver, pose display, SceneRotator bridge and USB setup."""

from __future__ import annotations

import ipaddress
import sys
import time

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtNetwork import QHostAddress, QNetworkInterface
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton,
    QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget)
from serial.tools import list_ports

from .network import Bridge
from .protocol import CALIBRATING, IMU_ERROR
from .usb import UsbStreamWorker, UsbWorker
from .view import HeadView


def port_box(value: int) -> QSpinBox:
    widget = QSpinBox()
    widget.setRange(1, 65535)
    widget.setValue(value)
    return widget


def lan_addresses() -> list[tuple[str, str]]:
    candidates = []
    for interface in QNetworkInterface.allInterfaces():
        if not (interface.flags() & QNetworkInterface.InterfaceFlag.IsUp
                and interface.flags() & QNetworkInterface.InterfaceFlag.IsRunning):
            continue
        label = interface.humanReadableName()
        virtual = any(word in label.lower() for word in ("virtual", "vethernet", "vpn", "wsl", "tailscale"))
        for entry in interface.addressEntries():
            try:
                address = ipaddress.IPv4Address(entry.ip().toString())
            except ValueError:
                continue
            if address.is_loopback or address.is_link_local or address.is_unspecified:
                continue
            candidates.append((virtual, str(address), label))
    return [(ip, label) for _, ip, label in sorted(candidates)]


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("M5StickS3 Head Tracker")
        self.resize(940, 690)
        self.settings = QSettings("M5StickS3Playground", "HeadTrackerMonitor")
        self.bridge = Bridge(self)
        self.bridge.error.connect(self.show_error)
        self.worker: UsbWorker | None = None
        self.stream: UsbStreamWorker | None = None
        self.usb_connected = False
        self.restart_input = False
        self.closing = False
        root = QWidget()
        self.setCentralWidget(root)
        columns = QHBoxLayout(root)
        left = QVBoxLayout()
        columns.addLayout(left, 3)
        settings_panel = QWidget()
        right = QVBoxLayout(settings_panel)
        right.setContentsMargins(0, 0, 0, 0)
        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        settings_scroll.setMinimumWidth(440)
        settings_scroll.setWidget(settings_panel)
        columns.addWidget(settings_scroll, 2)
        self.head = HeadView()
        self.viewpoint = QComboBox()
        self.viewpoint.addItem("後方から見る（実際の動きと同じ左右）", "rear")
        self.viewpoint.addItem("前方から見る（向かい合う表示）", "front")
        self.viewpoint.currentIndexChanged.connect(self.change_viewpoint)
        left.addWidget(self.viewpoint)
        left.addWidget(self.head, 1)
        self.angles = QLabel()
        self.quaternion = QLabel()
        self.connection = QLabel("受信待ち")
        self.forwarding = QLabel("中継OFF")
        self.details = QLabel()
        for widget in (self.angles, self.quaternion, self.connection, self.forwarding, self.details):
            left.addWidget(widget)
        recenter = QPushButton("現在の向きを正面にする")
        recenter.clicked.connect(self.recenter)
        left.addWidget(recenter)

        network = QGroupBox("入力・SceneRotatorへの中継")
        form = QFormLayout(network)
        self.input_mode = QComboBox()
        self.input_mode.addItem("Wi-Fi（OSC / UDP）", "wifi")
        self.input_mode.addItem("USB（シリアル）", "usb")
        self.input_mode.setCurrentIndex(1 if self.settings.value("input_mode", "wifi") == "usb" else 0)
        form.addRow("姿勢データの入力", self.input_mode)
        self.receive_port = port_box(int(self.settings.value("receive_port", 9000)))
        form.addRow("受信UDPポート", self.receive_port)
        listen = QPushButton("接続開始 / 再開")
        listen.clicked.connect(self.listen)
        form.addRow(listen)
        self.target_ip = QLineEdit(str(self.settings.value("target_ip", "127.0.0.1")))
        self.target_port = port_box(int(self.settings.value("target_port", 9001)))
        form.addRow("SceneRotator IPv4", self.target_ip)
        form.addRow("送信UDPポート", self.target_port)
        self.relay = QCheckBox("中継ON（音場を逆回転）")
        self.relay.toggled.connect(self.apply_target)
        self.target_ip.editingFinished.connect(self.apply_target)
        self.target_port.valueChanged.connect(self.apply_target)
        form.addRow(self.relay)
        hint = QLabel("SceneRotatorの反転設定はOFF。\n受信ポートは本アプリと別に設定します。")
        hint.setWordWrap(True)
        form.addRow(hint)
        right.addWidget(network)

        usb = QGroupBox("USBポート・本体設定")
        usb_form = QFormLayout(usb)
        self.usb_ports = QComboBox()
        self.usb_ports.setMinimumContentsLength(18)
        self.usb_ports.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        usb_form.addRow("USBポート", self.usb_ports)
        refresh = QPushButton("ポート一覧を更新")
        refresh.clicked.connect(self.refresh_ports)
        usb_form.addRow(refresh)
        self.ssid = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.device_target = QComboBox()
        self.device_target.setEditable(True)
        self.device_target.setMinimumContentsLength(18)
        self.device_target.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        for address, label in lan_addresses():
            self.device_target.addItem(f"{address} ({label})", address)
        if self.device_target.count() == 0:
            self.device_target.addItem("192.168.1.100", "192.168.1.100")
        usb_form.addRow("Wi-Fi SSID", self.ssid)
        usb_form.addRow("パスワード", self.password)
        usb_form.addRow("送信先PC IPv4", self.device_target)
        save = QPushButton("Wi-Fi設定と送信先を保存して接続")
        save.clicked.connect(self.configure)
        receiver = QPushButton("姿勢データの送信先を変更")
        receiver.clicked.connect(self.configure_receiver)
        status = QPushButton("本体の設定・Wi-Fi接続状況を確認")
        status.clicked.connect(lambda: self.start_usb({"cmd": "status"}))
        self.usb_actions = [save, receiver, status]
        for button, description in (
            (save, "SSID・パスワードと送信先PCを本体に保存し、Wi-Fiに接続します。"),
            (receiver, "Wi-Fiで姿勢を送るPCのIP・受信UDPポートを変更します。SSID・パスワードは維持します。"),
            (status, "保存済みSSID・送信先と、現在のWi-Fi接続状況・本体IPをUSBで読み出して表示します。"),
        ):
            usb_form.addRow(button)
            explanation = QLabel(description)
            explanation.setWordWrap(True)
            explanation.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
            explanation.setContentsMargins(4, 0, 4, 6)
            usb_form.addRow(explanation)
        self.usb_status = QLabel("USB入力ではこのポートを使います。\nWi-Fi入力では設定後にUSBを外せます。")
        self.usb_status.setWordWrap(True)
        usb_form.addRow(self.usb_status)
        right.addWidget(usb)
        right.addStretch()
        self.message = QLabel()
        self.message.setWordWrap(True)
        right.addWidget(self.message)
        self.refresh_ports()
        saved_port = str(self.settings.value("usb_port", ""))
        index = self.usb_ports.findData(saved_port)
        if index >= 0:
            self.usb_ports.setCurrentIndex(index)
        self.input_mode.currentIndexChanged.connect(self.listen)
        self.apply_target()
        self.listen()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_view)
        self.timer.start(33)

    def show_error(self, message: str) -> None:
        self.message.setText(message)

    def change_viewpoint(self) -> None:
        self.head.viewpoint = self.viewpoint.currentData()
        self.head.update()

    def listen(self) -> None:
        self.receive_port.setEnabled(self.input_mode.currentData() == "wifi")
        self.bridge.stop_input()
        if self.worker is not None:
            self.worker.requestInterruption()
            self.restart_input = True
            return
        if self.stream is not None:
            self.stream.requestInterruption()
            self.restart_input = True
            return
        self.restart_input = False
        if self.input_mode.currentData() == "wifi":
            if self.bridge.listen(self.receive_port.value()):
                self.message.setText("Wi-Fi受信開始。入力切り替え時の姿勢を正面にします。")
        elif self.usb_ports.currentData():
            self.bridge.use_usb()
            self.usb_connected = False
            stream = UsbStreamWorker(self.usb_ports.currentData(), self)
            self.stream = stream
            stream.pose.connect(lambda pose, owner=stream: self.usb_pose(owner, pose))
            stream.connected.connect(lambda connected, owner=stream: self.stream_connected(owner, connected))
            stream.invalid.connect(lambda count, owner=stream: self.usb_invalid(owner, count))
            stream.result.connect(self.usb_result)
            stream.failure.connect(self.stream_failure)
            stream.finished.connect(lambda owner=stream: self.stream_finished(owner))
            self.message.setText("USB接続中。ポート変更後は接続を再開してください。")
            stream.start()
        else:
            self.message.setText("USBポートを選択して接続を開始してください。")
        self.apply_target()

    def usb_pose(self, owner, pose) -> None:
        if owner is self.stream and self.bridge.transport == "usb" and self.usb_connected:
            self.bridge.feed_usb(pose, owner.port)

    def usb_invalid(self, owner, count: int) -> None:
        if owner is self.stream and self.bridge.transport == "usb":
            self.bridge.invalid += count

    def stream_connected(self, owner, connected: bool) -> None:
        if owner is self.stream:
            self.usb_connected = connected
            if self.bridge.transport == "usb":
                self.bridge.use_usb()
                if connected:
                    self.message.setText("USB接続済み。Wi-Fi設定なしで姿勢を受信できます。")

    def stream_failure(self, message: str) -> None:
        self.show_error(message)
        self.set_usb_actions(True)

    def stream_finished(self, owner) -> None:
        if owner is self.stream:
            self.stream = None
            self.usb_connected = False
            self.set_usb_actions(True)
        owner.deleteLater()
        if self.closing:
            self.close()
        elif self.restart_input:
            self.listen()

    def set_usb_actions(self, enabled: bool) -> None:
        for button in self.usb_actions:
            button.setEnabled(enabled)

    def apply_target(self, *_args) -> None:
        try:
            address = ipaddress.IPv4Address(self.target_ip.text().strip())
            if address.is_unspecified or address.is_multicast or str(address) == "255.255.255.255":
                raise ValueError
            if (self.input_mode.currentData() == "wifi" and address.is_loopback
                    and self.target_port.value() == self.receive_port.value()):
                raise ValueError
        except ValueError:
            self.bridge.enabled = False
            self.show_error("有効な送信先IPv4と、受信とは別の送信ポートを指定してください。")
            return
        self.bridge.destination = QHostAddress(str(address))
        self.bridge.destination_port = self.target_port.value()
        self.bridge.enabled = self.relay.isChecked()

    def recenter(self) -> None:
        if self.bridge.tracker.ready(time.monotonic()):
            self.bridge.tracker.recenter()
            self.bridge.forward()

    def refresh_ports(self) -> None:
        previous = self.usb_ports.currentData()
        self.usb_ports.clear()
        for port in sorted(list_ports.comports(), key=lambda item: (item.vid != 0x303A, item.vid is None, item.device)):
            self.usb_ports.addItem(f"{port.device} — {port.description}", port.device)
        index = self.usb_ports.findData(previous)
        if index >= 0:
            self.usb_ports.setCurrentIndex(index)

    def selected_device_ip(self) -> str:
        index = self.device_target.currentIndex()
        if index >= 0 and self.device_target.currentText() == self.device_target.itemText(index):
            return self.device_target.itemData(index)
        return self.device_target.currentText().strip()

    def receiver_command(self) -> dict | None:
        try:
            address = ipaddress.IPv4Address(self.selected_device_ip())
            if address.is_loopback or address.is_unspecified or address.is_multicast or str(address) == "255.255.255.255":
                raise ValueError
        except ValueError:
            self.show_error("本体から到達できるPCのLAN側IPv4を指定してください。")
            return None
        return {"receiver_ip": str(address), "receiver_port": self.receive_port.value()}

    def configure_receiver(self) -> None:
        command = self.receiver_command()
        if command is not None:
            self.start_usb({"cmd": "receiver", **command})

    def configure(self) -> None:
        receiver = self.receiver_command()
        if receiver is None:
            return
        if not 1 <= len(self.ssid.text().encode()) <= 32 or len(self.password.text().encode()) > 63:
            self.show_error("SSIDは1～32バイト、パスワードは0～63バイトで指定してください。")
            return
        command = {"cmd": "configure", "ssid": self.ssid.text(), "password": self.password.text(), **receiver}
        self.password.clear()
        self.start_usb(command)

    def start_usb(self, command: dict) -> None:
        if self.stream is not None:
            if not self.usb_connected or self.usb_ports.currentData() != self.stream.port:
                command.clear()
                self.show_error("USB接続を確認してください。ポートを変更した場合は接続を再開してください。")
                return
            self.stream.submit(command)
            self.set_usb_actions(False)
            self.usb_status.setText("USB通信中…")
            return
        if self.worker is not None or not self.usb_ports.currentData():
            command.clear()
            self.show_error("USB処理中、またはUSBポートが選択されていません。")
            return
        self.worker = UsbWorker(self.usb_ports.currentData(), command, self)
        self.worker.result.connect(self.usb_result)
        self.worker.failure.connect(self.show_error)
        self.worker.finished.connect(self.usb_finished)
        self.set_usb_actions(False)
        self.usb_status.setText("USB通信中…")
        self.worker.start()

    def usb_result(self, response: dict) -> None:
        self.set_usb_actions(True)
        if not response.get("ok"):
            self.usb_status.setText("本体設定に失敗しました: " + str(response.get("error", "unknown")))
            return
        self.ssid.setText(str(response.get("ssid", "")))
        receiver_ip = str(response.get("receiver_ip", ""))
        index = self.device_target.findData(receiver_ip)
        if index >= 0:
            self.device_target.setCurrentIndex(index)
        elif receiver_ip:
            self.device_target.setCurrentText(receiver_ip)
        self.usb_status.setText(
            f"保存先: {response.get('receiver_ip')}:{response.get('receiver_port')}\n"
            f"Wi-Fi: {response.get('wifi')} / IP: {response.get('ip')}\n"
            "保存直後は接続待ちです。数秒後に状態を取得してください。")

    def usb_finished(self) -> None:
        self.worker.deleteLater()
        self.worker = None
        self.set_usb_actions(True)
        if self.closing:
            self.close()
        elif self.restart_input:
            self.listen()

    def refresh_view(self) -> None:
        now = time.monotonic()
        tracker = self.bridge.tracker
        q = tracker.relative()
        self.head.orientation = q
        self.head.update()
        yaw, pitch, roll = q.ypr()
        self.angles.setText(f"Yaw {yaw:+7.2f}°    Pitch {pitch:+7.2f}°    Roll {roll:+7.2f}°")
        self.quaternion.setText("w x y z: " + "  ".join(f"{v:+.4f}" for v in q.values()))
        age = "—" if tracker.last_packet is None else f"{(now-tracker.last_packet)*1000:.0f} ms"
        if self.bridge.transport == "usb" and not self.usb_connected:
            status = "USB接続待ち / 切断"
        elif self.bridge.transport == "stopped":
            status = "入力停止 / 切り替え中"
        elif self.bridge.transport == "wifi" and self.bridge.listening_port is None:
            status = "受信停止（ポートを確認）"
        elif not tracker.connected(now):
            status = "受信待ち / 切断"
        elif tracker.state == IMU_ERROR:
            status = "本体IMUエラー"
        elif tracker.state == CALIBRATING:
            status = "本体校正中・静止してください"
        else:
            status = "追跡中"
        self.connection.setText(f"{status} | {self.bridge.source or '—'} | {tracker.rate(now):.1f} Hz | 最終受信 {age}")
        sending = (self.bridge.enabled and tracker.ready(now) and self.bridge.last_sent is not None
                   and now-self.bridge.last_sent < .5 and not self.bridge.send_error)
        self.forwarding.setText("送信中（SceneRotatorの受信確認は未実施）" if sending else "中継OFF / 有効姿勢待ち")
        self.details.setText(f"送信 {self.bridge.sent} / 不正パケット {self.bridge.invalid}")

    def closeEvent(self, event) -> None:
        self.closing = True
        self.restart_input = False
        self.bridge.stop_input()
        if self.worker is not None or self.stream is not None:
            if self.worker is not None:
                self.worker.requestInterruption()
            if self.stream is not None:
                self.stream.requestInterruption()
            self.show_error("USB通信を終了しています…")
            event.ignore()
            return
        for key, value in (("receive_port", self.receive_port.value()),
                           ("target_ip", self.target_ip.text()), ("target_port", self.target_port.value()),
                           ("input_mode", self.input_mode.currentData()),
                           ("usb_port", self.usb_ports.currentData() or "")):
            self.settings.setValue(key, value)
        self.bridge.receiver.close()
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()
