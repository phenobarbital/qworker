PYTHON_VERSION := 3.11
VERSION_FILE := qw/version.py
export PIP_REQUIRE_VIRTUALENV=true

HAS_UV := $(shell command -v uv 2> /dev/null)

# Version bump helper: $(call _bump,file,field)
define _bump
	@python3 -c "\
import re, sys; \
content = open('$(1)').read(); \
ver = re.search(r\"__version__\s*=\s*'([^']+)'\", content).group(1); \
parts = ver.split('.'); \
parts[$(2)] = str(int(parts[$(2)]) + 1); \
[parts.__setitem__(i, '0') for i in range($(2)+1, 3)]; \
new_ver = '.'.join(parts); \
print(f'Bumping version: {ver} -> {new_ver}'); \
open('$(1)', 'w').write(content.replace(f\"__version__ = '{ver}'\", f\"__version__ = '{new_ver}'\")) \
"
endef

.PHONY: install-uv venv install develop lock sync update test format lint \
        release clean distclean bump-patch bump-minor bump-major \
        add add-dev remove info help

install-uv:  ## Install uv package manager
	curl -LsSf https://astral.sh/uv/install.sh | sh

venv:  ## Create virtual environment with uv
	uv venv --python $(PYTHON_VERSION) .venv
	@echo 'Run `source .venv/bin/activate` to activate the venv.'

install:  ## Install package (production only, no dev deps)
	uv sync --frozen --no-dev

develop:  ## Install package with all extras and dev deps
	uv sync --all-extras

lock:  ## Generate/update uv.lock
	uv lock

sync:  ## Sync environment with uv.lock
	uv sync

update:  ## Upgrade all dependencies
	uv lock --upgrade

test:  ## Run tests with pytest
	uv run pytest tests/ -v

format:  ## Format code with black
	uv run black qw

lint:  ## Lint code with pylint and check formatting with black
	uv run pylint --rcfile .pylintrc qw/*.py
	uv run black --check qw

release: lint test clean  ## Build and publish release to PyPI
	uv build
	uv publish dist/*.tar.gz dist/*.whl

clean:  ## Remove build artifacts
	rm -rf build/ dist/ *.egg-info/
	find . -name "*.pyc" -delete
	find . -name "*.pyo" -delete
	find . -type d -name __pycache__ -exec rm -rf {} +

distclean: clean  ## Remove venv and lock file
	rm -rf .venv
	rm -f uv.lock

bump-patch:  ## Bump patch version (x.y.Z)
	$(call _bump,$(VERSION_FILE),2)

bump-minor:  ## Bump minor version (x.Y.0)
	$(call _bump,$(VERSION_FILE),1)

bump-major:  ## Bump major version (X.0.0)
	$(call _bump,$(VERSION_FILE),0)

add:  ## Add a dependency: make add PKG=<package>
	uv add $(PKG)

add-dev:  ## Add a dev dependency: make add-dev PKG=<package>
	uv add --dev $(PKG)

remove:  ## Remove a dependency: make remove PKG=<package>
	uv remove $(PKG)

info:  ## Show dependency tree
	uv tree

help:  ## Show this help message
	@echo "QWorker — uv-based Makefile"
	@echo ""
	@echo "Available targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
