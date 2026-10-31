# Developer shortcuts. Everything here also works without make; see CONTRIBUTING.md.
PYTHON ?= python3
VENV   ?= .venv
BIN    := $(VENV)/bin

.PHONY: help install demo test lint format smoke clean

help:  ## Show this help
	@grep -E '^[a-z]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-9s %s\n", $$1, $$2}'

$(BIN)/crowdcount: pyproject.toml
	$(PYTHON) -m venv $(VENV)
	$(BIN)/python -m pip install --upgrade pip
	$(BIN)/python -m pip install -e ".[dev]"
	@touch $@

install: $(BIN)/crowdcount  ## Create .venv and install CrowdCount with dev tools

demo: install  ## Run the demo (sample video + dashboard on http://localhost:8080)
	$(BIN)/crowdcount demo

test: install  ## Run the test suite
	$(BIN)/python -m pytest

lint: install  ## Lint and check formatting
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .

format: install  ## Auto-fix lint issues and format
	$(BIN)/ruff check --fix .
	$(BIN)/ruff format .

smoke: install  ## Run the real detector headless on 60 demo frames
	$(BIN)/crowdcount detect --source demo --no-display --no-realtime --max-frames 60 \
		--model yolo26n.pt --conf 0.35 --min-area 800 --metrics-out runs/smoke

clean:  ## Remove caches and run outputs (keeps .venv and data/)
	rm -rf .pytest_cache .ruff_cache runs build dist src/*.egg-info
	find . -name __pycache__ -type d -prune -not -path './$(VENV)/*' -exec rm -rf {} +
