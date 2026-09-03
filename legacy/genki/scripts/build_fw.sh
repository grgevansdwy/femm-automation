#!/bin/sh

build_fw() {
  mkdir -p build
  cd build
  # cmake ../ -DCMAKE_BUILD_TYPE=Debug
  cmake ../ -DCMAKE_BUILD_TYPE=MinSizeRel
  make -j
  make ota
  cd ..
}

build_web() {
  cd web/app
  npm install
  cd ..
  mkdir build
  cd build
  ../build.py
  cd ..
}

build_fw
build_web

zip -j /mnt/c/Users/Vendor/Desktop/Firmware/vendor_firmware_custom_$(git rev-parse --short=8 HEAD).zip build/tx-m7-ota.bin build/rx-m4-ota.bin web/build/WEB.RFS