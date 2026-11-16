// Spike step 2: minimal check. On ESP32-S3 under QEMU, does merely linking the
// ADC driver (any analogRead call) stop the firmware before setup() runs?
// Control: the step 1 sketch (no ADC) boots fine.
void setup() {
  Serial.begin(115200);
  Serial.println("\n[adc_min] setup() reached");
}

void loop() {
  Serial.printf("[adc_min] analogRead=%d\n", analogRead(1));
  delay(1000);
}
