"""Import pipeline: CSV -> parse -> deduplicate -> categorize -> SQLite."""

import shutil
from pathlib import Path

from .adapters import detect_and_parse
from .categorizer import categorize
from .db import Database
from .models import CategoryDB, Transaction


def _matches_import_filter(txn: Transaction, filters: list[dict]) -> bool:
    combined = f"{txn.store_raw} | {txn.sub_description}".lower()
    for f in filters:
        pat = f["pattern"].lower()
        if f["match_type"] == "exact":
            if txn.store_raw.lower() == pat:
                return True
        else:
            if pat in combined:
                return True
    return False


def import_file(
    db: Database,
    csv_path: Path,
    category_db: CategoryDB,
    archive_dir: Path | None = None,
) -> dict:
    """Import a single CSV file into the database.

    Returns a summary dict with counts.
    """
    file_hash = Database.hash_file(csv_path)

    if db.file_already_imported(file_hash):
        return {
            "file": csv_path.name,
            "status": "skipped",
            "reason": "already imported",
            "parsed": 0,
            "inserted": 0,
            "duplicates": 0,
        }

    txns = detect_and_parse(csv_path)
    filters = db.get_import_filters()
    if filters:
        txns = [t for t in txns if not _matches_import_filter(t, filters)]
    if not txns:
        return {
            "file": csv_path.name,
            "status": "empty",
            "reason": "no transactions parsed",
            "parsed": 0,
            "inserted": 0,
            "duplicates": 0,
        }

    for txn in txns:
        result = categorize(txn, category_db)
        txn.store_normalized = result.normalized_store
        txn.category = result.category or ""
        txn.confidence = result.confidence

    inserted, dupes = db.insert_transactions(txns)

    db.log_import(csv_path.name, file_hash, len(txns), inserted)

    if archive_dir and inserted > 0:
        archive_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(csv_path, archive_dir / csv_path.name)

    classified = sum(1 for t in txns if t.category)

    return {
        "file": csv_path.name,
        "status": "imported",
        "parsed": len(txns),
        "inserted": inserted,
        "duplicates": dupes,
        "classified": classified,
        "unclassified": len(txns) - classified,
    }


def import_directory(
    db: Database,
    csv_dir: Path,
    category_db: CategoryDB,
    archive_dir: Path | None = None,
) -> list[dict]:
    """Import all CSV files from a directory."""
    csv_files = sorted(csv_dir.glob("*.csv"))
    if not csv_files:
        print(f"No CSV files found in {csv_dir}")
        return []

    results = []
    for csv_path in csv_files:
        result = import_file(db, csv_path, category_db, archive_dir)
        results.append(result)

    return results


def print_import_summary(results: list[dict]):
    total_parsed = 0
    total_inserted = 0
    total_dupes = 0
    total_classified = 0
    total_unclassified = 0

    for r in results:
        status_icon = {
            "imported": "+",
            "skipped": "~",
            "empty": "!",
        }.get(r["status"], "?")

        print(f"  [{status_icon}] {r['file']}: ", end="")

        if r["status"] == "skipped":
            print(r["reason"])
            continue
        if r["status"] == "empty":
            print(r["reason"])
            continue

        print(
            f"{r['parsed']} parsed, {r['inserted']} new, "
            f"{r['duplicates']} dupes, "
            f"{r.get('classified', 0)} classified"
        )
        total_parsed += r["parsed"]
        total_inserted += r["inserted"]
        total_dupes += r["duplicates"]
        total_classified += r.get("classified", 0)
        total_unclassified += r.get("unclassified", 0)

    print(
        f"\n  Total: {total_parsed} parsed, {total_inserted} new, "
        f"{total_dupes} duplicates"
    )
    if total_inserted > 0:
        rate = total_classified / (total_classified + total_unclassified) * 100
        print(
            f"  Classification: {total_classified}/{total_inserted} " f"({rate:.0f}%)"
        )
