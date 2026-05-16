"""Scotia debit CSV adapters (old 5-col and new 7-col formats)."""

from pathlib import Path

import pandas as pd

from ..models import Transaction, TxnType
from .base import (
    COMMON_IGNORABLE,
    BaseAdapter,
    is_ignorable,
    is_transfer_withdrawal,
)

GENERIC_DESCRIPTIONS = [
    "pos purchase",
    "pre-authorized payment",
    "miscellaneous payment",
    "recurring payment",
]


def _is_generic(desc: str) -> bool:
    return desc.lower().strip() in GENERIC_DESCRIPTIONS


class ScotiaDebitNewAdapter(BaseAdapter):
    """New format: 7+ cols with headers including Balance column.
    Description is often generic ('pos purchase') — real merchant
    is in Sub-description."""

    name = "scotia_debit_new"

    def can_parse(self, path: Path, peek_df: pd.DataFrame) -> bool:
        return "Balance" in peek_df.columns

    def ignorable_patterns(self) -> list[str]:
        return COMMON_IGNORABLE + [
            "free interac e-transfer",
            "interac e-transfer",
        ]

    def parse(self, path: str | Path) -> list[Transaction]:
        path = Path(path)
        df = pd.read_csv(path)
        txns: list[Transaction] = []

        for _, row in df.iterrows():
            desc = str(row.get("Description", "")).lower().strip()
            sub = str(row.get("Sub-description", "") or "").lower().strip()

            if is_ignorable(desc, sub, self.ignorable_patterns()):
                continue

            if desc in ("withdrawal", "deposit"):
                if is_transfer_withdrawal(desc, sub):
                    continue

            amount = float(row["Amount"])
            txn_type = TxnType.EXPENSE if amount < 0 else TxnType.INCOME

            if _is_generic(desc) and sub:
                store_raw = sub
            else:
                store_raw = desc

            txns.append(
                Transaction(
                    date=pd.to_datetime(row["Date"]).date(),
                    amount=abs(amount),
                    store_raw=store_raw,
                    sub_description=sub if store_raw != sub else "",
                    txn_type=txn_type,
                    source_file=path.name,
                )
            )
        return txns


class ScotiaDebitOldAdapter(BaseAdapter):
    """Old format: 5 cols, no headers (Date, Amount, Null, Type, Store)."""

    name = "scotia_debit_old"

    def can_parse(self, path: Path, peek_df: pd.DataFrame) -> bool:
        try:
            df = pd.read_csv(path, header=None, nrows=1)
            return len(df.columns) == 5
        except Exception:
            return False

    def ignorable_patterns(self) -> list[str]:
        return COMMON_IGNORABLE + [
            "free interac e-transfer",
            "interac e-transfer",
        ]

    def parse(self, path: str | Path) -> list[Transaction]:
        path = Path(path)
        df = pd.read_csv(
            path,
            header=None,
            names=["date", "amount", "null", "type", "store"],
        )
        txns: list[Transaction] = []

        for _, row in df.iterrows():
            store = str(row["store"]).lower().strip()
            txn_type_str = str(row.get("type", "")).lower().strip()

            if is_ignorable(store, "", self.ignorable_patterns()):
                continue

            amount = float(row["amount"])
            txn_type = TxnType.EXPENSE if amount < 0 else TxnType.INCOME

            txns.append(
                Transaction(
                    date=pd.to_datetime(row["date"]).date(),
                    amount=abs(amount),
                    store_raw=store,
                    sub_description=store,
                    txn_type=txn_type,
                    source_file=path.name,
                )
            )
        return txns
