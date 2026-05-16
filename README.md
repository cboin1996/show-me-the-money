# show-me-the-money

CLI transaction categorizer and budget tracker. Drop bank CSV exports, get categorized expenses, budget tracking, and an interactive HTML dashboard.

## Features

- **Bank-agnostic parsing** — adapter architecture with auto-detection. Scotia credit/debit supported (old + new formats). Add new banks by implementing one adapter file.
- **SQLite database** — transactions, category rules, store name normalization, budgets, import history. All queryable.
- **Smart dedup** — file-level SHA256 hash + row-level composite unique index. Re-import safely.
- **Auto-categorization** — exact match, store pair normalization, substring matching. Generic bank descriptions (e.g. "pos purchase") resolved via sub-description.
- **HTML dashboard** — self-contained Chart.js dark-theme dashboard with stacked bar charts, donut breakdown, trend lines, and a filterable transaction table.
- **Budget tracking** — set monthly budgets per category, copy between months, visualized in dashboard.
- **Soft delete** — recycle bin for transactions.

## Setup

```bash
make setup
```

Or manually:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Usage

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
smtm report --html       # interactive HTML dashboard

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
make lint          # format with black + isort
make lint-check    # check formatting (CI)
make test          # pytest with coverage
make test-stdout   # pytest without XML artifacts
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
