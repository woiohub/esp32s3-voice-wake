idf_component_register(
    SRCS "ble_prov.c"
    INCLUDE_DIRS "include"
    REQUIRES vw_utils
    PRIV_REQUIRES bt json
)
