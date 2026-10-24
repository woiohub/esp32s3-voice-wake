# 独立测试工程：MAX98357A 最小播放测试
# 不依赖主项目组件，可单独 build / flash，用来判断功放/喇叭到底有没有声音
cmake_minimum_required(VERSION 3.16)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(amp_test)
