"""Version-one OSC/USB wire formats and shared pose validation."""

from dataclasses import dataclass
import re

from pythonosc.osc_message import OscMessage
from pythonosc.osc_message_builder import OscMessageBuilder

from .orientation import Quaternion

POSE_ADDRESS = "/headtracker/pose"
SCENE_ADDRESS = "/SceneRotator/quaternions"
CALIBRATING, READY, IMU_ERROR = range(3)
MAX_COUNTER = 0x7FFFFFFF


@dataclass(frozen=True)
class Pose:
    session: str
    sequence: int
    state: int
    recenter: int
    orientation: Quaternion


def validate_pose(session, sequence, state, recenter, values) -> Pose:
    if not isinstance(session, str) or not re.fullmatch(r"[0-9a-f]{16}", session):
        raise ValueError("Invalid session")
    if any(type(v) is not int or not 0 <= v <= MAX_COUNTER
           for v in (sequence, state, recenter)) or state not in range(3):
        raise ValueError("Invalid counters/state")
    if not isinstance(values, (list, tuple)) or len(values) != 4:
        raise ValueError("Expected four quaternion components")
    if any(type(v) not in (int, float) for v in values):
        raise ValueError("Quaternion must be numeric")
    return Pose(session, sequence, state, recenter, Quaternion(*(float(v) for v in values)).normalized())


def decode_usb_pose(message: dict) -> Pose:
    if not isinstance(message, dict) or message.get("type") != "pose":
        raise ValueError("Unexpected USB message")
    return validate_pose(message.get("session"), message.get("sequence"), message.get("state"),
                         message.get("recenter"), message.get("quaternion"))


def decode_pose(data: bytes) -> Pose:
    # Fixed schema: padded address(20), tags(12), session(20), 7 words(28).
    # python-osc tolerates some truncated arguments, so check length ourselves.
    if len(data) != 80:
        raise ValueError("Unexpected packet length")
    try:
        message = OscMessage(data)
        args = message.params
        if message.address != POSE_ADDRESS or len(args) != 8:
            raise ValueError("Unexpected OSC message")
        session, sequence, state, recenter, *values = args
        if any(type(v) is not float for v in values):
            raise ValueError("Quaternion must be float32")
        # Require the precise OSC types, not merely values that decode similarly.
        tags = b",siiiffff\0"
        address_end = (len(POSE_ADDRESS.encode()) + 4) & ~3
        if data[address_end:address_end + len(tags)] != tags:
            raise ValueError("Unexpected OSC types")
        return validate_pose(session, sequence, state, recenter, values)
    except (ValueError, TypeError, IndexError, OverflowError) as error:
        raise ValueError("Invalid head tracker packet") from error
    except Exception as error:
        # python-osc parse exceptions vary by release. Never crash the UDP loop.
        raise ValueError("Malformed OSC") from error


def encode_pose(pose: Pose) -> bytes:
    message = OscMessageBuilder(POSE_ADDRESS)
    for value, kind in zip((pose.session, pose.sequence, pose.state, pose.recenter,
                            *pose.orientation.values()), "siiiffff", strict=True):
        message.add_arg(value, arg_type=kind)
    return message.build().dgram


def encode_scene(orientation: Quaternion) -> bytes:
    message = OscMessageBuilder(SCENE_ADDRESS)
    for value in orientation.values():
        message.add_arg(float(value), arg_type="f")
    return message.build().dgram
