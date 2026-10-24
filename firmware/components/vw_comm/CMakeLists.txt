idf_component_register(
    SRCS "net_proto.c" "net_client.c" "wifi_client.c"
    INCLUDE_DIRS "include"
    REQUIRES vw_utils
    PRIV_REQUIRES esp_wifi esp_netif esp_event nvs_flash lwip esp_timer
)
