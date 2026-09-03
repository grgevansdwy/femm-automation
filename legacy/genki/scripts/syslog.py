# /// script
# dependencies = [
#     "pandas",
# ]
# ///

import math
import sys

import pandas as pd

def print_row(row, count=1, indent=0):
    print(f"{" " * 2 * indent}t={row['Seconds']} {row['Code']} {row['Event']}{f" (x{count})" if count > 1 else ""}")

def main(csv):
    row = None
    count = 0
    data = pd.read_csv(csv, header=None, names=['Device','Seconds','Level','Run','Code','Event'])
    for _, next_row in data.iterrows():
        if row is not None:
            if next_row['Code'] == row['Code']:
                count += 1
            else:
                if row['Code'] == 'GS09':
                    print_row(row)
                    print("  In-Between Runs")
                elif row['Event'] in ["Entering Power Tracking", "Logging ADC Values to:"]:
                    pass
                elif row['Code'] == 'ST06':
                    run_id = row['Event'][len("/LOG/ADC/TX_"):len("/LOG/ADC/TX_0000")]
                    print_row(row, indent=1)
                elif row['Code'] in ['ST07', 'ST08']:
                    pass
                elif row['Event'] == 'Charging Stopped':
                    print("  In-Between Runs")
                else:
                    print_row(row, count=count, indent=2)
                count = 1
        row = next_row

if __name__ == '__main__':
    main(sys.argv[1])
