// Spike step 5, question 5: do Arduino's networking classes work unchanged over QEMU's open_eth?
// Licence zone: firmware -> permissive, MIT/Apache-2.0 to be decided.
// Path B only (needs CONFIG_ETH_USE_OPENETH=y; see sdkconfig.arduino_openeth).
// The only non-Arduino line is QemuEth.begin() (qemu_eth.h), which stands in for ETH.begin().
#include <WiFi.h>        // for WiFiClient (an alias of NetworkClient in core 3.x)
#include <HTTPClient.h>
#include <WebServer.h>
#include "qemu_eth.h"

WebServer server(80);
static unsigned long requests = 0;

void onNetEvent(arduino_event_id_t event, arduino_event_info_t info) {
  Serial.printf("ARD EVENT %d %s\n", (int)event, Network.eventName(event));
}

void testOutbound() {
  IPAddress ip;
  unsigned long t = millis();
  int ok = Network.hostByName("example.com", ip);
  Serial.printf("ARD DNS example.com ok=%d ip=%s ms=%lu\n", ok, ip.toString().c_str(), millis() - t);

  WiFiClient c;  // the class an unmodified Wi-Fi sketch would use
  t = millis();
  if (c.connect("example.com", 80)) {
    c.print("GET / HTTP/1.0\r\nHost: example.com\r\n\r\n");
    String status = c.readStringUntil('\n');
    size_t n = status.length();
    while (c.connected() || c.available()) {
      if (c.available()) { c.read(); n++; } else { delay(1); }
    }
    status.trim();
    Serial.printf("ARD WiFiClient GET example.com -> %s bytes=%u ms=%lu\n", status.c_str(), (unsigned)n, millis() - t);
  } else {
    Serial.println("ARD WiFiClient connect FAIL");
  }

  HTTPClient http;
  t = millis();
  http.begin("http://10.0.2.2:18071/hello.txt");
  int code = http.GET();
  String body = code > 0 ? http.getString() : String(http.errorToString(code));
  body.trim();
  Serial.printf("ARD HTTPClient GET host -> %d body='%s' ms=%lu\n", code, body.c_str(), millis() - t);
  http.end();
}

void setup() {
  Serial.begin(115200);
  delay(100);
  Serial.println("ARD start");
  Network.onEvent(onNetEvent);
  if (!QemuEth.begin()) {
    Serial.println("ARD QemuEth.begin FAIL");
    return;
  }
  unsigned long t = millis();
  bool got = QemuEth.waitStatusBits(ESP_NETIF_HAS_IP_BIT, 15000) & ESP_NETIF_HAS_IP_BIT;
  Serial.printf("ARD ETH hasIP=%d ip=%s gw=%s dns=%s mac=%s ms=%lu\n", got, QemuEth.localIP().toString().c_str(),
                QemuEth.gatewayIP().toString().c_str(), QemuEth.dnsIP().toString().c_str(), QemuEth.macAddress().c_str(),
                millis() - t);
  Serial.print("ARD iface: ");
  Serial.println(QemuEth);
  testOutbound();

  server.on("/", []() {
    requests++;
    server.send(200, "text/plain", String("hello from Arduino WebServer in QEMU, request #") + requests + "\n");
  });
  server.begin();
  Serial.println("ARD WebServer listening on :80");
}

void loop() {
  server.handleClient();
  delay(1);
}
