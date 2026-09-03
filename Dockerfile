FROM python:3.13-slim

# X11 client libs + Mesa software OpenGL - what imgui_bundle's native
# extension links against (confirmed via ldd) beyond what -slim ships.
# No GPU passthrough assumed, so libgl1 pulls in Mesa's llvmpipe software
# rasterizer as a fallback.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libx11-6 libxext6 libxau6 libxdmcp6 libxcb1 \
      libxrandr2 libxinerama1 libxcursor1 libxi6 \
      libgl1 libglx-mesa0 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

# Workspace members (see root pyproject.toml) must all exist on disk for
# `uv sync` to resolve the workspace, even though only `gui` (+ the
# coil_simulation it drives) gets installed below.
COPY . .

RUN chmod +x coil_simulation/vendor/xfemm-4.0-linux64/femmcli \
             coil_simulation/vendor/xfemm-4.0-linux64/fmesher \
             coil_simulation/vendor/xfemm-4.0-linux64/fsolver \
             coil_simulation/vendor/xfemm-4.0-linux64/hsolver \
             coil_simulation/vendor/xfemm-4.0-linux64/esolver

RUN uv sync --package gui --package coil_simulation

# gui/README.md: "Linux, with a display (the app opens a real window - no
# headless mode)". The container has no display of its own - it must reuse
# the host's X11 server: run with
#   -e DISPLAY=$DISPLAY -v /tmp/.X11-unix:/tmp/.X11-unix
# and allow it through the host's X server first: xhost +local:docker
CMD ["uv", "run", "--package", "gui", "python", "gui/src/gui/main.py"]
