"""Generate live synthetic OSC input; no recording or playback."""

import argparse
import math
import secrets
import socket
import time

from .orientation import Quaternion
from .protocol import READY, Pose, encode_pose


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()
    session = secrets.token_hex(8)
    start = time.monotonic()
    sequence = 0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        try:
            while True:
                elapsed = time.monotonic() - start
                q = Quaternion.from_ypr(60*math.sin(elapsed), 20*math.sin(elapsed*.7),
                                        15*math.sin(elapsed*.5))
                sender.sendto(encode_pose(Pose(session, sequence, READY, 0, q)),
                              (args.host, args.port))
                sequence = (sequence+1) & 0x7fffffff
                time.sleep(.02)
        except KeyboardInterrupt:
            pass
