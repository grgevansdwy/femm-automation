# Vendored xfemm binaries

`xfemm-4.0-linux64/` started as the prebuilt Linux release from
https://github.com/crobarcro/xfemm/releases/download/v4.0/xfemm-4.0-Linux64.tar.gz
(v4.0, 2023-04-02).

## `femmcli` is patched

The vendored `femmcli` binary is **not** the stock release build - it has
`xfemm_flux_linkage_fix.patch` applied and was rebuilt from source. The
original, unpatched binary is kept alongside it as `femmcli.orig_unpatched`
for reference/rollback.

**The bug:** `cfemm/fpproc/fpproc.cpp`'s `GetFluxLinkage()`, in the branch
handling a circuit carrying zero prescribed current (i.e. every coil except
the one being driven in a given solve), computed

```cpp
FluxLinkage = GetVoltageDrop(circnum) / (2.*PI*Frequency);
```

dividing by the real angular frequency instead of the imaginary `j*omega`.
This rotates every mutual-inductance term (flux linkage in an undriven coil)
by exactly 90 degrees relative to real FEMM - confirmed to 12 significant
figures (`buggy_value = 1j * correct_value`) against real FEMM 4.2 under
Wine, on three structurally different production configs
(`backend/data/configs/base_coil.json`, `Gen3_backplane.json`,
`seedcoil.json`). Self-inductance/ESR terms (driven circuit, nonzero current)
use a different code path and were never affected.

**The fix** (`xfemm_flux_linkage_fix.patch`, one line):

```cpp
FluxLinkage = GetVoltageDrop(circnum) / (I*2.*PI*Frequency);
```

**Validated**: after the fix, all 12 self/mutual inductance and resistance
terms match real FEMM under Wine to ~10⁻⁸% (machine precision) on all three
test configs above - see `docs/Engine.md` for the full writeup.

**Not yet done:** reporting this upstream to https://github.com/crobarcro/xfemm.
This patch is only applied to this repo's vendored copy.

## Reproducing the build

```
# cmake via uvx (no system install needed)
uvx cmake -S cfemm -B cfemm/build -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5
uvx cmake --build cfemm/build --target femmcli-bin -j$(nproc)
# binary lands at cfemm/bin/femmcli
```

(Source: https://github.com/crobarcro/xfemm, tag v4.0, with
`xfemm_flux_linkage_fix.patch` applied to `cfemm/fpproc/fpproc.cpp` first.)
