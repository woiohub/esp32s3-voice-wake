idf_component_register(
    SRCS "app_main.c"
    INCLUDE_DIRS "."
    REQUIRES vw_utils vw_audio vw_config vw_wakeword vw_voice
)
