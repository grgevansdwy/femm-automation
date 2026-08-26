#!/usr/bin/env python3

import argparse
import cv2
import numpy as np
import time

def main():
  parser = argparse.ArgumentParser()
  parser.add_argument('--device', type=str, default='/dev/video0',
                      help="Video device file (see v4l2-ctl --list-devices).")
  args = parser.parse_args()

  cap = cv2.VideoCapture(args.device, cv2.CAP_V4L)
  cap.set(cv2.CAP_PROP_CONVERT_RGB, 0.0)
  START_TIME = time.time()
  while cap.isOpened():
    ret, frame = cap.read()
    if not ret:
      continue
    elapsed_time = int(time.time() - START_TIME)
    filename = f"{elapsed_time:04d}.npy"
    with open(filename, 'wb') as fd:
      np.save(fd, frame)
      print(f"Captured: {filename}")
    if elapsed_time < 30:
      time.sleep(1)
    elif elapsed_time < 60:
      time.sleep(2)
    elif elapsed_time < 90:
      time.sleep(5)
    else:
      time.sleep(10)

if __name__ == '__main__':
  main()