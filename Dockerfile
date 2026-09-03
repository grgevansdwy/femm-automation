FROM python:3.13-slim

RUN pip install --no-cache-dir uv

WORKDIR /app

# Workspace members (see root pyproject.toml) must all exist on disk for
# `uv sync` to resolve the workspace, even though only `webapp` gets
# installed below.
COPY . .

RUN chmod +x coil_simulation/vendor/xfemm-4.0-linux64/femmcli \
             coil_simulation/vendor/xfemm-4.0-linux64/fmesher \
             coil_simulation/vendor/xfemm-4.0-linux64/fsolver \
             coil_simulation/vendor/xfemm-4.0-linux64/hsolver \
             coil_simulation/vendor/xfemm-4.0-linux64/esolver

RUN uv sync --package webapp --package coil_simulation

EXPOSE 8000

CMD ["uv", "run", "--package", "webapp", "uvicorn", "webapp.main:app", \
     "--app-dir", "legacy/webapp/src", "--host", "0.0.0.0", "--port", "8000"]
