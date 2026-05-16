"""CLI entry point for showMeTheMoney."""
import argparse
import sys
from pathlib import Path

from . import categorizer, database, parsers, reports
from .models import TxnType


def cmd_import(args):
    """Import and categorize transactions."""
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    db = database.load_db(args.db_dir)
    if not db.categories:
        print("ERROR: No expense categories found in DB.")
        sys.exit(1)

    csv_dir = Path(args.csv_dir)
    csv_files = list(csv_dir.glob("*.csv"))
    if not csv_files:
        print(f"No CSV files found in {csv_dir}")
        sys.exit(1)

    print(f"Parsing {len(csv_files)} file(s) from {csv_dir}/")
    txns = parsers.parse_directory(csv_dir)
    print(f"  {len(txns)} transactions loaded")

    expenses = [t for t in txns if t.txn_type == TxnType.EXPENSE]
    income = [t for t in txns if t.txn_type == TxnType.INCOME]
    print(f"  {len(expenses)} expenses, {len(income)} income")

    classified, unclassified = categorizer.categorize_batch(expenses, db)
    pct = len(classified) / len(expenses) * 100 if expenses else 0
    print(f"  {len(classified)} classified ({pct:.0f}%), "
          f"{len(unclassified)} unclassified")

    if unclassified:
        if args.batch:
            uncl_path = out_dir / "unclassified.csv"
            reports.to_csv(unclassified, str(uncl_path))
            print(f"  Unclassified -> {uncl_path}")
        else:
            _interactive_classify(unclassified, db, classified)
            database.save_db(db, args.db_dir)

    # Reports
    summary = reports.monthly_summary(classified)
    if not summary.empty:
        summary.to_csv(str(out_dir / "monthly_summary.csv"))
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
        print(f"  {'TOTAL':<20} ${sum(avgs.values()):,.0f}")

    if income:
        inc_summary = reports.income_summary(income)
        if not inc_summary.empty:
            inc_summary.to_csv(str(out_dir / "income_summary.csv"))

    reports.to_csv(classified, str(out_dir / "classified.csv"))
    print(f"\n  Classified -> {out_dir / 'classified.csv'}")


def cmd_profile(args):
    """Profile CSVs: show date range, transaction counts, and
    unclassified merchants without importing."""
    db = database.load_db(args.db_dir)
    csv_dir = Path(args.csv_dir)
    csv_files = list(csv_dir.glob("*.csv"))

    if not csv_files:
        print(f"No CSV files found in {csv_dir}")
        sys.exit(1)

    print(f"Profiling {len(csv_files)} file(s) from {csv_dir}/\n")
    txns = parsers.parse_directory(csv_dir)

    expenses = [t for t in txns if t.txn_type == TxnType.EXPENSE]
    income = [t for t in txns if t.txn_type == TxnType.INCOME]
    dates = [t.date for t in txns]

    print(f"  Date range: {min(dates)} to {max(dates)}")
    print(f"  Span: {(max(dates) - min(dates)).days} days "
          f"({(max(dates) - min(dates)).days / 30:.1f} months)")
    print(f"  Total: {len(txns)} transactions")
    print(f"  Expenses: {len(expenses)}")
    print(f"  Income: {len(income)}")

    # Classification preview
    classified, unclassified = categorizer.categorize_batch(expenses, db)
    pct = len(classified) / len(expenses) * 100 if expenses else 0
    print(f"\n  Classification rate: {pct:.0f}% "
          f"({len(classified)}/{len(expenses)})")
    print(f"  Unclassified: {len(unclassified)} transactions")

    if unclassified:
        # Group by normalized store
        from collections import Counter
        store_counts = Counter(
            (t.store_normalized or t.store_raw) for t in unclassified
        )
        store_amounts = {}
        for t in unclassified:
            key = t.store_normalized or t.store_raw
            store_amounts[key] = store_amounts.get(key, 0) + t.amount

        print(f"\n  Top {min(20, len(store_counts))} unclassified "
              f"(by total spend):")
        sorted_stores = sorted(store_amounts.items(),
                               key=lambda x: -x[1])
        for store, amount in sorted_stores[:20]:
            count = store_counts[store]
            print(f"    ${amount:>8,.2f} ({count:>2}x)  {store}")

        total_uncl = sum(t.amount for t in unclassified)
        print(f"\n  Total unclassified spend: ${total_uncl:,.0f}")

    # Source file breakdown
    print(f"\n  Source files:")
    from collections import defaultdict
    by_file = defaultdict(list)
    for t in txns:
        by_file[t.source_file].append(t)
    for fname, file_txns in sorted(by_file.items()):
        file_dates = [t.date for t in file_txns]
        print(f"    {fname}: {len(file_txns)} txns "
              f"({min(file_dates)} to {max(file_dates)})")


def cmd_suggest(args):
    """Suggest categories for unclassified merchants and optionally
    apply them to the database."""
    db = database.load_db(args.db_dir)
    csv_dir = Path(args.csv_dir)

    txns = parsers.parse_directory(csv_dir)
    expenses = [t for t in txns if t.txn_type == TxnType.EXPENSE]
    _, unclassified = categorizer.categorize_batch(expenses, db)

    if not unclassified:
        print("No unclassified transactions. You're at 100%!")
        return

    # Keyword-based suggestions
    keyword_map = {
        "Dining": ["restaurant", "cafe", "coffee", "pizza", "sushi",
                   "burger", "grill", "kitchen", "bakery", "pub",
                   "bar", "brew", "tap", "lounge", "wine", "beer",
                   "pho", "taco", "noodle", "ramen", "wok", "diner",
                   "eatery", "bistro", "food", "eat"],
        "Groceries": ["market", "grocery", "iga", "safeway", "save-on",
                      "superstore", "no frills", "costco", "bulk barn",
                      "fresh", "farm", "organic"],
        "Transportation": ["gas", "petro", "esso", "shell", "chevron",
                           "parking", "transit", "compass", "uber",
                           "lyft", "taxi", "cab", "auto", "car wash"],
        "Travel": ["airline", "air ", "hotel", "hostel", "motel",
                   "resort", "airbnb", "vrbo", "ferry", "bcf",
                   "rental", "hertz", "avis", "expedia", "booking"],
        "Entertainment": ["ski", "snowboard", "mountain", "cinema",
                          "theatre", "theater", "concert", "ticket",
                          "game", "steam", "playstation", "xbox",
                          "museum", "gallery", "park"],
        "Shopping": ["sport", "athletic", "shoe", "cloth", "wear",
                     "fashion", "store", "shop", "mart", "hardware",
                     "electronics", "tech"],
        "Health": ["pharmacy", "drug", "medical", "dental", "clinic",
                   "physio", "chiro", "optical", "vision", "wellness",
                   "barber", "hair", "salon", "spa"],
    }

    from collections import Counter
    store_amounts = {}
    store_counts = Counter()
    for t in unclassified:
        key = t.store_normalized or t.store_raw
        store_amounts[key] = store_amounts.get(key, 0) + t.amount
        store_counts[key] += 1

    suggestions = {}
    for store in store_amounts:
        for category, keywords in keyword_map.items():
            if any(kw in store for kw in keywords):
                suggestions[store] = category
                break

    if not suggestions:
        print("No suggestions available. Use interactive mode "
              "(`smtm import`) to classify manually.")
        return

    print(f"Suggested categories for {len(suggestions)} merchants:\n")
    sorted_sugg = sorted(suggestions.items(),
                         key=lambda x: -store_amounts[x[0]])
    for store, cat in sorted_sugg:
        amt = store_amounts[store]
        cnt = store_counts[store]
        print(f"  {cat:<16} ${amt:>7,.2f} ({cnt}x)  {store}")

    total_suggested = sum(store_amounts[s] for s in suggestions)
    print(f"\n  Would classify: ${total_suggested:,.0f} more")

    if args.apply:
        for store, cat in suggestions.items():
            db.store_to_category[store] = [cat]
        database.save_db(db, args.db_dir)
        print(f"\n  Applied {len(suggestions)} new mappings to DB.")
        print(f"  Re-run `smtm import --batch` to regenerate reports.")
    else:
        print(f"\n  To apply: smtm suggest --apply")


def _interactive_classify(unclassified, db, classified):
    """Prompt user to classify unknown merchants."""
    print(f"\n{len(unclassified)} unclassified transactions:")
    print(f"Categories: {', '.join(f'({i}) {c}' for i, c in enumerate(db.categories))}")

    seen = set()
    for txn in unclassified:
        key = txn.store_normalized or txn.store_raw
        if key in seen:
            if key in db.store_to_category:
                txn.category = db.store_to_category[key][0]
                classified.append(txn)
            continue
        seen.add(key)

        print(f"\n  Store: {txn.store_raw}")
        print(f"  Amount: ${txn.amount:.2f} | Date: {txn.date}")
        print(f"  (s) skip | (q) quit")

        choice = input("  Category #: ").strip().lower()
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
                for other in unclassified:
                    norm = other.store_normalized or other.store_raw
                    if norm == key and other is not txn:
                        other.category = cat
                        classified.append(other)
            except (ValueError, IndexError):
                print("  Invalid, skipping.")


def main():
    parser = argparse.ArgumentParser(
        prog="smtm",
        description="showMeTheMoney — transaction categorizer and budget tool",
    )
    parser.add_argument(
        "--csv-dir", default="data/new",
        help="Directory containing bank CSV files",
    )
    parser.add_argument(
        "--db-dir", default="data/db",
        help="Directory containing category database",
    )
    parser.add_argument(
        "--out-dir", default="data/output",
        help="Output directory for reports",
    )

    sub = parser.add_subparsers(dest="command")

    # import command
    imp = sub.add_parser("import", help="Import and categorize transactions")
    imp.add_argument("--batch", action="store_true",
                     help="Non-interactive (skip prompts for unknowns)")

    # profile command
    sub.add_parser("profile", help="Preview data without importing")

    # suggest command
    sug = sub.add_parser("suggest",
                         help="Suggest categories for unknowns")
    sug.add_argument("--apply", action="store_true",
                     help="Apply suggestions to the database")

    args = parser.parse_args()

    if args.command == "import":
        cmd_import(args)
    elif args.command == "profile":
        cmd_profile(args)
    elif args.command == "suggest":
        cmd_suggest(args)
    else:
        # Default: run import in batch mode for backwards compat
        args.batch = True
        cmd_import(args)


if __name__ == "__main__":
    main()
