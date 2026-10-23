"""Receiver state, independent of Qt and wall-clock time."""

from collections import deque

from .orientation import Quaternion
from .protocol import CALIBRATING, MAX_COUNTER, READY, Pose


class Tracker:
    timeout = 0.5

    def __init__(self) -> None:
        self.session: str | None = None
        self.retired_sessions: set[str] = set()
        self.sequence: int | None = None
        self.recenter_counter: int | None = None
        self.reference: Quaternion | None = None
        self.latest: Quaternion | None = None
        self.state = CALIBRATING
        self.last_packet: float | None = None
        self.last_ready: float | None = None
        self.arrivals: deque[float] = deque()

    def accept(self, pose: Pose, now: float) -> bool:
        if pose.session in self.retired_sessions:
            return False
        if pose.session != self.session:
            if self.session is not None:
                self.retired_sessions.add(self.session)
            self.session = pose.session
            self.sequence = None
            self.reference = self.latest = None
            self.recenter_counter = None
            self.last_ready = None
            self.arrivals.clear()
        if self.sequence is not None:
            delta = (pose.sequence - self.sequence) & MAX_COUNTER
            if delta == 0 or delta >= (MAX_COUNTER + 1) // 2:
                return False
        self.sequence = pose.sequence
        self.last_packet = now
        self.state = pose.state
        if pose.state != READY:
            self.reference = self.latest = None
            self.last_ready = None
            self.arrivals.clear()
            return True
        self.latest = pose.orientation
        if self.reference is None or pose.recenter != self.recenter_counter:
            self.reference = self.latest
        self.recenter_counter = pose.recenter
        self.last_ready = now
        self.arrivals.append(now)
        self._prune(now)
        return True

    def _prune(self, now: float) -> None:
        while self.arrivals and self.arrivals[0] < now - 1.0:
            self.arrivals.popleft()

    def rate(self, now: float) -> float:
        self._prune(now)
        if len(self.arrivals) < 2:
            return 0.0
        elapsed = self.arrivals[-1] - self.arrivals[0]
        return (len(self.arrivals)-1) / elapsed if elapsed > 0 else 0.0

    def connected(self, now: float) -> bool:
        return self.last_packet is not None and now - self.last_packet < self.timeout

    def ready(self, now: float) -> bool:
        return self.state == READY and self.last_ready is not None and now-self.last_ready < self.timeout

    def recenter(self) -> None:
        if self.latest is not None:
            self.reference = self.latest

    def relative(self) -> Quaternion:
        if self.reference is None or self.latest is None:
            return Quaternion()
        return (self.reference.inverse() * self.latest).normalized()
