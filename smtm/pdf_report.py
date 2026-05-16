"""PDF report generation for financial summary."""

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
    """Generate a multi-page PDF financial report."""
    pdf = FinanceReport("show-me-the-money - Financial Report")
    pdf.alias_nb_pages()

    expenses = [
        t for t in transactions if t.txn_type == TxnType.EXPENSE and not t.is_deleted
    ]
    income = [
        t for t in transactions if t.txn_type == TxnType.INCOME and not t.is_deleted
    ]

    total_expenses = sum(t.amount for t in expenses)
    total_income = sum(t.amount for t in income)
    net_savings = total_income - total_expenses

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
            f"{stats['total']} transactions",
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
    pdf.kv_row("Classification Rate", f"{stats.get('classification_rate', 0):.1f}%")
    pdf.kv_row("Expense Transactions", str(stats.get("expenses", 0)))
    pdf.kv_row("Income Transactions", str(stats.get("income", 0)))

    # Top 10 largest expenses
    pdf.section_title("Top 10 Largest Expenses")
    top10 = sorted(expenses, key=lambda t: -t.amount)[:10]
    headers = ["Date", "Store", "Category", "Amount"]
    widths = [28, 62, 50, 30]
    rows = [
        [
            t.date.isoformat(),
            (t.store_normalized or t.store_raw),
            t.category or "-",
            f"${t.amount:,.2f}",
        ]
        for t in top10
    ]
    pdf.table(headers, rows, widths)

    # --- PAGE 2: Analytics ---
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(0, 12, "Analytics", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # Spending velocity
    velocity = analytics.get("velocity", {})
    if velocity:
        pdf.section_title("Spending Velocity")
        pdf.kv_row("Current Month", velocity.get("month", ""))
        pdf.kv_row("Spent So Far", f"${velocity.get('spent_so_far', 0):,.2f}")
        pdf.kv_row("Daily Rate", f"${velocity.get('daily_rate', 0):,.2f}/day")
        pdf.kv_row("Projected Total", f"${velocity.get('projected_total', 0):,.2f}")
        pdf.kv_row(
            "Previous Month",
            f"${velocity.get('prev_month_total', 0):,.2f}",
        )

    # Savings rate trend
    savings_data = analytics.get("savings_rate", [])
    if savings_data:
        pdf.section_title("Savings Rate by Month")
        headers = ["Month", "Income", "Expenses", "Rate"]
        widths = [30, 40, 40, 30]
        rows = [
            [
                s["month"],
                f"${s['income']:,.2f}",
                f"${s['expenses']:,.2f}",
                f"{s['rate']:.1f}%",
            ]
            for s in savings_data
        ]
        pdf.table(headers, rows, widths)

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

    # --- PAGE 3: Merchants & Recurring ---
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

    # Category concentration
    conc = analytics.get("concentration", {})
    if conc:
        pdf.subsection_title("Category Concentration")
        pdf.kv_row(
            "Top 3 categories account for",
            f"{conc.get('top3_pct', 0):.1f}% of spending",
        )
        for c in conc.get("top3_categories", []):
            pdf.kv_row(f"  {c['category']}", f"${c['amount']:,.2f} ({c['pct']:.1f}%)")

    # --- PAGE 4: Budgets ---
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(0, 12, "Budget Status", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    if budgets:
        # Group by month
        from collections import defaultdict

        monthly_budgets: dict[str, list[dict]] = defaultdict(list)
        for b in budgets:
            monthly_budgets[b["month"]].append(b)

        # Compute actual spend per category per month
        monthly_cat_spend: dict[str, dict[str, float]] = defaultdict(
            lambda: defaultdict(float)
        )
        for t in expenses:
            cat = t.category or "Uncategorized"
            monthly_cat_spend[t.date.strftime("%Y-%m")][cat] += t.amount

        for month in sorted(monthly_budgets.keys(), reverse=True):
            pdf.subsection_title(month)
            headers = ["Category", "Budget", "Actual", "Remaining", "Status"]
            widths = [40, 28, 28, 32, 30]
            rows = []
            for b in sorted(monthly_budgets[month], key=lambda x: x["category"]):
                actual = monthly_cat_spend.get(month, {}).get(b["category"], 0)
                remaining = b["amount"] - actual
                pct = (actual / b["amount"] * 100) if b["amount"] > 0 else 0
                status = "OVER" if pct > 100 else f"{pct:.0f}%"
                rows.append(
                    [
                        b["category"],
                        f"${b['amount']:,.2f}",
                        f"${actual:,.2f}",
                        f"${remaining:,.2f}",
                        status,
                    ]
                )
            pdf.table(headers, rows, widths)
            pdf.ln(4)
    else:
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(100, 116, 139)
        pdf.cell(0, 8, "No budgets configured.", new_x="LMARGIN", new_y="NEXT")

    # --- PAGE 5: Outliers ---
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
            "Transactions with z-score >= 2.0 (significantly above category average)",
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

    pdf.output(str(output_path))
    return str(output_path)
