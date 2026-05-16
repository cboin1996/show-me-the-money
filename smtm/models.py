"""Core data models."""
from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class TxnType(Enum):
    EXPENSE = "expense"
    INCOME = "income"


@dataclass
class Transaction:
    date: date
    amount: float
    store_raw: str
    store_normalized: str = ""
    category: str = ""
    txn_type: TxnType = TxnType.EXPENSE
    source_file: str = ""
    sub_description: str = ""

    @property
    def month(self) -> str:
        return self.date.strftime("%Y-%m")


@dataclass
class CategoryDB:
    """In-memory representation of the merchant database."""
    categories: list[str] = field(default_factory=list)
    store_to_category: dict[str, list[str]] = field(default_factory=dict)
    store_pairs: dict[str, str] = field(default_factory=dict)
