"""CLI entry point for showMeTheMoney."""

import argparse
import sys
from pathlib import Path

from .db import Database
from .models import TxnType

DEFAULT_DB_PATH = "data/smtm.db"
DEFAULT_CSV_DIR = "data/new"
DEFAULT_JSON_DIR = "data/db"
DEFAULT_ARCHIVE_DIR = "data/archive"
DEFAULT_OUTPUT_DIR = "data/output"


def get_db(args) -> Database:
    db = Database(getattr(args, "db_path", DEFAULT_DB_PATH))
    db.initialize()
    json_dir = Path(getattr(args, "json_dir", DEFAULT_JSON_DIR))
    if json_dir.exists() and (json_dir / "storesWithExpenses.json").exists():
        rules = db.get_category_rules()
        if not rules:
            print("Migrating legacy JSON database...")
            db.migrate_from_json(json_dir)
    return db


def cmd_import(args):
    from .importer import import_directory, print_import_summary

    db = get_db(args)
    category_db = db.load_category_db()
    csv_dir = Path(args.csv_dir)
    archive_dir = Path(args.archive_dir) if args.archive else None

    if not csv_dir.exists():
        print(f"Directory not found: {csv_dir}")
        sys.exit(1)

    csv_files = list(csv_dir.glob("*.csv"))
    if not csv_files:
        print(f"No CSV files in {csv_dir}")
        sys.exit(1)

    print(f"Importing {len(csv_files)} file(s) from {csv_dir}/")
    results = import_directory(db, csv_dir, category_db, archive_dir)
    print_import_summary(results)

    stats = db.get_stats()
    print(
        f"\n  DB total: {stats['total']} transactions "
        f"({stats['expenses']} expenses, {stats['income']} income)"
    )
    print(f"  Classification: {stats['classification_rate']:.0f}%")
    if stats["date_min"] and stats["date_max"]:
        print(f"  Date range: {stats['date_min']} to {stats['date_max']}")

    db.close()


def cmd_profile(args):
    from collections import Counter, defaultdict

    from .adapters import parse_directory
    from .categorizer import categorize_batch

    db = get_db(args)
    category_db = db.load_category_db()
    csv_dir = Path(args.csv_dir)

    csv_files = list(csv_dir.glob("*.csv"))
    if not csv_files:
        print(f"No CSV files found in {csv_dir}")
        sys.exit(1)

    print(f"Profiling {len(csv_files)} file(s) from {csv_dir}/\n")
    txns = parse_directory(csv_dir)

    expenses = [t for t in txns if t.txn_type == TxnType.EXPENSE]
    income = [t for t in txns if t.txn_type == TxnType.INCOME]
    dates = [t.date for t in txns]

    print(f"  Date range: {min(dates)} to {max(dates)}")
    print(
        f"  Span: {(max(dates) - min(dates)).days} days "
        f"({(max(dates) - min(dates)).days / 30:.1f} months)"
    )
    print(f"  Total: {len(txns)} transactions")
    print(f"  Expenses: {len(expenses)}")
    print(f"  Income: {len(income)}")

    classified, unclassified = categorize_batch(expenses, category_db)
    pct = len(classified) / len(expenses) * 100 if expenses else 0
    print(
        f"\n  Classification rate: {pct:.0f}% " f"({len(classified)}/{len(expenses)})"
    )
    print(f"  Unclassified: {len(unclassified)} transactions")

    if unclassified:
        store_counts = Counter(
            (t.store_normalized or t.store_raw) for t in unclassified
        )
        store_amounts: dict[str, float] = {}
        for t in unclassified:
            key = t.store_normalized or t.store_raw
            store_amounts[key] = store_amounts.get(key, 0) + t.amount

        print(
            f"\n  Top {min(20, len(store_counts))} unclassified " f"(by total spend):"
        )
        sorted_stores = sorted(store_amounts.items(), key=lambda x: -x[1])
        for store, amount in sorted_stores[:20]:
            count = store_counts[store]
            print(f"    ${amount:>8,.2f} ({count:>2}x)  {store}")

        total_uncl = sum(t.amount for t in unclassified)
        print(f"\n  Total unclassified spend: ${total_uncl:,.0f}")

    print(f"\n  Source files:")
    by_file: dict[str, list] = defaultdict(list)
    for t in txns:
        by_file[t.source_file].append(t)
    for fname, file_txns in sorted(by_file.items()):
        file_dates = [t.date for t in file_txns]
        print(
            f"    {fname}: {len(file_txns)} txns "
            f"({min(file_dates)} to {max(file_dates)})"
        )

    db.close()


def cmd_suggest(args):
    db = get_db(args)
    category_db = db.load_category_db()

    txns = db.get_expenses()
    if not txns:
        from .adapters import parse_directory
        from .categorizer import categorize_batch

        csv_dir = Path(args.csv_dir)
        all_txns = parse_directory(csv_dir)
        expenses = [t for t in all_txns if t.txn_type == TxnType.EXPENSE]
        _, unclassified = categorize_batch(expenses, category_db)
    else:
        unclassified = [t for t in txns if not t.category]

    if not unclassified:
        print("No unclassified transactions. You're at 100%!")
        db.close()
        return

    from .categorizer import KEYWORD_SUGGESTIONS

    keyword_map = KEYWORD_SUGGESTIONS

    from collections import Counter

    store_amounts: dict[str, float] = {}
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
        print("No suggestions available. Classify manually with " "interactive mode.")
        db.close()
        return

    print(f"Suggested categories for {len(suggestions)} merchants:\n")
    sorted_sugg = sorted(suggestions.items(), key=lambda x: -store_amounts[x[0]])
    for store, cat in sorted_sugg:
        amt = store_amounts[store]
        cnt = store_counts[store]
        print(f"  {cat:<16} ${amt:>7,.2f} ({cnt}x)  {store}")

    total_suggested = sum(store_amounts[s] for s in suggestions)
    print(f"\n  Would classify: ${total_suggested:,.0f} more")

    if args.apply:
        for store, cat in suggestions.items():
            db.add_category_rule(store, cat, "exact")
        updated = db.recategorize_all(db.load_category_db())
        print(
            f"\n  Applied {len(suggestions)} new rules. "
            f"Re-categorized {updated} transactions."
        )

    db.close()


def cmd_report(args):
    db = get_db(args)

    if args.pdf:
        from .pdf_report import generate_pdf
        from .server import compute_analytics

        txns = db.get_all_transactions()
        stats = db.get_stats()
        budgets = db.get_budgets()
        analytics = compute_analytics(txns, budgets)
        output = Path(args.output or f"{DEFAULT_OUTPUT_DIR}/report.pdf")
        output.parent.mkdir(parents=True, exist_ok=True)
        generate_pdf(txns, stats, budgets, analytics, output)
        print(f"PDF report generated: {output}")
    elif args.html:
        from .dashboard import generate_dashboard

        txns = db.get_all_transactions()
        budgets = db.get_budgets()
        stats = db.get_stats()
        output = Path(args.output or f"{DEFAULT_OUTPUT_DIR}/dashboard.html")
        output.parent.mkdir(parents=True, exist_ok=True)
        generate_dashboard(txns, budgets, stats, str(output))
        print(f"Dashboard generated: {output}")
    else:
        from . import reports

        expenses = db.get_expenses()
        income = db.get_income()

        summary = reports.monthly_summary(expenses)
        if not summary.empty:
            out_dir = Path(args.output or DEFAULT_OUTPUT_DIR)
            out_dir.mkdir(parents=True, exist_ok=True)
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

    db.close()


def cmd_budget(args):
    db = get_db(args)

    if args.budget_action == "set":
        for assignment in args.assignments:
            cat, amt = assignment.split("=")
            db.set_budget(args.month, cat.strip(), float(amt.strip()))
            print(f"  {args.month} {cat.strip()} = ${float(amt.strip()):,.0f}")

    elif args.budget_action == "copy":
        count = db.copy_budget(args.from_month, args.to_month)
        print(
            f"  Copied {count} budget entries from "
            f"{args.from_month} to {args.to_month}"
        )

    elif args.budget_action == "show":
        month = getattr(args, "month", None)
        budgets = db.get_budgets(month)
        if not budgets:
            print("  No budgets set.")
        else:
            current_month = ""
            for b in budgets:
                if b["month"] != current_month:
                    current_month = b["month"]
                    print(f"\n  {current_month}:")
                print(f"    {b['category']:<20} ${b['amount']:,.0f}")

    db.close()


def cmd_history(args):
    db = get_db(args)
    history = db.get_import_history()
    if not history:
        print("  No imports yet.")
    else:
        for h in history:
            print(
                f"  {h['imported_at']}  {h['source_file']:<30} "
                f"{h['row_count']} rows, {h['new_count']} new"
            )
    db.close()


def cmd_delete(args):
    db = get_db(args)
    for uuid in args.uuids:
        if db.soft_delete(uuid):
            print(f"  Deleted {uuid[:8]}...")
        else:
            print(f"  Not found: {uuid[:8]}...")
    db.close()


def cmd_reimburse(args):
    db = get_db(args)

    if args.reimburse_action == "add":
        label = args.label or args.pattern
        db.add_reimburser(args.pattern, label, args.match_type)
        print(f"  Added reimburser: {args.pattern} ({args.match_type})")
    elif args.reimburse_action == "remove":
        if db.remove_reimburser(args.pattern):
            print(f"  Removed: {args.pattern}")
        else:
            print(f"  Not found: {args.pattern}")
    elif args.reimburse_action == "list":
        reimbursers = db.get_reimbursers()
        if not reimbursers:
            print("  No reimbursers configured.")
        else:
            print(f"  {'Pattern':<30} {'Label':<20} {'Match'}")
            print(f"  {'-'*30} {'-'*20} {'-'*10}")
            for r in reimbursers:
                print(f"  {r['pattern']:<30} {r['label']:<20} {r['match_type']}")
    elif args.reimburse_action == "pending":
        pending = db.get_pending_reimbursements()
        if not pending:
            print("  No pending reimbursements.")
        else:
            print(f"  {'Date':<12} {'From':<25} {'Amount':<12} UUID")
            print(f"  {'-'*12} {'-'*25} {'-'*12} {'-'*8}")
            for p in pending:
                print(
                    f"  {p['date']:<12} {p['reimburser']:<25} "
                    f"${p['amount']:<11,.2f} {p['uuid'][:8]}"
                )
    elif args.reimburse_action == "link":
        if db.link_transactions(args.expense_uuid, args.income_uuid):
            print(f"  Linked {args.income_uuid[:8]}... -> {args.expense_uuid[:8]}...")
        else:
            print("  Failed: transactions not found or already linked.")

    db.close()


def cmd_serve(args):
    from .server import run_server

    run_server(
        db_path=args.db_path,
        host=args.host,
        port=args.port,
        csv_dir=args.csv_dir,
    )


def main():
    parser = argparse.ArgumentParser(
        prog="smtm",
        description="showMeTheMoney — transaction categorizer and budget tool",
    )
    parser.add_argument(
        "--db-path",
        default=DEFAULT_DB_PATH,
        help="Path to SQLite database",
    )
    parser.add_argument(
        "--csv-dir",
        default=DEFAULT_CSV_DIR,
        help="Directory containing bank CSV files",
    )
    parser.add_argument(
        "--json-dir",
        default=DEFAULT_JSON_DIR,
        help="Legacy JSON database directory (for migration)",
    )

    sub = parser.add_subparsers(dest="command")

    # import
    imp = sub.add_parser("import", help="Import and categorize transactions")
    imp.add_argument("--batch", action="store_true", help="Non-interactive mode")
    imp.add_argument(
        "--archive", action="store_true", help="Copy imported files to archive"
    )
    imp.add_argument("--archive-dir", default=DEFAULT_ARCHIVE_DIR)

    # profile
    sub.add_parser("profile", help="Preview CSV data without importing")

    # suggest
    sug = sub.add_parser("suggest", help="Suggest categories for unknowns")
    sug.add_argument(
        "--apply", action="store_true", help="Apply suggestions to the database"
    )

    # report
    rep = sub.add_parser("report", help="Generate reports")
    rep.add_argument("--html", action="store_true", help="Generate HTML dashboard")
    rep.add_argument("--pdf", action="store_true", help="Generate PDF report")
    rep.add_argument("--output", "-o", help="Output path")

    # budget
    budg = sub.add_parser("budget", help="Manage monthly budgets")
    budg_sub = budg.add_subparsers(dest="budget_action")

    budg_set = budg_sub.add_parser("set", help="Set budget amounts")
    budg_set.add_argument("month", help="Month (YYYY-MM)")
    budg_set.add_argument("assignments", nargs="+", help="Category=Amount pairs")

    budg_copy = budg_sub.add_parser("copy", help="Copy budget between months")
    budg_copy.add_argument("from_month")
    budg_copy.add_argument("to_month")

    budg_show = budg_sub.add_parser("show", help="Show budgets")
    budg_show.add_argument("month", nargs="?", help="Filter by month")

    # history
    sub.add_parser("history", help="Show import history")

    # delete
    dlt = sub.add_parser("delete", help="Soft-delete transactions")
    dlt.add_argument("uuids", nargs="+", help="Transaction UUIDs")

    # reimburse
    reimb = sub.add_parser("reimburse", help="Manage reimbursers and pending offsets")
    reimb_sub = reimb.add_subparsers(dest="reimburse_action")

    reimb_add = reimb_sub.add_parser("add", help="Add a known reimburser")
    reimb_add.add_argument("pattern", help="Store name pattern to match")
    reimb_add.add_argument("--label", help="Display label")
    reimb_add.add_argument(
        "--match-type", default="substring", choices=["exact", "substring"]
    )

    reimb_rm = reimb_sub.add_parser("remove", help="Remove a reimburser")
    reimb_rm.add_argument("pattern", help="Pattern to remove")

    reimb_sub.add_parser("list", help="List configured reimbursers")
    reimb_sub.add_parser("pending", help="Show pending reimbursements")

    reimb_link = reimb_sub.add_parser("link", help="Link income to expense")
    reimb_link.add_argument("income_uuid", help="Income transaction UUID")
    reimb_link.add_argument("expense_uuid", help="Expense transaction UUID")

    # serve
    srv = sub.add_parser("serve", help="Start interactive web dashboard")
    srv.add_argument("--host", default="127.0.0.1", help="Bind address")
    srv.add_argument("--port", type=int, default=8000, help="Port number")

    args = parser.parse_args()

    commands = {
        "import": cmd_import,
        "profile": cmd_profile,
        "suggest": cmd_suggest,
        "report": cmd_report,
        "budget": cmd_budget,
        "history": cmd_history,
        "delete": cmd_delete,
        "reimburse": cmd_reimburse,
        "serve": cmd_serve,
    }

    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
