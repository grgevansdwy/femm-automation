import argparse
import math

def calculate_missing(L, C, F):
    if [L is not None, C is not None, F is not None].count(True) != 2:
        raise ValueError("Exactly two of L/C/F must be provided.")
    if L is None:
        L = 1.0 / ((2.0 * math.pi * F) ** 2 * C)
        print(f"{L*1e9:0.1f} nH")
    if C is None:
        C = 1.0 / ((2.0 * math.pi * F) ** 2 * L)
        print(f"{C*1e12:0.1f} pF")
    if F is None:
        F = 1.0 / (2.0 * math.pi * math.sqrt(L * C))
        print(f"{F/1e6:0.3f} MHz")

def main():
    parser = argparse.ArgumentParser(description="Calculate the L/C/F given the other two.")
    parser.add_argument("-l", type=float, help="Inductance [nH]")
    parser.add_argument("-c", type=float, help="Capacitance [pF]")
    parser.add_argument("-f", type=float, help="Resonant frequency [MHz]")
    args = parser.parse_args()
    L = None if args.l is None else args.l * 1e-9
    C = None if args.c is None else args.c * 1e-12
    F = None if args.f is None else args.f * 1e6
    calculate_missing(L, C, F)

if __name__ == "__main__":
    main()
