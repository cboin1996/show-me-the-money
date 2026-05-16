"""Bank CSV adapter registry with auto-detection."""

from pathlib import Path

import pandas as pd

from ..models import Transaction
from .base import BaseAdapter
from .scotia_credit import ScotiaCreditNewAdapter, ScotiaCreditOldAdapter
from .scotia_debit import ScotiaDebitNewAdapter, ScotiaDebitOldAdapter

ADAPTERS: list[type[BaseAdapter]] = [
    ScotiaDebitNewAdapter,
    ScotiaCreditNewAdapter,
    ScotiaDebitOldAdapter,
    ScotiaCreditOldAdapter,
]


def detect_and_parse(path: str | Path) -> list[Transaction]:
    path = Path(path)
    try:
        peek = pd.read_csv(path, nrows=2)
    except Exception:
        peek = pd.read_csv(path, header=None, nrows=2)

    for adapter_cls in ADAPTERS:
        adapter = adapter_cls()
        if adapter.can_parse(path, peek):
            return adapter.parse(path)

    print(f"  WARNING: No adapter matched {path.name}, skipping")
    return []


def parse_directory(csv_dir: str | Path) -> list[Transaction]:
    csv_dir = Path(csv_dir)
    txns: list[Transaction] = []
    for f in sorted(csv_dir.glob("*.csv")):
        txns.extend(detect_and_parse(f))
    return txns
