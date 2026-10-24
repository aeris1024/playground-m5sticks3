#include <Arduino.h>
#include <ArduinoJson.h>
#include <M5Unified.h>
#include <Preferences.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <esp_system.h>
#include <atomic>

#include "estimator.hpp"
#include "forward_indicator.hpp"
#include "osc.hpp"

namespace {

struct Configuration {
  String ssid;
  String password;
  IPAddress receiver;
  uint16_t port = 9000;
  uint32_t revision = 0;
  bool valid = false;
};

struct Snapshot {
  char session[17]{};
  uint32_t recenter = 0;
  headtracker::State state = headtracker::Calibrating;
  FusionQuaternion orientation = FUSION_QUATERNION_IDENTITY;
  FusionVector accel{}, gyro{};
  uint32_t sample_count = 0;
  FusionQuaternion front_reference = FUSION_QUATERNION_IDENTITY;
  bool front_reference_valid = false;
};

struct BatteryStatus {
  int percent = -1;
  m5::Power_Class::is_charging_t charging = m5::Power_Class::charge_unknown;
};

Configuration configuration;
Snapshot snapshot;
BatteryStatus battery_status;
SemaphoreHandle_t configuration_mutex;
portMUX_TYPE snapshot_mutex = portMUX_INITIALIZER_UNLOCKED;
bool reset_requested = false;
bool imu_ready = false;
bool storage_ready = false;
Preferences preferences;
String serial_line;
bool dropping_line = false;
M5Canvas display_canvas(&M5.Display);
bool display_ready = false;
std::atomic<bool> usb_stream_enabled{false};
uint32_t usb_stream_renewed = 0;
constexpr uint32_t kUsbLeaseMs = 5000;

Configuration get_configuration() {
  xSemaphoreTake(configuration_mutex, portMAX_DELAY);
  Configuration copy = configuration;
  xSemaphoreGive(configuration_mutex);
  return copy;
}

Snapshot get_snapshot() {
  portENTER_CRITICAL(&snapshot_mutex);
  Snapshot copy = snapshot;
  portEXIT_CRITICAL(&snapshot_mutex);
  return copy;
}

BatteryStatus get_battery_status() {
  portENTER_CRITICAL(&snapshot_mutex);
  const auto copy = battery_status;
  portEXIT_CRITICAL(&snapshot_mutex);
  return copy;
}

void update_battery_status() {
  // Called between IMU acquisitions so the shared I2C bus has one polling task.
  BatteryStatus next;
  next.percent = M5.Power.getBatteryLevel();
  if (next.percent >= 0 && next.percent <= 100) { next.charging = M5.Power.isCharging(); }
  else { next.percent = -1; }
  portENTER_CRITICAL(&snapshot_mutex);
  battery_status = next;
  portEXIT_CRITICAL(&snapshot_mutex);
}

void new_session(Snapshot& value) {
  snprintf(value.session, sizeof(value.session), "%08lx%08lx",
           static_cast<unsigned long>(esp_random()), static_cast<unsigned long>(esp_random()));
  value.state = headtracker::Calibrating;
  value.orientation = FUSION_QUATERNION_IDENTITY;
  value.sample_count = 0;
  value.front_reference_valid = false;
}

void sensor_task(void*) {
  headtracker::Estimator estimator;
  Snapshot current = get_snapshot();
  TickType_t wake = xTaskGetTickCount();
  uint32_t last_sample = micros();
  uint32_t last_valid = last_sample;
  uint32_t last_battery = millis() - 1000;
  uint8_t pending_mask = 0;
  while (true) {
    portENTER_CRITICAL(&snapshot_mutex);
    bool reset = reset_requested;
    reset_requested = false;
    portEXIT_CRITICAL(&snapshot_mutex);
    if (reset) {
      estimator.reset();
      new_session(current);
      last_sample = micros();
      pending_mask = 0;
    }
    const auto mask = imu_ready ? M5.Imu.update() : m5::IMU_Class::sensor_mask_none;
    pending_mask |= mask;
    const uint32_t now = micros();
    if ((pending_mask & 3) == 3) {
      pending_mask = 0;
      const auto data = M5.Imu.getImuData();
      const float dt = (now - last_sample) * 1.0e-6f;
      if (now - last_valid > 100000) {
        estimator.reset();
        new_session(current);
      }
      last_sample = last_valid = now;
      estimator.update(headtracker::head_axes(data.accel.x, data.accel.y, data.accel.z),
                       headtracker::head_axes(data.gyro.x, data.gyro.y, data.gyro.z), dt);
      current.state = estimator.state;
      current.orientation = estimator.orientation();
      current.accel = headtracker::head_axes(data.accel.x, data.accel.y, data.accel.z);
      current.gyro = headtracker::head_axes(data.gyro.x, data.gyro.y, data.gyro.z);
      ++current.sample_count;
    } else if (!imu_ready || now - last_valid > 250000) {
      estimator.error();
      current.state = headtracker::ImuError;
    }
    portENTER_CRITICAL(&snapshot_mutex);
    if (current.state != headtracker::Ready) {
      current.front_reference_valid = false;
    } else if (!current.front_reference_valid || current.recenter != snapshot.recenter) {
      current.front_reference = current.orientation;
      current.front_reference_valid = true;
    }
    current.recenter = snapshot.recenter;
    snapshot = current;
    portEXIT_CRITICAL(&snapshot_mutex);
    const uint32_t battery_now = millis();
    if (battery_now - last_battery >= 1000) {
      last_battery = battery_now;
      update_battery_status();
    }
    vTaskDelayUntil(&wake, pdMS_TO_TICKS(10));
  }
}

void network_task(void*) {
  WiFiUDP udp;
  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);
  uint32_t revision = UINT32_MAX;
  uint32_t last_connect = 0;
  uint32_t last_send = 0;
  uint32_t sequence = 0;
  char previous_session[17]{};
  Configuration active;
  while (true) {
    Configuration next = get_configuration();
    const uint32_t now = millis();
    if (next.revision != revision) {
      active = next;
      revision = next.revision;
      WiFi.disconnect(false, false);
      udp.stop();
      if (active.valid) {
        WiFi.begin(active.ssid.c_str(), active.password.c_str());
        last_connect = now;
      }
    }
    if (active.valid && WiFi.status() != WL_CONNECTED && now - last_connect >= 15000) {
      WiFi.disconnect(false, false);
      WiFi.begin(active.ssid.c_str(), active.password.c_str());
      last_connect = now;
    }
    if (active.valid && WiFi.status() == WL_CONNECTED && now - last_send >= 20) {
      last_send = now;
      const auto pose = get_snapshot();
      if (strcmp(previous_session, pose.session) != 0) {
        strcpy(previous_session, pose.session);
        sequence = 0;
      }
      headtracker::PosePacket packet(pose.session, sequence++, pose.state,
                                    pose.recenter, pose.orientation.array);
      if (udp.beginPacket(active.receiver, active.port)) {
        udp.write(packet.data(), packet.size());
        udp.endPacket();
      }
    }
    vTaskDelay(pdMS_TO_TICKS(2));
  }
}

bool valid_address(const IPAddress& ip) {
  return ip[0] != 0 && ip[0] != 127 && ip[0] < 224 && ip != IPAddress(255,255,255,255);
}

void respond(const String& id, bool ok, const char* error = nullptr) {
  JsonDocument response;
  response["id"] = id;
  response["ok"] = ok;
  if (error) { response["error"] = error; }
  const auto config = get_configuration();
  response["ssid"] = config.ssid;
  response["receiver_ip"] = config.valid ? config.receiver.toString() : "";
  response["receiver_port"] = config.port;
  response["wifi"] = WiFi.status() == WL_CONNECTED ? "connected" : "disconnected";
  response["ip"] = WiFi.localIP().toString();
  const auto pose = get_snapshot();
  response["state"] = static_cast<int>(pose.state);
  response["usb_stream"] = usb_stream_enabled.load();
  const auto battery = get_battery_status();
  response["battery_percent"] = battery.percent;
  if (battery.charging == m5::Power_Class::charge_unknown) { response["battery_charging"] = nullptr; }
  else { response["battery_charging"] = battery.charging == m5::Power_Class::is_charging; }
  response["uptime_ms"] = millis();
  response["reset_reason"] = static_cast<int>(esp_reset_reason());
  response["sample_count"] = pose.sample_count;
  auto quaternion = response["quaternion"].to<JsonArray>();
  auto accel = response["accel"].to<JsonArray>();
  auto gyro = response["gyro"].to<JsonArray>();
  for (float value : pose.orientation.array) { quaternion.add(value); }
  for (float value : pose.accel.array) { accel.add(value); }
  for (float value : pose.gyro.array) { gyro.add(value); }
  response["front_reference_valid"] = pose.front_reference_valid;
  if (pose.front_reference_valid && pose.state == headtracker::Ready) {
    const auto direction = headtracker::reference_front(pose.front_reference, pose.orientation);
    auto front = response["front_direction"].to<JsonArray>();
    for (float value : direction.array) { front.add(value); }
  }
  String encoded;
  serializeJson(response, encoded);
  encoded += '\n';
  Serial.write(reinterpret_cast<const uint8_t*>(encoded.c_str()), encoded.length());
}

void command(const String& line) {
  JsonDocument request;
  if (deserializeJson(request, line)) { respond("", false, "invalid_json"); return; }
  const String id = request["id"].as<String>();
  if (id.length() > 32) { respond("", false, "invalid_id"); return; }
  const String action = request["cmd"].as<String>();
  if (action == "status") { respond(id, true); return; }
  if (action == "stream") {
    if (!request["enabled"].is<bool>()) { respond(id, false, "invalid_stream_flag"); return; }
    usb_stream_enabled = request["enabled"].as<bool>();
    usb_stream_renewed = millis();
    respond(id, true);
    return;
  }
  if (action != "configure" && action != "receiver") { respond(id, false, "unknown_command"); return; }
  if ((action == "configure" && (!request["ssid"].is<const char*>() || !request["password"].is<const char*>())) ||
      !request["receiver_ip"].is<const char*>() || !request["receiver_port"].is<int>()) {
    respond(id, false, "invalid_configuration"); return;
  }
  Configuration next = get_configuration();
  if (action == "configure") {
    next.ssid = request["ssid"].as<String>();
    next.password = request["password"].as<String>();
  }
  const int port = request["receiver_port"].as<int>();
  if (next.ssid.isEmpty() || next.ssid.length() > 32 || next.password.length() > 63 ||
      next.ssid.length() != strlen(next.ssid.c_str()) ||
      next.password.length() != strlen(next.password.c_str()) ||
      !next.receiver.fromString(request["receiver_ip"].as<const char*>()) ||
      !valid_address(next.receiver) || port < 1 || port > 65535) {
    respond(id, false, "invalid_configuration"); return;
  }
  next.port = port;
  next.valid = true;
  JsonDocument stored;
  stored["ssid"] = next.ssid;
  stored["password"] = next.password;
  stored["receiver_ip"] = next.receiver.toString();
  stored["receiver_port"] = next.port;
  String encoded;
  serializeJson(stored, encoded);
  if (!storage_ready || preferences.putString("config", encoded) == 0) {
    respond(id, false, "storage_failed"); return;
  }
  xSemaphoreTake(configuration_mutex, portMAX_DELAY);
  next.revision = configuration.revision + 1;
  configuration = next;
  xSemaphoreGive(configuration_mutex);
  respond(id, true);
}

void service_serial() {
  // Bound work per loop even if a host continuously sends bytes.
  for (int budget = 128; budget > 0 && Serial.available(); --budget) {
    char value = Serial.read();
    if (value == '\n') {
      if (!dropping_line && serial_line.length()) { command(serial_line); }
      serial_line = "";
      dropping_line = false;
    } else if (value != '\r' && !dropping_line) {
      if (serial_line.length() < 1024) { serial_line += value; }
      else { serial_line = ""; dropping_line = true; }
    }
  }
}

void service_usb_stream() {
  const uint32_t now = millis();
  if (usb_stream_enabled && now - usb_stream_renewed >= kUsbLeaseMs) { usb_stream_enabled = false; }
  if (!usb_stream_enabled || !Serial) { return; }
  static uint32_t last_send = 0;
  static uint32_t sequence = 0;
  static char session[17]{};
  if (now - last_send < 20) { return; }
  last_send = now;
  const auto pose = get_snapshot();
  if (strcmp(session, pose.session) != 0) { strcpy(session, pose.session); sequence = 0; }
  JsonDocument message;
  message["type"] = "pose";
  message["session"] = pose.session;
  message["sequence"] = sequence & 0x7fffffff;
  message["state"] = static_cast<int>(pose.state);
  message["recenter"] = pose.recenter;
  auto values = message["quaternion"].to<JsonArray>();
  for (float value : pose.orientation.array) { values.add(value); }
  char frame[256];
  if (measureJson(message) >= sizeof(frame)-1) { return; }
  const size_t size = serializeJson(message, frame, sizeof(frame)-1);
  frame[size] = '\n';
  // A host which stops reading must not block the display or sensor task.
  if (Serial.availableForWrite() >= static_cast<int>(size+1)) {
    Serial.write(reinterpret_cast<const uint8_t*>(frame), size+1);
    ++sequence;
  }
}

void usb_task(void*) {
  // Own all serial I/O here so JSON replies and poses cannot interleave.
  // LCD transfers and Wi-Fi reconnection must not delay USB pose delivery.
  TickType_t wake = xTaskGetTickCount();
  while (true) {
    service_serial();
    service_usb_stream();
    vTaskDelayUntil(&wake, pdMS_TO_TICKS(2));
  }
}

void draw_status() {
  if (!display_ready) { return; }
  const auto pose = get_snapshot();
  const auto config = get_configuration();
  // Complete the frame offscreen. Never expose a cleared LCD between frames.
  auto& canvas = display_canvas;
  canvas.fillScreen(TFT_BLACK);
  canvas.setTextSize(1);
  canvas.setTextColor(TFT_CYAN, TFT_BLACK);
  canvas.setCursor(5, 5);
  canvas.print("HEAD TRACKER");

  const auto battery = get_battery_status();
  const bool charging = battery.charging == m5::Power_Class::is_charging;
  const uint16_t battery_color = battery.percent < 0 ? TFT_DARKGREY : charging ? TFT_CYAN :
                                 battery.percent <= 20 ? TFT_RED : TFT_GREEN;
  const int bx = canvas.width() - 50;
  canvas.drawRect(bx, 5, 14, 8, battery_color);
  canvas.fillRect(bx + 14, 7, 2, 4, battery_color);
  if (battery.percent > 0) {
    canvas.fillRect(bx + 2, 7, (battery.percent * 10 + 99) / 100, 4, battery_color);
  }
  canvas.setTextColor(battery.percent >= 0 && battery.percent <= 20 ? TFT_RED : TFT_WHITE, TFT_BLACK);
  canvas.setCursor(bx + 19, 5);
  if (battery.percent >= 0) { canvas.printf("%3d%%", battery.percent); }
  else { canvas.print(" --%"); }
  if (charging) {
    canvas.setTextColor(TFT_CYAN, TFT_BLACK);
    canvas.setCursor(bx + 25, 18);
    canvas.print("CHG");
  }

  const int cx = canvas.width() / 2;
  constexpr int cy = 76, radius = 43;
  canvas.drawCircle(cx, cy, radius, TFT_DARKGREY);
  for (int mark = 0; mark < 12; ++mark) {
    const float angle = mark * 3.14159265359f / 6;
    canvas.drawLine(cx + std::sin(angle)*(radius-4), cy - std::cos(angle)*(radius-4),
                    cx + std::sin(angle)*radius, cy - std::cos(angle)*radius, TFT_DARKGREY);
  }
  canvas.setTextColor(TFT_CYAN, TFT_BLACK);
  canvas.setCursor(cx - 12, 21);
  canvas.print("HEAD");
  canvas.setCursor(11, cy - 3);
  canvas.print("L");
  canvas.setCursor(canvas.width() - 17, cy - 3);
  canvas.print("R");
  if (pose.state == headtracker::Ready && pose.front_reference_valid) {
    const auto direction = headtracker::reference_front(pose.front_reference, pose.orientation);
    const float length = std::hypot(direction.axis.x, direction.axis.y);
    if (length > .05f) {
      const float dx = -direction.axis.y / length;
      const float dy = -direction.axis.x / length;
      canvas.fillTriangle(cx + dx*34, cy + dy*34,
                          cx - dx*8 - dy*6, cy - dy*8 + dx*6,
                          cx - dx*8 + dy*6, cy - dy*8 - dx*6, TFT_YELLOW);
      canvas.fillCircle(cx, cy, 3, TFT_WHITE);
    } else {
      canvas.setTextColor(TFT_YELLOW, TFT_BLACK);
      canvas.setCursor(cx - 12, cy - 3);
      canvas.print(direction.axis.z > 0 ? "UP" : "DOWN");
    }
    canvas.setTextColor(TFT_WHITE, TFT_BLACK);
    canvas.setCursor(5, 128);
    canvas.printf("Yaw %+6.1f deg", headtracker::relative_yaw(pose.front_reference, pose.orientation));
  } else {
    canvas.setTextColor(TFT_YELLOW, TFT_BLACK);
    canvas.setCursor(cx - 12, cy - 3);
    canvas.print("WAIT");
  }
  canvas.setTextColor(pose.state == headtracker::Ready ? TFT_GREEN : TFT_YELLOW, TFT_BLACK);
  canvas.setCursor(5, 144);
  canvas.print(pose.state == headtracker::Ready ? "TRACKING" :
               pose.state == headtracker::ImuError ? "IMU ERROR" : "CAL: keep still");
  canvas.setTextColor(TFT_WHITE, TFT_BLACK);
  canvas.setCursor(5, 158);
  canvas.printf("WiFi: %s", WiFi.status() == WL_CONNECTED ? "connected" : "waiting");
  canvas.setCursor(5, 170);
  canvas.printf("IP:%s", WiFi.localIP().toString().c_str());
  canvas.setCursor(5, 182);
  if (config.valid) {
    canvas.printf("To:%s", config.receiver.toString().c_str());
    canvas.setCursor(5, 194);
    canvas.printf("Port:%u", config.port);
  } else {
    canvas.print("Setup via USB on PC");
  }
  canvas.setCursor(5, 206);
  canvas.print(usb_stream_enabled ? "USB: streaming" : "USB: standby");
  canvas.setTextColor(TFT_DARKGREY, TFT_BLACK);
  canvas.setCursor(5, 222);
  canvas.print("A:front B3s:cal");
  canvas.pushSprite(0, 0);
}

}  // namespace

void setup() {
  auto config = M5.config();
  config.internal_imu = true;
  M5.begin(config);
  // Portrait: the top of the dial is the non-USB end, i.e. head forward.
  M5.Display.setRotation(0);
  M5.Display.setBrightness(60);
  display_canvas.setColorDepth(16);
  display_ready = display_canvas.createSprite(M5.Display.width(), M5.Display.height()) != nullptr;
  if (!display_ready) {
    M5.Display.setTextColor(TFT_RED, TFT_BLACK);
    M5.Display.setCursor(3, 5);
    M5.Display.print("Display buffer failed");
  }
  Serial.begin(115200);
  Serial.setRxBufferSize(2048);
  Serial.setTxBufferSize(2048);
  serial_line.reserve(1024);
  configuration_mutex = xSemaphoreCreateMutex();
  storage_ready = preferences.begin("head-tracker", false);
  if (storage_ready && preferences.isKey("config")) {
    JsonDocument saved;
    const auto raw = preferences.getString("config", "");
    if (!deserializeJson(saved, raw)) {
      configuration.ssid = saved["ssid"].as<String>();
      configuration.password = saved["password"].as<String>();
      int port = saved["receiver_port"] | 9000;
      const char* ip = saved["receiver_ip"] | "";
      configuration.valid = configuration.receiver.fromString(ip) && valid_address(configuration.receiver)
                            && configuration.ssid.length() > 0 && configuration.ssid.length() <= 32
                            && configuration.password.length() <= 63 && port > 0 && port <= 65535;
      configuration.port = port;
    }
  }
  new_session(snapshot);
  imu_ready = M5.Imu.getType() == m5::imu_bmi270;
  if (imu_ready) {
    M5.Imu.setCalibration(0, 0, 0);
    M5.Imu.clearOffsetData();
    // Match M5Unified's default conversion factors: accel +/-8g, gyro +/-2000dps.
    auto* imu = M5.Imu.getImuInstancePtr(0);
    imu_ready = imu->writeRegister8(0x40, 0xA8) && imu->writeRegister8(0x41, 0x02)
                && imu->writeRegister8(0x42, 0xE8) && imu->writeRegister8(0x43, 0x00)
                && imu->writeRegister8(0x7D, 0x0E);
  }
  BaseType_t sensor_ok = xTaskCreatePinnedToCore(sensor_task, "head-imu", 6144, nullptr, 3, nullptr, 1);
  BaseType_t network_ok = xTaskCreatePinnedToCore(network_task, "head-osc", 6144, nullptr, 1, nullptr, 0);
  // HWCDC's FIFO flush must run on the same core as Serial.begin()/its ISR.
  // Priority stays below IMU acquisition and above the LCD loop.
  BaseType_t usb_ok = xTaskCreatePinnedToCore(usb_task, "head-usb", 6144, nullptr, 2, nullptr, 1);
  if (sensor_ok != pdPASS || network_ok != pdPASS || usb_ok != pdPASS) {
    portENTER_CRITICAL(&snapshot_mutex);
    snapshot.state = headtracker::ImuError;
    portEXIT_CRITICAL(&snapshot_mutex);
  }
}

void loop() {
  M5.update();
  if (M5.BtnA.wasPressed()) {
    portENTER_CRITICAL(&snapshot_mutex);
    snapshot.recenter = (snapshot.recenter + 1) & 0x7fffffff;
    portEXIT_CRITICAL(&snapshot_mutex);
  }
  static bool held = false;
  if (M5.BtnB.pressedFor(3000) && !held) {
    portENTER_CRITICAL(&snapshot_mutex);
    reset_requested = true;
    portEXIT_CRITICAL(&snapshot_mutex);
    held = true;
  }
  if (!M5.BtnB.isPressed()) { held = false; }
  static uint32_t last_display = 0;
  if (millis() - last_display >= 50) { last_display = millis(); draw_status(); }
  delay(2);
}
