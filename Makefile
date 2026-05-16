APP_NAME=smtm

.PHONY: setup
setup:
	python -m venv .venv
	. .venv/bin/activate && pip install -e ".[dev]"

.PHONY: lint
lint:
	. .venv/bin/activate && black $(APP_NAME) tests
	. .venv/bin/activate && isort $(APP_NAME) tests

.PHONY: lint-check
lint-check:
	. .venv/bin/activate && black --check $(APP_NAME) tests
	. .venv/bin/activate && isort --check $(APP_NAME) tests

.PHONY: test
test:
	. .venv/bin/activate && pytest --junitxml=junit/test-results.xml --cov=$(APP_NAME) --cov-report=xml --cov-report=html tests/ -v

.PHONY: test-stdout
test-stdout:
	. .venv/bin/activate && pytest --cov=$(APP_NAME) tests/ -v
