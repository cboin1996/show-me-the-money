"""Comprehensive tests for the smtm package."""
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from smtm import categorizer, reports
from smtm.adapters import detect_and_parse, parse_directory
from smtm.db import Database
from smtm.models import CategoryDB, Transaction, TxnType

FIXTURES = Path(__file__).parent / "fixtures"


# --- Fixtures ---


@pytest.fixture
def sample_category_db():
    return CategoryDB(
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


@pytest.fixture
def sqlite_db(tmp_path):
    db = Database(tmp_path / "test.db")
    db.initialize()
    return db


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


# --- JSON Migration tests ---


class TestJSONMigration:
    def test_migrate_from_json_files(self, tmp_path):
        json_dir = tmp_path / "json"
        json_dir.mkdir()
        (json_dir / "expenses.json").write_text(
            json.dumps({"Dining": ["Dining"], "Groceries": ["Groceries"]}))
        (json_dir / "storesWithExpenses.json").write_text(
            json.dumps({"uber eats": ["Dining"], "walmart": ["Groceries"]}))
        (json_dir / "storePairs.json").write_text(
            json.dumps({"petro-canada 68": "petro canada"}))

        db = Database(tmp_path / "test.db")
        db.initialize()
        db.migrate_from_json(json_dir)
        cat_db = db.load_category_db()
        assert "uber eats" in cat_db.store_to_category
        assert cat_db.store_pairs["petro-canada 68"] == "petro canada"

    def test_migrate_missing_dir(self, tmp_path):
        db = Database(tmp_path / "test.db")
        db.initialize()
        db.migrate_from_json(tmp_path / "nope")
        cat_db = db.load_category_db()
        assert cat_db.store_to_category == {}

    def test_migrate_empty_dir(self, tmp_path):
        json_dir = tmp_path / "json"
        json_dir.mkdir()
        db = Database(tmp_path / "test.db")
        db.initialize()
        db.migrate_from_json(json_dir)
        cat_db = db.load_category_db()
        assert cat_db.store_to_category == {}


# --- SQLite Database tests ---


class TestSQLiteDB:
    def test_initialize(self, sqlite_db):
        stats = sqlite_db.get_stats()
        assert stats["total"] == 0

    def test_insert_and_retrieve(self, sqlite_db):
        txn = Transaction(date=date(2026, 1, 5), amount=25.50,
                          store_raw="uber eats", category="Dining",
                          txn_type=TxnType.EXPENSE, source_file="test.csv")
        assert sqlite_db.insert_transaction(txn)
        txns = sqlite_db.get_all_transactions()
        assert len(txns) == 1
        assert txns[0].amount == 25.50
        assert txns[0].category == "Dining"

    def test_dedup_prevents_duplicates(self, sqlite_db):
        txn1 = Transaction(date=date(2026, 1, 5), amount=25.50,
                           store_raw="uber eats", source_file="test.csv")
        txn2 = Transaction(date=date(2026, 1, 5), amount=25.50,
                           store_raw="uber eats", source_file="test.csv")
        assert sqlite_db.insert_transaction(txn1)
        assert not sqlite_db.insert_transaction(txn2)
        assert len(sqlite_db.get_all_transactions()) == 1

    def test_different_files_not_deduped(self, sqlite_db):
        txn1 = Transaction(date=date(2026, 1, 5), amount=25.50,
                           store_raw="uber eats", source_file="a.csv")
        txn2 = Transaction(date=date(2026, 1, 5), amount=25.50,
                           store_raw="uber eats", source_file="b.csv")
        assert sqlite_db.insert_transaction(txn1)
        assert sqlite_db.insert_transaction(txn2)
        assert len(sqlite_db.get_all_transactions()) == 2

    def test_bulk_insert(self, sqlite_db):
        txns = [
            Transaction(date=date(2026, 1, i), amount=10.0 * i,
                        store_raw=f"store{i}", source_file="test.csv")
            for i in range(1, 6)
        ]
        inserted, dupes = sqlite_db.insert_transactions(txns)
        assert inserted == 5
        assert dupes == 0

    def test_soft_delete(self, sqlite_db):
        txn = Transaction(date=date(2026, 1, 5), amount=25.50,
                          store_raw="uber eats", source_file="test.csv")
        sqlite_db.insert_transaction(txn)
        assert len(sqlite_db.get_all_transactions()) == 1
        sqlite_db.soft_delete(txn.uuid)
        assert len(sqlite_db.get_all_transactions()) == 0
        assert len(sqlite_db.get_all_transactions(include_deleted=True)) == 1

    def test_category_rules(self, sqlite_db):
        sqlite_db.add_category_rule("uber eats", "Dining", "exact")
        sqlite_db.add_category_rule("petro", "Transportation", "substring")
        rules = sqlite_db.get_category_rules()
        assert len(rules) == 2

    def test_store_pairs(self, sqlite_db):
        sqlite_db.add_store_pair("petro-canada 68", "petro canada")
        pairs = sqlite_db.get_store_pairs()
        assert pairs["petro-canada 68"] == "petro canada"

    def test_budgets(self, sqlite_db):
        sqlite_db.set_budget("2026-01", "Dining", 500.0)
        sqlite_db.set_budget("2026-01", "Groceries", 300.0)
        budgets = sqlite_db.get_budgets("2026-01")
        assert len(budgets) == 2

    def test_copy_budget(self, sqlite_db):
        sqlite_db.set_budget("2026-01", "Dining", 500.0)
        sqlite_db.copy_budget("2026-01", "2026-02")
        budgets = sqlite_db.get_budgets("2026-02")
        assert len(budgets) == 1
        assert budgets[0]["amount"] == 500.0

    def test_import_log(self, sqlite_db):
        assert not sqlite_db.file_already_imported("abc123")
        sqlite_db.log_import("test.csv", "abc123", 10, 8)
        assert sqlite_db.file_already_imported("abc123")
        history = sqlite_db.get_import_history()
        assert len(history) == 1

    def test_migrate_from_json(self, sqlite_db, tmp_path):
        json_dir = tmp_path / "json"
        json_dir.mkdir()
        (json_dir / "expenses.json").write_text(
            json.dumps({"Dining": ["Dining"]}))
        (json_dir / "storesWithExpenses.json").write_text(
            json.dumps({"uber eats": ["Dining"]}))
        (json_dir / "storePairs.json").write_text(
            json.dumps({"petro-canada 68": "petro canada"}))
        sqlite_db.migrate_from_json(json_dir)
        cat_db = sqlite_db.load_category_db()
        assert "uber eats" in cat_db.store_to_category
        assert cat_db.store_to_category["uber eats"] == ["Dining"]
        assert cat_db.store_pairs.get("petro-canada 68") == "petro canada"

    def test_get_expenses_and_income(self, sqlite_db):
        sqlite_db.insert_transaction(
            Transaction(date=date(2026, 1, 5), amount=25.50,
                        store_raw="food", txn_type=TxnType.EXPENSE,
                        source_file="a.csv"))
        sqlite_db.insert_transaction(
            Transaction(date=date(2026, 1, 15), amount=5000,
                        store_raw="payroll", txn_type=TxnType.INCOME,
                        source_file="a.csv"))
        assert len(sqlite_db.get_expenses()) == 1
        assert len(sqlite_db.get_income()) == 1

    def test_stats(self, sqlite_db):
        sqlite_db.insert_transaction(
            Transaction(date=date(2026, 1, 5), amount=25.50,
                        store_raw="food", category="Dining",
                        txn_type=TxnType.EXPENSE, source_file="a.csv"))
        sqlite_db.insert_transaction(
            Transaction(date=date(2026, 1, 6), amount=10,
                        store_raw="unknown", txn_type=TxnType.EXPENSE,
                        source_file="a.csv"))
        stats = sqlite_db.get_stats()
        assert stats["total"] == 2
        assert stats["categorized"] == 1
        assert stats["classification_rate"] == 50.0


# --- Adapter tests ---


class TestAdaptersNewCredit:
    def test_parses_expenses(self):
        txns = detect_and_parse(FIXTURES / "new_credit.csv")
        expenses = [t for t in txns if t.txn_type == TxnType.EXPENSE]
        assert len(expenses) == 4

    def test_filters_payments(self):
        txns = detect_and_parse(FIXTURES / "new_credit.csv")
        stores = [t.store_raw for t in txns]
        assert not any("payment" in s for s in stores)

    def test_amounts_positive(self):
        txns = detect_and_parse(FIXTURES / "new_credit.csv")
        assert all(t.amount > 0 for t in txns)

    def test_dates_parsed(self):
        txns = detect_and_parse(FIXTURES / "new_credit.csv")
        assert all(isinstance(t.date, date) for t in txns)

    def test_stores_lowercase(self):
        txns = detect_and_parse(FIXTURES / "new_credit.csv")
        assert all(t.store_raw == t.store_raw.lower() for t in txns)


class TestAdaptersOldCredit:
    def test_parses_expenses(self):
        txns = detect_and_parse(FIXTURES / "old_credit.csv")
        expenses = [t for t in txns if t.txn_type == TxnType.EXPENSE]
        assert len(expenses) == 4

    def test_filters_payments(self):
        txns = detect_and_parse(FIXTURES / "old_credit.csv")
        stores = [t.store_raw for t in txns]
        assert not any("payment" in s for s in stores)


class TestAdaptersOldDebit:
    def test_parses_expenses(self):
        txns = detect_and_parse(FIXTURES / "old_debit.csv")
        expenses = [t for t in txns if t.txn_type == TxnType.EXPENSE]
        assert len(expenses) == 2

    def test_income_detected(self):
        txns = detect_and_parse(FIXTURES / "old_debit.csv")
        income = [t for t in txns if t.txn_type == TxnType.INCOME]
        assert len(income) == 1
        assert income[0].amount == 3500.00

    def test_filters_transfers(self):
        txns = detect_and_parse(FIXTURES / "old_debit.csv")
        stores = [t.store_raw for t in txns]
        assert not any("mb-credit card" in s for s in stores)


class TestAdapterAutoDetect:
    def test_detects_new_credit(self):
        txns = detect_and_parse(FIXTURES / "new_credit.csv")
        assert len(txns) > 0

    def test_detects_old_credit(self):
        txns = detect_and_parse(FIXTURES / "old_credit.csv")
        assert len(txns) > 0

    def test_detects_old_debit(self):
        txns = detect_and_parse(FIXTURES / "old_debit.csv")
        assert len(txns) > 0

    def test_parse_directory(self):
        txns = parse_directory(FIXTURES)
        assert len(txns) >= 8

    def test_empty_csv(self, tmp_path):
        empty = tmp_path / "empty.csv"
        empty.write_text("Date,Description,Amount\n")
        txns = detect_and_parse(empty)
        assert txns == []


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
        db = CategoryDB(store_to_category={"ab": ["Dining"]})
        txn = Transaction(date=date(2026, 1, 1), amount=10,
                          store_raw="abstract art gallery")
        result = categorizer.categorize(txn, db)
        assert result.category is None


class TestCategorizerGenericDescription:
    def test_pos_purchase_uses_sub_description(self):
        db = CategoryDB(store_to_category={
            "eurest": ["Dining"],
            "pos purchase": ["Shopping"],
        })
        txn = Transaction(
            date=date(2026, 1, 1), amount=13.65,
            store_raw="pos purchase",
            sub_description="Apos Eurest-Amazon-6     Vanco",
        )
        result = categorizer.categorize(txn, db)
        assert result.category == "Dining"

    def test_pos_purchase_not_matched_as_shopping(self):
        db = CategoryDB(store_to_category={
            "pos purchase": ["Shopping"],
        })
        txn = Transaction(
            date=date(2026, 1, 1), amount=13.65,
            store_raw="pos purchase",
            sub_description="some unknown store",
        )
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
        ("APOS Eurest-Amazon-6     Vanco", "eurest-amazon-6"),
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
        assert avgs["Groceries"] == 25.0


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


# --- End-to-end tests ---


class TestEndToEnd:
    def test_full_pipeline(self, sample_category_db):
        txns = parse_directory(FIXTURES)
        expenses = [t for t in txns if t.txn_type == TxnType.EXPENSE]

        classified, unclassified = categorizer.categorize_batch(
            expenses, sample_category_db)
        assert len(classified) > 0
        assert len(classified) + len(unclassified) == len(expenses)

        summary = reports.monthly_summary(classified)
        assert not summary.empty
        assert "TOTAL" in summary.columns

        total_classified = sum(t.amount for t in classified)
        total_from_summary = summary["TOTAL"].sum()
        assert abs(total_classified - total_from_summary) < 0.01

    def test_cli_import(self, tmp_path):
        db_path = tmp_path / "test.db"
        json_dir = tmp_path / "json"
        json_dir.mkdir()
        # Write minimal category DB
        (json_dir / "expenses.json").write_text('{"expense":["Dining"]}')
        (json_dir / "storesWithExpenses.json").write_text(
            '{"uber eats":["Dining"]}'
        )
        (json_dir / "storePairs.json").write_text("{}")

        result = subprocess.run(
            [sys.executable, "-m", "smtm.cli",
             "--csv-dir", str(FIXTURES),
             "--db-path", str(db_path),
             "--json-dir", str(json_dir),
             "import"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "Importing" in result.stdout

    def test_cli_profile(self, tmp_path):
        db_path = tmp_path / "test.db"
        result = subprocess.run(
            [sys.executable, "-m", "smtm.cli",
             "--csv-dir", str(FIXTURES),
             "--db-path", str(db_path),
             "profile"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "Date range" in result.stdout
        assert "Classification rate" in result.stdout

    def test_import_dedup(self, tmp_path):
        """Importing the same files twice should produce 0 new on second run."""
        db_path = tmp_path / "test.db"
        cmd = [
            sys.executable, "-m", "smtm.cli",
            "--csv-dir", str(FIXTURES),
            "--db-path", str(db_path),
            "import",
        ]
        subprocess.run(cmd, capture_output=True, text=True)
        result = subprocess.run(cmd, capture_output=True, text=True)
        assert result.returncode == 0
        assert "already imported" in result.stdout

    def test_amounts_not_lost(self, sample_category_db):
        txns = parse_directory(FIXTURES)
        expenses = [t for t in txns if t.txn_type == TxnType.EXPENSE]
        total_input = sum(t.amount for t in expenses)

        classified, unclassified = categorizer.categorize_batch(
            expenses, sample_category_db)
        total_output = (sum(t.amount for t in classified) +
                        sum(t.amount for t in unclassified))

        assert abs(total_input - total_output) < 0.01

    def test_no_duplicate_categorization(self, sample_category_db):
        txns = parse_directory(FIXTURES)
        expenses = [t for t in txns if t.txn_type == TxnType.EXPENSE]

        classified, unclassified = categorizer.categorize_batch(
            expenses, sample_category_db)

        classified_ids = set(id(t) for t in classified)
        unclassified_ids = set(id(t) for t in unclassified)
        assert classified_ids.isdisjoint(unclassified_ids)
        assert len(classified) + len(unclassified) == len(expenses)
