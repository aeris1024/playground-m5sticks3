#include <Arduino.h>
#include <M5Unified.h>

namespace {

constexpr uint32_t kRefreshIntervalMs = 100;
constexpr float kGyroRangeDps = 250.0f;

bool paused = false;
bool canvas_ready = false;
uint32_t last_refresh_ms = 0;
M5Canvas canvas(&M5.Display);

const char* imu_name(m5::imu_t type) {
  switch (type) {
    case m5::imu_sh200q:
      return "SH200Q";
    case m5::imu_mpu6050:
      return "MPU6050";
    case m5::imu_mpu6886:
      return "MPU6886";
    case m5::imu_mpu9250:
      return "MPU9250";
    case m5::imu_bmi270:
      return "BMI270";
    case m5::imu_none:
      return "not found";
    default:
      return "unknown";
  }
}

void draw_layout(const char* name) {
  canvas.fillScreen(TFT_BLACK);
  canvas.setTextSize(1);
  canvas.setTextColor(TFT_CYAN, TFT_BLACK);
  canvas.setCursor(42, 3);
  canvas.print("TILT");
  canvas.setTextColor(TFT_YELLOW, TFT_BLACK);
  canvas.setCursor(136, 3);
  canvas.print("GYRO +/-250 dps");
  canvas.setTextColor(TFT_DARKGREY, TFT_BLACK);
  canvas.setCursor(4, 124);
  canvas.printf("%s | A: pause", name);
}

void draw_level(const m5::imu_data_t& data) {
  constexpr int32_t center_x = 60;
  constexpr int32_t center_y = 69;
  constexpr int32_t radius = 48;

  canvas.fillRect(0, 16, 121, 106, TFT_BLACK);
  canvas.drawCircle(center_x, center_y, radius, TFT_DARKGREY);
  canvas.drawCircle(center_x, center_y, radius - 1, TFT_DARKGREY);
  canvas.drawFastHLine(center_x - radius, center_y, radius * 2, TFT_DARKGREY);
  canvas.drawFastVLine(center_x, center_y - radius, radius * 2, TFT_DARKGREY);
  canvas.drawCircle(center_x, center_y, 12, TFT_DARKGREY);

  float x = data.accel.x;
  float y = data.accel.y;
  const float length = sqrtf(x * x + y * y);
  if (length > 1.0f) {
    x /= length;
    y /= length;
  }

  const int32_t bubble_x = center_x + static_cast<int32_t>(x * (radius - 7));
  const int32_t bubble_y = center_y - static_cast<int32_t>(y * (radius - 7));
  const uint16_t bubble_color = data.accel.z >= 0.0f ? TFT_GREEN : TFT_RED;
  canvas.fillCircle(bubble_x, bubble_y, 6, bubble_color);
  canvas.drawCircle(bubble_x, bubble_y, 6, TFT_WHITE);
}

void draw_gyro_bar(int32_t y, const char* axis, float value,
                   uint16_t color) {
  constexpr int32_t plot_left = 143;
  constexpr int32_t plot_center = 188;
  constexpr int32_t half_width = 45;

  canvas.fillRect(124, y - 7, 114, 15, TFT_BLACK);
  canvas.setTextSize(1);
  canvas.setTextColor(TFT_WHITE, TFT_BLACK);
  canvas.setCursor(126, y - 3);
  canvas.print(axis);
  canvas.drawFastHLine(plot_left, y, half_width * 2, TFT_DARKGREY);
  canvas.drawFastVLine(plot_center, y - 6, 13, TFT_WHITE);

  const float limited = fmaxf(-kGyroRangeDps, fminf(kGyroRangeDps, value));
  const int32_t bar = static_cast<int32_t>(limited / kGyroRangeDps * half_width);
  if (bar > 0) {
    canvas.fillRect(plot_center + 1, y - 4, bar, 9, color);
  } else if (bar < 0) {
    canvas.fillRect(plot_center + bar, y - 4, -bar, 9, color);
  }
}

void draw_graphics(const m5::imu_data_t& data) {
  draw_level(data);
  draw_gyro_bar(37, "X", data.gyro.x, TFT_RED);
  draw_gyro_bar(69, "Y", data.gyro.y, TFT_GREEN);
  draw_gyro_bar(101, "Z", data.gyro.z, TFT_BLUE);
  canvas.pushSprite(0, 0);
}

void draw_pause_state() {
  canvas.fillRect(canvas.width() - 52, 124, 48, 10, TFT_BLACK);
  canvas.setTextSize(1);
  canvas.setTextColor(paused ? TFT_RED : TFT_GREEN, TFT_BLACK);
  canvas.setCursor(canvas.width() - 52, 124);
  canvas.print(paused ? "PAUSED" : "RUN");
}

}  // namespace

void setup() {
  auto config = M5.config();
  config.internal_imu = true;
  M5.begin(config);

  Serial.begin(115200);
  delay(100);

  M5.Display.setRotation(1);
  canvas.setColorDepth(16);
  canvas_ready =
      canvas.createSprite(M5.Display.width(), M5.Display.height()) != nullptr;
  if (!canvas_ready) {
    M5.Display.setTextColor(TFT_RED, TFT_BLACK);
    M5.Display.setCursor(6, 6);
    M5.Display.print("Canvas allocation failed.");
    Serial.println("error: canvas allocation failed");
    return;
  }
  const auto type = M5.Imu.getType();
  const char* name = imu_name(type);
  draw_layout(name);

  Serial.printf("imu-monitor started, imu=%s\n", name);
  Serial.println("millis,ax_g,ay_g,az_g,gx_dps,gy_dps,gz_dps");

  if (type == m5::imu_none) {
    canvas.setTextColor(TFT_RED, TFT_BLACK);
    canvas.setCursor(6, 45);
    canvas.print("Internal IMU was not detected.");
    Serial.println("error: internal IMU was not detected");
  } else {
    draw_pause_state();
  }
  canvas.pushSprite(0, 0);
}

void loop() {
  if (!canvas_ready) {
    delay(1000);
    return;
  }

  M5.update();

  if (M5.BtnA.wasPressed()) {
    paused = !paused;
    draw_pause_state();
    canvas.pushSprite(0, 0);
    Serial.println(paused ? "# paused" : "# resumed");
  }

  if (!M5.Imu.update() || paused) {
    delay(1);
    return;
  }

  const uint32_t now = millis();
  if (now - last_refresh_ms < kRefreshIntervalMs) {
    delay(1);
    return;
  }
  last_refresh_ms = now;

  const auto data = M5.Imu.getImuData();
  draw_graphics(data);
  Serial.printf("%lu,%.4f,%.4f,%.4f,%.3f,%.3f,%.3f\n",
                static_cast<unsigned long>(now), data.accel.x, data.accel.y,
                data.accel.z, data.gyro.x, data.gyro.y, data.gyro.z);
}
