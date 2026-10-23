#pragma once

#include <cmath>
#include <cstdint>

extern "C" {
#include "fusion/FusionAhrs.h"
}

namespace headtracker {

enum State : int32_t { Calibrating = 0, Ready = 1, ImuError = 2 };

// Pure sensor processing, also exercised on the host without Arduino.
class Estimator {
 public:
  Estimator() { reset(); }

  void reset() {
    FusionAhrsInitialise(&ahrs_);
    FusionAhrsSettings settings = fusionAhrsDefaultSettings;
    settings.sampleRate = 100.0f;
    settings.convention = FusionConventionNwu;
    settings.gain = 0.5f;
    settings.gyroscopeRange = 2000.0f;
    settings.accelerationRejection = 10.0f;
    settings.rejectionTimeout = 5.0f;
    FusionAhrsSetSettings(&ahrs_, &settings);
    state = Calibrating;
    calibrated_ = false;
    bias_ = {};
    clear_window();
  }

  void error() { state = ImuError; }

  void update(FusionVector accel, FusionVector gyro, float dt) {
    if (dt <= 0 || dt > .05f || !finite(accel) || !finite(gyro)) {
      clear_window();
      return;
    }
    if (!calibrated_) {
      float gravity = length(accel);
      bool still = std::fabs(gravity - 1.0f) < .08f && length(gyro) < 3.0f;
      if (count_ > 0) {
        for (int i = 0; i < 3; ++i) {
          still &= std::fabs(gyro.array[i] - first_gyro_.array[i]) < .8f;
          still &= std::fabs(accel.array[i] - first_accel_.array[i]) < .04f;
        }
      }
      if (!still) {
        clear_window();
        return;
      }
      if (count_ == 0) { first_gyro_ = gyro; first_accel_ = accel; }
      ++count_;
      duration_ += dt;
      for (int i = 0; i < 3; ++i) {
        sum_.array[i] += gyro.array[i];
        square_.array[i] += gyro.array[i] * gyro.array[i];
      }
      if (duration_ < 3.0f || count_ < 250) { return; }
      for (int i = 0; i < 3; ++i) {
        const float mean = sum_.array[i] / count_;
        if (square_.array[i] / count_ - mean * mean > .1225f) {
          clear_window();
          return;
        }
        bias_.array[i] = mean;
      }
      calibrated_ = true;
    }
    for (int i = 0; i < 3; ++i) { gyro.array[i] -= bias_.array[i]; }
    FusionAhrsSetSamplePeriod(&ahrs_, dt);
    FusionAhrsUpdateNoMagnetometer(&ahrs_, gyro, accel);
    state = FusionAhrsGetFlags(&ahrs_).startup ? Calibrating : Ready;
  }

  FusionQuaternion orientation() const { return FusionAhrsGetQuaternion(&ahrs_); }
  State state = Calibrating;

 private:
  static bool finite(FusionVector value) {
    return std::isfinite(value.axis.x) && std::isfinite(value.axis.y) && std::isfinite(value.axis.z);
  }
  static float length(FusionVector value) {
    return std::sqrt(value.axis.x*value.axis.x + value.axis.y*value.axis.y + value.axis.z*value.axis.z);
  }
  void clear_window() { count_ = 0; duration_ = 0; sum_ = {}; square_ = {}; }
  FusionAhrs ahrs_{};
  FusionVector bias_{}, sum_{}, square_{}, first_gyro_{}, first_accel_{};
  uint32_t count_ = 0;
  float duration_ = 0;
  bool calibrated_ = false;
};

// StickS3 axes, verified with screen up and USB behind the head:
// sensor X points backward, Y right, Z up. Rotate 180 degrees about Z.
inline FusionVector head_axes(float x, float y, float z) {
  FusionVector result{};
  result.axis.x = -x;
  result.axis.y = -y;
  result.axis.z = z;
  return result;
}

}  // namespace headtracker
