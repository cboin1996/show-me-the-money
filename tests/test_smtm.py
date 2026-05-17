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
        categories=[
            "Dining",
            "Groceries",
            "Transportation",
            "Health",
            "Shopping",
            "Entertainment",
            "Subscriptions",
            "Insurance",
            "Utilities",
            "Fees",
            "Travel",
            "Misc",
        ],
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
        Transaction(
            date=date(2026, 1, 5),
            amount=25.50,
            store_raw="uber eats",
            txn_type=TxnType.EXPENSE,
        ),
        Transaction(
            date=date(2026, 1, 10),
            amount=50.00,
            store_raw="walmart",
            txn_type=TxnType.EXPENSE,
        ),
        Transaction(
            date=date(2026, 1, 15),
            amount=5000.00,
            store_raw="payroll",
            txn_type=TxnType.INCOME,
        ),
        Transaction(
            date=date(2026, 2, 5),
            amount=30.00,
            store_raw="uber eats",
            txn_type=TxnType.EXPENSE,
        ),
        Transaction(
            date=date(2026, 2, 10),
            amount=65.00,
            store_raw="petro canada",
            txn_type=TxnType.EXPENSE,
        ),
    ]


# --- JSON Migration tests ---


class TestJSONMigration:
    def test_migrate_from_json_files(self, tmp_path):
        json_dir = tmp_path / "json"
        json_dir.mkdir()
        (json_dir / "expenses.json").write_text(
            json.dumps({"Dining": ["Dining"], "Groceries": ["Groceries"]})
        )
        (json_dir / "storesWithExpenses.json").write_text(
            json.dumps({"uber eats": ["Dining"], "walmart": ["Groceries"]})
        )
        (json_dir / "storePairs.json").write_text(
            json.dumps({"petro-canada 68": "petro canada"})
        )

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
        txn = Transaction(
            date=date(2026, 1, 5),
            amount=25.50,
            store_raw="uber eats",
            category="Dining",
            txn_type=TxnType.EXPENSE,
            source_file="test.csv",
        )
        assert sqlite_db.insert_transaction(txn)
        txns = sqlite_db.get_all_transactions()
        assert len(txns) == 1
        assert txns[0].amount == 25.50
        assert txns[0].category == "Dining"

    def test_dedup_prevents_duplicates(self, sqlite_db):
        txn1 = Transaction(
            date=date(2026, 1, 5),
            amount=25.50,
            store_raw="uber eats",
            source_file="test.csv",
        )
        txn2 = Transaction(
            date=date(2026, 1, 5),
            amount=25.50,
            store_raw="uber eats",
            source_file="test.csv",
        )
        assert sqlite_db.insert_transaction(txn1)
        assert not sqlite_db.insert_transaction(txn2)
        assert len(sqlite_db.get_all_transactions()) == 1

    def test_different_files_not_deduped(self, sqlite_db):
        txn1 = Transaction(
            date=date(2026, 1, 5),
            amount=25.50,
            store_raw="uber eats",
            source_file="a.csv",
        )
        txn2 = Transaction(
            date=date(2026, 1, 5),
            amount=25.50,
            store_raw="uber eats",
            source_file="b.csv",
        )
        assert sqlite_db.insert_transaction(txn1)
        assert sqlite_db.insert_transaction(txn2)
        assert len(sqlite_db.get_all_transactions()) == 2

    def test_bulk_insert(self, sqlite_db):
        txns = [
            Transaction(
                date=date(2026, 1, i),
                amount=10.0 * i,
                store_raw=f"store{i}",
                source_file="test.csv",
            )
            for i in range(1, 6)
        ]
        inserted, dupes = sqlite_db.insert_transactions(txns)
        assert inserted == 5
        assert dupes == 0

    def test_soft_delete(self, sqlite_db):
        txn = Transaction(
            date=date(2026, 1, 5),
            amount=25.50,
            store_raw="uber eats",
            source_file="test.csv",
        )
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
        (json_dir / "expenses.json").write_text(json.dumps({"Dining": ["Dining"]}))
        (json_dir / "storesWithExpenses.json").write_text(
            json.dumps({"uber eats": ["Dining"]})
        )
        (json_dir / "storePairs.json").write_text(
            json.dumps({"petro-canada 68": "petro canada"})
        )
        sqlite_db.migrate_from_json(json_dir)
        cat_db = sqlite_db.load_category_db()
        assert "uber eats" in cat_db.store_to_category
        assert cat_db.store_to_category["uber eats"] == ["Dining"]
        assert cat_db.store_pairs.get("petro-canada 68") == "petro canada"

    def test_get_expenses_and_income(self, sqlite_db):
        sqlite_db.insert_transaction(
            Transaction(
                date=date(2026, 1, 5),
                amount=25.50,
                store_raw="food",
                txn_type=TxnType.EXPENSE,
                source_file="a.csv",
            )
        )
        sqlite_db.insert_transaction(
            Transaction(
                date=date(2026, 1, 15),
                amount=5000,
                store_raw="payroll",
                txn_type=TxnType.INCOME,
                source_file="a.csv",
            )
        )
        assert len(sqlite_db.get_expenses()) == 1
        assert len(sqlite_db.get_income()) == 1

    def test_stats(self, sqlite_db):
        sqlite_db.insert_transaction(
            Transaction(
                date=date(2026, 1, 5),
                amount=25.50,
                store_raw="food",
                category="Dining",
                txn_type=TxnType.EXPENSE,
                source_file="a.csv",
            )
        )
        sqlite_db.insert_transaction(
            Transaction(
                date=date(2026, 1, 6),
                amount=10,
                store_raw="unknown",
                txn_type=TxnType.EXPENSE,
                source_file="a.csv",
            )
        )
        stats = sqlite_db.get_stats()
        assert stats["total"] == 2
        assert stats["categorized"] == 1
        assert stats["classification_rate"] == 50.0

    def test_link_transactions(self, sqlite_db):
        expense = Transaction(
            date=date(2026, 3, 1),
            amount=100.0,
            store_raw="restaurant",
            txn_type=TxnType.EXPENSE,
            source_file="test.csv",
        )
        income = Transaction(
            date=date(2026, 3, 2),
            amount=50.0,
            store_raw="friend etransfer",
            txn_type=TxnType.INCOME,
            source_file="test.csv",
        )
        sqlite_db.insert_transaction(expense)
        sqlite_db.insert_transaction(income)

        assert sqlite_db.link_transactions(expense.uuid, income.uuid)

        txns = sqlite_db.get_all_transactions()
        exp_txn = [t for t in txns if t.uuid == expense.uuid][0]
        assert exp_txn.adjustment == 50.0
        assert exp_txn.linked_to == income.uuid
        assert exp_txn.effective_amount == 50.0

        # Income should be soft-deleted
        all_txns = sqlite_db.get_all_transactions(include_deleted=True)
        inc_txn = [t for t in all_txns if t.uuid == income.uuid][0]
        assert inc_txn.is_deleted
        assert inc_txn.linked_to == expense.uuid

    def test_unlink_transactions(self, sqlite_db):
        expense = Transaction(
            date=date(2026, 4, 1),
            amount=200.0,
            store_raw="store",
            txn_type=TxnType.EXPENSE,
            source_file="test.csv",
        )
        income = Transaction(
            date=date(2026, 4, 2),
            amount=75.0,
            store_raw="refund",
            txn_type=TxnType.INCOME,
            source_file="test.csv",
        )
        sqlite_db.insert_transaction(expense)
        sqlite_db.insert_transaction(income)
        sqlite_db.link_transactions(expense.uuid, income.uuid)

        assert sqlite_db.unlink_transactions(expense.uuid)

        txns = sqlite_db.get_all_transactions()
        exp_txn = [t for t in txns if t.uuid == expense.uuid][0]
        assert exp_txn.adjustment == 0.0
        assert exp_txn.linked_to == ""
        # Income restored
        inc_txn = [t for t in txns if t.uuid == income.uuid][0]
        assert not inc_txn.is_deleted

    def test_reimbursers_crud(self, sqlite_db):
        assert sqlite_db.get_reimbursers() == []
        sqlite_db.add_reimburser("john", "John Doe", "substring")
        sqlite_db.add_reimburser("jane@exact", "", "exact")
        reimbursers = sqlite_db.get_reimbursers()
        assert len(reimbursers) == 2
        assert reimbursers[0]["pattern"] == "jane@exact"
        assert reimbursers[1]["pattern"] == "john"
        assert reimbursers[1]["label"] == "John Doe"
        assert sqlite_db.remove_reimburser("john")
        assert not sqlite_db.remove_reimburser("nonexistent")
        assert len(sqlite_db.get_reimbursers()) == 1

    def test_pending_reimbursements(self, sqlite_db):
        sqlite_db.add_reimburser("friend", "My Friend", "substring")
        income = Transaction(
            date=date(2026, 5, 1),
            amount=100.0,
            store_raw="e-transfer from friend bob",
            txn_type=TxnType.INCOME,
            source_file="test.csv",
            store_normalized="e-transfer from friend bob",
        )
        sqlite_db.insert_transaction(income)
        pending = sqlite_db.get_pending_reimbursements()
        assert len(pending) >= 1
        match = [p for p in pending if p["uuid"] == income.uuid]
        assert len(match) == 1
        assert match[0]["amount"] == 100.0
        assert match[0]["reimburser"] == "My Friend"

    def test_reimburser_pairs_crud(self, sqlite_db):
        assert sqlite_db.get_reimburser_pairs() == []
        sqlite_db.add_reimburser_pair("canada life", "humanity wellness")
        sqlite_db.add_reimburser_pair("employer", "gym membership")
        pairs = sqlite_db.get_reimburser_pairs()
        assert len(pairs) == 2
        assert pairs[0]["reimburser_pattern"] == "canada life"
        assert pairs[0]["expense_pattern"] == "humanity wellness"
        assert sqlite_db.remove_reimburser_pair("canada life", "humanity wellness")
        assert not sqlite_db.remove_reimburser_pair("nonexist", "nope")
        assert len(sqlite_db.get_reimburser_pairs()) == 1

    def test_pending_with_suggested_expenses(self, sqlite_db):
        """When pairs are configured, pending shows suggested expense matches."""
        sqlite_db.add_reimburser("canada life", "Canada Life", "substring")
        sqlite_db.add_reimburser_pair("canada life", "wellness")
        # Add income from reimburser
        income = Transaction(
            date=date(2026, 5, 10),
            amount=80.0,
            store_raw="canada life ins",
            txn_type=TxnType.INCOME,
            source_file="test.csv",
            store_normalized="canada life ins",
        )
        # Add expense that matches the pair
        expense = Transaction(
            date=date(2026, 5, 5),
            amount=80.0,
            store_raw="humanity wellness center",
            txn_type=TxnType.EXPENSE,
            source_file="test.csv",
            store_normalized="humanity wellness center",
        )
        sqlite_db.insert_transaction(income)
        sqlite_db.insert_transaction(expense)
        pending = sqlite_db.get_pending_reimbursements()
        match = [p for p in pending if p["uuid"] == income.uuid]
        assert len(match) == 1
        assert "suggested_expenses" in match[0]
        suggestions = match[0]["suggested_expenses"]
        assert len(suggestions) >= 1
        assert suggestions[0]["uuid"] == expense.uuid

    def test_discover_reimburser_pairs(self, sqlite_db):
        """Discover pairs from historical links."""
        expense = Transaction(
            date=date(2026, 3, 1),
            amount=100.0,
            store_raw="wellness spa",
            txn_type=TxnType.EXPENSE,
            source_file="test.csv",
            store_normalized="wellness spa",
        )
        income = Transaction(
            date=date(2026, 3, 5),
            amount=100.0,
            store_raw="insurance co",
            txn_type=TxnType.INCOME,
            source_file="test.csv",
            store_normalized="insurance co",
        )
        sqlite_db.insert_transaction(expense)
        sqlite_db.insert_transaction(income)
        sqlite_db.link_transactions(expense.uuid, income.uuid)
        discovered = sqlite_db.discover_reimburser_pairs()
        assert len(discovered) >= 1
        assert discovered[0]["reimburser_pattern"] == "insurance co"
        assert discovered[0]["expense_pattern"] == "wellness spa"
        assert discovered[0]["link_count"] == 1

    def test_get_distinct_stores(self, sqlite_db):
        txn1 = Transaction(
            date=date(2026, 1, 1),
            amount=50.0,
            store_raw="walmart #123",
            store_normalized="walmart",
            txn_type=TxnType.EXPENSE,
            source_file="test.csv",
        )
        txn2 = Transaction(
            date=date(2026, 1, 2),
            amount=5000.0,
            store_raw="employer inc",
            store_normalized="employer inc",
            txn_type=TxnType.INCOME,
            source_file="test.csv",
        )
        sqlite_db.insert_transaction(txn1)
        sqlite_db.insert_transaction(txn2)
        stores = sqlite_db.get_distinct_stores()
        assert len(stores["expenses"]) >= 1
        assert len(stores["income"]) >= 1
        assert any(s["raw"] == "walmart #123" for s in stores["expenses"])

    def test_discover_store_pairs(self, sqlite_db):
        # Insert transactions with similar store names
        for i in range(3):
            sqlite_db.insert_transaction(
                Transaction(
                    date=date(2026, 1, i + 1),
                    amount=10.0 + i,
                    store_raw=f"walmart #{i}",
                    store_normalized=f"walmart #{i}",
                    txn_type=TxnType.EXPENSE,
                    source_file=f"f{i}.csv",
                )
            )
        suggestions = sqlite_db.discover_store_pairs()
        assert len(suggestions) >= 1
        assert all("raw" in s for s in suggestions)
        assert all("suggested_normalized" in s for s in suggestions)

    def test_detect_duplicates(self, sqlite_db):
        # Similar normalized store names that should be merged
        sqlite_db.insert_transaction(
            Transaction(
                date=date(2026, 3, 1),
                amount=20.0,
                store_raw="dominos pizza",
                store_normalized="dominos pizza",
                txn_type=TxnType.EXPENSE,
                source_file="a.csv",
            )
        )
        sqlite_db.insert_transaction(
            Transaction(
                date=date(2026, 3, 5),
                amount=25.0,
                store_raw="dominos",
                store_normalized="dominos",
                txn_type=TxnType.EXPENSE,
                source_file="b.csv",
            )
        )
        dupes = sqlite_db.detect_duplicates()
        assert len(dupes) == 1
        assert len(dupes[0]["variants"]) == 2
        assert dupes[0]["total_txns"] == 2
        names = [v["name"] for v in dupes[0]["variants"]]
        assert "dominos pizza" in names
        assert "dominos" in names


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
        txn = Transaction(date=date(2026, 1, 1), amount=25, store_raw="uber eats")
        result = categorizer.categorize(txn, db)
        assert result.category == "Dining"
        assert result.confidence == "exact"

    def test_case_insensitive(self):
        db = CategoryDB(store_to_category={"uber eats": ["Dining"]})
        txn = Transaction(date=date(2026, 1, 1), amount=25, store_raw="UBER EATS")
        result = categorizer.categorize(txn, db)
        assert result.category == "Dining"


class TestCategorizerSubstring:
    def test_substring_in_long_bank_string(self):
        db = CategoryDB(store_to_category={"shoppers drug mart": ["Health"]})
        txn = Transaction(
            date=date(2026, 1, 1),
            amount=13,
            store_raw="shoppers drug mart #22       vancouver    bc  (apple pay)",
        )
        result = categorizer.categorize(txn, db)
        assert result.category == "Health"
        assert result.confidence == "substring"

    def test_short_key_not_matched(self):
        db = CategoryDB(store_to_category={"ab": ["Dining"]})
        txn = Transaction(
            date=date(2026, 1, 1), amount=10, store_raw="abstract art gallery"
        )
        result = categorizer.categorize(txn, db)
        assert result.category is None


class TestCategorizerGenericDescription:
    def test_pos_purchase_uses_sub_description(self):
        db = CategoryDB(
            store_to_category={
                "eurest": ["Dining"],
                "pos purchase": ["Shopping"],
            }
        )
        txn = Transaction(
            date=date(2026, 1, 1),
            amount=13.65,
            store_raw="pos purchase",
            sub_description="Apos Eurest-Amazon-6     Vanco",
        )
        result = categorizer.categorize(txn, db)
        assert result.category == "Dining"

    def test_pos_purchase_not_matched_as_shopping(self):
        db = CategoryDB(
            store_to_category={
                "pos purchase": ["Shopping"],
            }
        )
        txn = Transaction(
            date=date(2026, 1, 1),
            amount=13.65,
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
        txn = Transaction(date=date(2026, 1, 1), amount=65, store_raw="petro-canada 68")
        result = categorizer.categorize(txn, db)
        assert result.category == "Transportation"


class TestCategorizerBatch:
    def test_splits_classified_and_unclassified(self):
        db = CategoryDB(
            store_to_category={
                "uber eats": ["Dining"],
                "walmart": ["Groceries"],
            }
        )
        txns = [
            Transaction(date=date(2026, 1, 1), amount=25, store_raw="uber eats"),
            Transaction(
                date=date(2026, 1, 1), amount=50, store_raw="totally unknown xyz"
            ),
            Transaction(date=date(2026, 1, 1), amount=10, store_raw="walmart"),
        ]
        classified, unclassified = categorizer.categorize_batch(txns, db)
        assert len(classified) == 2
        assert len(unclassified) == 1
        assert classified[0].category == "Dining"
        assert classified[1].category == "Groceries"
        assert unclassified[0].category == ""

    def test_mutates_transactions(self):
        db = CategoryDB(store_to_category={"test": ["Dining"]})
        txn = Transaction(date=date(2026, 1, 1), amount=10, store_raw="test")
        categorizer.categorize_batch([txn], db)
        assert txn.category == "Dining"
        assert txn.store_normalized != ""


class TestCleanStoreName:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            (
                "SHOPPERS DRUG MART #22       VANCOUVER    BC  (Apple Pay) ",
                "shoppers drug mart #22",
            ),
            ("UBER EATS                    TORONTO      ON  (Apple Pay) ", "uber eats"),
            ("PETRO-CANADA 68              NORTH VANCOUVBC  ", "petro-canada 68"),
            ("APPLE.COM/BILL           866-712-7753 ON  ", "apple.com/bill"),
            ("simple store", "simple store"),
            ("APOS Eurest-Amazon-6     Vanco", "eurest-amazon-6"),
        ],
    )
    def test_cleaning(self, raw, expected):
        result = categorizer.clean_store_name(raw)
        assert result == expected


# --- Reports tests ---


class TestMonthlySummary:
    def test_basic_summary(self, sample_txns):
        db = CategoryDB(
            store_to_category={
                "uber eats": ["Dining"],
                "walmart": ["Groceries"],
                "petro canada": ["Transportation"],
            }
        )
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
        db = CategoryDB(
            store_to_category={
                "uber eats": ["Dining"],
                "walmart": ["Groceries"],
                "petro canada": ["Transportation"],
            }
        )
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
            Transaction(
                date=date(2026, 1, 1),
                amount=100,
                store_raw="a",
                category="Dining",
                txn_type=TxnType.EXPENSE,
            ),
            Transaction(
                date=date(2026, 2, 1),
                amount=200,
                store_raw="b",
                category="Dining",
                txn_type=TxnType.EXPENSE,
            ),
            Transaction(
                date=date(2026, 1, 1),
                amount=50,
                store_raw="c",
                category="Groceries",
                txn_type=TxnType.EXPENSE,
            ),
        ]
        summary = reports.monthly_summary(txns)
        avgs = reports.category_averages(summary)
        assert avgs["Dining"] == 150.0
        assert avgs["Groceries"] == 25.0


class TestToCsv:
    def test_writes_csv(self, tmp_path):
        txns = [
            Transaction(
                date=date(2026, 1, 1),
                amount=25.50,
                store_raw="test",
                store_normalized="test",
                category="Dining",
                txn_type=TxnType.EXPENSE,
                source_file="test.csv",
            ),
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
            expenses, sample_category_db
        )
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
        (json_dir / "storesWithExpenses.json").write_text('{"uber eats":["Dining"]}')
        (json_dir / "storePairs.json").write_text("{}")

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "smtm.cli",
                "--csv-dir",
                str(FIXTURES),
                "--db-path",
                str(db_path),
                "--json-dir",
                str(json_dir),
                "import",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "Importing" in result.stdout

    def test_cli_profile(self, tmp_path):
        db_path = tmp_path / "test.db"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "smtm.cli",
                "--csv-dir",
                str(FIXTURES),
                "--db-path",
                str(db_path),
                "profile",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "Date range" in result.stdout
        assert "Classification rate" in result.stdout

    def test_cli_reimburse(self, tmp_path):
        db_path = tmp_path / "test.db"
        base = [
            sys.executable,
            "-m",
            "smtm.cli",
            "--db-path",
            str(db_path),
            "--csv-dir",
            str(FIXTURES),
        ]
        # Import first so there's income data
        subprocess.run(base + ["import"], capture_output=True, text=True)
        # Add reimburser
        result = subprocess.run(
            base + ["reimburse", "add", "payroll", "--match-type", "substring"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "Added reimburser" in result.stdout
        # List
        result = subprocess.run(
            base + ["reimburse", "list"], capture_output=True, text=True
        )
        assert result.returncode == 0
        assert "payroll" in result.stdout
        # Pending
        result = subprocess.run(
            base + ["reimburse", "pending"], capture_output=True, text=True
        )
        assert result.returncode == 0
        # Remove
        result = subprocess.run(
            base + ["reimburse", "remove", "payroll"], capture_output=True, text=True
        )
        assert result.returncode == 0
        assert "Removed" in result.stdout
        # Pairs
        result = subprocess.run(
            base + ["reimburse", "add-pair", "canada life", "wellness"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "Pair added" in result.stdout
        result = subprocess.run(
            base + ["reimburse", "pairs"], capture_output=True, text=True
        )
        assert result.returncode == 0
        assert "canada life" in result.stdout
        assert "wellness" in result.stdout
        # Discover
        result = subprocess.run(
            base + ["reimburse", "discover"], capture_output=True, text=True
        )
        assert result.returncode == 0
        # Remove pair
        result = subprocess.run(
            base + ["reimburse", "remove-pair", "canada life", "wellness"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "Pair removed" in result.stdout

    def test_cli_stores(self, tmp_path):
        db_path = tmp_path / "test.db"
        base = [
            sys.executable,
            "-m",
            "smtm.cli",
            "--db-path",
            str(db_path),
            "--csv-dir",
            str(FIXTURES),
        ]
        subprocess.run(base + ["import"], capture_output=True, text=True)
        # List
        result = subprocess.run(
            base + ["stores", "list"], capture_output=True, text=True
        )
        assert result.returncode == 0
        assert "expense stores" in result.stdout
        # Discover
        result = subprocess.run(
            base + ["stores", "discover"], capture_output=True, text=True
        )
        assert result.returncode == 0

    def test_cli_recategorize(self, tmp_path):
        db_path = tmp_path / "test.db"
        base = [
            sys.executable,
            "-m",
            "smtm.cli",
            "--db-path",
            str(db_path),
            "--csv-dir",
            str(FIXTURES),
        ]
        subprocess.run(base + ["import"], capture_output=True, text=True)
        result = subprocess.run(base + ["recategorize"], capture_output=True, text=True)
        assert result.returncode == 0
        assert "Re-categorized" in result.stdout

    def test_cli_delete_restore(self, tmp_path):
        db_path = tmp_path / "test.db"
        base = [
            sys.executable,
            "-m",
            "smtm.cli",
            "--db-path",
            str(db_path),
            "--csv-dir",
            str(FIXTURES),
        ]
        subprocess.run(base + ["import"], capture_output=True, text=True)
        db = Database(str(db_path))
        db.initialize()
        txns = db.get_all_transactions()
        uuid = txns[0].uuid
        db.close()
        # Delete
        result = subprocess.run(base + ["delete", uuid], capture_output=True, text=True)
        assert "Deleted" in result.stdout
        # Restore
        result = subprocess.run(
            base + ["delete", "--restore", uuid], capture_output=True, text=True
        )
        assert "Restored" in result.stdout

    def test_cli_reimburse_unlink(self, tmp_path):
        db_path = tmp_path / "test.db"
        base = [
            sys.executable,
            "-m",
            "smtm.cli",
            "--db-path",
            str(db_path),
            "--csv-dir",
            str(FIXTURES),
        ]
        subprocess.run(base + ["import"], capture_output=True, text=True)
        # Unlink a non-linked txn should fail gracefully
        db = Database(str(db_path))
        db.initialize()
        txns = db.get_expenses()
        uuid = txns[0].uuid
        db.close()
        result = subprocess.run(
            base + ["reimburse", "unlink", uuid], capture_output=True, text=True
        )
        assert result.returncode == 0
        assert "Not found or not linked" in result.stdout

    def test_import_dedup(self, tmp_path):
        """Importing the same files twice should produce 0 new on second run."""
        db_path = tmp_path / "test.db"
        cmd = [
            sys.executable,
            "-m",
            "smtm.cli",
            "--csv-dir",
            str(FIXTURES),
            "--db-path",
            str(db_path),
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
            expenses, sample_category_db
        )
        total_output = sum(t.amount for t in classified) + sum(
            t.amount for t in unclassified
        )

        assert abs(total_input - total_output) < 0.01

    def test_no_duplicate_categorization(self, sample_category_db):
        txns = parse_directory(FIXTURES)
        expenses = [t for t in txns if t.txn_type == TxnType.EXPENSE]

        classified, unclassified = categorizer.categorize_batch(
            expenses, sample_category_db
        )

        classified_ids = set(id(t) for t in classified)
        unclassified_ids = set(id(t) for t in unclassified)
        assert classified_ids.isdisjoint(unclassified_ids)
        assert len(classified) + len(unclassified) == len(expenses)


# --- Server API tests ---


import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from smtm.server import Handler, compute_anomalies, compute_uncategorized


@pytest.fixture(scope="class")
def server_fixture(tmp_path_factory):
    """Start a test server with sample data."""
    tmp = tmp_path_factory.mktemp("server")
    db = Database(tmp / "test.db")
    db.initialize()

    # Seed transactions
    txns = [
        Transaction(
            date=date(2026, 1, 5),
            amount=25.50,
            store_raw="uber eats",
            category="Dining",
            confidence="exact",
            txn_type=TxnType.EXPENSE,
            source_file="test.csv",
            store_normalized="uber eats",
        ),
        Transaction(
            date=date(2026, 1, 10),
            amount=50.00,
            store_raw="walmart",
            category="Groceries",
            confidence="exact",
            txn_type=TxnType.EXPENSE,
            source_file="test.csv",
            store_normalized="walmart",
        ),
        Transaction(
            date=date(2026, 1, 15),
            amount=5000.00,
            store_raw="payroll",
            txn_type=TxnType.INCOME,
            source_file="test.csv",
            store_normalized="payroll",
        ),
        Transaction(
            date=date(2026, 1, 20),
            amount=15.00,
            store_raw="unknown shop",
            txn_type=TxnType.EXPENSE,
            source_file="test.csv",
            store_normalized="unknown shop",
        ),
        Transaction(
            date=date(2026, 1, 22),
            amount=12.00,
            store_raw="uber eats",
            category="Dining",
            confidence="exact",
            txn_type=TxnType.EXPENSE,
            source_file="test.csv",
            store_normalized="uber eats",
        ),
        Transaction(
            date=date(2026, 1, 25),
            amount=300.00,
            store_raw="uber eats",
            category="Dining",
            confidence="exact",
            txn_type=TxnType.EXPENSE,
            source_file="test.csv",
            store_normalized="uber eats",
        ),
    ]
    for t in txns:
        db.insert_transaction(t)

    db.add_category_rule("uber eats", "Dining", "exact")
    db.add_category_rule("walmart", "Groceries", "exact")
    db.set_budget("2026-01", "Dining", 200.0)
    db.set_budget("2026-01", "Groceries", 300.0)

    Handler.db = db
    Handler.csv_dir = tmp / "uploads"
    Handler.csv_dir.mkdir()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    yield {
        "base_url": f"http://127.0.0.1:{port}",
        "db": db,
        "tmp": tmp,
        "txns": txns,
    }

    server.shutdown()
    db.close()


def _get(base_url, path):
    req = urllib.request.Request(f"{base_url}{path}")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def _post(base_url, path, data=None):
    body = json.dumps(data).encode() if data else b"{}"
    req = urllib.request.Request(
        f"{base_url}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def _patch(base_url, path, data):
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        f"{base_url}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="PATCH",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def _delete(base_url, path):
    req = urllib.request.Request(f"{base_url}{path}", method="DELETE")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def _upload(base_url, path, filename, content):
    boundary = "----TestBoundary123"
    body = (
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: text/csv\r\n\r\n"
        ).encode()
        + content
        + f"\r\n--{boundary}--\r\n".encode()
    )
    req = urllib.request.Request(
        f"{base_url}{path}",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


class TestServer:
    def test_dashboard_html(self, server_fixture):
        url = server_fixture["base_url"]
        req = urllib.request.Request(f"{url}/")
        with urllib.request.urlopen(req) as resp:
            html = resp.read().decode()
            assert resp.status == 200
            assert "show-me-the-money" in html
            assert "Chart.js" in html or "chart.js" in html

    def test_api_transactions(self, server_fixture):
        url = server_fixture["base_url"]
        data = _get(url, "/api/transactions")
        assert len(data["transactions"]) == 6
        assert data["total"] == 6
        assert all("uuid" in t for t in data["transactions"])

    def test_api_transactions_paginated(self, server_fixture):
        url = server_fixture["base_url"]
        data = _get(url, "/api/transactions?offset=0&limit=2")
        assert len(data["transactions"]) == 2
        assert data["total"] == 6
        data2 = _get(url, "/api/transactions?offset=2&limit=2")
        assert len(data2["transactions"]) == 2
        assert data["transactions"][0]["uuid"] != data2["transactions"][0]["uuid"]

    def test_api_overview(self, server_fixture):
        url = server_fixture["base_url"]
        data = _get(url, "/api/overview")
        assert "summary" in data
        assert "anomalies" in data
        assert "analytics" in data
        assert data["summary"]["total_expenses"] > 0
        assert "2026-01" in data["summary"]["months"]

    def test_api_stats(self, server_fixture):
        url = server_fixture["base_url"]
        data = _get(url, "/api/stats")
        assert data["stats"]["total"] == 6
        assert data["stats"]["expenses"] == 5
        assert data["stats"]["income"] == 1

    def test_api_summary(self, server_fixture):
        url = server_fixture["base_url"]
        data = _get(url, "/api/summary")
        s = data["summary"]
        assert s["total_expenses"] > 0
        assert "2026-01" in s["months"]
        assert "Dining" in s["categories"]

    def test_api_budgets_crud(self, server_fixture):
        url = server_fixture["base_url"]
        data = _get(url, "/api/budgets")
        assert len(data["budgets"]) >= 2

        _post(
            url,
            "/api/budgets",
            {"month": "2026-02", "category": "Dining", "amount": 500},
        )
        data = _get(url, "/api/budgets?month=2026-02")
        assert any(
            b["category"] == "Dining" and b["amount"] == 500 for b in data["budgets"]
        )

        result = _post(
            url, "/api/budgets/copy", {"from_month": "2026-02", "to_month": "2026-03"}
        )
        assert result["count"] >= 1

    def test_api_delete_restore(self, server_fixture):
        url = server_fixture["base_url"]
        txns = _get(url, "/api/transactions")["transactions"]
        uuid = txns[0]["uuid"]

        _delete(url, f"/api/transactions/{uuid}")
        txns_after = _get(url, "/api/transactions")["transactions"]
        assert len(txns_after) == len(txns) - 1

        deleted = _get(url, "/api/transactions/deleted")["transactions"]
        assert any(t["uuid"] == uuid for t in deleted)

        _post(url, f"/api/transactions/{uuid}/restore")
        txns_restored = _get(url, "/api/transactions")["transactions"]
        assert len(txns_restored) == len(txns)

    def test_api_rules_crud(self, server_fixture):
        url = server_fixture["base_url"]
        rules = _get(url, "/api/rules")["rules"]
        initial_count = len(rules)

        _post(
            url,
            "/api/rules",
            {"pattern": "starbucks", "category": "Dining", "match_type": "exact"},
        )
        rules_after = _get(url, "/api/rules")["rules"]
        assert len(rules_after) == initial_count + 1
        assert any(r["pattern"] == "starbucks" for r in rules_after)

    def test_api_recategorize(self, server_fixture):
        url = server_fixture["base_url"]
        result = _post(url, "/api/recategorize")
        assert "updated" in result

    def test_api_anomalies(self, server_fixture):
        url = server_fixture["base_url"]
        data = _get(url, "/api/anomalies")
        # $300 uber eats should be anomaly (avg of 25.5, 12, 300 = ~112, 300 > 2*112)
        assert len(data["anomalies"]) >= 1
        assert any(a["amount"] == 300.0 for a in data["anomalies"])

    def test_api_uncategorized(self, server_fixture):
        url = server_fixture["base_url"]
        data = _get(url, "/api/uncategorized")
        assert len(data["merchants"]) >= 1
        assert any(m["store"] == "unknown shop" for m in data["merchants"])

    def test_api_suggest(self, server_fixture):
        url = server_fixture["base_url"]
        data = _get(url, "/api/suggest")
        # "unknown shop" contains "shop" -> Shopping
        assert any(s["category"] == "Shopping" for s in data["suggestions"])

    def test_api_store_pairs(self, server_fixture):
        url = server_fixture["base_url"]
        _post(
            url,
            "/api/store-pairs",
            {"raw_name": "petro-68", "normalized_name": "petro canada"},
        )
        data = _get(url, "/api/store-pairs")
        assert data["store_pairs"]["petro-68"] == "petro canada"

    def test_api_store_pairs_discover(self, server_fixture):
        url = server_fixture["base_url"]
        data = _get(url, "/api/store-pairs/discover")
        assert "suggestions" in data
        assert isinstance(data["suggestions"], list)

    def test_api_stores(self, server_fixture):
        url = server_fixture["base_url"]
        data = _get(url, "/api/stores")
        assert "expenses" in data
        assert "income" in data
        assert len(data["expenses"]) > 0
        assert len(data["income"]) > 0
        assert "raw" in data["expenses"][0]
        assert "normalized" in data["expenses"][0]
        assert "count" in data["expenses"][0]

    def test_api_history(self, server_fixture):
        url = server_fixture["base_url"]
        data = _get(url, "/api/history")
        assert isinstance(data["history"], list)

    def test_api_update_category(self, server_fixture):
        url = server_fixture["base_url"]
        txns = _get(url, "/api/transactions")["transactions"]
        uncat = [
            t for t in txns if t["category"] == "Uncategorized" or not t["category"]
        ]
        if uncat:
            uuid = uncat[0]["uuid"]
            _patch(url, f"/api/transactions/{uuid}/category", {"category": "Misc"})
            txns_after = _get(url, "/api/transactions")["transactions"]
            updated = [t for t in txns_after if t["uuid"] == uuid][0]
            assert updated["category"] == "Misc"

    def test_api_import_preview(self, server_fixture):
        url = server_fixture["base_url"]
        csv_content = (FIXTURES / "new_credit.csv").read_bytes()
        data = _upload(url, "/api/import/preview", "new_credit.csv", csv_content)
        assert data["preview"]["parsed"] > 0
        assert data["preview"]["expenses"] > 0
        # Preview shouldn't change DB
        txns = _get(url, "/api/transactions")["transactions"]
        assert len(txns) >= 6

    def test_api_import(self, server_fixture):
        url = server_fixture["base_url"]
        txns_before = _get(url, "/api/transactions")["transactions"]
        csv_content = (FIXTURES / "new_credit.csv").read_bytes()
        data = _upload(url, "/api/import", "new_credit.csv", csv_content)
        assert data["result"]["status"] == "imported"
        assert data["result"]["inserted"] > 0
        txns_after = _get(url, "/api/transactions")["transactions"]
        assert len(txns_after) > len(txns_before)

    def test_api_analytics(self, server_fixture):
        url = server_fixture["base_url"]
        data = _get(url, "/api/analytics")
        a = data["analytics"]
        assert "mom_deltas" in a
        assert "savings_rate" in a
        assert "velocity" in a
        assert "recurring" in a
        assert "day_of_week" in a
        assert "top_merchants" in a
        assert "concentration" in a
        assert "zscore_outliers" in a
        assert len(a["day_of_week"]) == 7
        assert a["velocity"]["days_in_month"] > 0
        assert isinstance(a["concentration"]["top3_pct"], float)

    def test_api_pdf_report(self, server_fixture):
        url = server_fixture["base_url"]
        resp = urllib.request.urlopen(f"{url}/api/report/pdf")
        assert resp.status == 200
        assert resp.headers["Content-Type"] == "application/pdf"
        body = resp.read()
        assert body[:4] == b"%PDF"
        assert len(body) > 1000

    def test_api_link_unlink(self, server_fixture):
        url = server_fixture["base_url"]
        db = server_fixture["db"]
        # Get an expense and income uuid
        expenses = db.get_expenses()
        income = db.get_income()
        exp_uuid = expenses[0].uuid
        inc_uuid = income[0].uuid
        inc_amount = income[0].amount
        # Link
        data = json.dumps({"expense_uuid": exp_uuid, "income_uuid": inc_uuid}).encode()
        req = urllib.request.Request(
            f"{url}/api/link",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(req)
        result = json.loads(resp.read())
        assert result["ok"]
        # Verify expense has adjustment
        txn = [t for t in db.get_all_transactions() if t.uuid == exp_uuid][0]
        assert txn.adjustment == inc_amount
        assert txn.linked_to == inc_uuid
        # Verify income is soft-deleted
        inc_txn = [
            t
            for t in db.get_all_transactions(include_deleted=True)
            if t.uuid == inc_uuid
        ][0]
        assert inc_txn.is_deleted
        # Unlink
        data = json.dumps({"expense_uuid": exp_uuid}).encode()
        req = urllib.request.Request(
            f"{url}/api/unlink",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(req)
        result = json.loads(resp.read())
        assert result["ok"]
        # Verify restored
        txn = [t for t in db.get_all_transactions() if t.uuid == exp_uuid][0]
        assert txn.adjustment == 0
        assert txn.linked_to == ""

    def test_api_reimbursers_crud(self, server_fixture):
        url = server_fixture["base_url"]
        # Initially empty
        data = _get(url, "/api/reimbursers")
        initial_count = len(data["reimbursers"])
        # Add
        result = _post(
            url,
            "/api/reimbursers",
            {"pattern": "friend", "label": "Friend", "match_type": "substring"},
        )
        assert result["ok"]
        data = _get(url, "/api/reimbursers")
        assert len(data["reimbursers"]) == initial_count + 1
        assert any(r["pattern"] == "friend" for r in data["reimbursers"])
        # Delete
        result = _delete(url, "/api/reimbursers/friend")
        assert result["ok"]
        data = _get(url, "/api/reimbursers")
        assert len(data["reimbursers"]) == initial_count

    def test_api_pending_reimbursements(self, server_fixture):
        url = server_fixture["base_url"]
        db = server_fixture["db"]
        # Add reimburser that matches "payroll"
        db.add_reimburser("payroll", "Employer", "substring")
        data = _get(url, "/api/reimbursements/pending")
        # payroll income exists in fixture, should show as pending
        assert len(data["pending"]) >= 1
        assert any(p["store"] == "payroll" for p in data["pending"])
        # Cleanup
        db.remove_reimburser("payroll")

    def test_api_reimburser_pairs(self, server_fixture):
        url = server_fixture["base_url"]
        # Initially empty
        data = _get(url, "/api/reimburser-pairs")
        initial = len(data["pairs"])
        # Add pair
        result = _post(
            url,
            "/api/reimburser-pairs",
            {"reimburser_pattern": "canada life", "expense_pattern": "wellness"},
        )
        assert result["ok"]
        data = _get(url, "/api/reimburser-pairs")
        assert len(data["pairs"]) == initial + 1
        # Delete pair
        result = _post(
            url,
            "/api/reimburser-pairs/delete",
            {"reimburser_pattern": "canada life", "expense_pattern": "wellness"},
        )
        assert result["ok"]
        data = _get(url, "/api/reimburser-pairs")
        assert len(data["pairs"]) == initial

    def test_api_reimburser_pairs_discover(self, server_fixture):
        url = server_fixture["base_url"]
        data = _get(url, "/api/reimburser-pairs/discover")
        assert "discovered" in data

    def test_api_reimburser_pairs_accept(self, server_fixture):
        url = server_fixture["base_url"]
        pairs = [{"reimburser_pattern": "ins co", "expense_pattern": "gym"}]
        result = _post(url, "/api/reimburser-pairs/accept", {"pairs": pairs})
        assert result["ok"]
        assert result["added"] == 1
        # Verify
        data = _get(url, "/api/reimburser-pairs")
        assert any(p["expense_pattern"] == "gym" for p in data["pairs"])
        # Cleanup
        _post(
            url,
            "/api/reimburser-pairs/delete",
            {"reimburser_pattern": "ins co", "expense_pattern": "gym"},
        )

    def test_api_pending_with_suggestions(self, server_fixture):
        """Pending reimbursements include suggested expenses when pairs configured."""
        url = server_fixture["base_url"]
        db = server_fixture["db"]
        db.add_reimburser("payroll", "Employer", "substring")
        db.add_reimburser_pair("payroll", "uber eats")
        data = _get(url, "/api/reimbursements/pending")
        pending = [p for p in data["pending"] if p["store"] == "payroll"]
        assert len(pending) >= 1
        # Should have suggested_expenses with uber eats matches
        assert "suggested_expenses" in pending[0]
        assert len(pending[0]["suggested_expenses"]) > 0
        # Cleanup
        db.remove_reimburser("payroll")
        db.remove_reimburser_pair("payroll", "uber eats")

    def test_404(self, server_fixture):
        url = server_fixture["base_url"]
        try:
            urllib.request.urlopen(f"{url}/api/nonexistent")
            assert False, "Should have raised"
        except urllib.error.HTTPError as e:
            assert e.code == 404


# --- PDF Report unit tests ---


class TestPdfReport:
    @pytest.fixture
    def rich_db(self, tmp_path):
        """DB with enough data to exercise all PDF sections."""
        db = Database(tmp_path / "pdf_test.db")
        db.initialize()
        txns = []
        stores = [
            ("starbucks", "Dining", 7.50),
            ("safeway", "Groceries", 85.00),
            ("netflix", "Subscriptions", 15.99),
            ("shell", "Transportation", 60.00),
            ("amazon", "Shopping", 45.00),
        ]
        for month in range(1, 5):
            for store, cat, amt in stores:
                txns.append(
                    Transaction(
                        date=date(2026, month, 10),
                        amount=amt,
                        store_raw=store,
                        store_normalized=store,
                        category=cat,
                        txn_type=TxnType.EXPENSE,
                        source_file="test.csv",
                    )
                )
            txns.append(
                Transaction(
                    date=date(2026, month, 1),
                    amount=5000.0,
                    store_raw="employer",
                    store_normalized="employer",
                    category="",
                    txn_type=TxnType.INCOME,
                    source_file="test.csv",
                )
            )
        db.insert_transactions(txns)
        db.set_budget("2026-01", "Dining", 100.0)
        db.set_budget("2026-01", "Groceries", 200.0)
        db.set_budget("2026-02", "Dining", 100.0)
        return db

    def test_generates_multi_page_pdf(self, rich_db):
        import tempfile

        from smtm.pdf_report import generate_pdf
        from smtm.server import compute_analytics

        txns = rich_db.get_all_transactions()
        stats = rich_db.get_stats()
        budgets = rich_db.get_budgets()
        analytics = compute_analytics(txns, budgets)

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            out = Path(f.name)

        try:
            generate_pdf(txns, stats, budgets, analytics, out)
            content = out.read_bytes()
            assert content[:4] == b"%PDF"
            # Multi-page: should be substantial
            assert len(content) > 3000
        finally:
            out.unlink()

    def test_pdf_has_expected_pages(self, rich_db):
        import tempfile

        from smtm.pdf_report import generate_pdf
        from smtm.server import compute_analytics

        txns = rich_db.get_all_transactions()
        stats = rich_db.get_stats()
        budgets = rich_db.get_budgets()
        analytics = compute_analytics(txns, budgets)

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            out = Path(f.name)

        try:
            generate_pdf(txns, stats, budgets, analytics, out)
            raw = out.read_bytes().decode("latin-1")
            # Count pages via /Type /Page entries
            page_count = raw.count("/Type /Page\n")
            assert page_count >= 5
        finally:
            out.unlink()

    def test_pdf_with_linked_offsets_is_larger(self, rich_db):
        import tempfile

        from smtm.pdf_report import generate_pdf
        from smtm.server import compute_analytics

        # Generate without offsets
        txns = rich_db.get_all_transactions()
        stats = rich_db.get_stats()
        budgets = rich_db.get_budgets()
        analytics = compute_analytics(txns, budgets)

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            out1 = Path(f.name)
        generate_pdf(txns, stats, budgets, analytics, out1)
        size_without = out1.stat().st_size

        # Link and regenerate
        expenses = rich_db.get_expenses()
        income = rich_db.get_income()
        rich_db.link_transactions(expenses[0].uuid, income[0].uuid)

        txns = rich_db.get_all_transactions()
        stats = rich_db.get_stats()
        analytics = compute_analytics(txns, budgets)

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            out2 = Path(f.name)
        generate_pdf(txns, stats, budgets, analytics, out2)
        size_with = out2.stat().st_size

        try:
            # With offsets section, PDF should be larger
            assert size_with > size_without
        finally:
            out1.unlink()
            out2.unlink()

    def test_handles_empty_data(self):
        import tempfile

        from smtm.pdf_report import generate_pdf

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            out = Path(f.name)

        try:
            generate_pdf(
                [],
                {
                    "total": 0,
                    "categorized": 0,
                    "expenses": 0,
                    "income": 0,
                    "classification_rate": 0,
                    "date_min": None,
                    "date_max": None,
                },
                [],
                {},
                out,
            )
            assert out.exists()
            content = out.read_bytes()
            assert content[:4] == b"%PDF"
        finally:
            out.unlink()


# --- Analytics unit tests ---


class TestAnalytics:
    def test_recurring_detection(self):
        from smtm.server import compute_analytics

        txns = [
            Transaction(
                date=date(2026, 1, 15),
                amount=12.99,
                store_raw="netflix",
                store_normalized="netflix",
                category="Subscriptions",
                txn_type=TxnType.EXPENSE,
                source_file="a.csv",
            ),
            Transaction(
                date=date(2026, 2, 15),
                amount=12.99,
                store_raw="netflix",
                store_normalized="netflix",
                category="Subscriptions",
                txn_type=TxnType.EXPENSE,
                source_file="a.csv",
            ),
            Transaction(
                date=date(2026, 3, 15),
                amount=12.99,
                store_raw="netflix",
                store_normalized="netflix",
                category="Subscriptions",
                txn_type=TxnType.EXPENSE,
                source_file="a.csv",
            ),
        ]
        result = compute_analytics(txns, [])
        recurring = result["recurring"]
        assert len(recurring) >= 1
        netflix = next((r for r in recurring if r["store"] == "netflix"), None)
        assert netflix is not None
        assert netflix["avg_amount"] == 12.99
        assert netflix["annual_cost"] == 155.88

    def test_mom_deltas(self):
        from smtm.server import compute_analytics

        txns = [
            Transaction(
                date=date(2026, 1, 5),
                amount=100.0,
                store_raw="store",
                category="Dining",
                txn_type=TxnType.EXPENSE,
                source_file="a.csv",
            ),
            Transaction(
                date=date(2026, 2, 5),
                amount=150.0,
                store_raw="store",
                category="Dining",
                txn_type=TxnType.EXPENSE,
                source_file="a.csv",
            ),
        ]
        result = compute_analytics(txns, [])
        mom = result["mom_deltas"]
        assert len(mom) == 1
        assert mom[0]["category"] == "Dining"
        assert mom[0]["change_pct"] == 50.0

    def test_zscore_outliers(self):
        from smtm.server import compute_analytics

        txns = [
            Transaction(
                date=date(2026, 1, i),
                amount=20.0,
                store_raw="cafe",
                category="Dining",
                txn_type=TxnType.EXPENSE,
                source_file="a.csv",
            )
            for i in range(1, 12)
        ] + [
            Transaction(
                date=date(2026, 1, 15),
                amount=500.0,
                store_raw="fancy place",
                category="Dining",
                txn_type=TxnType.EXPENSE,
                source_file="a.csv",
            ),
        ]
        result = compute_analytics(txns, [])
        outliers = result["zscore_outliers"]
        assert len(outliers) >= 1
        assert outliers[0]["amount"] == 500.0
        assert outliers[0]["z_score"] > 2.0

    def test_savings_rate(self):
        from smtm.server import compute_analytics

        txns = [
            Transaction(
                date=date(2026, 1, 5),
                amount=1000.0,
                store_raw="store",
                category="Shopping",
                txn_type=TxnType.EXPENSE,
                source_file="a.csv",
            ),
            Transaction(
                date=date(2026, 1, 1),
                amount=5000.0,
                store_raw="employer",
                txn_type=TxnType.INCOME,
                source_file="a.csv",
            ),
        ]
        result = compute_analytics(txns, [])
        sr = result["savings_rate"]
        assert len(sr) == 1
        assert sr[0]["rate"] == 80.0

    def test_day_of_week(self):
        from smtm.server import compute_analytics

        txns = [
            Transaction(
                date=date(2026, 1, 5),
                amount=100.0,
                store_raw="store",
                category="Shopping",
                txn_type=TxnType.EXPENSE,
                source_file="a.csv",
            ),
        ]
        result = compute_analytics(txns, [])
        dow = result["day_of_week"]
        assert len(dow) == 7
        monday = dow[0]
        assert monday["day"] == "Mon"
        assert monday["total"] == 100.0


# --- Anomaly detection unit tests ---


class TestAnomalyDetection:
    def test_flags_outlier(self):
        txns = [
            Transaction(
                date=date(2026, 1, i),
                amount=20.0,
                store_raw="cafe",
                category="Dining",
                txn_type=TxnType.EXPENSE,
                source_file="a.csv",
            )
            for i in range(1, 6)
        ] + [
            Transaction(
                date=date(2026, 1, 10),
                amount=200.0,
                store_raw="fancy dinner",
                category="Dining",
                txn_type=TxnType.EXPENSE,
                source_file="a.csv",
            )
        ]
        anomalies = compute_anomalies(txns)
        assert len(anomalies) == 1
        assert anomalies[0]["amount"] == 200.0
        assert anomalies[0]["multiplier"] > 2.0

    def test_skips_small_categories(self):
        txns = [
            Transaction(
                date=date(2026, 1, 1),
                amount=10.0,
                store_raw="a",
                category="Rare",
                txn_type=TxnType.EXPENSE,
                source_file="a.csv",
            ),
            Transaction(
                date=date(2026, 1, 2),
                amount=1000.0,
                store_raw="b",
                category="Rare",
                txn_type=TxnType.EXPENSE,
                source_file="a.csv",
            ),
        ]
        anomalies = compute_anomalies(txns)
        assert len(anomalies) == 0

    def test_no_anomalies_when_uniform(self):
        txns = [
            Transaction(
                date=date(2026, 1, i),
                amount=50.0,
                store_raw="store",
                category="Shopping",
                txn_type=TxnType.EXPENSE,
                source_file="a.csv",
            )
            for i in range(1, 10)
        ]
        anomalies = compute_anomalies(txns)
        assert len(anomalies) == 0


class TestUncategorizedGrouping:
    def test_groups_by_store(self):
        db_obj = Database(":memory:")
        db_obj.initialize()
        db_obj.insert_transaction(
            Transaction(
                date=date(2026, 1, 1),
                amount=25.0,
                store_raw="mystery",
                store_normalized="mystery",
                txn_type=TxnType.EXPENSE,
                source_file="a.csv",
            )
        )
        db_obj.insert_transaction(
            Transaction(
                date=date(2026, 1, 2),
                amount=30.0,
                store_raw="mystery",
                store_normalized="mystery",
                txn_type=TxnType.EXPENSE,
                source_file="a.csv",
            )
        )
        db_obj.insert_transaction(
            Transaction(
                date=date(2026, 1, 3),
                amount=10.0,
                store_raw="other",
                store_normalized="other",
                txn_type=TxnType.EXPENSE,
                source_file="a.csv",
            )
        )
        result = compute_uncategorized(db_obj)
        assert len(result) == 2
        mystery = next(g for g in result if g["store"] == "mystery")
        assert mystery["count"] == 2
        assert mystery["total_spend"] == 55.0
