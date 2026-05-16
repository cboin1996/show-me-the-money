"""Core data models."""
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from uuid import uuid4


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
    confidence: str = ""
    txn_type: TxnType = TxnType.EXPENSE
    source_file: str = ""
    sub_description: str = ""
    uuid: str = ""
    is_deleted: bool = False

    def __post_init__(self):
        if not self.uuid:
            self.uuid = str(uuid4())

    @property
    def month(self) -> str:
        return self.date.strftime("%Y-%m")


@dataclass
class CategoryDB:
    """In-memory representation of the merchant database."""
    categories: list[str] = field(default_factory=list)
    store_to_category: dict[str, list[str]] = field(default_factory=dict)
    store_pairs: dict[str, str] = field(default_factory=dict)
