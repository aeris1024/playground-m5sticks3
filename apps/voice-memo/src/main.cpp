#include <Arduino.h>
#include <M5Unified.h>

#include <cmath>
#include <cstring>
#include <esp_heap_caps.h>

namespace {

constexpr uint32_t kSampleRateHz = 16000;
constexpr uint32_t kMaxDurationMs = 30000;
constexpr size_t kChunkSamples = 256;
constexpr size_t kRingCount = 4;
constexpr size_t kPipelineLag = 2;
constexpr size_t kMaxSamples =
    static_cast<size_t>(kSampleRateHz) * (kMaxDurationMs / 1000);
constexpr uint8_t kSpeakerVolume = 180;
constexpr uint32_t kUiRefreshIntervalMs = 100;
constexpr uint32_t kPlayStartGraceMs = 50;
constexpr uint8_t kSpeakerTaskPriority = 5;
constexpr uint8_t kSpeakerTaskCore = 1;
constexpr size_t kSpeakerDmaLen = 512;
constexpr size_t kSpeakerDmaCount = 8;

static_assert(kMaxSamples % kChunkSamples == 0,
              "max sample count must be a multiple of the record chunk");

enum class State : uint8_t {
  IdleEmpty,
  IdleHasData,
  Recording,
  Playing,
};

State state = State::IdleEmpty;
bool canvas_ready = false;
bool buffer_ready = false;
bool fatal = false;

int16_t* rec_data = nullptr;
int16_t rec_ring[kRingCount][kChunkSamples];
size_t recorded_samples = 0;
size_t rec_queue_idx = 0;
size_t rec_queued_count = 0;
int16_t input_peak = 0;
uint32_t play_start_ms = 0;
uint32_t last_ui_refresh_ms = 0;
bool play_started = false;

M5Canvas canvas(&M5.Display);

const char* state_label(State value) {
  switch (value) {
    case State::Recording:
      return "REC";
    case State::Playing:
      return "PLAY";
    case State::IdleEmpty:
      return "EMPTY";
    case State::IdleHasData:
      return recorded_samples >= kMaxSamples ? "FULL" : "IDLE";
  }
  return "IDLE";
}

uint16_t state_color(State value) {
  switch (value) {
    case State::Recording:
      return TFT_RED;
    case State::Playing:
      return TFT_GREEN;
    case State::IdleEmpty:
      return TFT_DARKGREY;
    case State::IdleHasData:
      return recorded_samples >= kMaxSamples ? TFT_YELLOW : TFT_CYAN;
  }
  return TFT_WHITE;
}

float duration_seconds(size_t samples) {
  return static_cast<float>(samples) / static_cast<float>(kSampleRateHz);
}

void log_state(const char* label) {
  Serial.printf("state=%s samples=%u\n", label,
                static_cast<unsigned>(recorded_samples));
}

void wait_mic_idle() {
  while (M5.Mic.isRecording()) {
    delay(1);
  }
}

void wait_speaker_idle() {
  while (M5.Speaker.isPlaying()) {
    delay(1);
  }
}

void enter_mic_mode() {
  wait_speaker_idle();
  M5.Speaker.end();
  M5.Mic.begin();
}

void apply_speaker_config() {
  auto cfg = M5.Speaker.config();
  cfg.sample_rate = kSampleRateHz;
  cfg.task_priority = kSpeakerTaskPriority;
  cfg.task_pinned_core = kSpeakerTaskCore;
  cfg.dma_buf_len = kSpeakerDmaLen;
  cfg.dma_buf_count = kSpeakerDmaCount;
  M5.Speaker.config(cfg);
}

void enter_speaker_mode() {
  wait_mic_idle();
  M5.Mic.end();
  if (!M5.Speaker.isRunning()) {
    apply_speaker_config();
  }
  M5.Speaker.begin();
  M5.Speaker.setVolume(kSpeakerVolume);
}

void leave_audio() {
  wait_mic_idle();
  wait_speaker_idle();
  M5.Mic.end();
  M5.Speaker.end();
}

int16_t chunk_peak(const int16_t* samples, size_t count) {
  int32_t peak = 0;
  for (size_t i = 0; i < count; ++i) {
    int32_t value = samples[i];
    if (value < 0) {
      value = -value;
    }
    if (value > peak) {
      peak = value;
    }
  }
  return static_cast<int16_t>(peak);
}

float meter_ratio() {
  if (state == State::Recording) {
    return static_cast<float>(input_peak) / 32767.0f;
  }
  if (state == State::Playing && recorded_samples > 0) {
    const uint32_t duration_ms =
        static_cast<uint32_t>(recorded_samples * 1000 / kSampleRateHz);
    uint32_t elapsed = millis() - play_start_ms;
    if (elapsed > duration_ms) {
      elapsed = duration_ms;
    }
    if (duration_ms == 0) {
      return 0.0f;
    }
    return static_cast<float>(elapsed) / static_cast<float>(duration_ms);
  }
  return 0.0f;
}

void draw_ui() {
  if (!canvas_ready) {
    return;
  }

  canvas.fillScreen(TFT_BLACK);
  canvas.setTextSize(1);
  canvas.setTextColor(TFT_CYAN, TFT_BLACK);
  canvas.setCursor(8, 4);
  canvas.print("VOICE MEMO");

  const char* label = state_label(state);
  canvas.setTextSize(2);
  canvas.setTextColor(state_color(state), TFT_BLACK);
  canvas.setCursor(8, 22);
  canvas.print(label);

  canvas.setTextSize(1);
  canvas.setTextColor(TFT_WHITE, TFT_BLACK);
  canvas.setCursor(8, 50);
  if (state == State::Playing) {
    const float total = duration_seconds(recorded_samples);
    const float elapsed = meter_ratio() * total;
    canvas.printf("%.1f / %.1f s", static_cast<double>(elapsed),
                  static_cast<double>(total));
  } else if (state == State::Recording) {
    canvas.printf("%.1f / %.1f s",
                  static_cast<double>(duration_seconds(recorded_samples)),
                  static_cast<double>(duration_seconds(kMaxSamples)));
  } else {
    canvas.printf("%.1f / %.1f s",
                  static_cast<double>(duration_seconds(recorded_samples)),
                  static_cast<double>(duration_seconds(kMaxSamples)));
  }

  constexpr int32_t bar_x = 8;
  constexpr int32_t bar_y = 78;
  constexpr int32_t bar_w = 224;
  constexpr int32_t bar_h = 14;
  canvas.drawRect(bar_x, bar_y, bar_w, bar_h, TFT_DARKGREY);
  canvas.fillRect(bar_x + 1, bar_y + 1, bar_w - 2, bar_h - 2, TFT_BLACK);
  const float ratio = fmaxf(0.0f, fminf(1.0f, meter_ratio()));
  const int32_t fill = static_cast<int32_t>(ratio * static_cast<float>(bar_w - 2));
  if (fill > 0) {
    const uint16_t fill_color =
        state == State::Playing ? TFT_GREEN : TFT_RED;
    canvas.fillRect(bar_x + 1, bar_y + 1, fill, bar_h - 2, fill_color);
  }

  canvas.setTextColor(TFT_DARKGREY, TFT_BLACK);
  canvas.setCursor(8, 120);
  switch (state) {
    case State::Recording:
      canvas.print("A: stop   B: play");
      break;
    case State::Playing:
      canvas.print("A: rec    B: replay");
      break;
    case State::IdleEmpty:
      canvas.print("A: rec    B: --");
      break;
    case State::IdleHasData:
      canvas.print("A: rec    B: play");
      break;
  }

  canvas.pushSprite(0, 0);
  last_ui_refresh_ms = millis();
}

void show_fatal(const char* message) {
  fatal = true;
  M5.Display.fillScreen(TFT_BLACK);
  M5.Display.setTextColor(TFT_RED, TFT_BLACK);
  M5.Display.setCursor(6, 6);
  M5.Display.print(message);
  Serial.printf("error: %s\n", message);
}

void commit_ready_chunk(size_t ring_idx) {
  if (recorded_samples + kChunkSamples > kMaxSamples) {
    return;
  }
  std::memcpy(rec_data + recorded_samples, rec_ring[ring_idx],
              kChunkSamples * sizeof(int16_t));
  input_peak = chunk_peak(rec_ring[ring_idx], kChunkSamples);
  recorded_samples += kChunkSamples;
}

void flush_rec_pipeline() {
  wait_mic_idle();
  if (rec_queued_count == 0) {
    return;
  }
  const size_t pending =
      rec_queued_count >= kPipelineLag ? kPipelineLag : rec_queued_count;
  for (size_t i = pending; i > 0; --i) {
    const size_t ring_idx = (rec_queue_idx + kRingCount - i) % kRingCount;
    commit_ready_chunk(ring_idx);
  }
}

void start_recording() {
  recorded_samples = 0;
  rec_queue_idx = 0;
  rec_queued_count = 0;
  input_peak = 0;
  play_started = false;
  enter_mic_mode();
  state = State::Recording;
  log_state("REC");
  draw_ui();
}

void finish_recording(bool from_limit) {
  flush_rec_pipeline();
  M5.Mic.end();
  if (recorded_samples == 0) {
    state = State::IdleEmpty;
    log_state("EMPTY");
  } else {
    state = State::IdleHasData;
    log_state(from_limit ? "FULL" : "IDLE");
  }
  draw_ui();
}

void start_playing() {
  if (recorded_samples == 0) {
    state = State::IdleEmpty;
    log_state("EMPTY");
    draw_ui();
    return;
  }

  enter_speaker_mode();
  M5.Speaker.stop();
  M5.Speaker.playRaw(rec_data, recorded_samples, kSampleRateHz, false, 1, 0);
  play_start_ms = millis();
  play_started = true;
  state = State::Playing;
  log_state("PLAY");
  draw_ui();
}

void finish_playing() {
  wait_speaker_idle();
  M5.Speaker.end();
  play_started = false;
  state = recorded_samples == 0 ? State::IdleEmpty : State::IdleHasData;
  log_state(recorded_samples >= kMaxSamples ? "FULL" : "IDLE");
  draw_ui();
}

void poll_recording() {
  if (recorded_samples >= kMaxSamples) {
    finish_recording(true);
    return;
  }

  if (!M5.Mic.record(rec_ring[rec_queue_idx], kChunkSamples, kSampleRateHz)) {
    return;
  }

  if (rec_queued_count >= kPipelineLag) {
    const size_t ready_idx =
        (rec_queue_idx + kRingCount - kPipelineLag) % kRingCount;
    commit_ready_chunk(ready_idx);
  }
  rec_queue_idx = (rec_queue_idx + 1) % kRingCount;
  rec_queued_count++;
  if (millis() - last_ui_refresh_ms >= kUiRefreshIntervalMs) {
    draw_ui();
  }

  if (recorded_samples >= kMaxSamples) {
    finish_recording(true);
  }
}

void poll_playing() {
  const uint32_t now = millis();
  if (now - play_start_ms >= kPlayStartGraceMs && !M5.Speaker.isPlaying()) {
    finish_playing();
    return;
  }
  if (now - last_ui_refresh_ms < kUiRefreshIntervalMs) {
    return;
  }
  draw_ui();
}

}  // namespace

void setup() {
  auto config = M5.config();
  config.internal_mic = true;
  config.internal_spk = true;
  config.internal_imu = false;
  M5.begin(config);

  Serial.begin(115200);
  delay(100);

  M5.Display.setRotation(1);
  M5.Display.fillScreen(TFT_BLACK);
  M5.Display.setTextColor(TFT_WHITE, TFT_BLACK);

  rec_data = static_cast<int16_t*>(
      heap_caps_malloc(kMaxSamples * sizeof(int16_t), MALLOC_CAP_SPIRAM));
  buffer_ready = rec_data != nullptr;
  if (!buffer_ready) {
    show_fatal("PSRAM allocation failed.");
    return;
  }
  std::memset(rec_data, 0, kMaxSamples * sizeof(int16_t));

  apply_speaker_config();

  canvas.setColorDepth(16);
  canvas.setPsram(false);
  canvas_ready =
      canvas.createSprite(M5.Display.width(), M5.Display.height()) != nullptr;
  if (!canvas_ready) {
    show_fatal("Canvas allocation failed.");
    return;
  }

  leave_audio();
  state = State::IdleEmpty;
  Serial.println("voice-memo started");
  log_state("EMPTY");
  draw_ui();
}

void loop() {
  if (fatal) {
    delay(1000);
    return;
  }

  M5.update();

  if (M5.BtnA.wasPressed()) {
    if (state == State::Recording) {
      finish_recording(false);
    } else if (state == State::Playing) {
      M5.Speaker.stop();
      wait_speaker_idle();
      M5.Speaker.end();
      start_recording();
    } else {
      start_recording();
    }
  }

  if (M5.BtnB.wasPressed()) {
    if (state == State::Recording) {
      finish_recording(false);
      start_playing();
    } else if (state == State::Playing) {
      start_playing();
    } else if (recorded_samples > 0) {
      start_playing();
    } else {
      log_state("EMPTY");
      draw_ui();
    }
  }

  if (state == State::Recording) {
    poll_recording();
  } else if (state == State::Playing) {
    poll_playing();
  }

  delay(1);
}
