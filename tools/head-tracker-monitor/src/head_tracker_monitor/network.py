"""Qt event-driven UDP bridge. No receive acknowledgement is implied."""

import time

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QAbstractSocket, QHostAddress, QUdpSocket

from .protocol import Pose, decode_pose, encode_scene
from .tracker import Tracker


class Bridge(QObject):
    error = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.receiver = QUdpSocket(self)
        self.sender = QUdpSocket(self)
        self.receiver.readyRead.connect(self.receive)
        self.tracker = Tracker()
        self.source: str | None = None
        self.invalid = 0
        self.sent = 0
        self.enabled = False
        self.destination = QHostAddress("127.0.0.1")
        self.destination_port = 9001
        self.listening_port: int | None = None
        self.send_error = ""
        self.last_sent: float | None = None
        self.transport = "wifi"

    def stop_input(self) -> None:
        self.receiver.close()
        self.transport = "stopped"
        self.listening_port = None
        self.tracker = Tracker()
        self.source = None
        self.invalid = self.sent = 0
        self.last_sent = None
        self.send_error = ""

    def use_usb(self) -> None:
        self.stop_input()
        self.transport = "usb"

    def listen(self, port: int) -> bool:
        self.stop_input()
        self.transport = "wifi"
        if not self.receiver.bind(QHostAddress.SpecialAddress.AnyIPv4, port,
                                  QAbstractSocket.BindFlag.DontShareAddress):
            self.error.emit(f"UDP {port}: {self.receiver.errorString()}")
            return False
        self.listening_port = self.receiver.localPort()
        return True

    def accept_pose(self, pose: Pose, source: str, transport: str) -> bool:
        if transport != self.transport or (self.source is not None and self.source != source):
            return False
        if not self.tracker.accept(pose, time.monotonic()):
            return False
        self.source = source
        return True

    def feed_usb(self, pose: Pose, port: str) -> None:
        if self.accept_pose(pose, f"USB {port}", "usb"):
            self.forward()

    def receive(self) -> None:
        changed = False
        # Drain the queue before forwarding; never replay a burst of old poses.
        while self.receiver.hasPendingDatagrams():
            packet = self.receiver.receiveDatagram()
            source = packet.senderAddress().toString()
            if self.source is not None and self.source != source:
                continue
            try:
                pose = decode_pose(bytes(packet.data()))
            except ValueError:
                self.invalid += 1
                continue
            if self.accept_pose(pose, source, "wifi"):
                changed = True
        if changed:
            self.forward()

    def forward(self) -> None:
        now = time.monotonic()
        if not self.enabled or not self.tracker.ready(now):
            return
        if self.listening_port == self.destination_port and self.destination.isLoopback():
            self.send_error = "受信ポートと送信ポートを分けてください。"
            return
        data = encode_scene(self.tracker.relative().inverse())
        if self.sender.writeDatagram(data, self.destination, self.destination_port) != len(data):
            self.send_error = self.sender.errorString()
            self.error.emit(self.send_error)
        else:
            self.send_error = ""
            self.sent += 1
            self.last_sent = now
