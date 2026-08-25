PYTHON ?= python

.PHONY: help sync test lint build check clean

help: ## Show available targets.
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_.-]+:.*## / {printf "%-16s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

sync: ## Install the package with development and GCS dependencies.
	$(PYTHON) -m pip install -e ".[dev,gcs]"

test: ## Run the complete unit test suite.
	$(PYTHON) -m unittest discover -s tests -v

lint: ## Run Ruff checks.
	$(PYTHON) -m ruff check --no-cache model_evolution tests

build: ## Build the wheel and source distribution.
	$(PYTHON) -m build

check: lint test ## Run the local quality gates.

clean: ## Remove local build output.
	$(PYTHON) -c 'import shutil; [shutil.rmtree(path, ignore_errors=True) for path in ("build", "dist", "model_evolution.egg-info")]'
