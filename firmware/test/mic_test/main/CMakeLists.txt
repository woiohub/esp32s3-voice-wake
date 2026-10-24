idf_component_register(
    SRCS "mic_test.c"
    INCLUDE_DIRS "."
    REQUIRES driver esp_driver_i2s esp_driver_gpio
)
