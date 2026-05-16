"""CSV parsers for various bank formats."""
from datetime import date
from pathlib import Path

import pandas as pd

from .models import Transaction, TxnType

# Scotia ignorable transaction patterns (transfers between accounts)
IGNORABLE_PATTERNS = [
    "mb-credit card/loc pay",
    "mb-transfer",
    "pc to",
    "pc from",
    "payment from",
    "customer transfer dr.",
    "free interac e-transfer",
    "interac e-transfer",
]


def _is_ignorable(store: str) -> bool:
    s = store.lower()
    return any(pat in s for pat in IGNORABLE_PATTERNS)


def parse_scotia_new_credit(path: str | Path) -> list[Transaction]:
    """New format: 7 cols with headers (Filter, Date, Description, ...)."""
    df = pd.read_csv(path)
    txns = []
    for _, row in df.iterrows():
        store = str(row.get("Description", "")).lower().strip()
        if _is_ignorable(store):
            continue
        amount = float(row["Amount"])
        txn_type = TxnType.EXPENSE if row["Type of Transaction"] == "Debit" else TxnType.INCOME
        txns.append(Transaction(
            date=pd.to_datetime(row["Date"]).date(),
            amount=abs(amount),
            store_raw=store,
            sub_description=str(row.get("Sub-description", "") or "").lower().strip(),
            txn_type=txn_type,
            source_file=Path(path).name,
        ))
    return txns


def parse_scotia_new_debit(path: str | Path) -> list[Transaction]:
    """New format: 7 cols with Balance column."""
    df = pd.read_csv(path)
    txns = []
    for _, row in df.iterrows():
        store = str(row.get("Description", "")).lower().strip()
        sub = str(row.get("Sub-description", "") or "").lower().strip()
        if _is_ignorable(store) or _is_ignorable(sub):
            continue
        amount = float(row["Amount"])
        txn_type = TxnType.EXPENSE if amount < 0 else TxnType.INCOME
        txns.append(Transaction(
            date=pd.to_datetime(row["Date"]).date(),
            amount=abs(amount),
            store_raw=store,
            sub_description=sub,
            txn_type=txn_type,
            source_file=Path(path).name,
        ))
    return txns


def parse_scotia_old_credit(path: str | Path) -> list[Transaction]:
    """Old format: 3 cols, no headers (Date, Store, Amount)."""
    df = pd.read_csv(path, header=None, names=["date", "store", "amount"])
    txns = []
    for _, row in df.iterrows():
        store = str(row["store"]).lower().strip()
        if _is_ignorable(store):
            continue
        amount = float(row["amount"])
        txn_type = TxnType.INCOME if amount > 0 else TxnType.EXPENSE
        txns.append(Transaction(
            date=pd.to_datetime(row["date"]).date(),
            amount=abs(amount),
            store_raw=store,
            txn_type=txn_type,
            source_file=Path(path).name,
        ))
    return txns


def parse_scotia_old_debit(path: str | Path) -> list[Transaction]:
    """Old format: 5 cols, no headers (Date, Amount, Null, Type, Store)."""
    df = pd.read_csv(path, header=None,
                     names=["date", "amount", "null", "type", "store"])
    txns = []
    for _, row in df.iterrows():
        store = str(row["store"]).lower().strip()
        if _is_ignorable(store):
            continue
        amount = float(row["amount"])
        txn_type = TxnType.EXPENSE if amount < 0 else TxnType.INCOME
        txns.append(Transaction(
            date=pd.to_datetime(row["date"]).date(),
            amount=abs(amount),
            store_raw=store,
            sub_description=store,
            txn_type=txn_type,
            source_file=Path(path).name,
        ))
    return txns


def detect_and_parse(path: str | Path) -> list[Transaction]:
    """Auto-detect CSV format and parse."""
    path = Path(path)
    df_peek = pd.read_csv(path, nrows=1)

    if "Balance" in df_peek.columns:
        return parse_scotia_new_debit(path)
    elif "Type of Transaction" in df_peek.columns:
        return parse_scotia_new_credit(path)
    else:
        # Old format — detect by column count
        df_cols = pd.read_csv(path, header=None, nrows=1)
        if len(df_cols.columns) == 5:
            return parse_scotia_old_debit(path)
        else:
            return parse_scotia_old_credit(path)


def parse_directory(csv_dir: str | Path) -> list[Transaction]:
    """Parse all CSVs in a directory."""
    csv_dir = Path(csv_dir)
    txns = []
    for f in sorted(csv_dir.glob("*.csv")):
        txns.extend(detect_and_parse(f))
    return txns
