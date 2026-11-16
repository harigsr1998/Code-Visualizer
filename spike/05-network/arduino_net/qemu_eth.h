// Spike step 5: Arduino network interface for QEMU's emulated OpenCores Ethernet MAC (open_eth).
// Licence zone: firmware (compiled into the user's firmware) -> permissive, MIT/Apache-2.0 to be decided.
//
// Arduino-ESP32 3.3.12's ETHClass only knows the ESP32 internal EMAC and SPI chips (W5500, DM9051,
// KSZ8851), so ETH.begin() can't drive open_eth. This class does what ETHClass::begin() does
// (ETH.cpp, lines ~233-430) with the openeth MAC + DP83848 PHY that QEMU models, and registers as
// Arduino network interface ETH0. Everything above it (Network, NetworkClient/WiFiClient,
// HTTPClient, WebServer, Network.onEvent with ARDUINO_EVENT_ETH_*) is Arduino's own code, unchanged.
// Needs CONFIG_ETH_USE_OPENETH=y (path B only; path A's precompiled libs don't include openeth).
#pragma once
#include "Network.h"
#include "esp_eth.h"

class QemuEthClass : public NetworkInterface {
public:
  bool begin();
  esp_eth_handle_t handle() const { return _eth_handle; }
  void _onEthEvent(int32_t event_id);

protected:
  size_t printDriverInfo(Print &out) const override;

private:
  esp_eth_handle_t _eth_handle = nullptr;
};

extern QemuEthClass QemuEth;
