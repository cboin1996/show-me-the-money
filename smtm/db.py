"""SQLite database layer for transaction storage and querying."""

import hashlib
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

from .models import CategoryDB, Transaction, TxnType

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT UNIQUE NOT NULL,
    date TEXT NOT NULL,
    amount REAL NOT NULL,
    store_raw TEXT NOT NULL,
    store_normalized TEXT DEFAULT '',
    sub_description TEXT DEFAULT '',
    category TEXT DEFAULT '',
    confidence TEXT DEFAULT '',
    txn_type TEXT NOT NULL DEFAULT 'expense',
    source_file TEXT DEFAULT '',
    is_deleted INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_txn_dedup
    ON transactions(date, amount, store_raw, source_file)
    WHERE is_deleted = 0;

CREATE INDEX IF NOT EXISTS idx_txn_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_txn_category ON transactions(category);
CREATE INDEX IF NOT EXISTS idx_txn_month ON transactions(date);

CREATE TABLE IF NOT EXISTS category_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT NOT NULL,
    category TEXT NOT NULL,
    match_type TEXT NOT NULL DEFAULT 'exact',
    priority INTEGER DEFAULT 0,
    UNIQUE(pattern, match_type)
);

CREATE TABLE IF NOT EXISTS store_pairs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_name TEXT UNIQUE NOT NULL,
    normalized_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS budgets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    month TEXT NOT NULL,
    category TEXT NOT NULL,
    amount REAL NOT NULL,
    UNIQUE(month, category)
);

CREATE TABLE IF NOT EXISTS import_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    row_count INTEGER DEFAULT 0,
    new_count INTEGER DEFAULT 0,
    imported_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_import_hash ON import_log(file_hash);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);
"""


class Database:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def initialize(self):
        with self.conn:
            self.conn.executescript(SCHEMA_SQL)
            existing = self.conn.execute(
                "SELECT version FROM schema_version LIMIT 1"
            ).fetchone()
            if not existing:
                self.conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (SCHEMA_VERSION,),
                )

    # -- Import log --

    def file_already_imported(self, file_hash: str) -> bool:
        row = self.conn.execute(
            "SELECT id FROM import_log WHERE file_hash = ?", (file_hash,)
        ).fetchone()
        return row is not None

    def log_import(
        self, source_file: str, file_hash: str, row_count: int, new_count: int
    ):
        with self.conn:
            self.conn.execute(
                "INSERT INTO import_log "
                "(source_file, file_hash, row_count, new_count) "
                "VALUES (?, ?, ?, ?)",
                (source_file, file_hash, row_count, new_count),
            )

    # -- Transactions --

    def insert_transaction(self, txn: Transaction) -> bool:
        """Insert a transaction. Returns True if inserted, False if duplicate."""
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT INTO transactions "
                    "(uuid, date, amount, store_raw, store_normalized, "
                    "sub_description, category, confidence, txn_type, "
                    "source_file, is_deleted) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        txn.uuid,
                        txn.date.isoformat(),
                        txn.amount,
                        txn.store_raw,
                        txn.store_normalized,
                        txn.sub_description,
                        txn.category,
                        txn.confidence,
                        txn.txn_type.value,
                        txn.source_file,
                        int(txn.is_deleted),
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def insert_transactions(self, txns: list[Transaction]) -> tuple[int, int]:
        """Bulk insert. Returns (inserted_count, duplicate_count)."""
        inserted = 0
        dupes = 0
        for txn in txns:
            if self.insert_transaction(txn):
                inserted += 1
            else:
                dupes += 1
        return inserted, dupes

    def get_all_transactions(self, include_deleted: bool = False) -> list[Transaction]:
        if include_deleted:
            rows = self.conn.execute(
                "SELECT * FROM transactions ORDER BY date DESC"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM transactions WHERE is_deleted = 0 " "ORDER BY date DESC"
            ).fetchall()
        return [self._row_to_txn(r) for r in rows]

    def get_expenses(self, include_deleted: bool = False) -> list[Transaction]:
        sql = "SELECT * FROM transactions WHERE txn_type = 'expense'"
        if not include_deleted:
            sql += " AND is_deleted = 0"
        sql += " ORDER BY date DESC"
        return [self._row_to_txn(r) for r in self.conn.execute(sql).fetchall()]

    def get_income(self, include_deleted: bool = False) -> list[Transaction]:
        sql = "SELECT * FROM transactions WHERE txn_type = 'income'"
        if not include_deleted:
            sql += " AND is_deleted = 0"
        sql += " ORDER BY date DESC"
        return [self._row_to_txn(r) for r in self.conn.execute(sql).fetchall()]

    def soft_delete(self, uuid: str) -> bool:
        with self.conn:
            cursor = self.conn.execute(
                "UPDATE transactions SET is_deleted = 1 WHERE uuid = ?",
                (uuid,),
            )
        return cursor.rowcount > 0

    def restore(self, uuid: str) -> bool:
        with self.conn:
            cursor = self.conn.execute(
                "UPDATE transactions SET is_deleted = 0 WHERE uuid = ?",
                (uuid,),
            )
        return cursor.rowcount > 0

    def update_category(self, uuid: str, category: str, confidence: str = "manual"):
        with self.conn:
            self.conn.execute(
                "UPDATE transactions SET category = ?, confidence = ? "
                "WHERE uuid = ?",
                (category, confidence, uuid),
            )

    def recategorize_all(self, category_db: CategoryDB):
        """Re-run categorization on all uncategorized transactions."""
        from .categorizer import categorize

        rows = self.conn.execute(
            "SELECT * FROM transactions "
            "WHERE (category = '' OR category IS NULL) AND is_deleted = 0"
        ).fetchall()
        updated = 0
        with self.conn:
            for row in rows:
                txn = self._row_to_txn(row)
                result = categorize(txn, category_db)
                if result.category:
                    self.conn.execute(
                        "UPDATE transactions "
                        "SET category = ?, confidence = ?, store_normalized = ? "
                        "WHERE uuid = ?",
                        (
                            result.category,
                            result.confidence,
                            result.normalized_store,
                            txn.uuid,
                        ),
                    )
                    updated += 1
        return updated

    # -- Category rules --

    def get_category_rules(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM category_rules ORDER BY priority DESC, pattern"
        ).fetchall()
        return [dict(r) for r in rows]

    def add_category_rule(
        self, pattern: str, category: str, match_type: str = "exact", priority: int = 0
    ):
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO category_rules "
                "(pattern, category, match_type, priority) "
                "VALUES (?, ?, ?, ?)",
                (pattern, category, match_type, priority),
            )

    # -- Store pairs --

    def get_store_pairs(self) -> dict[str, str]:
        rows = self.conn.execute("SELECT * FROM store_pairs").fetchall()
        return {r["raw_name"]: r["normalized_name"] for r in rows}

    def add_store_pair(self, raw_name: str, normalized_name: str):
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO store_pairs "
                "(raw_name, normalized_name) VALUES (?, ?)",
                (raw_name, normalized_name),
            )

    # -- Budgets --

    def set_budget(self, month: str, category: str, amount: float):
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO budgets (month, category, amount) "
                "VALUES (?, ?, ?)",
                (month, category, amount),
            )

    def get_budgets(self, month: str | None = None) -> list[dict]:
        if month:
            rows = self.conn.execute(
                "SELECT * FROM budgets WHERE month = ? ORDER BY category",
                (month,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM budgets ORDER BY month, category"
            ).fetchall()
        return [dict(r) for r in rows]

    def copy_budget(self, from_month: str, to_month: str) -> int:
        budgets = self.get_budgets(from_month)
        for b in budgets:
            self.set_budget(to_month, b["category"], b["amount"])
        return len(budgets)

    # -- Import history --

    def get_import_history(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM import_log ORDER BY imported_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # -- Stats --

    def get_stats(self) -> dict:
        total = self.conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE is_deleted = 0"
        ).fetchone()[0]
        categorized = self.conn.execute(
            "SELECT COUNT(*) FROM transactions "
            "WHERE is_deleted = 0 AND category != '' AND category IS NOT NULL"
        ).fetchone()[0]
        expenses = self.conn.execute(
            "SELECT COUNT(*) FROM transactions "
            "WHERE is_deleted = 0 AND txn_type = 'expense'"
        ).fetchone()[0]
        income = self.conn.execute(
            "SELECT COUNT(*) FROM transactions "
            "WHERE is_deleted = 0 AND txn_type = 'income'"
        ).fetchone()[0]
        date_range = self.conn.execute(
            "SELECT MIN(date), MAX(date) FROM transactions WHERE is_deleted = 0"
        ).fetchone()
        return {
            "total": total,
            "categorized": categorized,
            "uncategorized": total - categorized,
            "expenses": expenses,
            "income": income,
            "classification_rate": (categorized / total * 100 if total else 0),
            "date_min": date_range[0],
            "date_max": date_range[1],
        }

    # -- Migration from JSON --

    def migrate_from_json(self, db_dir: str | Path):
        """Import category rules and store pairs from legacy JSON files."""
        db_dir = Path(db_dir)

        stores_path = db_dir / "storesWithExpenses.json"
        if stores_path.exists():
            with open(stores_path) as f:
                store_cats = json.load(f)
            with self.conn:
                for pattern, cats in store_cats.items():
                    if cats:
                        self.conn.execute(
                            "INSERT OR IGNORE INTO category_rules "
                            "(pattern, category, match_type) VALUES (?, ?, ?)",
                            (pattern, cats[0], "exact"),
                        )
            print(f"  Migrated {len(store_cats)} category rules")

        pairs_path = db_dir / "storePairs.json"
        if pairs_path.exists():
            with open(pairs_path) as f:
                pairs = json.load(f)
            with self.conn:
                for raw, norm in pairs.items():
                    self.conn.execute(
                        "INSERT OR IGNORE INTO store_pairs "
                        "(raw_name, normalized_name) VALUES (?, ?)",
                        (raw, norm),
                    )
            print(f"  Migrated {len(pairs)} store pairs")

        exp_path = db_dir / "expenses.json"
        if exp_path.exists():
            with open(exp_path) as f:
                data = json.load(f)
            categories = data.get("expense", [])
            if categories:
                print(f"  Found categories: {', '.join(categories)}")

    def load_category_db(self) -> CategoryDB:
        """Build a CategoryDB from SQLite data for the categorizer."""
        rules = self.get_category_rules()
        store_to_cat: dict[str, list[str]] = {}
        for r in rules:
            pattern = r["pattern"]
            if pattern not in store_to_cat:
                store_to_cat[pattern] = []
            store_to_cat[pattern].append(r["category"])

        pairs = self.get_store_pairs()

        categories_row = self.conn.execute(
            "SELECT DISTINCT category FROM category_rules ORDER BY category"
        ).fetchall()
        categories = [r[0] for r in categories_row]

        return CategoryDB(
            categories=categories,
            store_to_category=store_to_cat,
            store_pairs=pairs,
        )

    # -- Helpers --

    @staticmethod
    def _row_to_txn(row: sqlite3.Row) -> Transaction:
        return Transaction(
            date=date.fromisoformat(row["date"]),
            amount=row["amount"],
            store_raw=row["store_raw"],
            store_normalized=row["store_normalized"] or "",
            sub_description=row["sub_description"] or "",
            category=row["category"] or "",
            confidence=row["confidence"] or "",
            txn_type=TxnType(row["txn_type"]),
            source_file=row["source_file"] or "",
            uuid=row["uuid"],
            is_deleted=bool(row["is_deleted"]),
        )

    @staticmethod
    def hash_file(path: str | Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
