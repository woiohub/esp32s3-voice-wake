# 独立测试工程：INMP441 最小采集测试
# 不依赖主项目组件，可单独 build / flash，用来判断麦克风到底有没有输出数据
cmake_minimum_required(VERSION 3.16)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(mic_test)
