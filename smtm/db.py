"""SQLite database layer for transaction storage and querying."""

import hashlib
import json
import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path

from .models import CategoryDB, Transaction, TxnType

SCHEMA_VERSION = 6

_MIGRATIONS = [
    # 1: linked reimbursements
    "ALTER TABLE transactions ADD COLUMN linked_to TEXT DEFAULT ''",
    # 2: expense adjustment (partial offset amount)
    "ALTER TABLE transactions ADD COLUMN adjustment REAL DEFAULT 0.0",
    # 3: per-trip category exclusions
    "ALTER TABLE trips ADD COLUMN excluded_categories TEXT DEFAULT '[\"Investments\"]'",
    # 4: per-transaction solo flag (100% owner, not split)
    "ALTER TABLE trip_transactions ADD COLUMN is_solo INTEGER DEFAULT 0",
    # 5: soft-delete timestamp
    "ALTER TABLE transactions ADD COLUMN deleted_at TEXT DEFAULT ''",
    # 6: configurable import filters
    """CREATE TABLE IF NOT EXISTS import_filters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pattern TEXT UNIQUE NOT NULL,
        match_type TEXT NOT NULL DEFAULT 'substring',
        label TEXT NOT NULL DEFAULT ''
    )""",
]

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
    deleted_at TEXT DEFAULT '',
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

CREATE TABLE IF NOT EXISTS import_filters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT UNIQUE NOT NULL,
    match_type TEXT NOT NULL DEFAULT 'substring',
    label TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS trips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    notes TEXT DEFAULT '',
    excluded_categories TEXT DEFAULT '["Investments"]',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS trip_transactions (
    trip_id INTEGER NOT NULL,
    txn_uuid TEXT NOT NULL,
    is_solo INTEGER DEFAULT 0,
    PRIMARY KEY (trip_id, txn_uuid),
    FOREIGN KEY (trip_id) REFERENCES trips(id) ON DELETE CASCADE,
    FOREIGN KEY (txn_uuid) REFERENCES transactions(uuid) ON DELETE CASCADE
);

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
        self._seed_import_filters()

    def _migrate(self):
        row = self.conn.execute("SELECT version FROM schema_version").fetchone()
        ver = row["version"] if row else 0
        with self.conn:
            for i, sql in enumerate(_MIGRATIONS, start=1):
                if ver < i:
                    try:
                        self.conn.execute(sql)
                    except sqlite3.OperationalError:
                        pass  # column already exists — idempotent
                    self.conn.execute("UPDATE schema_version SET version = ?", (i,))
                    ver = i

    # -- Import filters --

    _DEFAULT_IMPORT_FILTERS = [
        ("mb-credit card/loc pay", "substring", "Credit card payment"),
        ("mb-transfer", "substring", "Inter-account transfer"),
        ("pc to", "substring", "Inter-account transfer"),
        ("pc from", "substring", "Inter-account transfer"),
        ("mb-cash advance", "substring", "Cash advance"),
        ("mb - cash advance", "substring", "Cash advance"),
        ("pc - payment", "substring", "Payment"),
        ("customer transfer dr.", "substring", "Customer transfer"),
        ("customer transfer cr.", "substring", "Customer transfer"),
        ("crd. card bill payment", "substring", "Credit card payment"),
        ("interac e-transfer", "substring", "E-transfer"),
        ("free interac e-transfer", "substring", "E-transfer"),
    ]

    def _seed_import_filters(self):
        count = self.conn.execute("SELECT COUNT(*) FROM import_filters").fetchone()[0]
        if count == 0:
            with self.conn:
                for pattern, match_type, label in self._DEFAULT_IMPORT_FILTERS:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO import_filters (pattern, match_type, label) "
                        "VALUES (?, ?, ?)",
                        (pattern, match_type, label),
                    )

    def get_import_filters(self) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT id, pattern, match_type, label FROM import_filters ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    def add_import_filter(
        self, pattern: str, match_type: str = "substring", label: str = ""
    ) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO import_filters (pattern, match_type, label) VALUES (?, ?, ?)",
                (pattern, match_type, label),
            )
        return cur.lastrowid

    def remove_import_filter(self, filter_id: int) -> bool:
        with self.conn:
            cur = self.conn.execute(
                "DELETE FROM import_filters WHERE id = ?", (filter_id,)
            )
        return cur.rowcount > 0

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
        from datetime import datetime

        with self.conn:
            cursor = self.conn.execute(
                "UPDATE transactions SET is_deleted = 1, deleted_at = ? WHERE uuid = ?",
                (datetime.now().strftime("%Y-%m-%d %H:%M"), uuid),
            )
        return cursor.rowcount > 0

    def restore(self, uuid: str) -> bool:
        with self.conn:
            cursor = self.conn.execute(
                "UPDATE transactions SET is_deleted = 0, deleted_at = '' WHERE uuid = ?",
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

    def update_txn_type(self, uuid: str, txn_type: str):
        with self.conn:
            self.conn.execute(
                "UPDATE transactions SET txn_type = ? WHERE uuid = ?",
                (txn_type, uuid),
            )

    def update_store_normalized(self, uuid: str, store_normalized: str) -> bool:
        with self.conn:
            cur = self.conn.execute(
                "UPDATE transactions SET store_normalized = ? WHERE uuid = ?",
                (store_normalized, uuid),
            )
        return cur.rowcount > 0

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

        def resolve(name):
            visited = set()
            while name.lower() in pairs_lower and name.lower() not in visited:
                visited.add(name.lower())
                name = pairs_lower[name.lower()]
            return name

        rows = self.conn.execute(
            "SELECT uuid, store_raw, store_normalized FROM transactions "
            "WHERE is_deleted = 0"
        ).fetchall()
        updated = 0
        with self.conn:
            for row in rows:
                raw_lower = row["store_raw"].lower().strip()
                norm_lower = (row["store_normalized"] or "").lower().strip()
                match = pairs_lower.get(raw_lower) or pairs_lower.get(norm_lower)
                if match:
                    final = resolve(match)
                    if final != (row["store_normalized"] or ""):
                        self.conn.execute(
                            "UPDATE transactions SET store_normalized = ? "
                            "WHERE uuid = ?",
                            (final, row["uuid"]),
                        )
                        updated += 1
        self.normalize_rules()
        return updated

    def normalize_rules(self) -> int:
        """Update exact rule patterns from raw store names to their normalized equivalents."""
        pairs = self.get_store_pairs()
        if not pairs:
            return 0
        pairs_lower = {k.lower(): v for k, v in pairs.items()}
        rules = self.conn.execute(
            "SELECT pattern, category FROM category_rules WHERE match_type = 'exact'"
        ).fetchall()
        updated = 0
        with self.conn:
            for rule in rules:
                p = rule["pattern"].lower()
                if p not in pairs_lower:
                    continue
                new_pattern = pairs_lower[p]
                if new_pattern.lower() == p:
                    continue
                try:
                    self.conn.execute(
                        "UPDATE category_rules SET pattern = ? "
                        "WHERE pattern = ? AND match_type = 'exact'",
                        (new_pattern, rule["pattern"]),
                    )
                    updated += 1
                except sqlite3.IntegrityError:
                    # Normalized pattern already has a rule — drop the raw duplicate
                    self.conn.execute(
                        "DELETE FROM category_rules WHERE pattern = ? AND match_type = 'exact'",
                        (rule["pattern"],),
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
            rules = [dict(r) for r in rows]
            store_counts: dict[str, int] = {}
            for r in self.conn.execute(
                "SELECT LOWER(store_normalized) as sn, COUNT(*) as cnt "
                "FROM transactions WHERE is_deleted=0 GROUP BY sn"
            ).fetchall():
                store_counts[r["sn"]] = r["cnt"]
        raw_counts: dict[str, int] = {}
        for r in self.conn.execute(
            "SELECT LOWER(store_raw) as k, COUNT(*) as cnt "
            "FROM transactions WHERE is_deleted=0 GROUP BY k"
        ).fetchall():
            raw_counts[r["k"]] = r["cnt"]
        for rule in rules:
            p = rule["pattern"].lower()
            mt = rule["match_type"]
            if mt == "exact":
                rule["txn_count"] = store_counts.get(p, 0) or raw_counts.get(p, 0)
            else:
                rule["txn_count"] = sum(
                    cnt for sn, cnt in store_counts.items() if p in sn
                ) or sum(cnt for sr, cnt in raw_counts.items() if p in sr)
        return rules

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
            rows = self.conn.execute(
                "SELECT e.uuid as exp_uuid, e.store_normalized as exp_norm, e.store_raw as exp_raw, "
                "e.amount as exp_amount, e.date as exp_date, e.category as exp_cat, "
                "e.linked_to as inc_uuid "
                "FROM transactions e "
                "WHERE e.linked_to != '' AND e.txn_type = 'expense' AND e.adjustment > 0"
            ).fetchall()
        if not rows:
            return []

        pair_data: dict[tuple[str, str], dict] = {}
        for row in rows:
            expense_store = (row["exp_norm"] or row["exp_raw"]).lower()
            with self._lock:
                inc_row = self.conn.execute(
                    "SELECT store_normalized, store_raw, amount, date FROM transactions WHERE uuid = ?",
                    (row["inc_uuid"],),
                ).fetchone()
            if not inc_row:
                continue
            income_store = (inc_row["store_normalized"] or inc_row["store_raw"]).lower()
            if income_store == expense_store:
                continue  # refund to same store — not a meaningful pair
            key = (income_store, expense_store)
            if key not in pair_data:
                pair_data[key] = {"link_count": 0, "examples": []}
            pair_data[key]["link_count"] += 1
            if len(pair_data[key]["examples"]) < 3:
                pair_data[key]["examples"].append(
                    {
                        "income_date": inc_row["date"],
                        "income_store": inc_row["store_normalized"]
                        or inc_row["store_raw"],
                        "income_amount": round(inc_row["amount"], 2),
                        "expense_date": row["exp_date"],
                        "expense_store": row["exp_norm"] or row["exp_raw"],
                        "expense_amount": round(row["exp_amount"], 2),
                        "expense_category": row["exp_cat"] or "",
                    }
                )

        discovered = [
            {
                "reimburser_pattern": k[0],
                "expense_pattern": k[1],
                "link_count": v["link_count"],
                "examples": v["examples"],
            }
            for k, v in pair_data.items()
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

    # -- Trips --

    def get_trips(self) -> list[dict]:
        from collections import defaultdict

        with self._lock:
            trip_rows = self.conn.execute(
                "SELECT id, name, start_date, end_date, notes, excluded_categories "
                "FROM trips ORDER BY start_date DESC"
            ).fetchall()
            txn_rows = self.conn.execute(
                "SELECT tt.trip_id, tr.amount, tr.category "
                "FROM trip_transactions tt "
                "JOIN transactions tr ON tr.uuid = tt.txn_uuid "
                "WHERE tr.is_deleted = 0 AND tr.txn_type = 'expense'"
            ).fetchall()

        txns_by_trip: dict[int, list] = defaultdict(list)
        for r in txn_rows:
            txns_by_trip[r["trip_id"]].append(r)

        result = []
        for t in trip_rows:
            excluded = set(
                c.lower() for c in json.loads(t["excluded_categories"] or "[]")
            )
            included = [
                r
                for r in txns_by_trip[t["id"]]
                if (r["category"] or "").lower() not in excluded
            ]
            result.append(
                {
                    "id": t["id"],
                    "name": t["name"],
                    "start_date": t["start_date"],
                    "end_date": t["end_date"],
                    "notes": t["notes"],
                    "excluded_categories": json.loads(t["excluded_categories"] or "[]"),
                    "txn_count": len(included),
                    "total_spend": round(sum(r["amount"] for r in included), 2),
                }
            )
        return result

    def get_trip(self, trip_id: int) -> dict | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT id, name, start_date, end_date, notes, excluded_categories "
                "FROM trips WHERE id = ?",
                (trip_id,),
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["excluded_categories"] = json.loads(d["excluded_categories"] or "[]")
        return d

    def get_trip_transactions(self, trip_id: int) -> list[dict]:
        """Return trip transactions as dicts including is_solo flag."""
        trip = self.get_trip(trip_id)
        excluded = set(
            c.lower() for c in ((trip or {}).get("excluded_categories") or [])
        )
        with self._lock:
            rows = self.conn.execute(
                "SELECT tr.*, tt.is_solo FROM transactions tr "
                "JOIN trip_transactions tt ON tt.txn_uuid = tr.uuid "
                "WHERE tt.trip_id = ? AND tr.is_deleted = 0 AND tr.txn_type = 'expense' ORDER BY tr.date",
                (trip_id,),
            ).fetchall()
        result = []
        for r in rows:
            if (r["category"] or "").lower() in excluded:
                continue
            txn = self._row_to_txn(r)
            d = {
                "date": txn.date.isoformat(),
                "month": txn.month,
                "amount": round(txn.amount, 2),
                "store_raw": txn.store_raw,
                "store_normalized": txn.store_normalized or txn.store_raw,
                "category": txn.category or "Uncategorized",
                "type": txn.txn_type.value,
                "uuid": txn.uuid,
                "is_solo": bool(r["is_solo"]),
            }
            result.append(d)
        return result

    def toggle_trip_solo(self, trip_id: int, txn_uuid: str) -> bool:
        with self._lock:
            row = self.conn.execute(
                "SELECT is_solo FROM trip_transactions WHERE trip_id = ? AND txn_uuid = ?",
                (trip_id, txn_uuid),
            ).fetchone()
        if not row:
            return False
        new_val = 0 if row["is_solo"] else 1
        with self.conn:
            self.conn.execute(
                "UPDATE trip_transactions SET is_solo = ? WHERE trip_id = ? AND txn_uuid = ?",
                (new_val, trip_id, txn_uuid),
            )
        return True

    def create_trip(
        self,
        name: str,
        start_date: str,
        end_date: str,
        notes: str = "",
        excluded_categories: list[str] | None = None,
    ) -> int:
        if excluded_categories is None:
            excluded_categories = ["Investments"]
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO trips (name, start_date, end_date, notes, excluded_categories) VALUES (?, ?, ?, ?, ?)",
                (name, start_date, end_date, notes, json.dumps(excluded_categories)),
            )
        return cur.lastrowid

    def delete_trip(self, trip_id: int) -> bool:
        with self.conn:
            cur = self.conn.execute("DELETE FROM trips WHERE id = ?", (trip_id,))
        return cur.rowcount > 0

    def auto_assign_trip(self, trip_id: int) -> int:
        trip = self.get_trip(trip_id)
        if not trip:
            return 0
        excluded = set(c.lower() for c in (trip.get("excluded_categories") or []))
        with self._lock:
            rows = self.conn.execute(
                "SELECT uuid, category FROM transactions WHERE date >= ? AND date <= ? AND is_deleted = 0 AND txn_type = 'expense'",
                (trip["start_date"], trip["end_date"]),
            ).fetchall()
        rows = [r for r in rows if (r["category"] or "").lower() not in excluded]
        added = 0
        with self.conn:
            for row in rows:
                try:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO trip_transactions (trip_id, txn_uuid) VALUES (?, ?)",
                        (trip_id, row["uuid"]),
                    )
                    added += 1
                except Exception:
                    pass
        return added

    def add_trip_transaction(self, trip_id: int, txn_uuid: str) -> bool:
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT OR IGNORE INTO trip_transactions (trip_id, txn_uuid) VALUES (?, ?)",
                    (trip_id, txn_uuid),
                )
            return True
        except Exception:
            return False

    def remove_trip_transaction(self, trip_id: int, txn_uuid: str) -> bool:
        with self.conn:
            cur = self.conn.execute(
                "DELETE FROM trip_transactions WHERE trip_id = ? AND txn_uuid = ?",
                (trip_id, txn_uuid),
            )
        return cur.rowcount > 0

    def update_trip(
        self,
        trip_id: int,
        name: str,
        start_date: str,
        end_date: str,
        notes: str = "",
        excluded_categories: list[str] | None = None,
    ) -> bool:
        fields = "name=?, start_date=?, end_date=?, notes=?"
        params: list = [name, start_date, end_date, notes]
        if excluded_categories is not None:
            fields += ", excluded_categories=?"
            params.append(json.dumps(excluded_categories))
        params.append(trip_id)
        with self.conn:
            cur = self.conn.execute(f"UPDATE trips SET {fields} WHERE id=?", params)
        return cur.rowcount > 0

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
            deleted_at=row["deleted_at"] if "deleted_at" in row.keys() else "",
        )

    @staticmethod
    def hash_file(path: str | Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
