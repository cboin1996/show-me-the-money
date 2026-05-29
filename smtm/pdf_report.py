"""PDF report generation for financial summary."""

from collections import defaultdict
from datetime import date
from pathlib import Path

from fpdf import FPDF

from .models import Transaction, TxnType


class FinanceReport(FPDF):
    def __init__(self, title: str = "Financial Report"):
        super().__init__()
        self.report_title = title
        self.set_auto_page_break(auto=True, margin=20)

    def header(self):
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(100, 100, 100)
        self.cell(0, 6, self.report_title, align="L")
        self.cell(0, 6, date.today().strftime("%B %d, %Y"), align="R", new_x="LMARGIN")
        self.ln(8)
        self.set_draw_color(200, 200, 200)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")

    def section_title(self, text: str):
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(30, 41, 59)
        self.ln(4)
        self.cell(0, 10, text, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def subsection_title(self, text: str):
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(51, 65, 85)
        self.ln(2)
        self.cell(0, 8, text, new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def kv_row(self, label: str, value: str):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(71, 85, 105)
        self.cell(60, 6, label)
        self.set_text_color(30, 41, 59)
        self.set_font("Helvetica", "B", 10)
        self.cell(0, 6, value, new_x="LMARGIN", new_y="NEXT")

    def table(self, headers: list[str], rows: list[list[str]], col_widths: list[int]):
        self.set_font("Helvetica", "B", 9)
        self.set_fill_color(241, 245, 249)
        self.set_text_color(30, 41, 59)
        for i, h in enumerate(headers):
            self.cell(col_widths[i], 7, h, border=1, fill=True)
        self.ln()
        self.set_font("Helvetica", "", 9)
        self.set_text_color(51, 65, 85)
        for row in rows:
            if self.get_y() > 265:
                self.add_page()
            for i, cell in enumerate(row):
                self.cell(col_widths[i], 6, cell[:30], border=1)
            self.ln()


def generate_pdf(
    transactions: list[Transaction],
    stats: dict,
    budgets: list[dict],
    analytics: dict,
    output_path: str | Path,
):
    """Generate a comprehensive multi-page PDF financial report."""
    pdf = FinanceReport("show-me-the-money - Financial Report")
    pdf.alias_nb_pages()

    expenses = [
        t for t in transactions if t.txn_type == TxnType.EXPENSE and not t.is_deleted
    ]
    income = [
        t for t in transactions if t.txn_type == TxnType.INCOME and not t.is_deleted
    ]

    total_expenses = sum(t.effective_amount for t in expenses)
    total_income = sum(t.amount for t in income)
    net_savings = total_income - total_expenses
    num_months = len(set(t.month for t in expenses)) or 1

    # --- PAGE 1: Overview ---
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(0, 12, "Financial Overview", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    if stats.get("date_min") and stats.get("date_max"):
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(100, 116, 139)
        pdf.cell(
            0,
            6,
            f"{stats['date_min']} to {stats['date_max']}  |  "
            f"{stats['total']} transactions  |  {num_months} months",
            new_x="LMARGIN",
            new_y="NEXT",
        )
        pdf.ln(4)

    pdf.section_title("Summary")
    pdf.kv_row("Total Expenses", f"${total_expenses:,.2f}")
    pdf.kv_row("Total Income", f"${total_income:,.2f}")
    pdf.kv_row("Net Savings", f"${net_savings:,.2f}")
    savings_rate = (net_savings / total_income * 100) if total_income > 0 else 0
    pdf.kv_row("Savings Rate", f"{savings_rate:.1f}%")
    pdf.kv_row("Avg Monthly Expenses", f"${total_expenses / num_months:,.2f}")
    pdf.kv_row("Avg Monthly Income", f"${total_income / num_months:,.2f}")
    pdf.kv_row("Avg Monthly Savings", f"${net_savings / num_months:,.2f}")
    pdf.kv_row("Classification Rate", f"{stats.get('classification_rate', 0):.1f}%")

    # Category breakdown with averages
    pdf.section_title("Expense Breakdown by Category")
    cat_totals: dict[str, float] = defaultdict(float)
    cat_counts: dict[str, int] = defaultdict(int)
    for t in expenses:
        cat = t.category or "Uncategorized"
        cat_totals[cat] += t.effective_amount
        cat_counts[cat] += 1
    sorted_cats = sorted(cat_totals.items(), key=lambda x: -x[1])

    headers = ["Category", "Total", "Monthly Avg", "% of Total", "Txns"]
    widths = [40, 32, 32, 28, 20]
    rows = []
    for cat, total in sorted_cats:
        pct = (total / total_expenses * 100) if total_expenses > 0 else 0
        rows.append(
            [
                cat,
                f"${total:,.2f}",
                f"${total / num_months:,.2f}",
                f"{pct:.1f}%",
                str(cat_counts[cat]),
            ]
        )
    pdf.table(headers, rows, widths)

    # Income vs Expense monthly comparison
    pdf.section_title("Monthly Income vs Expenses")
    monthly_exp: dict[str, float] = defaultdict(float)
    monthly_inc: dict[str, float] = defaultdict(float)
    for t in expenses:
        monthly_exp[t.month] += t.effective_amount
    for t in income:
        monthly_inc[t.month] += t.amount
    all_months = sorted(set(list(monthly_exp.keys()) + list(monthly_inc.keys())))

    headers = ["Month", "Income", "Expenses", "Net", "Savings Rate"]
    widths = [25, 32, 32, 32, 30]
    rows = []
    for m in all_months:
        inc = monthly_inc.get(m, 0)
        exp = monthly_exp.get(m, 0)
        net = inc - exp
        rate = (net / inc * 100) if inc > 0 else 0
        rows.append(
            [
                m,
                f"${inc:,.2f}",
                f"${exp:,.2f}",
                f"${net:,.2f}",
                f"{rate:.1f}%",
            ]
        )
    pdf.table(headers, rows, widths)

    # --- PAGE 2: Monthly Category Detail ---
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(0, 12, "Monthly Category Breakdown", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    monthly_cat: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for t in expenses:
        cat = t.category or "Uncategorized"
        monthly_cat[t.month][cat] += t.effective_amount

    categories = [c for c, _ in sorted_cats[:10]]
    if categories:
        headers = ["Month"] + [c[:12] for c in categories]
        widths = [22] + [int(168 / len(categories))] * len(categories)

        rows = []
        for m in all_months:
            row = [m]
            for cat in categories:
                amt = monthly_cat[m].get(cat, 0)
                row.append(f"${amt:,.0f}" if amt > 0 else "-")
            rows.append(row)
        avg_row = ["AVG"]
        for cat in categories:
            total = cat_totals.get(cat, 0)
            avg_row.append(f"${total / num_months:,.0f}")
        rows.append(avg_row)
        pdf.table(headers, rows, widths)

    # Top 10 largest expenses
    pdf.section_title("Top 10 Largest Expenses")
    top10 = sorted(expenses, key=lambda t: -t.effective_amount)[:10]
    headers = ["Date", "Store", "Category", "Amount"]
    widths = [28, 62, 50, 30]
    rows = [
        [
            t.date.isoformat(),
            (t.store_normalized or t.store_raw),
            t.category or "-",
            f"${t.effective_amount:,.2f}",
        ]
        for t in top10
    ]
    pdf.table(headers, rows, widths)

    # --- PAGE 3: Analytics ---
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(0, 12, "Spending Analytics", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # Spending velocity
    velocity = analytics.get("velocity", {})
    if velocity:
        pdf.section_title("Current Month Pace")
        pdf.kv_row("Month", velocity.get("month", ""))
        pdf.kv_row("Spent So Far", f"${velocity.get('spent_so_far', 0):,.2f}")
        pdf.kv_row("Daily Rate", f"${velocity.get('daily_rate', 0):,.2f}/day")
        pdf.kv_row("Projected Total", f"${velocity.get('projected_total', 0):,.2f}")
        pdf.kv_row("Previous Month", f"${velocity.get('prev_month_total', 0):,.2f}")

    # Month-over-month
    mom = analytics.get("mom_deltas", [])
    if mom:
        pdf.section_title("Month-over-Month Changes")
        headers = ["Category", "Previous", "Current", "Change"]
        widths = [45, 35, 35, 30]
        rows = [
            [
                d["category"],
                f"${d['previous']:,.2f}",
                f"${d['current']:,.2f}",
                f"{d['change_pct']:+.1f}%",
            ]
            for d in mom[:15]
        ]
        pdf.table(headers, rows, widths)

    # Day of week
    dow = analytics.get("day_of_week", [])
    if dow:
        pdf.subsection_title("Spending by Day of Week")
        headers = ["Day", "Total", "Txns", "Average"]
        widths = [25, 40, 25, 40]
        rows = [
            [d["day"], f"${d['total']:,.2f}", str(d["count"]), f"${d['avg']:,.2f}"]
            for d in dow
        ]
        pdf.table(headers, rows, widths)

    # Category concentration
    conc = analytics.get("concentration", {})
    if conc:
        pdf.subsection_title("Category Concentration")
        pdf.kv_row(
            "Top 3 categories",
            f"{conc.get('top3_pct', 0):.1f}% of total spending",
        )
        for c in conc.get("top3_categories", []):
            pdf.kv_row(f"  {c['category']}", f"${c['amount']:,.2f} ({c['pct']:.1f}%)")

    # --- PAGE 4: Merchants & Recurring ---
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(0, 12, "Merchants & Recurring", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # Top merchants
    merchants = analytics.get("top_merchants", [])
    if merchants:
        pdf.section_title("Top Merchants")
        headers = ["Store", "Visits", "Total", "Avg/Visit"]
        widths = [55, 25, 40, 40]
        rows = [
            [
                m["store"],
                str(m["visits"]),
                f"${m['total_spend']:,.2f}",
                f"${m['avg_per_visit']:,.2f}",
            ]
            for m in merchants[:15]
        ]
        pdf.table(headers, rows, widths)

    # Recurring charges
    recurring = analytics.get("recurring", [])
    if recurring:
        pdf.section_title("Recurring Charges")
        total_recurring = sum(r["annual_cost"] for r in recurring)
        pdf.kv_row("Total Annual Recurring", f"${total_recurring:,.2f}")
        pdf.kv_row("Monthly Recurring", f"${total_recurring / 12:,.2f}")
        pdf.ln(4)
        headers = ["Store", "Amount", "Frequency", "Annual Cost"]
        widths = [55, 30, 35, 40]
        rows = [
            [
                r["store"],
                f"${r['avg_amount']:,.2f}",
                f"~{r['avg_gap_days']:.0f} days",
                f"${r['annual_cost']:,.2f}",
            ]
            for r in recurring
        ]
        pdf.table(headers, rows, widths)

    # --- PAGE 5: Budget Status ---
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(0, 12, "Budget Status", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    if budgets:
        monthly_budgets: dict[str, list[dict]] = defaultdict(list)
        for b in budgets:
            monthly_budgets[b["month"]].append(b)

        monthly_cat_spend: dict[str, dict[str, float]] = defaultdict(
            lambda: defaultdict(float)
        )
        for t in expenses:
            cat = t.category or "Uncategorized"
            monthly_cat_spend[t.date.strftime("%Y-%m")][cat] += t.effective_amount

        for month in sorted(monthly_budgets.keys(), reverse=True):
            pdf.subsection_title(month)
            headers = ["Category", "Budget", "Actual", "Remaining", "Status"]
            widths = [40, 28, 28, 32, 30]
            rows = []
            total_budget = 0
            total_actual = 0
            for b in sorted(monthly_budgets[month], key=lambda x: x["category"]):
                actual = monthly_cat_spend.get(month, {}).get(b["category"], 0)
                remaining = b["amount"] - actual
                pct = (actual / b["amount"] * 100) if b["amount"] > 0 else 0
                status = "OVER" if pct > 100 else f"{pct:.0f}%"
                total_budget += b["amount"]
                total_actual += actual
                rows.append(
                    [
                        b["category"],
                        f"${b['amount']:,.2f}",
                        f"${actual:,.2f}",
                        f"${remaining:,.2f}",
                        status,
                    ]
                )
            # Totals row
            total_remaining = total_budget - total_actual
            total_pct = (total_actual / total_budget * 100) if total_budget > 0 else 0
            rows.append(
                [
                    "TOTAL",
                    f"${total_budget:,.2f}",
                    f"${total_actual:,.2f}",
                    f"${total_remaining:,.2f}",
                    f"{total_pct:.0f}%",
                ]
            )
            pdf.table(headers, rows, widths)
            pdf.ln(4)
    else:
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(100, 116, 139)
        pdf.cell(0, 8, "No budgets configured.", new_x="LMARGIN", new_y="NEXT")

    # --- PAGE 6: Outliers ---
    outliers = analytics.get("zscore_outliers", [])
    if outliers:
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 20)
        pdf.set_text_color(30, 41, 59)
        pdf.cell(0, 12, "Statistical Outliers", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(100, 116, 139)
        pdf.cell(
            0,
            6,
            "Transactions significantly above category average (z-score >= 2.0)",
            new_x="LMARGIN",
            new_y="NEXT",
        )
        pdf.ln(4)

        headers = ["Date", "Store", "Category", "Amount", "Z-Score"]
        widths = [25, 50, 40, 30, 25]
        rows = [
            [
                o["date"],
                o["store"],
                o["category"],
                f"${o['amount']:,.2f}",
                f"{o['z_score']:.2f}",
            ]
            for o in outliers[:20]
        ]
        pdf.table(headers, rows, widths)

    # --- Linked Offsets Summary ---
    linked = [t for t in expenses if t.adjustment > 0]
    if linked:
        pdf.section_title("Transaction Offsets")
        pdf.kv_row("Linked Transactions", str(len(linked)))
        pdf.kv_row("Total Offset Amount", f"${sum(t.adjustment for t in linked):,.2f}")
        pdf.ln(2)
        headers = ["Date", "Store", "Original", "Offset", "Effective"]
        widths = [25, 50, 30, 28, 30]
        rows = [
            [
                t.date.isoformat(),
                (t.store_normalized or t.store_raw),
                f"${t.amount:,.2f}",
                f"-${t.adjustment:,.2f}",
                f"${t.effective_amount:,.2f}",
            ]
            for t in sorted(linked, key=lambda x: -x.adjustment)[:15]
        ]
        pdf.table(headers, rows, widths)

    pdf.output(str(output_path))
    return str(output_path)
