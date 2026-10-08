cmake_minimum_required(VERSION 3.18.1)
project(opusjni C)

# libopus is built from the official release tarball. Drop an extracted copy into
# ./opus to build offline; otherwise CMake downloads and verifies it at configure time.
set(OPUS_VERSION 1.5.2)
set(OPUS_SHA256 65c1d2f78b9f2fb20082c38cbe47c951ad5839345876e46941612ee87f9a7ce1)

set(OPUS_BUILD_SHARED_LIBRARY OFF CACHE BOOL "" FORCE)
set(OPUS_BUILD_TESTING OFF CACHE BOOL "" FORCE)
set(OPUS_BUILD_PROGRAMS OFF CACHE BOOL "" FORCE)
set(OPUS_INSTALL_PKG_CONFIG_MODULE OFF CACHE BOOL "" FORCE)
set(OPUS_INSTALL_CMAKE_CONFIG_MODULE OFF CACHE BOOL "" FORCE)
set(OPUS_DRED OFF CACHE BOOL "" FORCE)
set(OPUS_OSCE OFF CACHE BOOL "" FORCE)
set(BUILD_TESTING OFF CACHE BOOL "" FORCE)

if(EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/opus/CMakeLists.txt)
    add_subdirectory(${CMAKE_CURRENT_SOURCE_DIR}/opus opus EXCLUDE_FROM_ALL)
else()
    include(FetchContent)
    FetchContent_Declare(
        opus
        URL https://downloads.xiph.org/releases/opus/opus-${OPUS_VERSION}.tar.gz
        URL_HASH SHA256=${OPUS_SHA256}
    )
    FetchContent_MakeAvailable(opus)
endif()

# Debug variants would otherwise compile libopus at -O0, too slow for real-time encoding.
target_compile_options(opus PRIVATE -O3)

add_library(opusjni SHARED opus_jni.c)
target_link_libraries(opusjni PRIVATE opus)
# 16 KB page alignment (required for Android 15+ devices using 16 KB pages).
target_link_options(opusjni PRIVATE "-Wl,-z,max-page-size=16384")
