#!/usr/bin/env python3

import argparse
import cv2
import numpy as np
import subprocess
import sys
import time

from vendor.interface import VendorSocket
from vendor.core import packettools as wpt

IMG_WIDTH = 256
IMG_HEIGHT = 192
IMG_SCALE = 3

BAD_TEMP_CELCIUS = 75
DURATION_SECONDS = 5 * 60

vendor = VendorSocket("ws://192.168.2.20/ws")

def finish(msg):
  vendor.set_parameter(wpt.DeviceID.RX_1, wpt.ParamID.ChargeEnable, 0)
  vendor.set_parameter(wpt.DeviceID.TX, wpt.ParamID.ChargeEnable, 0)
  print(msg)
  sys.exit()

def main():
  parser = argparse.ArgumentParser()
  parser.add_argument('--device', type=str, default='/dev/video0',
                      help="Video device file (see v4l2-ctl --list-devices).")
  args = parser.parse_args()

  cap = cv2.VideoCapture(args.device, cv2.CAP_V4L)
  cap.set(cv2.CAP_PROP_CONVERT_RGB, 0.0)

  print(f'Watching out for temperatures greater than {BAD_TEMP_CELCIUS}C...')
  serial_number = input("Scan serial number: ")

  cv2.namedWindow('GUI', cv2.WINDOW_GUI_NORMAL)
  cv2.resizeWindow('GUI', IMG_WIDTH * IMG_SCALE, IMG_HEIGHT * IMG_SCALE)
  t_seen = None

  print('Starting charging...')
  vendor.set_parameter(wpt.DeviceID.TX, wpt.ParamID.ChargeEnable, 1)
  vendor.set_parameter(wpt.DeviceID.RX_1, wpt.ParamID.ChargeEnable, 1)

  START_TIME = time.time()
  while cap.isOpened():
    if (time.time() - START_TIME) > DURATION_SECONDS:
      finish(f"Went for {DURATION_SECONDS} seconds without seeing temperatures greater than {BAD_TEMP_CELCIUS} C! Exiting...")

    ret, frame = cap.read()
    if not ret:
      continue

    data_visible, data_ir = np.array_split(frame, 2)
    t_kelvin = (data_ir[..., 0] + (data_ir[..., 1] * 256)) / 64
    t_celcius = t_kelvin - 273.15

    t_max_col, t_max_row = np.unravel_index(np.argmax(t_celcius), t_celcius.shape)

    t_max_celcius = np.max(t_celcius)
    t_text = f'{t_max_celcius:0.1f} C'
    output = cv2.cvtColor(data_visible, cv2.COLOR_YUV2BGR_YUYV)
    output = cv2.convertScaleAbs(output, alpha=1.0)
    output = cv2.resize(output, (IMG_WIDTH * IMG_SCALE, IMG_HEIGHT * IMG_SCALE), interpolation=cv2.INTER_CUBIC)

    image = cv2.applyColorMap(output, cv2.COLORMAP_JET)
    t_max_row *= IMG_SCALE
    t_max_col *= IMG_SCALE
    cv2.circle(image, (t_max_row, t_max_col), 5, (255, 255, 255), 3)
    cv2.putText(image, t_text, (t_max_row + 10, t_max_col + 10), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(image, t_text, (t_max_row + 10, t_max_col + 10), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)

    if t_max_celcius > BAD_TEMP_CELCIUS:
      filename = f'{serial_number}_{int(time.time() - START_TIME)}seconds.jpg'
      cv2.imwrite(filename, image)
      subprocess.check_call('play -nq -t alsa synth 1 sine 440', shell=True)
      finish(f"Found bad temperature: {t_text}! Saved picture to {filename} and turned off charging.")
    else:
      cv2.imshow('GUI', image)
      if cv2.waitKey(1) == 27 or cv2.getWindowProperty('GUI', cv2.WND_PROP_VISIBLE) < 1:
        finish("GUI closed prematurely")

if __name__ == '__main__':
  main()
