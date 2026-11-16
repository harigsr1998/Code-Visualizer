// Spike step 5: see qemu_eth.h. Licence zone: firmware -> permissive, MIT/Apache-2.0 to be decided.
#include "qemu_eth.h"
#include "esp_eth_mac_openeth.h"
#include "esp_eth_phy.h"
#include "esp_netif.h"
#include "esp_event.h"

QemuEthClass QemuEth;

static void eth_event_cb(void *, esp_event_base_t base, int32_t id, void *data) {
  if (base == ETH_EVENT && *(esp_eth_handle_t *)data == QemuEth.handle()) {
    QemuEth._onEthEvent(id);
  }
}

void QemuEthClass::_onEthEvent(int32_t id) {
  arduino_event_t ev;
  ev.event_id = ARDUINO_EVENT_MAX;
  if (id == ETHERNET_EVENT_CONNECTED) {
    ev.event_id = ARDUINO_EVENT_ETH_CONNECTED;
    ev.event_info.eth_connected = _eth_handle;
    setStatusBits(ESP_NETIF_CONNECTED_BIT);
  } else if (id == ETHERNET_EVENT_DISCONNECTED) {
    ev.event_id = ARDUINO_EVENT_ETH_DISCONNECTED;
    ev.event_info.eth_disconnected = _eth_handle;
    clearStatusBits(ESP_NETIF_CONNECTED_BIT | ESP_NETIF_HAS_IP_BIT);
  } else if (id == ETHERNET_EVENT_START) {
    ev.event_id = ARDUINO_EVENT_ETH_START;
    ev.event_info.eth_started = _eth_handle;
    setStatusBits(ESP_NETIF_STARTED_BIT);
  } else if (id == ETHERNET_EVENT_STOP) {
    ev.event_id = ARDUINO_EVENT_ETH_STOP;
    ev.event_info.eth_stopped = _eth_handle;
    clearStatusBits(ESP_NETIF_STARTED_BIT | ESP_NETIF_CONNECTED_BIT | ESP_NETIF_HAS_IP_BIT);
  }
  if (ev.event_id < ARDUINO_EVENT_MAX) {
    Network.postEvent(&ev);
  }
}

bool QemuEthClass::begin() {
  if (_esp_netif) {
    return true;
  }
  Network.begin();

  eth_mac_config_t mac_cfg = ETH_MAC_DEFAULT_CONFIG();
  eth_phy_config_t phy_cfg = ETH_PHY_DEFAULT_CONFIG();
  phy_cfg.autonego_timeout_ms = 100;  // as in ESP-IDF's protocol_examples_common/eth_connect.c
  esp_eth_mac_t *mac = esp_eth_mac_new_openeth(&mac_cfg);
  esp_eth_phy_t *phy = esp_eth_phy_new_dp83848(&phy_cfg);
  if (!mac || !phy) {
    log_e("openeth MAC/PHY create failed");
    return false;
  }
  esp_eth_config_t cfg = ETH_DEFAULT_CONFIG(mac, phy);
  if (esp_eth_driver_install(&cfg, &_eth_handle) != ESP_OK) {
    log_e("esp_eth_driver_install failed");
    return false;
  }
  esp_netif_config_t ncfg = ESP_NETIF_DEFAULT_ETH();  // if_key "ETH_DEF", like ETH index 0
  _esp_netif = esp_netif_new(&ncfg);
  if (!_esp_netif || esp_netif_attach(_esp_netif, esp_eth_new_netif_glue(_eth_handle)) != ESP_OK) {
    log_e("netif create/attach failed");
    return false;
  }
  esp_event_handler_instance_register(ETH_EVENT, ESP_EVENT_ANY_ID, &eth_event_cb, nullptr, nullptr);
  initNetif(ESP_NETIF_ID_ETH0);  // Arduino's IP events -> ARDUINO_EVENT_ETH_GOT_IP, hasIP(), localIP()...
  return esp_eth_start(_eth_handle) == ESP_OK;
}

size_t QemuEthClass::printDriverInfo(Print &out) const {
  return out.print(",QEMU_OPENETH");
}
