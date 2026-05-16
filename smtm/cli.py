"""CLI entry point for showMeTheMoney."""
import argparse
import sys
from pathlib import Path

from . import categorizer, database, parsers, reports
from .models import TxnType


def main():
    parser = argparse.ArgumentParser(
        prog="smtm",
        description="showMeTheMoney — transaction categorizer and budget tool",
    )
    parser.add_argument(
        "--csv-dir", default="data/new",
        help="Directory containing bank CSV files (default: data/new)",
    )
    parser.add_argument(
        "--db-dir", default="data/db",
        help="Directory containing category database (default: data/db)",
    )
    parser.add_argument(
        "--out-dir", default="data/output",
        help="Output directory for reports (default: data/output)",
    )
    parser.add_argument(
        "--batch", action="store_true",
        help="Run in batch mode (no interactive prompts)",
    )
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load database
    db = database.load_db(args.db_dir)
    if not db.categories:
        print("ERROR: No expense categories found. Run setup first.")
        sys.exit(1)

    # Parse CSVs
    csv_dir = Path(args.csv_dir)
    csv_files = list(csv_dir.glob("*.csv"))
    if not csv_files:
        print(f"No CSV files found in {csv_dir}")
        sys.exit(1)

    print(f"Parsing {len(csv_files)} file(s) from {csv_dir}/")
    txns = parsers.parse_directory(csv_dir)
    print(f"  {len(txns)} transactions loaded")

    # Separate income and expenses
    expenses = [t for t in txns if t.txn_type == TxnType.EXPENSE]
    income = [t for t in txns if t.txn_type == TxnType.INCOME]
    print(f"  {len(expenses)} expenses, {len(income)} income")

    # Categorize
    classified, unclassified = categorizer.categorize_batch(expenses, db)
    pct = len(classified) / len(expenses) * 100 if expenses else 0
    print(f"  {len(classified)} classified ({pct:.0f}%), "
          f"{len(unclassified)} unclassified")

    # Handle unclassified
    if unclassified:
        if args.batch:
            uncl_path = out_dir / "unclassified.csv"
            reports.to_csv(unclassified, str(uncl_path))
            print(f"  Unclassified -> {uncl_path}")
        else:
            # Interactive mode: prompt for each unknown merchant
            _interactive_classify(unclassified, db, classified)
            database.save_db(db, args.db_dir)

    # Generate reports
    summary = reports.monthly_summary(classified)
    if not summary.empty:
        summary_path = out_dir / "monthly_summary.csv"
        summary.to_csv(str(summary_path))
        print(f"\n{'='*60}")
        print("MONTHLY EXPENSE SUMMARY")
        print(f"{'='*60}")
        print(summary.to_string())

        print(f"\n{'='*60}")
        print("MONTHLY AVERAGES")
        print(f"{'='*60}")
        avgs = reports.category_averages(summary)
        for cat, avg in avgs.items():
            print(f"  {cat:<20} ${avg:,.0f}")
        total = sum(avgs.values())
        print(f"  {'TOTAL':<20} ${total:,.0f}")

    # Income report
    if income:
        inc_summary = reports.income_summary(income)
        if not inc_summary.empty:
            inc_path = out_dir / "income_summary.csv"
            inc_summary.to_csv(str(inc_path))
            print(f"\n  Income summary -> {inc_path}")

    # Export all classified
    all_path = out_dir / "classified.csv"
    reports.to_csv(classified, str(all_path))
    print(f"  Classified transactions -> {all_path}")


def _interactive_classify(
    unclassified: list, db: database.CategoryDB, classified: list
):
    """Prompt user to classify unknown merchants."""
    print(f"\n{len(unclassified)} unclassified transactions:")
    print(f"Categories: {', '.join(db.categories)}")

    seen = set()
    for txn in unclassified:
        key = txn.store_normalized or txn.store_raw
        if key in seen:
            # Already classified this merchant in this session
            if key in db.store_to_category:
                txn.category = db.store_to_category[key][0]
                classified.append(txn)
            continue
        seen.add(key)

        print(f"\n  Store: {txn.store_raw}")
        print(f"  Amount: ${txn.amount:.2f} | Date: {txn.date}")
        print(f"  Options: {', '.join(f'({i}) {c}' for i, c in enumerate(db.categories))}")
        print(f"  (s) skip | (q) quit interactive")

        choice = input("  Category: ").strip().lower()
        if choice == "q":
            break
        elif choice == "s":
            continue
        else:
            try:
                idx = int(choice)
                cat = db.categories[idx]
                db.store_to_category[key] = [cat]
                txn.category = cat
                classified.append(txn)
                # Apply to all other txns with same store
                for other in unclassified:
                    norm = other.store_normalized or other.store_raw
                    if norm == key and other is not txn:
                        other.category = cat
                        classified.append(other)
            except (ValueError, IndexError):
                print("  Invalid choice, skipping.")


if __name__ == "__main__":
    main()
