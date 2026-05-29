"""E2E test fixtures: fake dataset + live server."""

import socket
import threading
import time
import urllib.request
import uuid as uuid_mod
from datetime import date, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from smtm.db import Database
from smtm.models import Transaction, TxnType
from smtm.server import Handler


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _seed_fake_data(db: Database):
    """Seed a realistic fake dataset for e2e testing."""
    db.initialize()

    stores = [
        ("starbucks", "Dining", 6.50),
        ("uber eats", "Dining", 32.00),
        ("safeway", "Groceries", 85.00),
        ("costco wholesale", "Groceries", 150.00),
        ("netflix", "Subscriptions", 15.99),
        ("spotify premium", "Subscriptions", 11.99),
        ("shell gas station", "Transportation", 65.00),
        ("impark lot", "Transportation", 4.50),
        ("amazon marketplace", "Shopping", 45.00),
        ("winners outlet", "Shopping", 78.00),
        ("rexall pharmacy", "Health", 25.00),
        ("yoga studio west", "Health", 120.00),
        ("air canada flight", "Travel", 450.00),
        ("insurance co ltd", "Insurance", 135.00),
        ("hydro utility bill", "Utilities", 80.00),
        ("mystery store abc", "", 42.00),
        ("unknown shop xyz", "", 18.50),
        ("random place 123", "", 55.00),
    ]

    for store, cat, _ in stores:
        if cat:
            db.add_category_rule(store, cat, "exact")

    # Stores that should appear as "recurring" (same amount, monthly cadence)
    recurring_stores = {"netflix", "spotify premium", "insurance co ltd"}

    txns = []
    for month_offset in range(4):
        for i, (store, cat, base_amt) in enumerate(stores):
            if store in recurring_stores:
                # One charge per month on a fixed day for recurring detection
                txns.append(
                    Transaction(
                        date=date(2026, 1 + month_offset, 15),
                        amount=base_amt,
                        store_raw=store,
                        store_normalized=store,
                        category=cat,
                        confidence="exact" if cat else "",
                        txn_type=TxnType.EXPENSE,
                        source_file="fake_data.csv",
                    )
                )
            else:
                for j in range(2 + (i % 3)):
                    day = 1 + ((i * 3 + j * 7 + month_offset * 5) % 27)
                    amt = round(base_amt * (0.8 + (j * 0.2) + (month_offset * 0.05)), 2)
                    txns.append(
                        Transaction(
                            date=date(2026, 1 + month_offset, day),
                            amount=amt,
                            store_raw=store,
                            store_normalized=store,
                            category=cat,
                            confidence="exact" if cat else "",
                            txn_type=TxnType.EXPENSE,
                            source_file="fake_data.csv",
                        )
                    )

    for month_offset in range(4):
        txns.append(
            Transaction(
                date=date(2026, 1 + month_offset, 15),
                amount=5500.00,
                store_raw="employer inc",
                store_normalized="employer inc",
                category="",
                txn_type=TxnType.INCOME,
                source_file="fake_data.csv",
            )
        )

    # Outlier for anomaly detection
    txns.append(
        Transaction(
            date=date(2026, 3, 20),
            amount=2500.00,
            store_raw="big purchase store",
            store_normalized="big purchase store",
            category="Shopping",
            confidence="exact",
            txn_type=TxnType.EXPENSE,
            source_file="fake_data.csv",
        )
    )

    db.insert_transactions(txns)

    for month_offset in range(4):
        month = f"2026-{1 + month_offset:02d}"
        db.set_budget(month, "Dining", 300.0)
        db.set_budget(month, "Groceries", 500.0)
        db.set_budget(month, "Shopping", 200.0)
        db.set_budget(month, "Transportation", 150.0)
        db.set_budget(month, "Subscriptions", 50.0)

    db.log_import("fake_data.csv", "abc123def456", len(txns), len(txns))
    return len(txns)


@pytest.fixture(scope="session")
def e2e_server():
    """Start a server with fake data for the entire test session."""
    tmpdir = TemporaryDirectory()
    db_path = Path(tmpdir.name) / "test.db"
    csv_dir = Path(tmpdir.name) / "csv"
    csv_dir.mkdir()

    db = Database(str(db_path))
    txn_count = _seed_fake_data(db)
    db.close()

    db = Database(str(db_path))
    db.initialize()

    port = _free_port()
    Handler.db = db
    Handler.csv_dir = csv_dir
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    # Wait for server to be ready
    base = f"http://127.0.0.1:{port}"
    for _ in range(20):
        try:
            urllib.request.urlopen(f"{base}/api/stats")
            break
        except Exception:
            time.sleep(0.1)

    yield {
        "base_url": f"http://127.0.0.1:{port}",
        "db": db,
        "csv_dir": csv_dir,
        "txn_count": txn_count,
    }

    server.shutdown()
    db.close()
    tmpdir.cleanup()


@pytest.fixture(scope="session")
def base_url(e2e_server):
    return e2e_server["base_url"]
