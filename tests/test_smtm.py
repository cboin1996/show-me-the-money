"""Comprehensive tests for the smtm package."""
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from smtm import categorizer, database, parsers, reports
from smtm.models import CategoryDB, Transaction, TxnType

FIXTURES = Path(__file__).parent / "fixtures"


# --- Fixtures ---


@pytest.fixture
def sample_db(tmp_path):
    db = CategoryDB(
        categories=["Dining", "Groceries", "Transportation", "Health",
                    "Shopping", "Entertainment", "Subscriptions",
                    "Insurance", "Utilities", "Fees", "Travel", "Misc"],
        store_to_category={
            "uber eats": ["Dining"],
            "shoppers drug mart": ["Health"],
            "petro canada": ["Transportation"],
            "amazon": ["Shopping"],
            "walmart": ["Groceries"],
            "insurance": ["Insurance"],
            "netflix": ["Subscriptions"],
            "shaw": ["Utilities"],
        },
        store_pairs={
            "petro-canada 68": "petro canada",
        },
    )
    database.save_db(db, tmp_path)
    return tmp_path


@pytest.fixture
def sample_txns():
    return [
        Transaction(date=date(2026, 1, 5), amount=25.50,
                    store_raw="uber eats", txn_type=TxnType.EXPENSE),
        Transaction(date=date(2026, 1, 10), amount=50.00,
                    store_raw="walmart", txn_type=TxnType.EXPENSE),
        Transaction(date=date(2026, 1, 15), amount=5000.00,
                    store_raw="payroll", txn_type=TxnType.INCOME),
        Transaction(date=date(2026, 2, 5), amount=30.00,
                    store_raw="uber eats", txn_type=TxnType.EXPENSE),
        Transaction(date=date(2026, 2, 10), amount=65.00,
                    store_raw="petro canada", txn_type=TxnType.EXPENSE),
    ]


# --- Database tests ---


class TestDatabase:
    def test_load_save_roundtrip(self, tmp_path):
        db = CategoryDB(
            categories=["A", "B"],
            store_to_category={"store1": ["A"]},
            store_pairs={"s1": "store1"},
        )
        database.save_db(db, tmp_path)
        loaded = database.load_db(tmp_path)
        assert loaded.categories == ["A", "B"]
        assert loaded.store_to_category == {"store1": ["A"]}
        assert loaded.store_pairs == {"s1": "store1"}

    def test_load_empty_dir(self, tmp_path):
        db = database.load_db(tmp_path)
        assert db.categories == []
        assert db.store_to_category == {}
        assert db.store_pairs == {}

    def test_load_nonexistent_dir(self, tmp_path):
        db = database.load_db(tmp_path / "nope")
        assert db.categories == []

    def test_save_creates_dir(self, tmp_path):
        db = CategoryDB(categories=["X"])
        target = tmp_path / "sub" / "dir"
        database.save_db(db, target)
        assert (target / "expenses.json").exists()


# --- Parser tests ---


class TestParsersNewCredit:
    def test_parses_expenses(self):
        txns = parsers.parse_scotia_new_credit(FIXTURES / "new_credit.csv")
        expenses = [t for t in txns if t.txn_type == TxnType.EXPENSE]
        assert len(expenses) == 4

    def test_filters_payments(self):
        txns = parsers.parse_scotia_new_credit(FIXTURES / "new_credit.csv")
        stores = [t.store_raw for t in txns]
        assert not any("payment" in s for s in stores)

    def test_amounts_positive(self):
        txns = parsers.parse_scotia_new_credit(FIXTURES / "new_credit.csv")
        assert all(t.amount > 0 for t in txns)

    def test_dates_parsed(self):
        txns = parsers.parse_scotia_new_credit(FIXTURES / "new_credit.csv")
        assert all(isinstance(t.date, date) for t in txns)

    def test_stores_lowercase(self):
        txns = parsers.parse_scotia_new_credit(FIXTURES / "new_credit.csv")
        assert all(t.store_raw == t.store_raw.lower() for t in txns)


class TestParsersOldCredit:
    def test_parses_expenses(self):
        txns = parsers.parse_scotia_old_credit(FIXTURES / "old_credit.csv")
        expenses = [t for t in txns if t.txn_type == TxnType.EXPENSE]
        assert len(expenses) == 4

    def test_filters_payments(self):
        txns = parsers.parse_scotia_old_credit(FIXTURES / "old_credit.csv")
        stores = [t.store_raw for t in txns]
        assert not any("payment" in s for s in stores)

    def test_income_detected(self):
        # The payment FROM line should be filtered (ignorable)
        txns = parsers.parse_scotia_old_credit(FIXTURES / "old_credit.csv")
        income = [t for t in txns if t.txn_type == TxnType.INCOME]
        assert len(income) == 0


class TestParsersOldDebit:
    def test_parses_expenses(self):
        txns = parsers.parse_scotia_old_debit(FIXTURES / "old_debit.csv")
        expenses = [t for t in txns if t.txn_type == TxnType.EXPENSE]
        assert len(expenses) == 2

    def test_income_detected(self):
        txns = parsers.parse_scotia_old_debit(FIXTURES / "old_debit.csv")
        income = [t for t in txns if t.txn_type == TxnType.INCOME]
        assert len(income) == 1
        assert income[0].amount == 3500.00

    def test_filters_transfers(self):
        txns = parsers.parse_scotia_old_debit(FIXTURES / "old_debit.csv")
        stores = [t.store_raw for t in txns]
        assert not any("mb-credit card" in s for s in stores)


class TestAutoDetect:
    def test_detects_new_credit(self):
        txns = parsers.detect_and_parse(FIXTURES / "new_credit.csv")
        assert len(txns) > 0

    def test_detects_old_credit(self):
        txns = parsers.detect_and_parse(FIXTURES / "old_credit.csv")
        assert len(txns) > 0

    def test_detects_old_debit(self):
        txns = parsers.detect_and_parse(FIXTURES / "old_debit.csv")
        assert len(txns) > 0

    def test_parse_directory(self):
        txns = parsers.parse_directory(FIXTURES)
        # All 3 fixtures combined
        assert len(txns) >= 8


class TestParserEdgeCases:
    def test_empty_csv(self, tmp_path):
        empty = tmp_path / "empty.csv"
        empty.write_text("Date,Description,Amount\n")
        # New credit format with just header
        txns = parsers.detect_and_parse(empty)
        assert txns == []

    def test_duplicate_transactions(self):
        """Same transaction in two files should both be parsed
        (dedup is caller responsibility)."""
        txns1 = parsers.parse_scotia_new_credit(FIXTURES / "new_credit.csv")
        txns2 = parsers.parse_scotia_new_credit(FIXTURES / "new_credit.csv")
        assert len(txns1) == len(txns2)


# --- Categorizer tests ---


class TestCategorizerExact:
    def test_exact_match(self):
        db = CategoryDB(store_to_category={"uber eats": ["Dining"]})
        txn = Transaction(date=date(2026, 1, 1), amount=25,
                          store_raw="uber eats")
        result = categorizer.categorize(txn, db)
        assert result.category == "Dining"
        assert result.confidence == "exact"

    def test_case_insensitive(self):
        db = CategoryDB(store_to_category={"uber eats": ["Dining"]})
        txn = Transaction(date=date(2026, 1, 1), amount=25,
                          store_raw="UBER EATS")
        # store_raw should be lowercased by parser, but categorizer
        # handles it too
        result = categorizer.categorize(txn, db)
        assert result.category == "Dining"


class TestCategorizerSubstring:
    def test_substring_in_long_bank_string(self):
        db = CategoryDB(store_to_category={
            "shoppers drug mart": ["Health"]
        })
        txn = Transaction(
            date=date(2026, 1, 1), amount=13,
            store_raw="shoppers drug mart #22       vancouver    bc  (apple pay)"
        )
        result = categorizer.categorize(txn, db)
        assert result.category == "Health"
        assert result.confidence == "substring"

    def test_short_key_not_matched(self):
        """Keys shorter than 4 chars should not substring match."""
        db = CategoryDB(store_to_category={"ab": ["Dining"]})
        txn = Transaction(date=date(2026, 1, 1), amount=10,
                          store_raw="abstract art gallery")
        result = categorizer.categorize(txn, db)
        assert result.category is None


class TestCategorizerStorePairs:
    def test_pair_normalization(self):
        db = CategoryDB(
            store_to_category={"petro canada": ["Transportation"]},
            store_pairs={"petro-canada 68": "petro canada"},
        )
        txn = Transaction(date=date(2026, 1, 1), amount=65,
                          store_raw="petro-canada 68")
        result = categorizer.categorize(txn, db)
        assert result.category == "Transportation"


class TestCategorizerBatch:
    def test_splits_classified_and_unclassified(self):
        db = CategoryDB(store_to_category={
            "uber eats": ["Dining"],
            "walmart": ["Groceries"],
        })
        txns = [
            Transaction(date=date(2026, 1, 1), amount=25,
                        store_raw="uber eats"),
            Transaction(date=date(2026, 1, 1), amount=50,
                        store_raw="totally unknown xyz"),
            Transaction(date=date(2026, 1, 1), amount=10,
                        store_raw="walmart"),
        ]
        classified, unclassified = categorizer.categorize_batch(txns, db)
        assert len(classified) == 2
        assert len(unclassified) == 1
        assert classified[0].category == "Dining"
        assert classified[1].category == "Groceries"
        assert unclassified[0].category == ""

    def test_mutates_transactions(self):
        db = CategoryDB(store_to_category={"test": ["Dining"]})
        txn = Transaction(date=date(2026, 1, 1), amount=10,
                          store_raw="test")
        categorizer.categorize_batch([txn], db)
        assert txn.category == "Dining"
        assert txn.store_normalized != ""


class TestCleanStoreName:
    @pytest.mark.parametrize("raw,expected", [
        ("SHOPPERS DRUG MART #22       VANCOUVER    BC  (Apple Pay) ",
         "shoppers drug mart #22"),
        ("UBER EATS                    TORONTO      ON  (Apple Pay) ",
         "uber eats"),
        ("PETRO-CANADA 68              NORTH VANCOUVBC  ",
         "petro-canada 68"),
        ("APPLE.COM/BILL           866-712-7753 ON  ",
         "apple.com/bill"),
        ("simple store", "simple store"),
    ])
    def test_cleaning(self, raw, expected):
        result = categorizer.clean_store_name(raw)
        assert result == expected


# --- Reports tests ---


class TestMonthlySummary:
    def test_basic_summary(self, sample_txns):
        db = CategoryDB(store_to_category={
            "uber eats": ["Dining"],
            "walmart": ["Groceries"],
            "petro canada": ["Transportation"],
        })
        expenses = [t for t in sample_txns if t.txn_type == TxnType.EXPENSE]
        classified, _ = categorizer.categorize_batch(expenses, db)
        summary = reports.monthly_summary(classified)

        assert "Dining" in summary.columns
        assert "Groceries" in summary.columns
        assert summary.loc["2026-01", "Dining"] == 25.50
        assert summary.loc["2026-01", "Groceries"] == 50.00
        assert summary.loc["2026-02", "Dining"] == 30.00
        assert summary.loc["2026-02", "Transportation"] == 65.00

    def test_total_column(self, sample_txns):
        db = CategoryDB(store_to_category={
            "uber eats": ["Dining"], "walmart": ["Groceries"],
            "petro canada": ["Transportation"],
        })
        expenses = [t for t in sample_txns if t.txn_type == TxnType.EXPENSE]
        classified, _ = categorizer.categorize_batch(expenses, db)
        summary = reports.monthly_summary(classified)
        assert summary.loc["2026-01", "TOTAL"] == 75.50

    def test_empty_returns_empty_df(self):
        assert reports.monthly_summary([]).empty

    def test_ignores_income(self, sample_txns):
        summary = reports.monthly_summary(sample_txns)
        # monthly_summary only includes EXPENSE type transactions
        # Income ($5000 payroll) should not appear in the totals
        if not summary.empty:
            assert summary["TOTAL"].sum() < 5000


class TestCategoryAverages:
    def test_averages(self):
        txns = [
            Transaction(date=date(2026, 1, 1), amount=100,
                        store_raw="a", category="Dining",
                        txn_type=TxnType.EXPENSE),
            Transaction(date=date(2026, 2, 1), amount=200,
                        store_raw="b", category="Dining",
                        txn_type=TxnType.EXPENSE),
            Transaction(date=date(2026, 1, 1), amount=50,
                        store_raw="c", category="Groceries",
                        txn_type=TxnType.EXPENSE),
        ]
        summary = reports.monthly_summary(txns)
        avgs = reports.category_averages(summary)
        assert avgs["Dining"] == 150.0
        assert avgs["Groceries"] == 25.0  # 50/2 months


class TestIncomeSummary:
    def test_income(self):
        txns = [
            Transaction(date=date(2026, 1, 1), amount=5000,
                        store_raw="payroll", store_normalized="payroll",
                        txn_type=TxnType.INCOME),
            Transaction(date=date(2026, 2, 1), amount=5000,
                        store_raw="payroll", store_normalized="payroll",
                        txn_type=TxnType.INCOME),
        ]
        summary = reports.income_summary(txns)
        assert not summary.empty
        assert summary.loc["2026-01", "TOTAL"] == 5000
        assert summary.loc["2026-02", "TOTAL"] == 5000


class TestToCsv:
    def test_writes_csv(self, tmp_path):
        txns = [
            Transaction(date=date(2026, 1, 1), amount=25.50,
                        store_raw="test", store_normalized="test",
                        category="Dining", txn_type=TxnType.EXPENSE,
                        source_file="test.csv"),
        ]
        path = tmp_path / "out.csv"
        reports.to_csv(txns, str(path))
        df = pd.read_csv(path)
        assert len(df) == 1
        assert df.iloc[0]["amount"] == 25.50
        assert df.iloc[0]["category"] == "Dining"
        assert df.iloc[0]["type"] == "expense"


# --- End-to-end tests ---


class TestEndToEnd:
    def test_full_pipeline(self, sample_db):
        """Full pipeline: parse fixtures → categorize → report."""
        db = database.load_db(sample_db)
        txns = parsers.parse_directory(FIXTURES)
        expenses = [t for t in txns if t.txn_type == TxnType.EXPENSE]

        classified, unclassified = categorizer.categorize_batch(
            expenses, db)

        # Should classify some (uber eats, shoppers, petro, amazon)
        assert len(classified) > 0
        # Should have some unclassified too
        assert len(classified) + len(unclassified) == len(expenses)

        summary = reports.monthly_summary(classified)
        assert not summary.empty
        assert "TOTAL" in summary.columns

        # Verify amounts are preserved (no data loss)
        total_classified = sum(t.amount for t in classified)
        total_from_summary = summary["TOTAL"].sum()
        assert abs(total_classified - total_from_summary) < 0.01

    def test_cli_batch_mode(self, sample_db, tmp_path):
        """Test CLI runs without error in batch mode."""
        out = tmp_path / "output"
        result = subprocess.run(
            [sys.executable, "-m", "smtm.cli",
             "--csv-dir", str(FIXTURES),
             "--db-dir", str(sample_db),
             "--out-dir", str(out),
             "import", "--batch"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "classified" in result.stdout
        assert (out / "classified.csv").exists()

    def test_cli_profile(self, sample_db):
        """Test profile command."""
        result = subprocess.run(
            [sys.executable, "-m", "smtm.cli",
             "--csv-dir", str(FIXTURES),
             "--db-dir", str(sample_db),
             "profile"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "Date range" in result.stdout
        assert "Classification rate" in result.stdout

    def test_amounts_not_lost(self, sample_db):
        """Total input amount == classified + unclassified amounts."""
        db = database.load_db(sample_db)
        txns = parsers.parse_directory(FIXTURES)
        expenses = [t for t in txns if t.txn_type == TxnType.EXPENSE]
        total_input = sum(t.amount for t in expenses)

        classified, unclassified = categorizer.categorize_batch(
            expenses, db)
        total_output = (sum(t.amount for t in classified) +
                        sum(t.amount for t in unclassified))

        assert abs(total_input - total_output) < 0.01

    def test_no_duplicate_categorization(self, sample_db):
        """A transaction should appear in classified OR unclassified,
        never both."""
        db = database.load_db(sample_db)
        txns = parsers.parse_directory(FIXTURES)
        expenses = [t for t in txns if t.txn_type == TxnType.EXPENSE]

        classified, unclassified = categorizer.categorize_batch(
            expenses, db)

        classified_ids = set(id(t) for t in classified)
        unclassified_ids = set(id(t) for t in unclassified)
        assert classified_ids.isdisjoint(unclassified_ids)
        assert len(classified) + len(unclassified) == len(expenses)
