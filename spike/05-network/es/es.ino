// Spike step 5, question 5 (negative control): what does an unmodified wired-Ethernet sketch do
// under QEMU? ETH.begin() with the board defaults drives the ESP32 internal EMAC + LAN8720,
// which QEMU doesn't model. Licence zone: firmware -> permissive, MIT/Apache-2.0 to be decided.
#include <ETH.h>

void setup() {
  Serial.begin(115200);
  delay(100);
  Serial.println("STOCK start, calling ETH.begin()");
  unsigned long t = millis();
  // ETH.begin() without arguments returns false at once here: path B's generic board defines no
  // ETH_PHY_* pins. So use the explicit call a LAN8720 board sketch uses (PHY addr 1, MDC 23,
  // MDIO 18, no power pin, 50 MHz clock in on GPIO0) to exercise the internal EMAC.
  bool ok = ETH.begin(ETH_PHY_LAN8720, 1, 23, 18, -1, ETH_CLOCK_GPIO0_IN);
  Serial.printf("STOCK ETH.begin() returned %d after %lu ms\n", ok, millis() - t);
}

void loop() {
  Serial.printf("STOCK t=%lus linkUp=%d hasIP=%d ip=%s\n", millis() / 1000, ETH.linkUp(), ETH.hasIP(),
                ETH.localIP().toString().c_str());
  delay(2000);
}
