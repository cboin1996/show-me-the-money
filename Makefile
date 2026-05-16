APP_NAME=smtm

.PHONY: setup
setup:
	uv sync --extra dev

.PHONY: upgrade
upgrade:
	uv lock --upgrade
	uv sync --extra dev

.PHONY: lint
lint:
	uv run black $(APP_NAME) tests
	uv run isort $(APP_NAME) tests

.PHONY: lint-check
lint-check:
	uv run black --check $(APP_NAME) tests
	uv run isort --check $(APP_NAME) tests

.PHONY: test
test:
	uv run pytest --junitxml=junit/test-results.xml --cov=$(APP_NAME) --cov-report=xml --cov-report=html tests/ -v

.PHONY: test-stdout
test-stdout:
	uv run pytest --cov=$(APP_NAME) tests/ -v
