#!/usr/bin/env python3

import cv2
import enum
import glob
import numpy as np
import pathlib

IMG_WIDTH = 256
IMG_HEIGHT = 192
IMG_SCALE = 3

class LabelType(enum.Enum):
  RectTop = 1
  RectBottom = 2
  Global = 3

# Note: The fixture hasn't been finalized yet, so these values are bound to change.
SUBSECTIONS_TR302_PA = [
  # (x1, y1, x2, y2, LabelType)
  (0, 0, IMG_WIDTH - 1, IMG_HEIGHT - 1, LabelType.Global), # Whole image
  (131, 57, 147, 70, LabelType.RectTop), # GaNFET Q11
  (131, 82, 147, 95, LabelType.RectTop), # GaNFET Q1
]

SUBSECTIONS_TR1K_PA = [
  # (x1, y1, x2, y2, LabelType)
  (0, 0, IMG_WIDTH - 1, IMG_HEIGHT - 1, LabelType.Global), # Whole image
  (108, 28, 121, 35, LabelType.RectTop), # GaNFET
  (119, 44, 162, 50, LabelType.RectTop), # PA Caps
  (180, 42, 189, 49, LabelType.RectTop), # L3 core
  (138, 54, 143, 84, LabelType.RectBottom), # L2 core
  (57, 96, 67, 110, LabelType.RectTop), # L13 core
  (82, 107, 114, 111, LabelType.RectTop), # L2 left winding
  (86, 135, 89, 139, LabelType.RectTop), # L8 core
]

#SUBSECTIONS = SUBSECTIONS_TR1K_PA
SUBSECTIONS = SUBSECTIONS_TR302_PA

def max_in_rectangle(img, t_celcius_input, subsection):
  x1, y1, x2, y2, label_type = subsection
  cv2.rectangle(img, (x1 * IMG_SCALE, y1 * IMG_SCALE), (x2 * IMG_SCALE, y2 * IMG_SCALE),
                color=(0, 0, 0), thickness=int(1/2 * IMG_SCALE))

  t_celcius = t_celcius_input[y1:y2 + 1, x1:x2 + 1]

  y_max, x_max = np.unravel_index(np.argmax(t_celcius), t_celcius.shape)
  x_max = x1 + x_max
  y_max = y1 + y_max

  #label_txt = f'{np.max(t_celcius):0.1f} C'
  label_txt = f'{np.max(t_celcius):0.1f}'
  cv2.circle(img, (x_max * IMG_SCALE, y_max * IMG_SCALE),
             int((5/3 if label_type == LabelType.Global else 1) * IMG_SCALE),
             (255, 255, 255), int((1 if label_type == LabelType.Global else 2/3) * IMG_SCALE))
  if label_type != LabelType.Global:
    cv2.circle(img, (x_max * IMG_SCALE, y_max * IMG_SCALE), IMG_SCALE,
              (0, 0, 0), int(1/2 * IMG_SCALE))

  label_size = 0.75
  label_thickness = 2
  if label_type == LabelType.RectTop:
    label_x, label_y = (x1 - 1) * IMG_SCALE, (y1 - 1) * IMG_SCALE
  elif label_type == LabelType.RectBottom:
    label_x, label_y = (x1 - 1) * IMG_SCALE, (y2 + 8) * IMG_SCALE
  elif label_type == LabelType.Global:
    label_x, label_y = (x_max - 27) * IMG_SCALE, (y_max + 4) * IMG_SCALE
    label_size = 1
    label_thickness = 3
  else:
    assert(False)
  cv2.putText(img, label_txt, (label_x, label_y), cv2.FONT_HERSHEY_SIMPLEX, label_size, (0, 0, 0), label_thickness, cv2.LINE_AA)
  cv2.putText(img, label_txt, (label_x, label_y), cv2.FONT_HERSHEY_SIMPLEX, label_size, (255, 255, 255), label_thickness - 1, cv2.LINE_AA)

def frame_to_image(frame):
  data_visible, data_ir = np.array_split(frame, 2)
  t_kelvin = (data_ir[..., 0] + (data_ir[..., 1] * 256)) / 64
  t_celcius = t_kelvin - 273.15

  img = cv2.cvtColor(data_visible, cv2.COLOR_YUV2BGR_YUYV)
  img = cv2.convertScaleAbs(img, alpha=1.0)
  img = cv2.resize(img, (IMG_WIDTH * IMG_SCALE, IMG_HEIGHT * IMG_SCALE), interpolation=cv2.INTER_CUBIC)
  img = cv2.applyColorMap(img, cv2.COLORMAP_PLASMA)

  for subsection in SUBSECTIONS:
    max_in_rectangle(img, t_celcius, subsection)

  return img

def main():
  for file in sorted(glob.glob('*.npy')):
    with open(file, 'rb') as fd:
      img = frame_to_image(np.load(fd))
      out = f"{pathlib.Path(file).stem}.jpg"
      cv2.imwrite(out, img)
      print(f"Wrote {out}")

if __name__ == '__main__':
  main()
