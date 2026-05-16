"""Tests for the smtm package."""
import json
import tempfile
from datetime import date
from pathlib import Path

import pytest

from smtm import categorizer, database, parsers, reports
from smtm.models import CategoryDB, Transaction, TxnType

FIXTURES = Path(__file__).parent / "fixtures"


# --- Database tests ---


class TestDatabase:
    def test_load_db(self, tmp_path):
        (tmp_path / "expenses.json").write_text(
            json.dumps({"expense": ["Dining", "Groceries"]})
        )
        (tmp_path / "storesWithExpenses.json").write_text(
            json.dumps({"uber eats": ["Dining"], "walmart": ["Groceries"]})
        )
        (tmp_path / "storePairs.json").write_text(
            json.dumps({"uber eats toronto": "uber eats"})
        )

        db = database.load_db(tmp_path)
        assert db.categories == ["Dining", "Groceries"]
        assert db.store_to_category["uber eats"] == ["Dining"]
        assert db.store_pairs["uber eats toronto"] == "uber eats"

    def test_save_db(self, tmp_path):
        db = CategoryDB(
            categories=["Dining"],
            store_to_category={"test": ["Dining"]},
            store_pairs={"t": "test"},
        )
        database.save_db(db, tmp_path)
        assert (tmp_path / "expenses.json").exists()
        assert (tmp_path / "storesWithExpenses.json").exists()
        assert (tmp_path / "storePairs.json").exists()

        loaded = database.load_db(tmp_path)
        assert loaded.categories == ["Dining"]
        assert loaded.store_to_category == {"test": ["Dining"]}

    def test_load_missing_dir(self, tmp_path):
        db = database.load_db(tmp_path / "nonexistent")
        assert db.categories == []
        assert db.store_to_category == {}


# --- Parser tests ---


class TestParsers:
    def test_parse_new_credit(self):
        txns = parsers.parse_scotia_new_credit(FIXTURES / "new_credit.csv")
        # Should have 4 expenses (payment is income, filtered)
        expenses = [t for t in txns if t.txn_type == TxnType.EXPENSE]
        income = [t for t in txns if t.txn_type == TxnType.INCOME]
        assert len(expenses) == 4
        assert len(income) == 0  # payment from is filtered as ignorable
        assert expenses[0].store_raw == "uber eats"
        assert expenses[0].amount == 25.50
        assert expenses[0].date == date(2026, 4, 15)

    def test_parse_old_credit(self):
        txns = parsers.parse_scotia_old_credit(FIXTURES / "old_credit.csv")
        expenses = [t for t in txns if t.txn_type == TxnType.EXPENSE]
        income = [t for t in txns if t.txn_type == TxnType.INCOME]
        assert len(expenses) == 4
        assert len(income) == 0  # payment filtered
        assert expenses[0].amount == 25.50

    def test_parse_old_debit(self):
        txns = parsers.parse_scotia_old_debit(FIXTURES / "old_debit.csv")
        expenses = [t for t in txns if t.txn_type == TxnType.EXPENSE]
        income = [t for t in txns if t.txn_type == TxnType.INCOME]
        # MB-CREDIT CARD/LOC PAY is filtered, so 2 expenses + 1 income
        assert len(expenses) == 2
        assert len(income) == 1
        assert income[0].amount == 3500.00

    def test_detect_and_parse_new_credit(self):
        txns = parsers.detect_and_parse(FIXTURES / "new_credit.csv")
        assert len(txns) > 0
        assert all(isinstance(t, Transaction) for t in txns)

    def test_detect_and_parse_old_credit(self):
        txns = parsers.detect_and_parse(FIXTURES / "old_credit.csv")
        assert len(txns) > 0

    def test_detect_and_parse_old_debit(self):
        txns = parsers.detect_and_parse(FIXTURES / "old_debit.csv")
        assert len(txns) > 0

    def test_parse_directory(self):
        txns = parsers.parse_directory(FIXTURES)
        assert len(txns) > 0


# --- Categorizer tests ---


class TestCategorizer:
    @pytest.fixture
    def db(self):
        return CategoryDB(
            categories=["Dining", "Groceries", "Transportation", "Health",
                        "Shopping", "Insurance"],
            store_to_category={
                "uber eats": ["Dining"],
                "shoppers drug mart": ["Health"],
                "petro canada": ["Transportation"],
                "amazon": ["Shopping"],
                "walmart": ["Groceries"],
                "insurance": ["Insurance"],
            },
            store_pairs={
                "petro-canada 68": "petro canada",
            },
        )

    def test_exact_match(self, db):
        txn = Transaction(date=date(2026, 1, 1), amount=25, store_raw="uber eats")
        result = categorizer.categorize(txn, db)
        assert result.category == "Dining"
        assert result.confidence == "exact"

    def test_substring_match(self, db):
        txn = Transaction(
            date=date(2026, 1, 1), amount=13,
            store_raw="shoppers drug mart #22       vancouver    bc  (apple pay)"
        )
        result = categorizer.categorize(txn, db)
        assert result.category == "Health"
        assert result.confidence == "substring"

    def test_store_pair_normalization(self, db):
        txn = Transaction(
            date=date(2026, 1, 1), amount=65,
            store_raw="petro-canada 68"
        )
        result = categorizer.categorize(txn, db)
        assert result.category == "Transportation"

    def test_unknown_merchant(self, db):
        txn = Transaction(
            date=date(2026, 1, 1), amount=50,
            store_raw="totally unknown place xyz"
        )
        result = categorizer.categorize(txn, db)
        assert result.category is None
        assert result.confidence == "unknown"

    def test_categorize_batch(self, db):
        txns = [
            Transaction(date=date(2026, 1, 1), amount=25, store_raw="uber eats"),
            Transaction(date=date(2026, 1, 1), amount=50, store_raw="unknown xyz"),
            Transaction(date=date(2026, 1, 1), amount=10, store_raw="walmart"),
        ]
        classified, unclassified = categorizer.categorize_batch(txns, db)
        assert len(classified) == 2
        assert len(unclassified) == 1
        assert classified[0].category == "Dining"
        assert classified[1].category == "Groceries"

    def test_clean_store_name(self):
        assert categorizer.clean_store_name(
            "SHOPPERS DRUG MART #22       VANCOUVER    BC  (Apple Pay) "
        ) == "shoppers drug mart #22"
        assert categorizer.clean_store_name(
            "UBER EATS                    TORONTO      ON  (Apple Pay) "
        ) == "uber eats"


# --- Reports tests ---


class TestReports:
    def test_monthly_summary(self):
        txns = [
            Transaction(date=date(2026, 1, 5), amount=25, store_raw="a",
                        category="Dining", txn_type=TxnType.EXPENSE),
            Transaction(date=date(2026, 1, 15), amount=50, store_raw="b",
                        category="Groceries", txn_type=TxnType.EXPENSE),
            Transaction(date=date(2026, 2, 5), amount=30, store_raw="c",
                        category="Dining", txn_type=TxnType.EXPENSE),
        ]
        summary = reports.monthly_summary(txns)
        assert "Dining" in summary.columns
        assert "Groceries" in summary.columns
        assert summary.loc["2026-01", "Dining"] == 25
        assert summary.loc["2026-01", "TOTAL"] == 75
        assert summary.loc["2026-02", "Dining"] == 30

    def test_monthly_summary_empty(self):
        summary = reports.monthly_summary([])
        assert summary.empty

    def test_category_averages(self):
        txns = [
            Transaction(date=date(2026, 1, 1), amount=100, store_raw="a",
                        category="Dining", txn_type=TxnType.EXPENSE),
            Transaction(date=date(2026, 2, 1), amount=200, store_raw="b",
                        category="Dining", txn_type=TxnType.EXPENSE),
        ]
        summary = reports.monthly_summary(txns)
        avgs = reports.category_averages(summary)
        assert avgs["Dining"] == 150.0

    def test_income_summary(self):
        txns = [
            Transaction(date=date(2026, 1, 1), amount=5000, store_raw="payroll",
                        store_normalized="payroll", txn_type=TxnType.INCOME),
        ]
        summary = reports.income_summary(txns)
        assert not summary.empty
        assert summary.loc["2026-01", "TOTAL"] == 5000

    def test_to_csv(self, tmp_path):
        txns = [
            Transaction(date=date(2026, 1, 1), amount=25, store_raw="test",
                        category="Dining", txn_type=TxnType.EXPENSE),
        ]
        path = tmp_path / "out.csv"
        reports.to_csv(txns, str(path))
        assert path.exists()
        content = path.read_text()
        assert "Dining" in content
        assert "25" in content
