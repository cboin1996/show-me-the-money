"""Scotia credit card CSV adapters (old 3-col and new 7-col formats)."""
from pathlib import Path

import pandas as pd

from ..models import Transaction, TxnType
from .base import BaseAdapter, COMMON_IGNORABLE, is_ignorable


class ScotiaCreditNewAdapter(BaseAdapter):
    """New format: 7 cols with headers (Filter, Date, Description,
    Sub-description, Status, Type of Transaction, Amount)."""
    name = "scotia_credit_new"

    def can_parse(self, path: Path, peek_df: pd.DataFrame) -> bool:
        return (
            "Type of Transaction" in peek_df.columns
            and "Balance" not in peek_df.columns
        )

    def ignorable_patterns(self) -> list[str]:
        return COMMON_IGNORABLE + ["payment from"]

    def parse(self, path: str | Path) -> list[Transaction]:
        path = Path(path)
        df = pd.read_csv(path)
        txns: list[Transaction] = []

        for _, row in df.iterrows():
            store = str(row.get("Description", "")).lower().strip()
            sub = str(row.get("Sub-description", "") or "").lower().strip()

            if is_ignorable(store, sub, self.ignorable_patterns()):
                continue

            amount = float(row["Amount"])
            txn_type = (
                TxnType.EXPENSE
                if row["Type of Transaction"] == "Debit"
                else TxnType.INCOME
            )

            txns.append(Transaction(
                date=pd.to_datetime(row["Date"]).date(),
                amount=abs(amount),
                store_raw=store,
                sub_description=sub,
                txn_type=txn_type,
                source_file=path.name,
            ))
        return txns


class ScotiaCreditOldAdapter(BaseAdapter):
    """Old format: 3 cols, no headers (Date, Store, Amount)."""
    name = "scotia_credit_old"

    def can_parse(self, path: Path, peek_df: pd.DataFrame) -> bool:
        try:
            df = pd.read_csv(path, header=None, nrows=1)
            return len(df.columns) == 3
        except Exception:
            return False

    def ignorable_patterns(self) -> list[str]:
        return COMMON_IGNORABLE + ["payment from"]

    def parse(self, path: str | Path) -> list[Transaction]:
        path = Path(path)
        df = pd.read_csv(path, header=None, names=["date", "store", "amount"])
        if df.empty:
            return []

        txns: list[Transaction] = []
        for _, row in df.iterrows():
            store = str(row["store"]).lower().strip()

            if is_ignorable(store, "", self.ignorable_patterns()):
                continue

            try:
                amount = float(row["amount"])
            except (ValueError, TypeError):
                continue

            txn_type = TxnType.INCOME if amount > 0 else TxnType.EXPENSE

            txns.append(Transaction(
                date=pd.to_datetime(row["date"]).date(),
                amount=abs(amount),
                store_raw=store,
                txn_type=txn_type,
                source_file=path.name,
            ))
        return txns
