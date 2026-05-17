"""SQLite database layer for transaction storage and querying."""

import hashlib
import json
import sqlite3
import threading
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
    linked_to TEXT DEFAULT '',
    adjustment REAL DEFAULT 0.0,
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

CREATE TABLE IF NOT EXISTS reimbursers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT UNIQUE NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    match_type TEXT NOT NULL DEFAULT 'substring'
);

CREATE TABLE IF NOT EXISTS reimburser_pairs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reimburser_pattern TEXT NOT NULL,
    expense_pattern TEXT NOT NULL,
    UNIQUE(reimburser_pattern, expense_pattern)
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
        self._lock = threading.RLock()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            with self._lock:
                if self._conn is None:
                    self._conn = sqlite3.connect(
                        str(self.db_path), check_same_thread=False
                    )
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
            self._migrate()

    def _migrate(self):
        cols = {
            r[1]
            for r in self.conn.execute("PRAGMA table_info(transactions)").fetchall()
        }
        with self.conn:
            if "linked_to" not in cols:
                self.conn.execute(
                    "ALTER TABLE transactions ADD COLUMN linked_to TEXT DEFAULT ''"
                )
            if "adjustment" not in cols:
                self.conn.execute(
                    "ALTER TABLE transactions ADD COLUMN adjustment REAL DEFAULT 0.0"
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
                    "source_file, is_deleted, linked_to, adjustment) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                        txn.linked_to,
                        txn.adjustment,
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
        with self._lock:
            if include_deleted:
                rows = self.conn.execute(
                    "SELECT * FROM transactions ORDER BY date DESC"
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT * FROM transactions WHERE is_deleted = 0 "
                    "ORDER BY date DESC"
                ).fetchall()
            return [self._row_to_txn(r) for r in rows]

    def get_expenses(self, include_deleted: bool = False) -> list[Transaction]:
        with self._lock:
            sql = "SELECT * FROM transactions WHERE txn_type = 'expense'"
            if not include_deleted:
                sql += " AND is_deleted = 0"
            sql += " ORDER BY date DESC"
            return [self._row_to_txn(r) for r in self.conn.execute(sql).fetchall()]

    def get_income(self, include_deleted: bool = False) -> list[Transaction]:
        with self._lock:
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

    def renormalize_all(self) -> int:
        """Re-apply store pairs to ALL transactions, updating store_normalized."""
        pairs = self.get_store_pairs()
        if not pairs:
            return 0
        pairs_lower = {k.lower(): v for k, v in pairs.items()}
        rows = self.conn.execute(
            "SELECT uuid, store_raw FROM transactions WHERE is_deleted = 0"
        ).fetchall()
        updated = 0
        with self.conn:
            for row in rows:
                raw_lower = row["store_raw"].lower().strip()
                if raw_lower in pairs_lower:
                    new_norm = pairs_lower[raw_lower]
                    self.conn.execute(
                        "UPDATE transactions SET store_normalized = ? WHERE uuid = ?",
                        (new_norm, row["uuid"]),
                    )
                    updated += 1
        return updated

    def remove_store_pair(self, raw_name: str) -> bool:
        with self.conn:
            cursor = self.conn.execute(
                "DELETE FROM store_pairs WHERE raw_name = ?", (raw_name,)
            )
        return cursor.rowcount > 0

    # -- Category rules --

    def get_category_rules(self) -> list[dict]:
        with self._lock:
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
        with self._lock:
            rows = self.conn.execute("SELECT * FROM store_pairs").fetchall()
            return {r["raw_name"]: r["normalized_name"] for r in rows}

    def add_store_pair(self, raw_name: str, normalized_name: str):
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO store_pairs "
                "(raw_name, normalized_name) VALUES (?, ?)",
                (raw_name, normalized_name),
            )

    def get_distinct_stores(self) -> dict:
        """Get all unique store names with their pair/category status."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT store_raw, store_normalized, category, txn_type, "
                "COUNT(*) as count FROM transactions "
                "WHERE is_deleted = 0 GROUP BY store_raw, txn_type "
                "ORDER BY count DESC"
            ).fetchall()
        pairs = self.get_store_pairs()
        expense_stores = []
        income_stores = []
        for r in rows:
            entry = {
                "raw": r["store_raw"],
                "normalized": r["store_normalized"] or r["store_raw"],
                "category": r["category"] or "",
                "count": r["count"],
                "has_pair": r["store_raw"].lower() in {k.lower() for k in pairs},
            }
            if r["txn_type"] == "income":
                income_stores.append(entry)
            else:
                expense_stores.append(entry)
        return {"expenses": expense_stores, "income": income_stores}

    def discover_store_pairs(self) -> list[dict]:
        """Fuzzy-match raw store names to suggest normalization pairs."""
        from difflib import SequenceMatcher

        with self._lock:
            rows = self.conn.execute(
                "SELECT store_raw, store_normalized, COUNT(*) as count "
                "FROM transactions WHERE is_deleted = 0 AND txn_type = 'expense' "
                "GROUP BY store_raw ORDER BY count DESC"
            ).fetchall()
        existing_pairs = self.get_store_pairs()
        already_paired = {k.lower() for k in existing_pairs}

        stores = [
            {
                "raw": r["store_raw"],
                "norm": r["store_normalized"] or r["store_raw"],
                "count": r["count"],
            }
            for r in rows
            if r["store_raw"].lower() not in already_paired
        ]
        if len(stores) < 2:
            return []

        suggestions = []
        seen = set()
        for i, s in enumerate(stores):
            if s["raw"] in seen:
                continue
            group = [s]
            s_clean = s["norm"].lower()
            for j in range(i + 1, len(stores)):
                other = stores[j]
                if other["raw"] in seen:
                    continue
                o_clean = other["norm"].lower()
                # Check prefix match (first 5+ chars) or high sequence similarity
                prefix_len = min(len(s_clean), len(o_clean), 8)
                if prefix_len >= 4 and s_clean[:prefix_len] == o_clean[:prefix_len]:
                    group.append(other)
                    seen.add(other["raw"])
                elif len(s_clean) >= 4 and len(o_clean) >= 4:
                    ratio = SequenceMatcher(None, s_clean, o_clean).ratio()
                    if ratio >= 0.75:
                        group.append(other)
                        seen.add(other["raw"])
            if len(group) > 1:
                # Suggest normalizing to the most common variant
                best = max(group, key=lambda g: g["count"])
                for g in group:
                    if g["raw"] != best["raw"]:
                        suggestions.append(
                            {
                                "raw": g["raw"],
                                "suggested_normalized": best["norm"],
                                "similarity_group": best["norm"],
                                "count": g["count"],
                            }
                        )
                seen.add(s["raw"])
        suggestions.sort(key=lambda x: -x["count"])
        return suggestions

    def detect_duplicates(self) -> list[dict]:
        """Find normalized store names that are likely the same merchant via fuzzy match."""
        from difflib import SequenceMatcher

        with self._lock:
            rows = self.conn.execute(
                "SELECT store_normalized, store_raw, COUNT(*) as count, "
                "SUM(amount) as total FROM transactions "
                "WHERE is_deleted = 0 AND txn_type = 'expense' "
                "GROUP BY store_normalized ORDER BY count DESC"
            ).fetchall()
        stores = [
            {
                "name": r["store_normalized"] or r["store_raw"],
                "count": r["count"],
                "total": round(r["total"], 2),
            }
            for r in rows
            if r["store_normalized"] or r["store_raw"]
        ]
        if len(stores) < 2:
            return []

        duplicates = []
        seen: set[str] = set()
        for i, s in enumerate(stores):
            if s["name"] in seen:
                continue
            s_lower = s["name"].lower()
            group = [s]
            for j in range(i + 1, len(stores)):
                other = stores[j]
                if other["name"] in seen:
                    continue
                o_lower = other["name"].lower()
                # One name contains the other (e.g. "domino's" in "domino's pizza")
                if s_lower in o_lower or o_lower in s_lower:
                    group.append(other)
                    seen.add(other["name"])
                elif len(s_lower) >= 4 and len(o_lower) >= 4:
                    ratio = SequenceMatcher(None, s_lower, o_lower).ratio()
                    if ratio >= 0.8:
                        group.append(other)
                        seen.add(other["name"])
            if len(group) > 1:
                best = max(group, key=lambda g: g["count"])
                duplicates.append(
                    {
                        "suggested_name": best["name"],
                        "variants": [
                            {
                                "name": g["name"],
                                "count": g["count"],
                                "total": g["total"],
                            }
                            for g in group
                        ],
                        "total_txns": sum(g["count"] for g in group),
                    }
                )
                seen.add(s["name"])
        duplicates.sort(key=lambda d: -d["total_txns"])
        return duplicates

    # -- Reimbursers --

    def get_reimbursers(self) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM reimbursers ORDER BY pattern"
            ).fetchall()
            return [dict(r) for r in rows]

    def add_reimburser(
        self, pattern: str, label: str = "", match_type: str = "substring"
    ):
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO reimbursers (pattern, label, match_type) "
                "VALUES (?, ?, ?)",
                (pattern, label or pattern, match_type),
            )

    def remove_reimburser(self, pattern: str) -> bool:
        with self.conn:
            cursor = self.conn.execute(
                "DELETE FROM reimbursers WHERE pattern = ?", (pattern,)
            )
        return cursor.rowcount > 0

    def get_pending_reimbursements(self) -> list[dict]:
        """Find unlinked income from known reimbursers."""
        reimbursers = self.get_reimbursers()
        if not reimbursers:
            return []
        income = self.get_income()
        unlinked = [t for t in income if not t.linked_to]
        pending = []
        for t in unlinked:
            store = (t.store_normalized or t.store_raw).lower()
            for r in reimbursers:
                pattern = r["pattern"].lower()
                matched = False
                if r["match_type"] == "exact":
                    matched = store == pattern
                else:
                    matched = pattern in store
                if matched:
                    pending.append(
                        {
                            "uuid": t.uuid,
                            "date": t.date.isoformat(),
                            "store": t.store_normalized or t.store_raw,
                            "amount": round(t.amount, 2),
                            "reimburser": r["label"] or r["pattern"],
                            "reimburser_pattern": r["pattern"],
                        }
                    )
                    break
        # Attach suggested expenses based on reimburser_pairs
        pairs = self.get_reimburser_pairs()
        if pairs and pending:
            expenses = self.get_expenses()
            unlinked_expenses = [e for e in expenses if not e.linked_to]
            for p in pending:
                matched_expense_patterns = [
                    pair["expense_pattern"]
                    for pair in pairs
                    if pair["reimburser_pattern"].lower()
                    in p["reimburser_pattern"].lower()
                ]
                if matched_expense_patterns:
                    suggestions = []
                    for e in unlinked_expenses:
                        e_store = (e.store_normalized or e.store_raw).lower()
                        for ep in matched_expense_patterns:
                            if ep.lower() in e_store:
                                suggestions.append(
                                    {
                                        "uuid": e.uuid,
                                        "date": e.date.isoformat(),
                                        "store": e.store_normalized or e.store_raw,
                                        "amount": round(e.amount, 2),
                                    }
                                )
                                break
                    # Sort by closest amount match, then by date proximity
                    suggestions.sort(
                        key=lambda s: (
                            abs(s["amount"] - p["amount"]),
                            abs(
                                (
                                    date.fromisoformat(s["date"])
                                    - date.fromisoformat(p["date"])
                                ).days
                            ),
                        )
                    )
                    p["suggested_expenses"] = suggestions[:10]
        return pending

    # -- Reimburser pairs --

    def get_reimburser_pairs(self) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM reimburser_pairs ORDER BY reimburser_pattern"
            ).fetchall()
            return [dict(r) for r in rows]

    def add_reimburser_pair(
        self, reimburser_pattern: str, expense_pattern: str
    ) -> bool:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO reimburser_pairs "
                "(reimburser_pattern, expense_pattern) VALUES (?, ?)",
                (reimburser_pattern, expense_pattern),
            )
        return True

    def remove_reimburser_pair(
        self, reimburser_pattern: str, expense_pattern: str
    ) -> bool:
        with self.conn:
            cursor = self.conn.execute(
                "DELETE FROM reimburser_pairs "
                "WHERE reimburser_pattern = ? AND expense_pattern = ?",
                (reimburser_pattern, expense_pattern),
            )
        return cursor.rowcount > 0

    def discover_reimburser_pairs(self) -> list[dict]:
        """Scan historical links to discover reimburser-to-expense patterns."""
        with self._lock:
            # Find expenses that have been linked (have linked_to set)
            rows = self.conn.execute(
                "SELECT store_normalized, store_raw, linked_to FROM transactions "
                "WHERE linked_to != '' AND txn_type = 'expense' AND adjustment > 0"
            ).fetchall()
        if not rows:
            return []
        # For each linked expense, find the income it was linked to
        pair_counts: dict[tuple[str, str], int] = {}
        for row in rows:
            expense_store = (row["store_normalized"] or row["store_raw"]).lower()
            income_uuid = row["linked_to"]
            with self._lock:
                inc_row = self.conn.execute(
                    "SELECT store_normalized, store_raw FROM transactions "
                    "WHERE uuid = ?",
                    (income_uuid,),
                ).fetchone()
            if inc_row:
                income_store = (
                    inc_row["store_normalized"] or inc_row["store_raw"]
                ).lower()
                key = (income_store, expense_store)
                pair_counts[key] = pair_counts.get(key, 0) + 1
        # Return pairs sorted by frequency
        discovered = [
            {
                "reimburser_pattern": k[0],
                "expense_pattern": k[1],
                "link_count": v,
            }
            for k, v in pair_counts.items()
        ]
        discovered.sort(key=lambda x: x["link_count"], reverse=True)
        return discovered

    # -- Budgets --

    def set_budget(self, month: str, category: str, amount: float):
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO budgets (month, category, amount) "
                "VALUES (?, ?, ?)",
                (month, category, amount),
            )

    def get_budgets(self, month: str | None = None) -> list[dict]:
        with self._lock:
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
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM import_log ORDER BY imported_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    # -- Stats --

    def get_stats(self) -> dict:
        with self._lock:
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

        with self._lock:
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

    # -- Offsets / Linking --

    def link_transactions(self, expense_uuid: str, income_uuid: str) -> bool:
        """Link an income transaction to an expense as an offset."""
        with self._lock:
            income_row = self.conn.execute(
                "SELECT * FROM transactions WHERE uuid = ? AND is_deleted = 0",
                (income_uuid,),
            ).fetchone()
            expense_row = self.conn.execute(
                "SELECT * FROM transactions WHERE uuid = ? AND is_deleted = 0",
                (expense_uuid,),
            ).fetchone()
        if not income_row or not expense_row:
            return False
        offset_amount = income_row["amount"]
        with self.conn:
            self.conn.execute(
                "UPDATE transactions SET adjustment = adjustment + ?, linked_to = ? "
                "WHERE uuid = ?",
                (offset_amount, income_uuid, expense_uuid),
            )
            self.conn.execute(
                "UPDATE transactions SET linked_to = ?, is_deleted = 1 "
                "WHERE uuid = ?",
                (expense_uuid, income_uuid),
            )
        return True

    def unlink_transactions(self, expense_uuid: str) -> bool:
        """Remove offset link from an expense, restore the income transaction."""
        with self._lock:
            expense_row = self.conn.execute(
                "SELECT * FROM transactions WHERE uuid = ?",
                (expense_uuid,),
            ).fetchone()
        if not expense_row or not expense_row["linked_to"]:
            return False
        income_uuid = expense_row["linked_to"]
        with self._lock:
            income_row = self.conn.execute(
                "SELECT * FROM transactions WHERE uuid = ?",
                (income_uuid,),
            ).fetchone()
        if not income_row:
            return False
        with self.conn:
            self.conn.execute(
                "UPDATE transactions SET adjustment = adjustment - ?, linked_to = '' "
                "WHERE uuid = ?",
                (income_row["amount"], expense_uuid),
            )
            self.conn.execute(
                "UPDATE transactions SET linked_to = '', is_deleted = 0 "
                "WHERE uuid = ?",
                (income_uuid,),
            )
        return True

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
            linked_to=row["linked_to"] or "",
            adjustment=row["adjustment"] or 0.0,
        )

    @staticmethod
    def hash_file(path: str | Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
