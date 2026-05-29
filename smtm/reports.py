"""Report generation from categorized transactions."""

from collections import defaultdict

import pandas as pd

from .models import Transaction, TxnType


def monthly_summary(txns: list[Transaction]) -> pd.DataFrame:
    """Generate monthly expense summary by category."""
    expenses = [t for t in txns if t.txn_type == TxnType.EXPENSE]
    if not expenses:
        return pd.DataFrame()

    rows = [
        {
            "month": t.month,
            "category": t.category or "Uncategorized",
            "amount": t.amount,
        }
        for t in expenses
    ]
    df = pd.DataFrame(rows)
    pivot = df.pivot_table(
        index="month", columns="category", values="amount", aggfunc="sum", fill_value=0
    )
    pivot["TOTAL"] = pivot.sum(axis=1)
    return pivot.sort_index()


def income_summary(txns: list[Transaction]) -> pd.DataFrame:
    """Generate monthly income summary."""
    income = [t for t in txns if t.txn_type == TxnType.INCOME]
    if not income:
        return pd.DataFrame()

    rows = [
        {
            "month": t.month,
            "source": t.store_normalized or t.store_raw,
            "amount": t.amount,
        }
        for t in income
    ]
    df = pd.DataFrame(rows)
    pivot = df.pivot_table(
        index="month", columns="source", values="amount", aggfunc="sum", fill_value=0
    )
    pivot["TOTAL"] = pivot.sum(axis=1)
    return pivot.sort_index()


def category_averages(summary: pd.DataFrame) -> dict[str, float]:
    """Compute monthly averages per category from a summary DataFrame."""
    if summary.empty:
        return {}
    cols = [c for c in summary.columns if c != "TOTAL"]
    return {
        col: summary[col].mean()
        for col in sorted(cols, key=lambda c: -summary[c].mean())
    }


def to_csv(txns: list[Transaction], path: str) -> None:
    """Export transactions to CSV."""
    rows = [
        {
            "date": t.date.isoformat(),
            "amount": t.amount,
            "store_raw": t.store_raw,
            "store_normalized": t.store_normalized,
            "category": t.category,
            "type": t.txn_type.value,
            "source_file": t.source_file,
        }
        for t in txns
    ]
    pd.DataFrame(rows).to_csv(path, index=False)
