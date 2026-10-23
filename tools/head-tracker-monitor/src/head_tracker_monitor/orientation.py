"""Hamilton quaternions, scalar first; head +X forward, +Y left, +Z up."""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Quaternion:
    w: float = 1.0
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def values(self) -> tuple[float, float, float, float]:
        return self.w, self.x, self.y, self.z

    def normalized(self) -> Quaternion:
        if not all(math.isfinite(v) for v in self.values()):
            raise ValueError("Non-finite quaternion")
        norm = math.sqrt(sum(v * v for v in self.values()))
        if not 0.5 <= norm <= 1.5:
            raise ValueError("Quaternion must have approximately unit length")
        return Quaternion(*(v / norm for v in self.values()))

    def inverse(self) -> Quaternion:
        # All public pose inputs are normalized at the protocol boundary.
        return Quaternion(self.w, -self.x, -self.y, -self.z)

    def __mul__(self, other: Quaternion) -> Quaternion:
        w, x, y, z = self.values()
        a, b, c, d = other.values()
        return Quaternion(w*a-x*b-y*c-z*d, w*b+x*a+y*d-z*c,
                          w*c-x*d+y*a+z*b, w*d+x*c-y*b+z*a)

    def rotate(self, vector: tuple[float, float, float]) -> tuple[float, float, float]:
        result = self * Quaternion(0.0, *vector) * self.inverse()
        return result.x, result.y, result.z

    def ypr(self) -> tuple[float, float, float]:
        w, x, y, z = self.values()
        return tuple(math.degrees(v) for v in (
            math.atan2(2*(w*z+x*y), 1-2*(y*y+z*z)),
            math.asin(max(-1.0, min(1.0, 2*(w*y-z*x)))),
            math.atan2(2*(w*x+y*z), 1-2*(x*x+y*y)),
        ))

    @classmethod
    def from_ypr(cls, yaw: float, pitch: float, roll: float) -> Quaternion:
        cy, cp, cr = (math.cos(math.radians(a)/2) for a in (yaw, pitch, roll))
        sy, sp, sr = (math.sin(math.radians(a)/2) for a in (yaw, pitch, roll))
        return cls(cr*cp*cy+sr*sp*sy, sr*cp*cy-cr*sp*sy,
                   cr*sp*cy+sr*cp*sy, cr*cp*sy-sr*sp*cy)
