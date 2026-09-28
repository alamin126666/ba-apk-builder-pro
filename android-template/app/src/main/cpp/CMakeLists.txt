cmake_minimum_required(VERSION 3.22.1)
project(protected_loader LANGUAGES CXX)

add_library(protected_loader SHARED native_loader.cpp)
target_compile_features(protected_loader PRIVATE cxx_std_17)
target_compile_options(protected_loader PRIVATE -fvisibility=hidden -ffunction-sections -fdata-sections -fstack-protector-strong)
target_link_options(protected_loader PRIVATE -Wl,--gc-sections -Wl,--exclude-libs,ALL)
target_link_libraries(protected_loader PRIVATE android)
