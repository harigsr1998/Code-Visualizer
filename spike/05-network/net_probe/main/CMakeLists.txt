idf_component_register(SRCS "net_probe.c"
                    INCLUDE_DIRS "."
                    PRIV_REQUIRES nvs_flash esp_netif esp_event esp_timer lwip)
