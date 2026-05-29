# show-me-the-money

CLI transaction categorizer and budget tracker. Drop bank CSV exports, get
categorized expenses, budget tracking, and a fully interactive web dashboard.

## Features

- **Bank-agnostic parsing** — adapter architecture with auto-detection. Scotia
  credit/debit supported (old + new formats). Add new banks by implementing one
  adapter file.
- **SQLite database** — transactions, category rules, store name normalization,
  budgets, import history. All queryable.
- **Smart dedup** — file-level SHA256 hash + row-level composite unique index.
  Re-import safely.
- **Auto-categorization** — exact match, store pair normalization, substring
  matching. Generic bank descriptions (e.g. "pos purchase") resolved via
  sub-description.
- **Interactive web dashboard** — local server with drag-and-drop import, inline
  categorization, anomaly detection, budget vs actual charts, category rule
  management, recycle bin, trips. Zero external dependencies (stdlib
  `http.server`).
- **Static HTML export** — self-contained Chart.js dark-theme dashboard for
  sharing/archiving.
- **Anomaly detection** — flags transactions exceeding 2x their category
  average.
- **Budget tracking** — set monthly budgets per category, copy between months,
  budget vs actual visualization.
- **Trips** — tag a date range as a trip, auto-assign expenses, split costs
  with a partner (configurable %), mark individual transactions as "just me".
- **Reimbursements** — link income transactions to the expenses they offset
  (e.g. Canada Life covering a wellness charge).
- **Soft delete** — recycle bin with restore.

## Quickstart (fresh install)

```bash
# 1. Install uv (https://docs.astral.sh/uv/)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Clone and set up
git clone https://github.com/cboin1996/show-me-the-money ~/proj/show-me-the-money
cd ~/proj/show-me-the-money
make setup

# 3. Drop your bank CSVs in data/csv/
mkdir -p data/csv
cp ~/Downloads/*.csv data/csv/

# 4. Import
smtm import

# 5. Open the dashboard
smtm serve
```

Browser opens at `http://127.0.0.1:8000`. From there: categorize merchants,
set budgets, review anomalies — no further CLI required.

## Setup

Requires [uv](https://docs.astral.sh/uv/):

```bash
make setup
```

## Web Dashboard

```bash
smtm serve                     # http://127.0.0.1:8000
smtm serve --port 9000         # custom port
smtm serve --reload            # auto-reload on source changes (dev mode)
```

### Tabs

| Tab | What's here |
|-----|-------------|
| **Overview** | Monthly stacked bar, category donut, trend lines, income vs expenses, summary cards, anomalies |
| **Transactions** | Full transaction table — search, filter by category/date, bulk assign, bulk delete, inline category edit, export CSV |
| **Organize** | Recategorize uncategorized merchants, category rules, store pairs (raw→normalized), recycle bin |
| **Budgets** | Budget vs actual chart, set/edit monthly budgets, copy between months |
| **Reimburse** | Known reimbursers, link income to the expense it covers, pending unlinked reimbursements |
| **Trips** | Tag a date range as a trip, auto-assign expenses, configurable partner split %, per-transaction "just me" flag |
| **Analytics** | Spend velocity, day-of-week chart, top merchants, month-over-month table, recurring charges, z-score outliers |
| **Import** | Drag-and-drop CSV upload, preview before confirming, import history |

### Key workflows

**Import new transactions**

Drag CSVs onto the Import tab → preview shows parsed count and classification
breakdown → Confirm Import.

**Categorize uncategorized merchants**

Organize tab → uncategorized section groups merchants by store name with total
spend. Select category → all matching transactions categorized at once.

**Set a budget**

Budgets tab → enter month (YYYY-MM), pick category, enter amount → Save.

**Track a trip**

Trips tab → enter name, start/end dates, check which categories to exclude
from totals (Investments excluded by default) → Create & Auto-assign.

In trip detail: set split % (e.g. 60% you / 40% partner). For transactions
entirely yours (not shared), click **Just me** — they count 100% toward your
share regardless of split %.

**Link a reimbursement**

Reimburse tab → add a reimburser pattern (e.g. `canada life`, substring match).
Matching income transactions appear in Pending — link each to the expense it
covers.

## CLI

```bash
# Import CSVs from data/csv/ (auto-detects bank format)
smtm import

# Import from a specific directory
smtm import --csv-dir ~/Downloads/statements

# Preview without importing
smtm profile
smtm profile --csv-dir ~/Downloads/statements

# Suggest categories for unclassified transactions
smtm suggest
smtm suggest --apply           # auto-apply all suggestions

# Generate reports
smtm report                    # text summary to stdout
smtm report --html             # static HTML dashboard → data/report.html
smtm report --pdf              # PDF report → data/report.pdf

# Budget management
smtm budget set 2026-01 Dining=600 Groceries=400
smtm budget copy 2026-01 2026-02
smtm budget show
smtm budget show 2026-01

# Store name management
smtm stores list
smtm stores discover           # suggest raw→normalized pairs
smtm stores discover --apply
smtm stores dupes              # find duplicate normalized names

# Import history
smtm history

# Soft-delete / restore transactions (UUIDs visible in dashboard)
smtm delete <uuid>
smtm delete <uuid> --restore

# Reimburse
smtm reimburse add <pattern>
smtm reimburse list
smtm reimburse pending
smtm reimburse link <income-uuid> <expense-uuid>
```

Global flags (before the subcommand):

```bash
smtm --db-path /path/to/smtm.db --csv-dir /path/to/csvs <subcommand>
```

Defaults: `--db-path data/smtm.db`, `--csv-dir data/csv`.

## Development

```bash
make setup          # uv sync --extra dev + playwright install
make lint           # format with black + isort
make lint-check     # check formatting (CI)
make test           # pytest unit + integration tests with coverage
make test-e2e       # playwright e2e browser tests
make test-all       # unit + e2e together
make test-stdout    # unit tests without XML artifacts
make upgrade        # uv lock --upgrade
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

## Schema migrations

Migrations are numbered entries in `smtm/db.py::_MIGRATIONS`. To add a column:

```python
_MIGRATIONS = [
    ...existing entries...,
    # N: description
    "ALTER TABLE some_table ADD COLUMN new_col TEXT DEFAULT ''",
]
SCHEMA_VERSION = N
```

`schema_version` tracks which migrations have run. `db.initialize()` applies
pending ones on startup — safe to run multiple times.
