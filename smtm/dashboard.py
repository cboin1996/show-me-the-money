"""Generate a self-contained HTML financial dashboard."""
import json
from collections import defaultdict
from datetime import date

from .models import Transaction, TxnType


def _txns_to_json(txns: list[Transaction]) -> list[dict]:
    return [
        {
            "date": t.date.isoformat(),
            "month": t.month,
            "amount": round(t.amount, 2),
            "store_raw": t.store_raw,
            "store_normalized": t.store_normalized or t.store_raw,
            "category": t.category or "Uncategorized",
            "confidence": t.confidence,
            "type": t.txn_type.value,
            "source_file": t.source_file,
            "uuid": t.uuid,
        }
        for t in txns
    ]


def _compute_summary(txns: list[Transaction]) -> dict:
    expenses = [t for t in txns if t.txn_type == TxnType.EXPENSE]
    income = [t for t in txns if t.txn_type == TxnType.INCOME]
    total_exp = sum(t.amount for t in expenses)
    total_inc = sum(t.amount for t in income)
    categorized = sum(1 for t in expenses if t.category and t.category != "Uncategorized")
    dates = [t.date for t in txns] if txns else [date.today()]

    monthly_expenses: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for t in expenses:
        cat = t.category or "Uncategorized"
        monthly_expenses[t.month][cat] += t.amount

    monthly_income: dict[str, float] = defaultdict(float)
    for t in income:
        monthly_income[t.month] += t.amount

    months = sorted(set(t.month for t in txns))
    categories = sorted(set(
        t.category or "Uncategorized" for t in expenses
    ))

    monthly_data = []
    for m in months:
        row = {"month": m}
        for cat in categories:
            row[cat] = round(monthly_expenses[m].get(cat, 0), 2)
        row["_total_expense"] = round(sum(monthly_expenses[m].values()), 2)
        row["_income"] = round(monthly_income.get(m, 0), 2)
        row["_net"] = round(monthly_income.get(m, 0) - sum(monthly_expenses[m].values()), 2)
        monthly_data.append(row)

    cat_totals = {}
    for cat in categories:
        cat_totals[cat] = round(sum(
            monthly_expenses[m].get(cat, 0) for m in months
        ), 2)

    return {
        "total_expenses": round(total_exp, 2),
        "total_income": round(total_inc, 2),
        "net_savings": round(total_inc - total_exp, 2),
        "total_transactions": len(txns),
        "expense_count": len(expenses),
        "income_count": len(income),
        "classification_rate": round(
            categorized / len(expenses) * 100 if expenses else 0, 1
        ),
        "date_min": min(dates).isoformat(),
        "date_max": max(dates).isoformat(),
        "months": months,
        "categories": categories,
        "monthly_data": monthly_data,
        "category_totals": cat_totals,
        "num_months": len(months),
    }


def generate_dashboard(
    txns: list[Transaction],
    budgets: list[dict],
    stats: dict,
    output_path: str,
):
    summary = _compute_summary(txns)
    txn_json = _txns_to_json(txns)

    budget_by_month: dict[str, dict[str, float]] = defaultdict(dict)
    for b in budgets:
        budget_by_month[b["month"]][b["category"]] = b["amount"]

    html = _render_html(summary, txn_json, dict(budget_by_month))
    with open(output_path, "w") as f:
        f.write(html)


CATEGORY_COLORS = {
    "Dining": "#FF6384",
    "Groceries": "#4BC0C0",
    "Shopping": "#FFCE56",
    "Transportation": "#36A2EB",
    "Entertainment": "#9966FF",
    "Travel": "#FF9F40",
    "Health": "#C9CBCF",
    "Subscriptions": "#7BC8A4",
    "Insurance": "#E7E9ED",
    "Utilities": "#8B5CF6",
    "Fees": "#F87171",
    "Misc": "#94A3B8",
    "Uncategorized": "#DC2626",
    "Rent": "#059669",
}


def _get_color(cat: str, idx: int) -> str:
    if cat in CATEGORY_COLORS:
        return CATEGORY_COLORS[cat]
    fallback = [
        "#E11D48", "#0891B2", "#7C3AED", "#EA580C", "#2563EB",
        "#16A34A", "#CA8A04", "#DC2626", "#4F46E5", "#0D9488",
    ]
    return fallback[idx % len(fallback)]


def _render_html(summary: dict, txn_json: list[dict],
                 budgets: dict[str, dict[str, float]]) -> str:
    categories_json = json.dumps(summary["categories"])
    months_json = json.dumps(summary["months"])
    monthly_data_json = json.dumps(summary["monthly_data"])
    cat_totals_json = json.dumps(summary["category_totals"])
    txns_json_str = json.dumps(txn_json)
    budgets_json = json.dumps(budgets)

    datasets_js = []
    for i, cat in enumerate(summary["categories"]):
        color = _get_color(cat, i)
        datasets_js.append(
            f'{{ label: "{cat}", data: monthlyData.map(m => m["{cat}"] || 0), '
            f'backgroundColor: "{color}", borderWidth: 0 }}'
        )
    datasets_str = ",\n            ".join(datasets_js)

    donut_colors = [
        _get_color(c, i) for i, c in enumerate(summary["categories"])
    ]
    donut_colors_json = json.dumps(donut_colors)

    avg_monthly = round(
        summary["total_expenses"] / max(summary["num_months"], 1), 2
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>showMeTheMoney Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif; background: #0f172a; color: #e2e8f0; padding: 20px; }}
.header {{ text-align: center; margin-bottom: 30px; }}
.header h1 {{ font-size: 28px; color: #f8fafc; letter-spacing: -0.5px; }}
.header p {{ color: #94a3b8; margin-top: 4px; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 30px; }}
.card {{ background: #1e293b; border-radius: 12px; padding: 20px; }}
.card .label {{ font-size: 12px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.5px; }}
.card .value {{ font-size: 24px; font-weight: 700; margin-top: 4px; }}
.card .value.green {{ color: #4ade80; }}
.card .value.red {{ color: #f87171; }}
.card .value.blue {{ color: #60a5fa; }}
.card .value.purple {{ color: #a78bfa; }}
.charts {{ display: grid; grid-template-columns: 2fr 1fr; gap: 20px; margin-bottom: 30px; }}
.chart-box {{ background: #1e293b; border-radius: 12px; padding: 20px; }}
.chart-box h2 {{ font-size: 16px; margin-bottom: 12px; color: #f8fafc; }}
.trend-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 30px; }}
.filters {{ background: #1e293b; border-radius: 12px; padding: 16px; margin-bottom: 20px; display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }}
.filters label {{ font-size: 13px; color: #94a3b8; }}
.filters select, .filters input {{ background: #334155; border: 1px solid #475569; color: #e2e8f0; border-radius: 6px; padding: 6px 10px; font-size: 13px; }}
.filters input[type="text"] {{ width: 200px; }}
.table-wrap {{ background: #1e293b; border-radius: 12px; overflow: hidden; }}
.table-wrap h2 {{ font-size: 16px; padding: 16px 20px 0; color: #f8fafc; }}
.txn-count {{ padding: 4px 20px 12px; font-size: 13px; color: #94a3b8; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th {{ background: #334155; padding: 10px 12px; text-align: left; font-weight: 600; cursor: pointer; user-select: none; position: sticky; top: 0; }}
th:hover {{ background: #475569; }}
td {{ padding: 8px 12px; border-bottom: 1px solid #1e293b; }}
tr {{ background: #0f172a; }}
tr:hover {{ background: #1e293b; }}
.cat-badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
.uncategorized {{ color: #fca5a5; background: #7f1d1d; }}
.scroll-table {{ max-height: 600px; overflow-y: auto; }}
@media (max-width: 768px) {{
    .charts {{ grid-template-columns: 1fr; }}
    .trend-row {{ grid-template-columns: 1fr; }}
}}
</style>
</head>
<body>

<div class="header">
    <h1>showMeTheMoney</h1>
    <p>{summary['date_min']} to {summary['date_max']} &middot; {summary['num_months']} months &middot; {summary['total_transactions']} transactions</p>
</div>

<div class="cards">
    <div class="card">
        <div class="label">Total Expenses</div>
        <div class="value red">${summary['total_expenses']:,.0f}</div>
    </div>
    <div class="card">
        <div class="label">Total Income</div>
        <div class="value green">${summary['total_income']:,.0f}</div>
    </div>
    <div class="card">
        <div class="label">Net Savings</div>
        <div class="value {'green' if summary['net_savings'] >= 0 else 'red'}">${summary['net_savings']:,.0f}</div>
    </div>
    <div class="card">
        <div class="label">Avg Monthly Spend</div>
        <div class="value blue">${avg_monthly:,.0f}</div>
    </div>
    <div class="card">
        <div class="label">Classification</div>
        <div class="value purple">{summary['classification_rate']:.0f}%</div>
    </div>
</div>

<div class="charts">
    <div class="chart-box">
        <h2>Monthly Expenses by Category</h2>
        <canvas id="monthlyChart"></canvas>
    </div>
    <div class="chart-box">
        <h2>Expense Breakdown</h2>
        <canvas id="donutChart"></canvas>
    </div>
</div>

<div class="trend-row">
    <div class="chart-box">
        <h2>Monthly Trend</h2>
        <canvas id="trendChart"></canvas>
    </div>
    <div class="chart-box">
        <h2>Income vs Expenses</h2>
        <canvas id="incExpChart"></canvas>
    </div>
</div>

<div class="filters">
    <div>
        <label>Search</label><br>
        <input type="text" id="searchInput" placeholder="Store, category...">
    </div>
    <div>
        <label>Category</label><br>
        <select id="categoryFilter"><option value="">All</option></select>
    </div>
    <div>
        <label>Month</label><br>
        <select id="monthFilter"><option value="">All</option></select>
    </div>
    <div>
        <label>Type</label><br>
        <select id="typeFilter">
            <option value="">All</option>
            <option value="expense">Expenses</option>
            <option value="income">Income</option>
        </select>
    </div>
    <div>
        <label>Min $</label><br>
        <input type="number" id="minAmount" style="width:80px" step="0.01">
    </div>
    <div>
        <label>Max $</label><br>
        <input type="number" id="maxAmount" style="width:80px" step="0.01">
    </div>
</div>

<div class="table-wrap">
    <h2>Transactions</h2>
    <div class="txn-count" id="txnCount"></div>
    <div class="scroll-table">
        <table id="txnTable">
            <thead>
                <tr>
                    <th data-col="date">Date</th>
                    <th data-col="store">Store</th>
                    <th data-col="category">Category</th>
                    <th data-col="amount">Amount</th>
                    <th data-col="type">Type</th>
                    <th data-col="source">Source</th>
                </tr>
            </thead>
            <tbody id="txnBody"></tbody>
        </table>
    </div>
</div>

<script>
const allTxns = {txns_json_str};
const categories = {categories_json};
const months = {months_json};
const monthlyData = {monthly_data_json};
const catTotals = {cat_totals_json};
const budgets = {budgets_json};

// -- Populate filters --
const catFilter = document.getElementById('categoryFilter');
categories.forEach(c => {{
    const opt = document.createElement('option');
    opt.value = c; opt.textContent = c;
    catFilter.appendChild(opt);
}});
const monthFilter = document.getElementById('monthFilter');
months.forEach(m => {{
    const opt = document.createElement('option');
    opt.value = m; opt.textContent = m;
    monthFilter.appendChild(opt);
}});

// -- Charts --
const chartDefaults = {{ responsive: true, maintainAspectRatio: true }};
Chart.defaults.color = '#94a3b8';
Chart.defaults.borderColor = '#334155';

new Chart(document.getElementById('monthlyChart'), {{
    type: 'bar',
    data: {{
        labels: months,
        datasets: [{datasets_str}]
    }},
    options: {{
        ...chartDefaults,
        plugins: {{ legend: {{ position: 'bottom', labels: {{ boxWidth: 12, padding: 8, font: {{ size: 11 }} }} }} }},
        scales: {{
            x: {{ stacked: true, grid: {{ display: false }} }},
            y: {{ stacked: true, ticks: {{ callback: v => '$' + v.toLocaleString() }} }}
        }}
    }}
}});

new Chart(document.getElementById('donutChart'), {{
    type: 'doughnut',
    data: {{
        labels: categories,
        datasets: [{{ data: categories.map(c => catTotals[c] || 0), backgroundColor: {donut_colors_json}, borderWidth: 0 }}]
    }},
    options: {{
        ...chartDefaults,
        cutout: '60%',
        plugins: {{
            legend: {{ position: 'bottom', labels: {{ boxWidth: 10, padding: 6, font: {{ size: 11 }} }} }},
            tooltip: {{ callbacks: {{ label: ctx => ctx.label + ': $' + ctx.parsed.toLocaleString() }} }}
        }}
    }}
}});

new Chart(document.getElementById('trendChart'), {{
    type: 'line',
    data: {{
        labels: months,
        datasets: [{{
            label: 'Total Expenses',
            data: monthlyData.map(m => m._total_expense),
            borderColor: '#f87171', backgroundColor: 'rgba(248,113,113,0.1)',
            fill: true, tension: 0.3
        }}, {{
            label: 'Income',
            data: monthlyData.map(m => m._income),
            borderColor: '#4ade80', backgroundColor: 'rgba(74,222,128,0.1)',
            fill: true, tension: 0.3
        }}]
    }},
    options: {{
        ...chartDefaults,
        plugins: {{ legend: {{ position: 'bottom', labels: {{ boxWidth: 12, font: {{ size: 11 }} }} }} }},
        scales: {{ y: {{ ticks: {{ callback: v => '$' + v.toLocaleString() }} }} }}
    }}
}});

new Chart(document.getElementById('incExpChart'), {{
    type: 'bar',
    data: {{
        labels: months,
        datasets: [
            {{ label: 'Income', data: monthlyData.map(m => m._income), backgroundColor: '#4ade80' }},
            {{ label: 'Expenses', data: monthlyData.map(m => m._total_expense), backgroundColor: '#f87171' }}
        ]
    }},
    options: {{
        ...chartDefaults,
        plugins: {{ legend: {{ position: 'bottom', labels: {{ boxWidth: 12, font: {{ size: 11 }} }} }} }},
        scales: {{ y: {{ ticks: {{ callback: v => '$' + v.toLocaleString() }} }} }}
    }}
}});

// -- Table --
let sortCol = 'date';
let sortAsc = false;

function getFiltered() {{
    const search = document.getElementById('searchInput').value.toLowerCase();
    const cat = catFilter.value;
    const month = monthFilter.value;
    const type = document.getElementById('typeFilter').value;
    const minAmt = parseFloat(document.getElementById('minAmount').value) || 0;
    const maxAmt = parseFloat(document.getElementById('maxAmount').value) || Infinity;

    return allTxns.filter(t => {{
        if (search && !t.store_raw.includes(search) && !t.store_normalized.includes(search) && !t.category.toLowerCase().includes(search)) return false;
        if (cat && t.category !== cat) return false;
        if (month && t.month !== month) return false;
        if (type && t.type !== type) return false;
        if (t.amount < minAmt || t.amount > maxAmt) return false;
        return true;
    }}).sort((a, b) => {{
        let va = a[sortCol], vb = b[sortCol];
        if (sortCol === 'amount') {{ va = +va; vb = +vb; }}
        if (sortCol === 'store') {{ va = a.store_normalized; vb = b.store_normalized; }}
        if (sortCol === 'source') {{ va = a.source_file; vb = b.source_file; }}
        if (va < vb) return sortAsc ? -1 : 1;
        if (va > vb) return sortAsc ? 1 : -1;
        return 0;
    }});
}}

const colorMap = {json.dumps(CATEGORY_COLORS)};
function catBadge(cat) {{
    const bg = colorMap[cat] || '#475569';
    const cls = cat === 'Uncategorized' ? ' uncategorized' : '';
    return `<span class="cat-badge${{cls}}" style="background:${{bg}}22;color:${{bg}}">${{cat}}</span>`;
}}

function renderTable() {{
    const filtered = getFiltered();
    const body = document.getElementById('txnBody');
    document.getElementById('txnCount').textContent =
        `Showing ${{filtered.length}} of ${{allTxns.length}} transactions` +
        (filtered.length < allTxns.length ? ` — $$${{filtered.reduce((s,t) => s + t.amount, 0).toLocaleString(undefined, {{minimumFractionDigits:2}})}} total` : '');

    body.innerHTML = filtered.slice(0, 500).map(t => `
        <tr>
            <td>${{t.date}}</td>
            <td title="${{t.store_raw}}">${{t.store_normalized}}</td>
            <td>${{catBadge(t.category)}}</td>
            <td style="text-align:right;font-variant-numeric:tabular-nums">${{t.type === 'income' ? '+' : '-'}}$${{t.amount.toFixed(2)}}</td>
            <td>${{t.type}}</td>
            <td style="color:#64748b">${{t.source_file}}</td>
        </tr>
    `).join('');
}}

document.querySelectorAll('#txnTable th').forEach(th => {{
    th.addEventListener('click', () => {{
        const col = th.dataset.col;
        if (sortCol === col) sortAsc = !sortAsc;
        else {{ sortCol = col; sortAsc = true; }}
        renderTable();
    }});
}});

['searchInput', 'categoryFilter', 'monthFilter', 'typeFilter', 'minAmount', 'maxAmount']
    .forEach(id => document.getElementById(id).addEventListener('input', renderTable));

renderTable();
</script>
</body>
</html>"""
