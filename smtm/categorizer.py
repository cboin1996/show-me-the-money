"""Transaction categorization with fuzzy string matching."""
import re
from dataclasses import dataclass

from .models import CategoryDB, Transaction

# Regex to extract meaningful store name from bank strings
# Strips location suffixes, card numbers, phone numbers
_CLEAN_RE = re.compile(
    r"(\d{3}[-.]?\d{3}[-.]?\d{4})"  # phone numbers
    r"|(\s+(on|bc|ab|qc|mb|sk|ns|nb|pe|nl|nt|nu|yt|ca|tx|co|mn)\s*$)"  # province/state codes at end
    r"|(\s*\(apple pay\)\s*$)"  # (Apple Pay) suffix
    r"|(\s+\d{5,})"  # long numbers (postal, card)
    r"|(\s{2,}.*$)"  # anything after double space (city/location)
)


def clean_store_name(raw: str) -> str:
    """Strip location, phone, and payment method from bank strings."""
    cleaned = _CLEAN_RE.sub("", raw.lower().strip())
    # Remove trailing whitespace and common suffixes
    cleaned = cleaned.strip().rstrip(".,- ")
    return cleaned


@dataclass
class CategorizationResult:
    category: str | None
    normalized_store: str
    confidence: str  # "exact", "substring", "fuzzy", "unknown"


def categorize(txn: Transaction, db: CategoryDB) -> CategorizationResult:
    """Categorize a transaction against the database.

    Strategy (in order):
    1. Exact match on raw store name
    2. Exact match on cleaned store name
    3. Store pair normalization → exact match
    4. Substring match (known key found in raw name)
    5. Unknown
    """
    raw = txn.store_raw.lower().strip()
    cleaned = clean_store_name(raw)
    sub = txn.sub_description.lower().strip()

    # 1. Exact match on raw
    if raw in db.store_to_category:
        return CategorizationResult(
            db.store_to_category[raw][0], raw, "exact"
        )

    # 2. Exact match on cleaned
    if cleaned in db.store_to_category:
        return CategorizationResult(
            db.store_to_category[cleaned][0], cleaned, "exact"
        )

    # 3. Store pair normalization
    if raw in db.store_pairs:
        norm = db.store_pairs[raw]
        if norm in db.store_to_category:
            return CategorizationResult(
                db.store_to_category[norm][0], norm, "exact"
            )
    if cleaned in db.store_pairs:
        norm = db.store_pairs[cleaned]
        if norm in db.store_to_category:
            return CategorizationResult(
                db.store_to_category[norm][0], norm, "exact"
            )

    # 4. Substring match — check if any known key appears in the
    #    raw store name or sub-description
    for key, cats in db.store_to_category.items():
        if len(key) < 4:
            continue  # skip very short keys to avoid false matches
        if key in raw or key in cleaned or key in sub:
            return CategorizationResult(cats[0], key, "substring")

    # 5. Unknown
    return CategorizationResult(None, cleaned or raw, "unknown")


def categorize_batch(
    txns: list[Transaction], db: CategoryDB
) -> tuple[list[Transaction], list[Transaction]]:
    """Categorize a list of transactions. Returns (classified, unclassified)."""
    classified = []
    unclassified = []

    for txn in txns:
        result = categorize(txn, db)
        txn.store_normalized = result.normalized_store
        txn.category = result.category or ""
        if result.category:
            classified.append(txn)
        else:
            unclassified.append(txn)

    return classified, unclassified
