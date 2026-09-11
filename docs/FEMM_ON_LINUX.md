# Running FEMM on Linux

FEMM is Windows-only software. It runs here through **Wine**, a compatibility layer that
translates Windows system calls into Linux ones. It is not a virtual machine and not an
emulator — there is no second OS running, and performance is roughly the same as native
Windows.

Wine gives Windows programs a fake `C:` drive at `~/.wine/drive_c/`. That is why FEMM lives
at `~/.wine/drive_c/femm42/` — to FEMM, that path *is* `C:\femm42`.

**Status: installed and verified** on Debian 13 (trixie), amd64 — Wine 10.0, FEMM 4.2 (21Apr2019, x64).

---

## How to run it

```bash
wine ~/.wine/drive_c/femm42/bin/femm.exe
```

That's it. The FEMM window opens, plus two small helper windows (`FEMM Output` and
`Lua Console`) — that's normal, not an error.

### Or just type `femm`

A launcher script is already installed at `~/.local/bin/femm` (that directory is on your PATH),
so from any terminal:

```bash
femm
```

It's a three-line wrapper around the `wine` command above — `cat ~/.local/bin/femm` to see it.

### First things to click

1. **File → New → Magnetics Problem** — this is the mode the whole `femm_automation` codebase uses.
2. **File → Open →** `~/.wine/drive_c/femm42/examples/` — a folder of working models. Open a
   `.fem` file, hit the mesh button, then the solve button, then the post-processor.
3. **`~/.wine/drive_c/femm42/bin/manual.pdf`** — the official manual, already on disk.

Spend an hour here before touching any Python. You want a physical feel for **block labels**,
**boundary conditions**, and **circuit properties** — those three concepts are about 90% of what
the scripts in this repo are manipulating.

### Running a script without the GUI

FEMM's own scripting language is Lua. You can drive it headlessly:

```bash
wine ~/.wine/drive_c/femm42/bin/femm.exe -lua-script='c:\path\to\script.lua'
```

Note the path is in **Windows form** (`c:\...`), because FEMM is a Windows program.

### Opening a design from the GUI

The desktop app (`gui/`) has an **Open in FEMM** button next to every geometry
it draws — a config, a past run, an AI search's baseline or proposed design. It
does exactly what the command above does: generates a Lua script from
`engine.build_geometry()` (the solver's own drawing code) and hands it to
`femm.exe -lua-script=`, with no `quit()` at the end so the window stays open on
the model. Scripts and `.fem` files land in
`coil_simulation/data/femm_models/<design>/`. See `gui/README.md` for the rest.

---

## Good to know

- **Solved files are big.** A solve writes a `.ans` file next to the `.fem` model — one of the
  bundled examples produced 7 MB. Watch disk usage if you sweep a lot of cases.
- **Don't click inside the FEMM window while a script is driving it.** It steals focus from the
  automation and can interrupt the run. This applies to the Python scripts in this repo too.
- **Window-hiding doesn't work under Wine.** FEMM's "run hidden" flag is a native-Windows-only
  feature, so the GUI will always be visible when a script runs. Harmless, just expected.
- **Wine's first run per prefix** may offer to install *Mono* and *Gecko*. FEMM needs neither;
  they were declined during this install.

---

## Next step (not done yet)

Driving FEMM from Python needs `pyfemm`, which is a separate install with its own Linux quirks
(it talks to FEMM over a file-based link rather than ActiveX). That is deliberately **not** set
up yet — get comfortable in the GUI first.

---
---

## Appendix: how this was installed

Recorded for reproducibility. All of this has already been run on this machine.

### 1. Wine

The `wine` package depends on 32-bit components, and this machine was 64-bit-only, so i386 had
to be enabled first. This matters even though the 64-bit FEMM build was used, because FEMM's
*installer* stub is a 32-bit executable.

```bash
sudo dpkg --add-architecture i386
sudo apt update
sudo apt install -y wine
wine --version          # -> wine-10.0 (Debian 10.0~repack-6)
```

### 2. Initialise the Wine prefix

Creates `~/.wine/` (the fake `C:` drive). The `WINEDLLOVERRIDES` setting silently declines the
Mono and Gecko add-ons, which FEMM does not use.

```bash
export WINEDLLOVERRIDES="mscoree,mshtml="
export WINEDEBUG=-all
wineboot -u
```

### 3. Download FEMM and verify the checksum

FEMM 4.2, build **21Apr2019**, 64-bit — the current stable release from femm.info.

```bash
cd ~/Downloads
wget -O femm42bin_x64_21apr2019.exe \
  'https://www.femm.info/doku/lib/exe/fetch.php?media=upload:files:femm42bin_x64_21apr2019.exe'

sha256sum femm42bin_x64_21apr2019.exe
```

Computed hash matched the author's published `sha256.txt` exactly:

```
17384e8990b6305ec8ffef1f75b6ef091a266b6e49b30c151cd1daca2e952043
```

### 4. Run the installer through Wine

The installer is Inno Setup 5.5.7, so it accepts silent-install flags — no clicking required.
The install location is left at the default (`C:\femm42`), which matters later: `pyfemm`
auto-detects FEMM at the standard path.

```bash
export WINEDLLOVERRIDES="mscoree,mshtml=" WINEDEBUG=-all
wine femm42bin_x64_21apr2019.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR="C:\femm42"
```

To install interactively instead, just drop the flags: `wine femm42bin_x64_21apr2019.exe`.

### 5. Verification performed

Two checks, both passed:

**GUI** — launched `femm.exe`; the main window appeared (1432×720) along with the `FEMM Output`
and `Lua Console` windows, status bar reading `Ready`, and no errors on stderr.

**Solver** — ran a Lua script headlessly that opened a bundled example, meshed it, and solved it:

```lua
open("c:\\femm42\\examples\\ProximityExact.fem")
mi_saveas("c:\\femm42\\examples\\zz_verify.fem")
mi_analyze(1)
quit()
```

It produced a 7 MB `.ans` solution file and exited cleanly. This confirms the whole chain works
under Wine — GUI, the mesher (`triangle.exe`), the solver (`fkn.exe`), and Lua scripting. The
scratch files were deleted afterwards.

### Where things ended up

| Path | What |
|---|---|
| `~/.wine/drive_c/femm42/bin/femm.exe` | the program |
| `~/.wine/drive_c/femm42/bin/manual.pdf` | official manual |
| `~/.wine/drive_c/femm42/examples/` | ~20 working example models |
| `~/.wine/drive_c/femm42/mfiles/` | Octave/MATLAB interface (not used by this repo) |
| `~/Downloads/femm42bin_x64_21apr2019.exe` | installer, safe to delete |
| `~/.local/bin/femm` | launcher script, so `femm` works from any terminal |

### Uninstalling

```bash
wine ~/.wine/drive_c/femm42/unins000.exe    # remove FEMM only
rm -rf ~/.wine                              # nuke the whole Wine environment
```
