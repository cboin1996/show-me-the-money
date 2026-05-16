"""Generate a formatted financial summary report."""
from pathlib import Path

import pandas as pd

from .models import Transaction, TxnType
from . import reports


def generate_report(
    txns: list[Transaction],
    fixed_monthly: dict[str, float] | None = None,
    output_path: str = "data/output/financial_report.md",
) -> str:
    """Generate a markdown financial report.

    Args:
        txns: All parsed transactions
        fixed_monthly: Manual fixed expenses not in bank data
            (e.g., {"Rent (60% share)": 1830, "Storage": 120})
    """
    if fixed_monthly is None:
        fixed_monthly = {}

    expenses = [t for t in txns if t.txn_type == TxnType.EXPENSE]
    income = [t for t in txns if t.txn_type == TxnType.INCOME]

    summary = reports.monthly_summary(expenses)
    avgs = reports.category_averages(summary)
    months = len(summary) if not summary.empty else 1

    lines = []
    lines.append("# Financial Summary Report")
    lines.append("")
    lines.append(f"**Period:** {summary.index[0]} to {summary.index[-1]}"
                 if not summary.empty else "")
    lines.append(f"**Months analyzed:** {months}")
    lines.append(f"**Transactions:** {len(expenses)} expenses, "
                 f"{len(income)} income")
    lines.append("")

    # Income
    lines.append("## Monthly Income")
    lines.append("")
    if income:
        inc_summary = reports.income_summary(income)
        total_inc = sum(t.amount for t in income)
        lines.append(f"Average monthly income (from bank data): "
                     f"${total_inc / months:,.0f}")
        lines.append("")
    else:
        lines.append("No income transactions detected in bank data.")
        lines.append("")

    # Variable expenses (from bank data)
    lines.append("## Variable Expenses (from bank transactions)")
    lines.append("")
    lines.append("| Category | Monthly Avg |")
    lines.append("|----------|-------------|")
    variable_total = 0
    for cat, avg in avgs.items():
        lines.append(f"| {cat} | ${avg:,.0f} |")
        variable_total += avg
    lines.append(f"| **Subtotal** | **${variable_total:,.0f}** |")
    lines.append("")

    # Fixed expenses (manual input)
    if fixed_monthly:
        lines.append("## Fixed Monthly Expenses (not in bank data)")
        lines.append("")
        lines.append("| Expense | Amount |")
        lines.append("|---------|--------|")
        fixed_total = 0
        for name, amount in fixed_monthly.items():
            lines.append(f"| {name} | ${amount:,.0f} |")
            fixed_total += amount
        lines.append(f"| **Subtotal** | **${fixed_total:,.0f}** |")
        lines.append("")
    else:
        fixed_total = 0

    # Total
    grand_total = variable_total + fixed_total
    lines.append("## Monthly Total")
    lines.append("")
    lines.append(f"| | Amount |")
    lines.append(f"|--|--------|")
    lines.append(f"| Variable (bank) | ${variable_total:,.0f} |")
    lines.append(f"| Fixed (manual) | ${fixed_total:,.0f} |")
    lines.append(f"| **Total monthly expenses** | **${grand_total:,.0f}** |")
    lines.append("")

    # Monthly breakdown table
    if not summary.empty:
        lines.append("## Monthly Breakdown")
        lines.append("")
        lines.append(summary.to_markdown())
        lines.append("")

    report = "\n".join(lines)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(report)
    return report
