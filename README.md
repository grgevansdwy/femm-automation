# FEMM Coil Automation

Simulate three-coil wireless-power links (Loop, Tank and Receive, at 6.78 MHz) and compare coil designs without
building them. For a chosen separation distance it reports each coil's inductance, the mutual inductance
between them, the resistance (ESR) and the coupling. Edit designs by hand, or describe a goal in plain English
("keep the coupling, lower the resistance") and an AI agent tries geometry changes, simulating each one, then
shows you the best design next to your original.

How it is built: [`DESIGN.md`](DESIGN.md).

You need a Linux desktop running Debian or Ubuntu.

## Install (about 5 minutes)

Open a terminal (`Ctrl+Alt+T`) and run these in order.

**1. Download the project.** This needs your Bitbucket account set up with an SSH key. If it says
"Permission denied", ask a teammate for help, or for a zip (unzip it and skip the `git clone` line).

```
git clone git@bitbucket.org:example-org/femm-automation.git femm_automation
```

**2. Install `uv`**, which installs Python and everything else the project needs:

```
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**3. Install Wine and FEMM 4.2.** FEMM is Windows software, so Linux runs it through Wine. The app uses it for
the **Open in FEMM** button, which draws a design in the real FEMM program so you can look at the mesh yourself.

```
sudo apt install -y wine
```

Then download the FEMM 4.2 installer from <https://www.femm.info> and run it with `wine <installer>.exe`, keeping
the default folder (`C:\femm42`). If you put it elsewhere, set `FEMM_EXE` to the path of `femm.exe` before
starting the app.

**4. Close the terminal and open a new one.** (This lets it find `uv`.)

## Start it

```
cd femm_automation
uv run femm
```

The first time it downloads what it needs (a few minutes, lots of scrolling text). Then a window called
**FEMM Coil Automation** opens with 13 example designs. After that it starts in seconds. Close the window to stop it.

## Use it

**Manual Runs** (no AI needed)

1. Click a design on the left, for example **Gen2**.
2. Pick a distance in mm (try `5`) and click **Run**.
3. The result appears in **Run History**: L, M, ESR and k.

Edit the numbers in the middle and click **Save**. **Clone** first to keep the original. **Export** and
**Import** send a design to a coworker as a small file.

**AI Agent**

1. Pick the design to improve and say what you want, for example
   *"Keep the mutual inductance within 5% and cut the tank resistance by at least 20%."*
2. Answer its few questions, check the plan table, then click **Start Search**. Watch it try designs; when it
   finishes it summarises the best one.
3. Anything you don't mention is **free to change**. If you care about it, say so in the goal
   (*"keep every inductance within 10%"*), or the search may trade it away.
4. Want to stop early? Tell it in the chat ("I'm satisfied, stop"). Want to change the goal afterwards? Say so
   in the same chat and start the revised plan. Type `continue` to search further.
5. Only one search runs at a time; others wait in a queue. To share a chat, right-click it in the list:
   **Export JSON...** (a coworker opens it with **Import...**) or **Export PDF report...**.

Your designs and results are saved on your computer. **Backup All** / **Restore All** (top of the window) save
or load everything as one file, which is how to move to another computer.

## AI server

The agent talks to an LLM server over the network. By default that is the office server, so you must be on the
office network or VPN. To use a different one: start the AI Agent, click **Settings**, and under
**LLM & Budget** set **LLM server URL** and **Model name**. Any server that speaks the OpenAI
`/v1/chat/completions` API with tool calling works (for example `llama-server` from llama.cpp). Nothing is sent
anywhere else.

## Something wrong?

| What you see | What to do |
|---|---|
| `git: command not found` or `curl: command not found` | Run the command under this table, then redo the step |
| `uv: command not found` | Close the terminal, open a new one, try again (step 4) |
| `No such file or directory` after `cd femm_automation` | Wrong folder. `cd ~` then `ls` to find where it is |
| The window doesn't open, or a message mentions `libGL` or `libX11` | A fresh Linux install can lack the display libraries. Run the command under this table, then start it again |
| Black or empty window on a remote or virtual machine | The app needs a desktop with OpenGL. Use the machine's own screen, not a bare SSH session |
| The AI can't connect | Check the office network or VPN, then **Settings > LLM & Budget > LLM server URL** |
| The AI seems stuck after the computer woke from sleep | A request in flight during the sleep can be lost. It times out by itself (up to 15 minutes) and the search then retries it |
| A run says "failed" | Click it in Run History; the message says why |
| The **Open in FEMM** button says Wine or FEMM is missing | Do step 3; if FEMM is somewhere else, set `FEMM_EXE` |

**Fresh Linux install?** These packages are usually already there on a desktop, but a minimal one may lack them:

```
sudo apt update
sudo apt install -y git curl libgl1 libx11-6 libxrandr2 libxinerama1 libxcursor1 libxi6
```

## Use it from a terminal (pi)

Prefer chatting in a terminal? The `pi` agent runs the same simulations on the same designs. Finish the install
above first. Once, install Node.js 22.19 or newer (Ubuntu's own is usually older) and pi:

```
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
```

Close the terminal, open a new one, then:

```
nvm install 22
npm install -g @earendil-works/pi-coding-agent
```

Every time:

```
cd femm_automation
pi --approve
```

It is set up for the office AI server. (`--approve` lets pi use this project's coil tools.) Then ask in plain
English, for example *"Simulate Gen2 at 5 mm."* or *"On Gen2, cut the tank resistance by about 5% and keep
everything else within 2%."* Designs it saves are the same ones the window shows.

## For developers

Read [`DESIGN.md`](DESIGN.md). Run the tests (about 10 seconds, no AI server needed):

```
uv run --package frontend --group test pytest backend
```

Run the AI search without the window: `cd backend && uv run python -m agent`.
