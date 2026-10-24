idf_component_register(
    SRCS "wakeword.c"
    INCLUDE_DIRS "include"
    REQUIRES vw_utils
    PRIV_REQUIRES esp-sr
)
