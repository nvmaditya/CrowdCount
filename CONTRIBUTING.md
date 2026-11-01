# Contributing to CrowdCount

Thanks for your interest. Bug reports, fixes, docs and features are all welcome.

## Setup

You need Python 3.10 or newer.

```bash
git clone https://github.com/nvmaditya/CrowdCount.git
cd CrowdCount
make install            # creates .venv and installs CrowdCount with dev tools
make demo               # sanity check: dashboard on http://localhost:8080
```

Without `make`:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

On Linux without a GPU, install CPU-only PyTorch first to skip the multi-GB CUDA download:
`pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu`.

## Everyday commands

| Command | What it does |
| --- | --- |
| `make test` | Run the test suite (about 2 s; no model weights or GPU needed) |
| `make lint` | `ruff check` + `ruff format --check`, as in CI |
| `make format` | Auto-fix lint issues and format |
| `make smoke` | Run the real detector headless on 60 demo frames (downloads yolo26n, ~6 MB) |

## Project layout

```
src/crowdcount/
  cli.py          the `crowdcount` command
  config.py       Settings, crowdcount.toml, .env
  video.py        VideoSource (video files and streams)
  detector.py     PersonDetector (YOLO + tracker), box filters
  zones.py        zone model, validation, point-in-polygon, ZoneTracker  ← pure Python
  pipeline.py     the frame loop
  state.py        SharedState (live data shared with the server)
  server/         FastAPI app: public API, auth, admin, audit log, storage
  web/            Live, Alerts, Zones, Settings and sign-in pages (plain JS; vendored libraries in web/vendor)
tests/            pytest suite
docs/             user documentation
```

[docs/architecture.md](docs/architecture.md) explains how a frame flows through these.

## Guidelines

- **Tests.** Add or update tests with every behaviour change. Tests must not need torch or
  model weights: the pipeline is tested with a fake tracker on a generated video (see
  `tests/test_pipeline.py`), and the server through `TestClient` on an isolated app (the
  `make_client` fixture in `tests/conftest.py`).
- **Style.** Formatting and import order are ruff's. Type-hint public functions and give them
  a docstring. Use `logging`, not `print`, except for command output meant for stdout
  (`zones list`).
- **Errors.** Expected failures (bad input, missing files, unreachable cameras) should raise a
  specific exception with a message a user can act on. The CLI turns these into a one-line error
  and an exit code; don't let them become tracebacks.
- **Frontend.** No inline scripts or `on*=` attributes (the CSP blocks them), and no
  `innerHTML` with data: build nodes with `CC.el(...)` from `web/assets/js/common.js`.
- **Docs.** If you change a flag, endpoint or behaviour, update `docs/` and the README in the
  same change. Only document what the code does; ideas go in the README's roadmap, marked as
  planned.
- **Commits.** Use [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `build:`, `ci:`, `chore:`) with a body that
  explains why.

## Dependencies

Runtime dependency ranges live in `pyproject.toml`. `requirements.lock` records exact versions
from a verified environment. After changing dependencies, regenerate it from a clean venv:

```bash
python3 -m venv /tmp/lock && /tmp/lock/bin/pip install . \
  && /tmp/lock/bin/pip freeze --exclude crowdcount
```

Paste the output below the header comment in `requirements.lock`, and update the header's
platform line.

## Reporting bugs

Open an issue with:

- the command you ran and its output with `-v`
- `crowdcount --version`, your OS, Python version and device (`cpu`, `mps`, `cuda`)
- the source type (file or stream) and resolution, if relevant

## Releasing

1. Update `__version__` in `src/crowdcount/__init__.py` and add a `CHANGELOG.md` entry.
2. Tag the release: `git tag v2.x.y && git push --tags`.
