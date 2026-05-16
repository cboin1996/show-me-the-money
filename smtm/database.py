"""Database read/write for JSON store mappings."""
import json
from pathlib import Path

from .models import CategoryDB

EXPENSES_FILE = "expenses.json"
STORES_FILE = "storesWithExpenses.json"
PAIRS_FILE = "storePairs.json"


def load_db(db_dir: str | Path) -> CategoryDB:
    """Load the category database from a directory."""
    db_dir = Path(db_dir)
    db = CategoryDB()

    exp_path = db_dir / EXPENSES_FILE
    if exp_path.exists():
        with open(exp_path) as f:
            data = json.load(f)
        db.categories = data.get("expense", [])

    stores_path = db_dir / STORES_FILE
    if stores_path.exists():
        with open(stores_path) as f:
            db.store_to_category = json.load(f)

    pairs_path = db_dir / PAIRS_FILE
    if pairs_path.exists():
        with open(pairs_path) as f:
            db.store_pairs = json.load(f)

    return db


def save_db(db: CategoryDB, db_dir: str | Path) -> None:
    """Persist the category database to disk."""
    db_dir = Path(db_dir)
    db_dir.mkdir(parents=True, exist_ok=True)

    with open(db_dir / EXPENSES_FILE, "w") as f:
        json.dump({"expense": db.categories}, f, indent=2)

    with open(db_dir / STORES_FILE, "w") as f:
        json.dump(db.store_to_category, f, indent=2)

    with open(db_dir / PAIRS_FILE, "w") as f:
        json.dump(db.store_pairs, f, indent=2)
