#include <Arduino.h>
#include <M5Unified.h>

namespace {

uint32_t press_count = 0;

void draw_status(const char* message) {
  M5.Display.fillRect(0, 58, M5.Display.width(), 42, TFT_BLACK);
  M5.Display.setCursor(8, 62);
  M5.Display.printf("%s\n", message);
  M5.Display.printf("Presses: %lu", static_cast<unsigned long>(press_count));
}

}  // namespace

void setup() {
  auto config = M5.config();
  M5.begin(config);

  Serial.begin(115200);
  delay(100);

  M5.Display.setRotation(1);
  M5.Display.fillScreen(TFT_BLACK);
  M5.Display.setTextColor(TFT_WHITE, TFT_BLACK);
  M5.Display.setTextSize(1);
  M5.Display.setCursor(8, 8);
  M5.Display.println("M5StickS3");
  M5.Display.println("PlatformIO playground");
  M5.Display.drawFastHLine(8, 48, M5.Display.width() - 16, TFT_DARKGREY);
  draw_status("Ready: press A or B");

  Serial.println("hello-display started");
}

void loop() {
  M5.update();

  if (M5.BtnA.wasPressed()) {
    ++press_count;
    draw_status("Button A pressed");
    Serial.printf("button=A count=%lu\n", static_cast<unsigned long>(press_count));
  }

  if (M5.BtnB.wasPressed()) {
    --press_count;
    draw_status("Button B pressed");
    Serial.printf("button=B count=%lu\n", static_cast<unsigned long>(press_count));
  }

  delay(5);
}
