"""Transaction categorization with multi-strategy matching."""
import re
from dataclasses import dataclass

from .models import CategoryDB, Transaction

# v1 regex for extracting merchant from Scotia debit APOS/FPOS/OPOS strings
_SCOTIA_DEBIT_RE = re.compile(
    r"(?:(?<=(?:apos|fpos|opos) )(.*)(?=#))"
    r"|(?:(?<=(?:apos|fpos|opos) )(.*)\s{2,})"
    r"|(?:(?<=(?:apos|fpos|opos) )(.*))",
    re.IGNORECASE,
)

_CLEAN_RE = re.compile(
    r"(\d{3}[-.]?\d{3}[-.]?\d{4})"
    r"|(\s+(on|bc|ab|qc|mb|sk|ns|nb|pe|nl|nt|nu|yt|ca|tx|co|mn)\s*$)"
    r"|(\s*\(apple pay\)\s*$)"
    r"|(\s+\d{5,})"
    r"|(\s{2,}.*$)"
)

GENERIC_DESCRIPTIONS = frozenset([
    "pos purchase",
    "pre-authorized payment",
    "miscellaneous payment",
    "recurring payment",
    "cheque",
])


def clean_store_name(raw: str) -> str:
    """Strip location, phone, and payment method from bank strings."""
    s = raw.lower().strip()

    match = _SCOTIA_DEBIT_RE.search(s)
    if match:
        extracted = next((g for g in match.groups() if g), None)
        if extracted:
            s = extracted.strip()

    s = _CLEAN_RE.sub("", s).strip().rstrip(".,- ")
    return s


@dataclass
class CategorizationResult:
    category: str | None
    normalized_store: str
    confidence: str


def categorize(txn: Transaction, db: CategoryDB) -> CategorizationResult:
    """Categorize a transaction against the database.

    Strategy:
    1. Exact match on raw store name
    2. Exact match on cleaned store name
    3. Store pair normalization -> exact match
    4. Substring match (known key found in raw/cleaned/sub)
    5. Unknown
    """
    raw = txn.store_raw.lower().strip()
    cleaned = clean_store_name(raw)
    sub = txn.sub_description.lower().strip()
    sub_cleaned = clean_store_name(sub) if sub else ""

    skip_exact_raw = raw in GENERIC_DESCRIPTIONS

    if not skip_exact_raw and raw in db.store_to_category:
        return CategorizationResult(
            db.store_to_category[raw][0], raw, "exact"
        )

    if not skip_exact_raw and cleaned in db.store_to_category:
        return CategorizationResult(
            db.store_to_category[cleaned][0], cleaned, "exact"
        )

    for name in (raw, cleaned):
        if name in db.store_pairs:
            norm = db.store_pairs[name]
            if norm in db.store_to_category:
                return CategorizationResult(
                    db.store_to_category[norm][0], norm, "exact"
                )

    if sub_cleaned and sub_cleaned in db.store_to_category:
        return CategorizationResult(
            db.store_to_category[sub_cleaned][0], sub_cleaned, "exact"
        )
    if sub_cleaned and sub_cleaned in db.store_pairs:
        norm = db.store_pairs[sub_cleaned]
        if norm in db.store_to_category:
            return CategorizationResult(
                db.store_to_category[norm][0], norm, "exact"
            )

    search_strings = [s for s in (raw, cleaned, sub, sub_cleaned) if s]
    for key, cats in db.store_to_category.items():
        if len(key) < 4:
            continue
        if key in GENERIC_DESCRIPTIONS:
            continue
        if any(key in s for s in search_strings):
            return CategorizationResult(cats[0], key, "substring")

    best_name = sub_cleaned or cleaned or raw
    return CategorizationResult(None, best_name, "unknown")


def categorize_batch(
    txns: list[Transaction], db: CategoryDB
) -> tuple[list[Transaction], list[Transaction]]:
    """Categorize a list of transactions. Returns (classified, unclassified)."""
    classified: list[Transaction] = []
    unclassified: list[Transaction] = []

    for txn in txns:
        result = categorize(txn, db)
        txn.store_normalized = result.normalized_store
        txn.category = result.category or ""
        txn.confidence = result.confidence
        if result.category:
            classified.append(txn)
        else:
            unclassified.append(txn)

    return classified, unclassified
