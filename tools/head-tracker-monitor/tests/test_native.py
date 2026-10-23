"""Compile actual FW math/OSC code on hosts with GCC; no hardware needed."""

from pathlib import Path
import os
import shutil
import subprocess
import unittest

from head_tracker_monitor.orientation import Quaternion
from head_tracker_monitor.protocol import READY, Pose, encode_pose


class NativeFirmwareTests(unittest.TestCase):
    def test_estimator_and_wire_compatibility(self):
        gcc = shutil.which("gcc")
        if gcc is None:
            self.skipTest("GCC not installed")
        gxx = Path(gcc).with_name("g++.exe" if os.name == "nt" else "g++")
        if not gxx.exists():
            self.skipTest("Matching C++ compiler not installed")
        root = Path(__file__).resolve().parents[3]
        source = root/"apps"/"head-tracker"/"src"
        artifacts = root/".cache"/"head-tracker-tests"
        artifacts.mkdir(parents=True, exist_ok=True)
        objects = []
        for name in ("FusionAhrs", "FusionConvention"):
            output = artifacts/(name+".o")
            subprocess.run([gcc, "-std=c99", "-O2", "-fno-strict-aliasing", "-c",
                            str(source/"fusion"/(name+".c")), "-o", str(output)], check=True)
            objects.append(str(output))
        executable = artifacts/("native.exe" if os.name == "nt" else "native")
        subprocess.run([str(gxx), "-std=c++17", "-O2", "-I", str(source),
                        str(root/"tests"/"head_tracker_native.cpp"), *objects,
                        "-o", str(executable)], check=True)
        result = subprocess.run([str(executable)], check=True, capture_output=True, text=True)
        self.assertEqual(bytes.fromhex(result.stdout.strip()),
                         encode_pose(Pose("0123456789abcdef", 123, READY, 4, Quaternion())))
