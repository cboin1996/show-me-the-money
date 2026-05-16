# show-me-the-money

CLI transaction categorizer and budget tracker. Drop bank CSV exports, get categorized expenses, budget tracking, and a fully interactive web dashboard.

## Features

- **Bank-agnostic parsing** — adapter architecture with auto-detection. Scotia credit/debit supported (old + new formats). Add new banks by implementing one adapter file.
- **SQLite database** — transactions, category rules, store name normalization, budgets, import history. All queryable.
- **Smart dedup** — file-level SHA256 hash + row-level composite unique index. Re-import safely.
- **Auto-categorization** — exact match, store pair normalization, substring matching. Generic bank descriptions (e.g. "pos purchase") resolved via sub-description.
- **Interactive web dashboard** — local server with drag-and-drop import, inline categorization, anomaly detection, budget vs actual charts, category rule management, recycle bin. Zero external dependencies (stdlib `http.server`).
- **Static HTML export** — self-contained Chart.js dark-theme dashboard for sharing/archiving.
- **Anomaly detection** — flags transactions exceeding 2x their category average.
- **Budget tracking** — set monthly budgets per category, copy between months, budget vs actual visualization.
- **Soft delete** — recycle bin with restore.

## Setup

Requires [uv](https://docs.astral.sh/uv/):

```bash
make setup
```

## Usage

### Interactive Dashboard

```bash
smtm serve                     # http://127.0.0.1:8000
smtm serve --port 9000         # custom port
```

Opens a browser with the full dashboard: import CSVs via drag-and-drop, categorize merchants inline, view anomalies, manage budgets/rules/store pairs, and soft-delete/restore transactions.

### CLI

```bash
# Import CSVs (auto-detects bank format)
smtm import --csv-dir data/new

# Preview without importing
smtm profile --csv-dir data/new

# Suggest categories for unclassified transactions
smtm suggest
smtm suggest --apply

# Generate reports
smtm report              # text summary
smtm report --html       # static HTML dashboard

# Budget management
smtm budget set 2026-01 Dining=600 Groceries=400
smtm budget copy 2026-01 2026-02
smtm budget show

# Import history
smtm history

# Soft-delete transactions
smtm delete <uuid>
```

## Development

```bash
make setup         # uv sync --extra dev
make lint          # format with black + isort
make lint-check    # check formatting (CI)
make test          # pytest with coverage
make test-stdout   # pytest without XML artifacts
make upgrade       # uv lock --upgrade
```

## Adding a New Bank

Create `smtm/adapters/your_bank.py`:

```python
from .base import BaseAdapter

class YourBankAdapter(BaseAdapter):
    name = "your_bank"

    def can_parse(self, path, peek_df):
        # Return True if this CSV matches your bank's format
        ...

    def parse(self, path):
        # Return list[Transaction]
        ...

    def ignorable_patterns(self):
        return ["internal transfer", ...]
```

Register it in `smtm/adapters/__init__.py`.
