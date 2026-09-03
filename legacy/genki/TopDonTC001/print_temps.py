#!/usr/bin/env python3

import argparse
import cv2
import numpy as np
import subprocess
import sys
import time

def main():
  parser = argparse.ArgumentParser()
  parser.add_argument('--device', type=str, default='/dev/video0', help="Video device file (see v4l2-ctl --list-devices).")
  args = parser.parse_args()

  cap = cv2.VideoCapture(args.device, cv2.CAP_V4L)
  cap.set(cv2.CAP_PROP_CONVERT_RGB, 0.0)

  while cap.isOpened():
    ret, frame = cap.read()
    if not ret:
      continue

    _, data_ir = np.array_split(frame, 2)
    t_kelvin = (data_ir[..., 0] + (data_ir[..., 1] * 256)) / 64
    t_celcius = t_kelvin - 273.15

    print(np.max(t_celcius))

if __name__ == '__main__':
  main()
