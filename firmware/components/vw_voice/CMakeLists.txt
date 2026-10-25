idf_component_register(
    SRCS "voice_service.c"
    INCLUDE_DIRS "include"
    REQUIRES vw_audio vw_config vw_comm vw_wakeword vw_prov vw_utils
    PRIV_REQUIRES json esp_timer
)
