#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>

namespace headtracker {

// A deliberately narrow OSC 1.0 encoder. Integers/floats are big-endian 32-bit.
class PosePacket {
 public:
  PosePacket(const char* session, uint32_t sequence, int32_t state,
             uint32_t recenter, const float* quaternion) {
    string("/headtracker/pose");
    string(",siiiffff");
    string(session);
    integer(sequence & 0x7fffffff);
    integer(state);
    integer(recenter & 0x7fffffff);
    for (size_t i = 0; i < 4; ++i) {
      uint32_t bits;
      static_assert(sizeof(float) == 4, "OSC requires float32");
      std::memcpy(&bits, quaternion + i, 4);
      integer(bits);
    }
  }
  const uint8_t* data() const { return data_; }
  size_t size() const { return used_; }

 private:
  void string(const char* value) {
    do { data_[used_++] = *value; } while (*value++);
    while (used_ % 4) { data_[used_++] = 0; }
  }
  void integer(uint32_t value) {
    for (int shift = 24; shift >= 0; shift -= 8) { data_[used_++] = value >> shift; }
  }
  uint8_t data_[96]{};
  size_t used_ = 0;
};

}  // namespace headtracker
