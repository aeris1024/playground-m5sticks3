"""USB configuration and continuous pose reception, with one owner per port."""

import json
import secrets
import time
from queue import Empty, Queue

from PySide6.QtCore import QThread, Signal
import serial

from .protocol import decode_usb_pose


def public_response(response: dict) -> dict:
    allowed = ("ok", "error", "ssid", "receiver_ip", "receiver_port", "wifi", "ip", "state",
               "uptime_ms", "sample_count", "quaternion", "accel", "gyro", "front_reference_valid",
               "front_direction", "usb_stream", "battery_percent", "battery_charging", "reset_reason")
    return {key: response[key] for key in allowed if key in response}


class JsonLines:
    """Bounded framing, accepting split/coalesced lines and boot diagnostics."""

    def __init__(self) -> None:
        self.line = bytearray()
        self.dropping = False

    def feed(self, chunk: bytes) -> list[dict]:
        messages = []
        for byte in chunk:
            if byte == 10:
                if not self.dropping:
                    start = self.line.find(b"{")
                    if start >= 0:
                        try:
                            value = json.loads(self.line[start:])
                            if isinstance(value, dict):
                                messages.append(value)
                        except (ValueError, UnicodeError):
                            pass
                self.line.clear()
                self.dropping = False
            elif not self.dropping:
                if len(self.line) < 1024:
                    self.line.append(byte)
                else:
                    self.line.clear()
                    self.dropping = True
        return messages


class UsbWorker(QThread):
    result = Signal(dict)
    failure = Signal(str)

    def __init__(self, port: str, command: dict, parent=None) -> None:
        super().__init__(parent)
        self.port = port
        self.command = command

    def run(self) -> None:
        request_id = secrets.token_hex(4)
        self.command["id"] = request_id
        try:
            with serial.Serial(port=None, baudrate=115200, timeout=.1,
                               write_timeout=1) as connection:
                connection.dtr = True
                connection.rts = False
                connection.port = self.port
                connection.open()
                connection.reset_input_buffer()
                # USB Serial/JTAG may restart the device on connection. Probe with
                # harmless status requests before ever transmitting credentials.
                probe_id = "probe-"+request_id
                probe = (json.dumps({"cmd": "status", "id": probe_id})+"\n").encode()
                if self.read_response(connection, probe_id, probe) is None:
                    self.failure.emit("USB応答がありません。ポートとFWを確認してください。")
                    return
                connection.write((json.dumps(self.command, ensure_ascii=False)+"\n").encode())
                self.command.clear()
                response = self.read_response(connection, request_id)
                if response is None:
                    self.failure.emit("USB応答がありません。ポートとFWを確認してください。")
                    return
                # Display only protocol fields known to contain no password.
                self.result.emit(public_response(response))
        except (serial.SerialException, OSError):
            self.failure.emit("USBポートを開けません。モニターを終了し、接続を確認してください。")
        finally:
            self.command.clear()

    def read_response(self, connection, request_id: str, retry: bytes | None = None) -> dict | None:
        deadline = time.monotonic()+5
        next_probe = 0.0
        line = bytearray()
        while time.monotonic() < deadline and not self.isInterruptionRequested():
            if retry is not None and time.monotonic() >= next_probe:
                connection.write(retry)
                next_probe = time.monotonic()+.5
            chunk = connection.read(1)
            if chunk == b"\n":
                try:
                    # Boot diagnostics can precede the first JSON object.
                    start = line.find(b"{")
                    response = json.loads(line[start:] if start >= 0 else line)
                except (ValueError, UnicodeError):
                    line.clear()
                    continue
                line.clear()
                if isinstance(response, dict) and response.get("id") == request_id:
                    return response
            elif chunk:
                line.extend(chunk)
                if len(line) > 2048:
                    line.clear()
        return None


class UsbStreamError(Exception):
    pass


class UsbStreamWorker(QThread):
    pose = Signal(object)
    result = Signal(dict)
    failure = Signal(str)
    connected = Signal(bool)
    invalid = Signal(int)

    def __init__(self, port: str, parent=None) -> None:
        super().__init__(parent)
        self.port = port
        self.commands: Queue[dict] = Queue()

    def submit(self, command: dict) -> None:
        self.commands.put(command)

    def run(self) -> None:
        while not self.isInterruptionRequested():
            try:
                self.receive_connection()
            except UsbStreamError as error:
                self.failure.emit(str(error))
            except (serial.SerialException, OSError):
                self.failure.emit("USB切断 / 接続できません。ポートを確認してください。再接続を待っています。")
            finally:
                self.connected.emit(False)
                discarded = False
                while True:
                    try:
                        self.commands.get_nowait().clear()
                        discarded = True
                    except Empty:
                        break
                if discarded:
                    self.failure.emit("USB接続が切れたため、未送信の設定を取り消しました。")
            for _ in range(10):
                if self.isInterruptionRequested():
                    return
                self.msleep(100)

    @staticmethod
    def write_command(connection, command: dict) -> None:
        connection.write((json.dumps(command, ensure_ascii=False)+"\n").encode())

    def receive_connection(self) -> None:
        with serial.Serial(port=None, baudrate=115200, timeout=.05, write_timeout=.25) as connection:
            connection.dtr = True
            connection.rts = False
            connection.port = self.port
            connection.open()
            connection.reset_input_buffer()
            parser = JsonLines()
            renewal_id = "stream-"+secrets.token_hex(4)
            next_renew = 0.0
            last_ack = last_pose = time.monotonic()
            announced = False
            pending: dict[str, float] = {}
            try:
                while not self.isInterruptionRequested():
                    now = time.monotonic()
                    if now >= next_renew:
                        self.write_command(connection, {"cmd": "stream", "enabled": True, "id": renewal_id})
                        next_renew = now+2
                    # Configuration shares this connection: never open COM twice.
                    if announced and not pending:
                        try:
                            command = self.commands.get_nowait()
                        except Empty:
                            command = None
                        if command is not None:
                            request_id = "request-"+secrets.token_hex(4)
                            command["id"] = request_id
                            try:
                                self.write_command(connection, command)
                            finally:
                                command.clear()
                            pending[request_id] = now+5
                    chunk = connection.read(min(max(connection.in_waiting, 1), 4096))
                    newest = None
                    invalid = 0
                    for message in parser.feed(chunk):
                        if message.get("type") == "pose":
                            try:
                                newest = decode_usb_pose(message)
                                last_pose = time.monotonic()
                            except (ValueError, TypeError, OverflowError):
                                invalid += 1
                        elif message.get("id") == renewal_id:
                            if not message.get("ok") or not message.get("usb_stream"):
                                raise UsbStreamError("FWがUSB連続送信に対応していません。最新FWを書き込んでください。")
                            last_ack = time.monotonic()
                            if not announced:
                                announced = True
                                self.connected.emit(True)
                        elif isinstance(message.get("id"), str) and message["id"] in pending:
                            pending.pop(message["id"])
                            self.result.emit(public_response(message))
                    if newest is not None and announced:
                        self.pose.emit(newest)
                    if invalid:
                        self.invalid.emit(invalid)
                    now = time.monotonic()
                    for request_id, deadline in list(pending.items()):
                        if now >= deadline:
                            pending.pop(request_id)
                            self.failure.emit("USB設定の応答がありません。状態を取得して確認してください。")
                    if now-last_ack >= 5 or now-last_pose >= 5:
                        raise UsbStreamError("USB姿勢データの応答がありません。再接続を待っています。")
            finally:
                # Best effort; the five-second lease also stops abandoned streams.
                try:
                    self.write_command(connection, {"cmd": "stream", "enabled": False, "id": "stop"})
                except (serial.SerialException, OSError):
                    pass
