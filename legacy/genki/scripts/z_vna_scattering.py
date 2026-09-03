# /// script
# dependencies = [
#     "pandas",
#     "quantiphy",
#     "numpy",
# ]
# ///

import argparse
import sys

import numpy as np
import pandas as pd

def S_matrix_to_Z_matrix(s_matrix, freq_kHz, move_12_to_13=False, move_12_to_23=False):
    assert((not move_12_to_13) or (not move_12_to_23))

    W = 2 * np.pi * (freq_kHz * 1e3)
    Z0 = 50 # VNA settings
    I = np.identity(s_matrix.shape[0])

    Z = Z0 * ((I + s_matrix) @ np.linalg.inv(I - s_matrix))

    L1 = np.imag(Z[0, 0]) / W
    L2 = np.imag(Z[1, 1]) / W
    L3 = np.imag(Z[2, 2]) / W if s_matrix.shape[0] > 2 else None

    M12 = np.abs(np.imag(Z[0, 1]) / W)
    M13 = np.abs(np.imag(Z[0, 2]) / W) if s_matrix.shape[0] > 2 else None
    M23 = np.abs(np.imag(Z[1, 2]) / W) if s_matrix.shape[0] > 2 else None

    k12 = M12 / np.sqrt(L1 * L2)
    k13 = M13 / np.sqrt(L1 * L3) if s_matrix.shape[0] > 2 else None
    k23 = M23 / np.sqrt(L2 * L3) if s_matrix.shape[0] > 2 else None

    Rp1 = np.real(Z[0, 0])
    Rp2 = np.real(Z[1, 1])
    Rp3 = np.real(Z[2, 2]) if s_matrix.shape[0] > 2 else None

    if move_12_to_13:
        L3 = L2
        M13 = M12
        k13 = k12
        Rp3 = Rp2

        L1 = L1
        L2 = None
        M12 = None
        M23 = None
        k12 = None
        k23 = None
        Rp2 = None

    if move_12_to_23:
        L3 = L2
        L2 = L1
        M23 = M12
        k23 = k12
        Rp3 = Rp2
        Rp2 = Rp1

        L1 = None
        M12 = None
        M13 = None
        k12 = None
        k13 = None
        Rp1 = None

    return [
        None if L1 is None else L1 * 1e6,
        None if L2 is None else L2 * 1e6,
        None if L3 is None else L3 * 1e6,
        None if M12 is None else M12 * 1e6,
        None if M13 is None else M13 * 1e6,
        None if M23 is None else M23 * 1e6,
        k12,
        k13,
        k23,
        Rp1,
        Rp2,
        Rp3,
    ]

def main(csv, freq_khz):
    data = pd.read_csv(csv)
    print(",".join([
        "L1_uH", "L2_uH", "L3_uH",
        "M12_uH", "M13_uH", "M23_uH",
        "k12", "k13", "k23",
        "Rp1", "Rp2", "Rp3",
    ]))
    for _, row in data.iterrows():
        S = lambda a_mU, b_mU: (a_mU * 1e-3) + (1j * b_mU * 1e-3)
        s11 = S(row['Re[S11] (mU)'], row['Im[S11] (mU)'])
        s21 = S(row['Re[S21] (mU)'], row['Im[S21] (mU)'])
        s22 = S(row['Re[S22] (mU)'], row['Im[S22] (mU)'])
        s_matrix = np.matrix([
            [s11, s21],
            [s21, s22],
        ])
        ret = S_matrix_to_Z_matrix(s_matrix, freq_khz,
                                   move_12_to_13=row["Setup"] == "Just 1-3",
                                   move_12_to_23=row["Setup"] == "Just 2-3")
        print(",".join("" if x is None else f"{x:0.4f}" for x in ret))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="VNA S matrix -> Z matrix")
    parser.add_argument("--freq-khz", type=int, required=True, help="Frequency (kHz) to analyze at")
    parser.add_argument("--csv", type=str, required=True, help="CSV of data")
    args = parser.parse_args()
    main(args.csv, args.freq_khz)
