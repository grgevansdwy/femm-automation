# /// script
# dependencies = [
#     "pyserial",
# ]
# ///

import serial
import time
import re

# SERIAL_PORT = '/dev/ttyUSB0'
SERIAL_PORT = 'COM4'
BAUD_RATE = 115200

class ThreeDPrinter:
    def __init__(self, port, baudrate, timeout=5):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.ser = None

    def __enter__(self):
        self.ser = serial.Serial(self.port, self.baudrate, timeout=self.timeout)
        time.sleep(2)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.ser and self.ser.is_open:
            self.ser.close()

    def send_command(self, command, wait_for="ok"):
        command = command.strip() + '\n'
        self.ser.write(command.encode('utf-8'))
        responses = []
        while True:
            line = self.ser.readline().decode('utf-8').strip()
            print(line)
            if line:
                responses.append(line)
                if wait_for and wait_for.lower() in line.lower():
                    break
        return responses

    def get_position(self):
        responses = self.send_command("M114")
        for line in responses:
            if "X:" in line:
                match = re.search(r"X:([\d\.]+)\s+Y:([\d\.]+)\s+Z:([\d\.]+)", line)
                if match:
                    x, y, z = match.groups()
                    return {"X": float(x), "Y": float(y), "Z": float(z)}
        return None

if __name__ == "__main__":
    with ThreeDPrinter(SERIAL_PORT, BAUD_RATE) as printer:
        pos = printer.get_position()
        print(f"Current Position: {pos}")

        printer.send_command("G91")          # Relative positioning
        printer.send_command("G1 Y10 F3000") # Move Y +10mm
        printer.send_command("G90")          # Back to absolute positioning

        pos = printer.get_position()
        print(f"Current Position: {pos}")