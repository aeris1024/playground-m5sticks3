#pragma once

#include <cmath>

extern "C" {
#include "fusion/FusionMath.h"
}

namespace headtracker {

inline FusionQuaternion inverse_unit(FusionQuaternion q) {
  q.element.x = -q.element.x;
  q.element.y = -q.element.y;
  q.element.z = -q.element.z;
  return q;
}

inline FusionQuaternion exact_unit(FusionQuaternion q) {
  return FusionQuaternionScale(q, 1.0f / std::sqrt(FusionQuaternionNormSquared(q)));
}

// Express the reference's forward vector in the CURRENT head frame. This is
// a fixed target direction (like a compass needle), not the head's own heading.
inline FusionVector reference_front(FusionQuaternion reference, FusionQuaternion current) {
  const auto rotation = exact_unit(FusionQuaternionProduct(inverse_unit(current), reference));
  const auto matrix = FusionQuaternionToMatrix(rotation);
  FusionVector direction{};
  direction.axis.x = matrix.element.xx;
  direction.axis.y = matrix.element.yx;
  direction.axis.z = matrix.element.zx;
  return direction;
}

inline float relative_yaw(FusionQuaternion reference, FusionQuaternion current) {
  const auto rotation = exact_unit(FusionQuaternionProduct(inverse_unit(reference), current));
  return FusionQuaternionToEuler(rotation).angle.yaw;
}

}  // namespace headtracker
