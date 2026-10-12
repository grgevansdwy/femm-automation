# FEMM Coil Automation

## What this project does

A wireless power link is built from three coils (Loop, Tank and Receive, running at 6.78 MHz), and how well
it works depends on the exact geometry of each coil: turns, radius, trace width and thickness, layers,
ferrite and so on. This app runs a physics simulation of a design (a finite-element solve, using the
open-source xfemm solver, a Linux version of FEMM) and reports the inductance of each coil, the mutual
inductance between them, the resistance (ESR) and the coupling at a chosen separation distance, so you can
compare designs without building them. You can run and edit designs by hand in the window, or describe a goal
in plain English ("keep the coupling, lower the resistance") and an AI agent will ask you a few questions,
then try many geometry changes on its own, simulating each one, and show you the best design it found next to
your original.

**You need:** a Linux computer with a desktop (these steps assume Ubuntu or Debian), an internet connection,
and, for the AI, a connection to the office network.

## Set up

**1. Download the project.** This needs your Bitbucket account set up with an SSH key. If it says
"Permission denied", ask a teammate for help or for a zip of the folder (unzip it and skip this step).

```
git clone git@bitbucket.org:example-org/femm-automation.git femm_automation
```

**2. Install `uv`**, a helper that installs everything else the project needs (including Python):

```
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**3. Close the terminal and open a new one** (`Ctrl+Alt+T` again).

## Start it

In the terminal:

```
cd femm_automation
uv run femm
```

The very first time it downloads what it needs (a few minutes; you'll see text scrolling). Then a window called
**FEMM Coil Automation** opens with 13 example designs loaded. From now on, starting it is just these two
lines, and it opens in seconds. Close the window to stop it.

## How to use it

**Manual Runs** (top of the window, no AI needed)
1. Click a design on the left, for example **Gen2**.
2. Under **Run** on the right, pick a distance in mm (try `5`) and click **Run**.
3. The result appears in **Run History**: L, M, ESR and k.

Edit the numbers in the middle and click **Save** to change a design. **Clone** first to keep the original.
**Export** / **Import** send a design to a coworker as a small file.

**AI Agent** (top of the window)
1. Pick the design to improve, then say what you want, for example
   *"Keep the mutual inductance the same but cut the resistance."*
2. Click **Start Search** and watch it try designs. At the end you can compare the best one with the original.
   Only one search runs at a time. Start another chat's search meanwhile and it waits in a queue, then starts
   by itself when the running one ends (the queue is forgotten if you close the app).
3. Want something different once it has finished? Say so in the same chat (*"5% is enough"*, *"also check 8 mm"*).
   It shows the changed plan and what is different; click **Start Search** again to run it.
4. To share a chat, right-click it in the list on the left: **Export JSON...** saves the whole chat as a
   file a coworker opens with **Import...** on their computer, and **Export PDF report...** saves a
   report to read or print.

Your designs and results are saved on your computer. **Backup All** / **Restore All** at the top of the
window save or load everything as one file, which is how to move your work to another computer.

## Something wrong?

| What you see | What to do |
|---|---|
| `uv: command not found` | Close the terminal, open a new one, and try again (step 5) |
| `No such file or directory` after `cd femm_automation` | You're in the wrong folder. Type `cd ~` and try again, or `ls` to see where the folder is |
| The window doesn't open, or a message mentions `libGL` | Run `sudo apt install -y libgl1`, then start it again |
| The AI can't connect | Check you're on the office network or VPN. Then open **Settings** (in AI Agent mode) and check **LLM server URL** |
| A run says "failed" | Click it in Run History; the message says why |

## Use it from terminal (PI)

Prefer chatting in a terminal? The `pi` agent runs the same simulations on the same designs. Do the setup
above first (it needs `uv`).

**Once: install pi.** It needs Node.js 22.19 or newer, which Ubuntu's own version usually is not, so:

```
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
```

Close the terminal and open a new one, then:

```
nvm install 22
npm install -g @earendil-works/pi-coding-agent
```

**Every time:**

```
cd femm_automation
pi --approve
```

It's already set up to use the office AI server. (`--approve` lets pi use this project's coil tools; without it
they stay switched off.) Then just ask in plain English:

- *"List the coil designs."*
- *"Simulate Gen2 at 5 mm."*
- *"On Gen2, cut the tank resistance by about 5% and keep everything else within 2%."*

It runs the simulations for you and ends with a table of baseline vs. final numbers. Designs it saves are
the same ones the window shows, but it only saves when you ask it to.

## For developers and AI coding agents

Read [`docs/README.md`](docs/README.md) first. It points to one document per component:
[`Engine.md`](docs/Engine.md), [`GUI.md`](docs/GUI.md) and [`Agent.md`](docs/Agent.md).

Tests: `uv run --package frontend --group test pytest backend` (about 10 s, no AI server needed).
Run the AI search without the window: `cd backend && uv run python -m agent`.
The solver is [xfemm](backend/engine/vendor/README.md), a Linux-native reimplementation of FEMM, so there
is no Wine or Windows install.
