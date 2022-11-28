venv:
	python3.9 -m venv .venv
	echo 'run `source .venv/bin/activate` to start develop Queue Workers.'

install:
	pip install -e .

develop:
	pip install wheel==0.38.4
	pip install -e .
	pip install -Ur docs/requirements-dev.txt
	flit install --symlink

release:
	lint test clean
	flit publish

format:
	python -m black qw

lint:
	python -m pylint --rcfile .pylintrc qw/*.py
	python -m black --check qw

test:
	python -m coverage run -m qw.tests
	python -m coverage report
	python -m mypy qw/*.py

distclean:
	rm -rf .venv
