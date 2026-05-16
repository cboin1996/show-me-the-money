"""Base adapter interface for bank CSV parsing."""
from abc import ABC, abstractmethod
from pathlib import Path

import pandas as pd

from ..models import Transaction


COMMON_IGNORABLE = [
    "mb-credit card/loc pay",
    "mb-transfer",
    "pc to",
    "pc from",
    "mb-cash advance",
    "mb - cash advance",
    "pc - payment",
    "customer transfer dr.",
    "customer transfer cr.",
    "crd. card bill payment",
]


def is_ignorable(store: str, sub: str, patterns: list[str]) -> bool:
    combined = f"{store} | {sub}".lower()
    return any(pat in combined for pat in patterns)


def is_transfer_withdrawal(description: str, sub: str) -> bool:
    """Check if a withdrawal/deposit is an inter-account transfer (ignorable)
    vs a real transaction (keep)."""
    sub_lower = sub.lower()
    transfer_subs = [
        "interac e-transfer",
        "mb-transfer",
        "mb-credit card",
        "pc to",
        "pc from",
    ]
    return any(t in sub_lower for t in transfer_subs)


class BaseAdapter(ABC):
    name: str

    @abstractmethod
    def can_parse(self, path: Path, peek_df: pd.DataFrame) -> bool:
        ...

    @abstractmethod
    def parse(self, path: str | Path) -> list[Transaction]:
        ...

    @abstractmethod
    def ignorable_patterns(self) -> list[str]:
        ...
