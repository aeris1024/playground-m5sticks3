#include <cassert>
#include <cmath>
#include <cstdio>

#include "estimator.hpp"
#include "forward_indicator.hpp"
#include "osc.hpp"

void test_pitch_roll_hold() {
  // Screen up / USB behind the head. These sensor-space trajectories are
  // independent of the implementation's mapping: raw -Y is head pitch-down,
  // raw -X is left-ear-up. Include physically consistent gravity measurements.
  for (int axis = 0; axis < 2; ++axis) {
    headtracker::Estimator estimator;
    auto gravity = headtracker::head_axes(0, 0, 1);
    auto gyro = headtracker::head_axes(0, 0, 0);
    for (int i = 0; i < 700; ++i) { estimator.update(gravity, gyro, .01f); }
    assert(estimator.state == headtracker::Ready);
    for (int i = 1; i <= 100; ++i) {
      float angle = i * .3f * 3.14159265359f / 180.0f;
      gravity = axis == 1 ? headtracker::head_axes(std::sin(angle), 0, std::cos(angle))
                          : headtracker::head_axes(0, -std::sin(angle), std::cos(angle));
      gyro = axis == 1 ? headtracker::head_axes(0, -30, 0)
                       : headtracker::head_axes(-30, 0, 0);
      estimator.update(gravity, gyro, .01f);
    }
    gyro = headtracker::head_axes(0, 0, 0);
    // An angle must remain at 30 degrees after motion stops, not decay to zero.
    for (int i = 0; i < 300; ++i) { estimator.update(gravity, gyro, .01f); }
    const auto angles = FusionQuaternionToEuler(estimator.orientation());
    assert(std::fabs(angles.angle.yaw) < 1);
    assert(std::fabs(angles.angle.pitch - (axis == 1 ? 30 : 0)) < 1);
    assert(std::fabs(angles.angle.roll - (axis == 0 ? 30 : 0)) < 1);
  }
}

void test_reference_front() {
  const auto identity = FUSION_QUATERNION_IDENTITY;
  FusionQuaternion left90{};
  left90.element.w = left90.element.z = std::sqrt(.5f);
  auto direction = headtracker::reference_front(identity, identity);
  assert(std::fabs(direction.axis.x - 1) < .00001f);
  assert(std::fabs(direction.axis.y) < .00001f);
  // Turning the head left makes the fixed forward target appear on the right.
  direction = headtracker::reference_front(identity, left90);
  assert(std::fabs(direction.axis.x) < .00001f);
  assert(std::fabs(direction.axis.y + 1) < .00001f);
  assert(std::fabs(headtracker::relative_yaw(identity, left90) - 90) < .001f);
  auto right90 = headtracker::inverse_unit(left90);
  direction = headtracker::reference_front(identity, right90);
  assert(std::fabs(direction.axis.y - 1) < .00001f);
  // Recenter at a nonzero heading, and quaternion sign, must not change the target.
  direction = headtracker::reference_front(left90, left90);
  assert(std::fabs(direction.axis.x - 1) < .00001f);
  auto negative = FusionQuaternionScale(left90, -1);
  direction = headtracker::reference_front(left90, negative);
  assert(std::fabs(direction.axis.x - 1) < .00001f);
  FusionQuaternion down90{};
  down90.element.w = down90.element.y = std::sqrt(.5f);
  direction = headtracker::reference_front(identity, down90);
  assert(std::fabs(direction.axis.x) < .00001f);
  assert(std::fabs(direction.axis.y) < .00001f);
  assert(std::fabs(direction.axis.z - 1) < .00001f);
}

int main() {
  test_pitch_roll_hold();
  test_reference_front();
  headtracker::Estimator estimator;
  FusionVector gravity{};
  gravity.axis.z = 1;
  FusionVector gyro{};
  gyro.axis.x = .2f;
  gyro.axis.y = -.1f;
  gyro.axis.z = .3f;
  // Motion must restart the three-second stationary window.
  for (int i = 0; i < 200; ++i) { estimator.update(gravity, gyro, .01f); }
  FusionVector motion = gyro;
  motion.axis.z = 30;
  estimator.update(gravity, motion, .01f);
  for (int i = 0; i < 200; ++i) { estimator.update(gravity, gyro, .01f); }
  assert(estimator.state == headtracker::Calibrating);
  for (int i = 0; i < 450; ++i) { estimator.update(gravity, gyro, .01f); }
  assert(estimator.state == headtracker::Ready);
  auto q = estimator.orientation();
  assert(std::fabs(q.element.z) < .002f);
  // Known yaw rate after subtracting a stationary bias: +90 degrees in 1s.
  gyro.axis.z += 90;
  for (int i = 0; i < 100; ++i) { estimator.update(gravity, gyro, .01f); }
  q = estimator.orientation();
  const auto angles = FusionQuaternionToEuler(q);
  assert(std::fabs(angles.angle.yaw - 90) < 1);
  auto axes = headtracker::head_axes(1, 2, 3);
  assert(axes.axis.x == -1 && axes.axis.y == -2 && axes.axis.z == 3);
  const float identity[4] = {1, 0, 0, 0};
  headtracker::PosePacket packet("0123456789abcdef", 123, 1, 4, identity);
  for (size_t i = 0; i < packet.size(); ++i) { std::printf("%02x", packet.data()[i]); }
  std::printf("\n");
  return 0;
}
