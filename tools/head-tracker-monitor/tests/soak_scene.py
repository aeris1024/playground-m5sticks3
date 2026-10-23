"""Observe SceneRotator-format UDP output for a bounded hardware soak test."""

import argparse
import json
from pathlib import Path
import socket
import statistics
import time

from pythonosc.osc_message import OscMessage

from head_tracker_monitor.orientation import Quaternion


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9001)
    parser.add_argument("--seconds", type=float, default=600)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    arrivals = []
    invalid = 0
    first_ypr = last_ypr = None
    first = None
    waiting = time.monotonic()
    next_progress = waiting+30
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(("127.0.0.1", args.port))
        receiver.settimeout(.25)
        print(f"Listening on 127.0.0.1:{args.port}; enable GUI relay", flush=True)
        while True:
            now = time.monotonic()
            if first is None and now-waiting > 60:
                raise SystemExit("No valid OSC within 60s")
            if first is not None and now-first >= args.seconds:
                break
            try:
                data, _ = receiver.recvfrom(512)
            except TimeoutError:
                continue
            try:
                message = OscMessage(data)
                if message.address != "/SceneRotator/quaternions" or len(message.params) != 4:
                    raise ValueError
                if any(type(value) is not float for value in message.params):
                    raise ValueError
                q = Quaternion(*message.params).normalized()
            except Exception:
                invalid += 1
                continue
            timestamp = time.monotonic()
            if first is None:
                first = timestamp
                next_progress = first+30
                first_ypr = q.inverse().ypr()
            last_ypr = q.inverse().ypr()
            arrivals.append(timestamp)
            if timestamp >= next_progress:
                elapsed = timestamp-first
                print(f"elapsed={elapsed:.1f}s packets={len(arrivals)} rate={(len(arrivals)-1)/elapsed:.2f}Hz invalid={invalid}", flush=True)
                next_progress += 30
    gaps = [b-a for a, b in zip(arrivals, arrivals[1:])]
    report = {
        "duration_seconds": time.monotonic()-first,
        "packets": len(arrivals),
        "average_hz": (len(arrivals)-1)/(arrivals[-1]-arrivals[0]),
        "median_gap_ms": statistics.median(gaps)*1000,
        "max_gap_ms": max(gaps)*1000,
        "gaps_over_500ms": sum(gap >= .5 for gap in gaps),
        "invalid": invalid,
        "first_head_ypr_degrees": first_ypr,
        "last_head_ypr_degrees": last_ypr,
        "note": "Angle change is not a drift measurement unless the device remained stationary.",
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    if invalid or report["gaps_over_500ms"] or not 40 < report["average_hz"] <= 55:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
