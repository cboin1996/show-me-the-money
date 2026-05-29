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
            "effective_amount": round(t.effective_amount, 2),
            "store_raw": t.store_raw,
            "store_normalized": t.store_normalized or t.store_raw,
            "category": t.category or "Uncategorized",
            "confidence": t.confidence,
            "type": t.txn_type.value,
            "source_file": t.source_file,
            "uuid": t.uuid,
            "linked_to": t.linked_to,
            "adjustment": round(t.adjustment, 2),
            "deleted_at": t.deleted_at,
        }
        for t in txns
    ]


def _compute_summary(txns: list[Transaction]) -> dict:
    expenses = [t for t in txns if t.txn_type == TxnType.EXPENSE and not t.is_deleted]
    income = [t for t in txns if t.txn_type == TxnType.INCOME and not t.is_deleted]
    total_exp = sum(t.effective_amount for t in expenses)
    total_inc = sum(t.amount for t in income)
    categorized = sum(
        1 for t in expenses if t.category and t.category != "Uncategorized"
    )
    dates = [t.date for t in txns] if txns else [date.today()]

    monthly_expenses: dict[str, dict[str, float]] = defaultdict(
        lambda: defaultdict(float)
    )
    for t in expenses:
        cat = t.category or "Uncategorized"
        monthly_expenses[t.month][cat] += t.effective_amount

    monthly_income: dict[str, float] = defaultdict(float)
    for t in income:
        monthly_income[t.month] += t.amount

    months = sorted(set(t.month for t in txns))
    categories = sorted(set(t.category or "Uncategorized" for t in expenses))

    monthly_data = []
    for m in months:
        row = {"month": m}
        for cat in categories:
            row[cat] = round(monthly_expenses[m].get(cat, 0), 2)
        row["_total_expense"] = round(sum(monthly_expenses[m].values()), 2)
        row["_income"] = round(monthly_income.get(m, 0), 2)
        row["_net"] = round(
            monthly_income.get(m, 0) - sum(monthly_expenses[m].values()), 2
        )
        monthly_data.append(row)

    cat_totals = {}
    for cat in categories:
        cat_totals[cat] = round(sum(monthly_expenses[m].get(cat, 0) for m in months), 2)

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
        "#E11D48",
        "#0891B2",
        "#7C3AED",
        "#EA580C",
        "#2563EB",
        "#16A34A",
        "#CA8A04",
        "#DC2626",
        "#4F46E5",
        "#0D9488",
    ]
    return fallback[idx % len(fallback)]


def _render_html(
    summary: dict, txn_json: list[dict], budgets: dict[str, dict[str, float]]
) -> str:
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

    donut_colors = [_get_color(c, i) for i, c in enumerate(summary["categories"])]
    donut_colors_json = json.dumps(donut_colors)

    avg_monthly = round(summary["total_expenses"] / max(summary["num_months"], 1), 2)

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


def generate_server_html() -> str:
    """Generate the API-driven interactive dashboard HTML."""
    colors_json = json.dumps(CATEGORY_COLORS)

    return (
        """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>show-me-the-money</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif; background: #0f172a; color: #e2e8f0; padding: 20px; }
.header { margin-bottom: 8px; border-bottom: 1px solid #1e293b; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 30px; }
.card { background: #1e293b; border-radius: 12px; padding: 20px; }
.card .label { font-size: 12px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.5px; }
.card .value { font-size: 24px; font-weight: 700; margin-top: 4px; }
.card .value.green { color: #4ade80; }
.card .value.red { color: #f87171; }
.card .value.blue { color: #60a5fa; }
.card .value.purple { color: #a78bfa; }
.charts { display: grid; grid-template-columns: 2fr 1fr; gap: 20px; margin-bottom: 30px; }
.chart-box { background: #1e293b; border-radius: 12px; padding: 20px; }
.chart-box h2 { font-size: 16px; margin-bottom: 12px; color: #f8fafc; }
.chart-box canvas { max-height: 280px; }
.trend-row { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 30px; }
.section { background: #1e293b; border-radius: 12px; padding: 20px; margin-bottom: 20px; }
.section h2 { font-size: 16px; margin-bottom: 12px; color: #f8fafc; }
.section .subtitle { font-size: 13px; color: #94a3b8; margin-bottom: 12px; }
.filters { background: #1e293b; border-radius: 12px; padding: 16px; margin-bottom: 20px; display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }
.filters label { font-size: 13px; color: #94a3b8; }
.filters select, .filters input { background: #334155; border: 1px solid #475569; color: #e2e8f0; border-radius: 6px; padding: 6px 10px; font-size: 13px; }
.filters input[type="text"] { width: 200px; }
.table-wrap { background: #1e293b; border-radius: 12px; overflow: hidden; margin-bottom: 20px; }
.table-wrap h2 { font-size: 16px; padding: 16px 20px 0; color: #f8fafc; }
.txn-count { padding: 4px 20px 12px; font-size: 13px; color: #94a3b8; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { background: #334155; padding: 10px 12px; text-align: left; font-weight: 600; cursor: pointer; user-select: none; position: sticky; top: 0; }
th:hover { background: #475569; }
td { padding: 8px 12px; border-bottom: 1px solid #1e293b; }
tr { background: #0f172a; }
tr:hover { background: #1e293b; }
.cat-badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
.uncategorized { color: #fca5a5; background: #7f1d1d; }
.scroll-table { max-height: 600px; overflow-y: auto; }
.btn { background: #3b82f6; color: white; border: none; border-radius: 6px; padding: 6px 12px; font-size: 12px; cursor: pointer; font-weight: 600; }
.btn:hover { background: #2563eb; }
.btn-sm { padding: 4px 8px; font-size: 11px; }
.btn-danger { background: #dc2626; }
.btn-danger:hover { background: #b91c1c; }
.btn-success { background: #16a34a; }
.btn-success:hover { background: #15803d; }
.btn-warn { background: #d97706; }
.btn-warn:hover { background: #b45309; }
.btn-outline { background: transparent; border: 1px solid #475569; color: #94a3b8; }
.btn-outline:hover { border-color: #3b82f6; color: #3b82f6; }
.bulk-bar { background: #1e293b; border: 1px solid #3b82f6; border-radius: 8px; padding: 10px 16px; margin-bottom: 12px; display: flex; gap: 12px; align-items: center; font-size: 13px; color: #e2e8f0; }
.bulk-bar .count { font-weight: 700; color: #3b82f6; }
.over-budget { background: #7f1d1d33; border-left: 3px solid #dc2626; }
input[type="checkbox"] { accent-color: #3b82f6; width: 14px; height: 14px; cursor: pointer; }
.inline-cat-select { background: #334155; border: 1px solid #475569; color: #e2e8f0; border-radius: 4px; padding: 2px 6px; font-size: 11px; }
.anomaly-card { border-left: 3px solid #f59e0b; padding: 10px 14px; background: #1a1a2e; border-radius: 6px; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center; }
.anomaly-card .mult { color: #fbbf24; font-weight: 700; font-size: 16px; }
.import-zone { border: 2px dashed #475569; border-radius: 12px; padding: 40px; text-align: center; color: #94a3b8; cursor: pointer; transition: border-color 0.2s; }
.import-zone:hover, .import-zone.dragover { border-color: #3b82f6; color: #60a5fa; }
.import-zone input { display: none; }
.tab-bar { display: flex; gap: 4px; margin-bottom: 16px; flex-wrap: wrap; }
.tab { padding: 8px 16px; border-radius: 8px; cursor: pointer; font-size: 13px; color: #94a3b8; background: #0f172a; border: 1px solid #334155; }
.tab.active { background: #3b82f6; color: white; border-color: #3b82f6; }
.form-row { display: flex; gap: 8px; align-items: center; margin-bottom: 12px; flex-wrap: wrap; }
.form-row input, .form-row select { background: #334155; border: 1px solid #475569; color: #e2e8f0; border-radius: 6px; padding: 6px 10px; font-size: 13px; }
.hidden { display: none !important; }
.toast { position: fixed; bottom: 20px; right: 20px; background: #16a34a; color: white; padding: 12px 20px; border-radius: 8px; font-size: 14px; z-index: 9999; animation: fadeIn 0.3s; }
@keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
@media (max-width: 768px) {
    .charts { grid-template-columns: 1fr; }
    .trend-row { grid-template-columns: 1fr; }
}
</style>
</head>
<body>

<div class="header" style="display:flex;align-items:center;justify-content:space-between;padding:12px 24px">
    <div style="display:flex;align-items:baseline;gap:16px;flex-wrap:wrap">
        <span style="font-size:13px;color:#475569;font-weight:500;letter-spacing:0.05em;text-transform:uppercase">smtm</span>
        <span id="headerSub" style="font-size:13px;color:#64748b">Loading...</span>
    </div>
    <a href="/api/report/pdf" class="btn btn-outline" data-testid="export-pdf-btn" download="financial_report.pdf" style="white-space:nowrap;font-size:12px">PDF</a>
</div>

<div class="cards" id="cards" data-testid="summary-cards"></div>

<div class="tab-bar" id="mainTabs" data-testid="tab-bar">
    <div class="tab active" data-tab="overview" data-testid="tab-overview">Overview</div>
    <div class="tab" data-tab="transactions" data-testid="tab-transactions">Transactions</div>
    <div class="tab" data-tab="organize" data-testid="tab-organize">Organize</div>
    <div class="tab" data-tab="budgets" data-testid="tab-budgets">Budgets</div>
    <div class="tab" data-tab="reimburse" data-testid="tab-reimburse">Reimburse</div>
    <div class="tab" data-tab="trips" data-testid="tab-trips">Trips</div>
    <div class="tab" data-tab="analytics" data-testid="tab-analytics">Analytics</div>
    <div class="tab" data-tab="import" data-testid="tab-import">Import</div>
</div>

<!-- OVERVIEW TAB -->
<div id="tab-overview">
    <div class="charts">
        <div class="chart-box"><h2>Monthly Expenses by Category</h2><canvas id="monthlyChart"></canvas></div>
        <div class="chart-box"><h2>Expense Breakdown</h2><canvas id="donutChart"></canvas></div>
    </div>
    <div class="trend-row">
        <div class="chart-box"><h2>Monthly Trend</h2><canvas id="trendChart"></canvas></div>
        <div class="chart-box"><h2>Income vs Expenses</h2><canvas id="incExpChart"></canvas></div>
    </div>
    <div id="anomaliesSection" class="section hidden">
        <h2>Anomalies <span style="font-size:12px;color:#fbbf24">(transactions &gt; 2x category average)</span></h2>
        <input type="text" id="anomalySearch" placeholder="Search by store or category..." style="width:250px;margin-bottom:12px;background:#0f172a;border:1px solid #334155;color:#f8fafc;padding:6px 12px;border-radius:6px;font-size:13px">
        <div id="anomaliesList"></div>
    </div>
</div>

<!-- TRANSACTIONS TAB -->
<div id="tab-transactions" class="hidden">
    <div class="filters">
        <div><label>Search</label><br><input type="text" id="searchInput" placeholder="Store, category..."></div>
        <div><label>Category</label><br><select id="categoryFilter"><option value="">All</option></select></div>
        <div><label>Month</label><br><select id="monthFilter"><option value="">All</option></select></div>
        <div><label>Type</label><br><select id="typeFilter"><option value="">All</option><option value="expense">Expenses</option><option value="income">Income</option><option value="transfer">Transfers</option></select></div>
        <div><label>From</label><br><input type="date" id="dateFrom" style="width:130px"></div>
        <div><label>To</label><br><input type="date" id="dateTo" style="width:130px"></div>
        <div><label>Min $</label><br><input type="number" id="minAmount" style="width:80px" step="0.01"></div>
        <div><label>Max $</label><br><input type="number" id="maxAmount" style="width:80px" step="0.01"></div>
        <div style="margin-left:auto;align-self:flex-end"><button class="btn btn-outline" id="exportCsvBtn" data-testid="export-csv-btn">Export CSV</button></div>
    </div>
    <div id="bulkBar" class="bulk-bar hidden" data-testid="bulk-bar">
        <span><span class="count" id="bulkCount">0</span> selected</span>
        <select id="bulkCatSelect" class="inline-cat-select"><option value="">Assign category...</option></select>
        <button class="btn btn-sm btn-success" id="bulkCatBtn">Apply</button>
        <button class="btn btn-sm btn-danger" id="bulkDeleteBtn">Delete Selected</button>
        <button class="btn btn-sm btn-outline" id="bulkClearBtn">Clear</button>
    </div>
    <div class="table-wrap">
        <div class="txn-count" id="txnCount"></div>
        <div class="scroll-table">
            <table id="txnTable" data-testid="txn-table">
                <thead><tr><th><input type="checkbox" id="selectAll" data-testid="select-all"></th><th data-col="date">Date</th><th data-col="store">Store</th><th data-col="category">Category</th><th data-col="amount">Amount</th><th data-col="type">Type</th><th>Trip</th><th>Actions</th></tr></thead>
                <tbody id="txnBody" data-testid="txn-body"></tbody>
            </table>
        </div>
    </div>
</div>

<!-- ORGANIZE TAB (merged Categorize + Manage data hygiene) -->
<div id="tab-organize" class="hidden">
    <div class="section" style="display:flex;gap:12px;align-items:center;padding:16px 20px">
        <button class="btn btn-success" id="recatAllBtn" data-testid="recategorize-btn">Re-categorize All Uncategorized</button>
        <span id="recatResult" data-testid="recategorize-result" style="font-size:13px;color:#94a3b8"></span>
    </div>
    <div id="uncategorizedSection" class="section">
        <h2>Uncategorized Merchants</h2>
        <p class="subtitle">Select a category to auto-classify all transactions from that merchant</p>
        <div class="scroll-table"><table><thead><tr><th>Store</th><th>Count</th><th>Total Spend</th><th>Category</th></tr></thead><tbody id="uncatBody"></tbody></table></div>
    </div>
    <div id="suggestSection" class="section">
        <h2>Keyword Suggestions</h2>
        <p class="subtitle">Auto-detected categories based on store name keywords</p>
        <div style="margin-bottom:12px"><button class="btn btn-success" id="applyAllSuggBtn">Apply All</button></div>
        <div class="scroll-table"><table><thead><tr><th>Store</th><th>Suggested</th><th>Amount</th><th>Count</th><th>Actions</th></tr></thead><tbody id="suggestBody"></tbody></table></div>
    </div>
    <div class="section">
        <h2>Category Rules <span id="rulesCount" style="font-size:12px;color:#94a3b8"></span></h2>
        <p class="subtitle">Rules map store names to categories. <strong>Editing a transaction category also saves a rule</strong> so future imports self-categorize.</p>
        <div class="form-row">
            <input type="text" id="newRulePattern" placeholder="Pattern (store name)" list="dl-expense-stores">
            <select id="newRuleCat"></select>
            <select id="newRuleType"><option value="exact">exact</option><option value="substring">substring</option></select>
            <button class="btn" id="addRuleBtn">Add Rule</button>
            <button class="btn btn-outline btn-sm" id="normalizeRulesBtn" title="Update any rules using raw bank names to use normalized store names instead">Normalize rule patterns</button>
        </div>
        <div class="form-row">
            <input type="text" id="rulesSearch" placeholder="Search rules..." style="width:250px">
        </div>
        <div class="scroll-table" style="max-height:300px"><table><thead><tr><th>When...</th><th>Category</th><th>Covers</th></tr></thead><tbody id="rulesBody"></tbody></table></div>
    </div>
    <div class="section">
        <h2>Store Pairs <span id="pairsCount" style="font-size:12px;color:#94a3b8"></span></h2>
        <p class="subtitle">Map raw bank names to clean merchant names. Changes auto-propagate to all transactions.</p>
        <div class="form-row">
            <input type="text" id="newPairRaw" placeholder="Raw name" list="dl-expense-raw">
            <input type="text" id="newPairNorm" placeholder="Normalized name" list="dl-all-stores">
            <button class="btn" id="addPairBtn">Add Pair</button>
        </div>
        <div class="form-row">
            <input type="text" id="pairsSearch" placeholder="Search pairs..." style="width:250px">
        </div>
        <div class="scroll-table" style="max-height:300px"><table><thead><tr><th>Normalized name</th><th></th></tr></thead><tbody id="pairsBody"></tbody></table></div>
        <h3 style="margin-top:16px;font-size:14px">Suggested Pairs <span id="suggestedPairsCount" style="font-size:12px;color:#94a3b8"></span></h3>
        <p style="font-size:12px;color:#94a3b8;margin-bottom:8px">Fuzzy-matched store names that look like the same merchant</p>
        <button class="btn btn-outline btn-sm" id="discoverStorePairsBtn" style="margin-bottom:8px">Discover Unpaired Stores</button>
        <div id="suggestedPairsSection" class="hidden">
            <div class="scroll-table" style="max-height:250px"><table><thead><tr><th>Raw Name</th><th>Suggested Normal</th><th>Txns</th><th></th></tr></thead><tbody id="suggestedPairsBody"></tbody></table></div>
            <button class="btn btn-success btn-sm" id="acceptAllPairsBtn" style="margin-top:8px">Accept All</button>
        </div>
        <h3 style="margin-top:16px;font-size:14px">Duplicate Stores <span id="duplicatesCount" style="font-size:12px;color:#94a3b8"></span></h3>
        <p style="font-size:12px;color:#94a3b8;margin-bottom:8px">Normalized names that look like the same merchant (fuzzy match)</p>
        <button class="btn btn-outline btn-sm" id="detectDuplicatesBtn" style="margin-bottom:8px">Detect Duplicates</button>
        <div id="duplicatesSection" class="hidden">
            <div class="scroll-table" style="max-height:300px"><table><thead><tr><th>Suggested Name</th><th>Variants</th><th>Txns</th><th></th></tr></thead><tbody id="duplicatesBody"></tbody></table></div>
            <button class="btn btn-success btn-sm" id="consolidateAllBtn" style="margin-top:8px">Consolidate All</button>
        </div>
    </div>
    <div class="section">
        <h2>Recycle Bin</h2>
        <div class="scroll-table"><table><thead><tr><th>Date</th><th>Store / Category</th><th>Amount</th><th>Deleted</th><th></th></tr></thead><tbody id="recycleBody"></tbody></table></div>
    </div>
</div>

<!-- BUDGETS TAB -->
<div id="tab-budgets" class="hidden">
    <div class="section">
        <h2>Budget vs Actual</h2>
        <div class="form-row">
            <label style="color:#94a3b8;font-size:13px">Month:</label>
            <select id="budgetMonth"></select>
        </div>
        <canvas id="budgetChart" style="max-height:350px"></canvas>
    </div>
    <div class="section">
        <h2>Set Budget</h2>
        <div class="form-row">
            <input type="month" id="newBudgetMonth" placeholder="YYYY-MM">
            <select id="newBudgetCat"></select>
            <input type="number" id="newBudgetAmt" placeholder="Amount" step="50" style="width:100px">
            <button class="btn" id="setBudgetBtn">Set</button>
        </div>
        <div class="form-row">
            <input type="month" id="copyFromMonth" placeholder="From">
            <input type="month" id="copyToMonth" placeholder="To">
            <button class="btn" id="copyBudgetBtn">Copy Month</button>
        </div>
        <div class="scroll-table"><table><thead><tr><th>Month</th><th>Category</th><th>Budget</th><th>Status</th></tr></thead><tbody id="budgetBody"></tbody></table></div>
    </div>
</div>

<!-- REIMBURSE TAB -->
<div id="tab-reimburse" class="hidden">
    <div class="section">
        <h2>Reimbursers <span id="reimbursersCount" style="font-size:12px;color:#94a3b8"></span></h2>
        <p class="subtitle">Track income sources that offset specific expenses (e.g. Canada Life covering wellness costs).</p>
        <div class="form-row">
            <input type="text" id="newReimburserPattern" placeholder="Pattern (e.g. canada life)" list="dl-income-stores">
            <input type="text" id="newReimburserLabel" placeholder="Label (optional)">
            <select id="newReimburserType"><option value="substring">substring</option><option value="exact">exact</option></select>
            <button class="btn" id="addReimburserBtn">Add Reimburser</button>
        </div>
        <div class="scroll-table" style="max-height:200px"><table><thead><tr><th>Pattern</th><th>Label</th><th>Match</th><th></th></tr></thead><tbody id="reimbursersBody"></tbody></table></div>
        <h3 style="margin-top:16px;font-size:14px">Reimburser Pairs <span id="reimbPairsCount" style="font-size:12px;color:#94a3b8"></span></h3>
        <p style="font-size:12px;color:#94a3b8;margin-bottom:8px">Link a reimburser to the expense they typically cover</p>
        <div class="form-row">
            <input type="text" id="newPairReimburser" placeholder="Reimburser pattern (e.g. canada life)" list="dl-income-stores">
            <input type="text" id="newPairExpense" placeholder="Expense pattern (e.g. humanity wellness)" list="dl-expense-stores">
            <button class="btn" id="addReimbPairBtn">Add Pair</button>
            <button class="btn btn-outline" id="discoverPairsBtn">Discover from History</button>
        </div>
        <div class="scroll-table" style="max-height:150px"><table><thead><tr><th>Reimburser</th><th>Expense</th><th></th></tr></thead><tbody id="reimburserPairsBody"></tbody></table></div>
        <div id="discoveredPairs" class="hidden" style="margin-top:12px">
            <h4 style="font-size:13px;color:#fbbf24;margin-bottom:8px">Discovered Patterns</h4>
            <div class="scroll-table" style="max-height:300px"><table><thead><tr><th>Pattern &amp; examples</th><th></th></tr></thead><tbody id="discoveredPairsBody"></tbody></table></div>
            <button class="btn btn-success btn-sm" id="acceptAllDiscoveredBtn" style="margin-top:8px">Accept All</button>
        </div>
        <h3 style="margin-top:16px;font-size:14px">Pending Reimbursements</h3>
        <p style="font-size:12px;color:#94a3b8;margin-bottom:8px">Income from known reimbursers not yet linked to an expense.</p>
        <div class="scroll-table" style="max-height:300px"><table><thead><tr><th>Date</th><th>From</th><th>Amount</th><th>Suggested Expense</th><th>Actions</th></tr></thead><tbody id="pendingReimbBody" data-testid="pending-reimb-body"></tbody></table></div>
    </div>
</div>

<!-- TRIPS TAB -->
<div id="tab-trips" class="hidden">
    <div class="section">
        <h2>Trips</h2>
        <p class="subtitle">Tag a date range as a trip to track shared expenses and calculate splits.</p>
        <div class="form-row">
            <input type="text" id="newTripName" placeholder="Trip name (e.g. Pemberton Weekend)">
            <input type="date" id="newTripStart">
            <input type="date" id="newTripEnd">
            <input type="text" id="newTripNotes" placeholder="Notes (optional)" style="width:160px">
            <button class="btn btn-success" id="createTripBtn">Create &amp; Auto-assign</button>
        </div>
        <div style="margin-top:8px">
            <label style="font-size:12px;color:#94a3b8;display:block;margin-bottom:4px">Exclude from trip totals:</label>
            <div id="newTripExcludedCats" style="display:flex;flex-wrap:wrap;gap:8px"></div>
        </div>
        <div class="scroll-table" style="margin-top:16px"><table><thead><tr><th>Name</th><th>Dates</th><th>Txns</th><th>Total Spend</th><th>Notes</th><th></th></tr></thead><tbody id="tripsBody"></tbody></table></div>
    </div>
    <div id="tripDetailSection" class="section hidden">
        <div style="display:flex;gap:12px;align-items:center;margin-bottom:16px">
            <h2 id="tripDetailName" style="margin:0"></h2>
            <span id="tripDetailDates" style="font-size:13px;color:#94a3b8"></span>
            <button class="btn btn-sm btn-outline" onclick="App.closeTripDetail()">&#x2715; Close</button>
        </div>
        <div style="display:flex;gap:16px;align-items:center;margin-bottom:16px;flex-wrap:wrap">
            <span id="tripDetailTotal" style="font-size:18px;font-weight:600;color:#f87171"></span>
            <span style="font-size:13px;color:#94a3b8">Split:</span>
            <input type="number" id="tripSplitPct" value="60" min="0" max="100" step="5" style="width:60px;background:#0f172a;border:1px solid #334155;color:#f8fafc;padding:4px 8px;border-radius:4px;font-size:13px"> <span style="font-size:13px;color:#94a3b8">% your share</span>
            <span id="tripSplitResult" style="font-size:15px;font-weight:600;color:#4ade80"></span>
        </div>
        <div style="margin-bottom:12px">
            <label style="font-size:12px;color:#94a3b8;display:block;margin-bottom:4px">Exclude from totals:</label>
            <div id="tripExcludedCats" style="display:flex;flex-wrap:wrap;gap:8px"></div>
            <button class="btn btn-sm btn-outline" id="saveTripExclusionsBtn" style="margin-top:6px">Save exclusions &amp; recompute</button>
        </div>
        <div class="scroll-table"><table><thead><tr><th>Date</th><th>Store</th><th>Category</th><th>Amount</th><th></th></tr></thead><tbody id="tripTxnBody"></tbody></table></div>
    </div>
</div>

<!-- ANALYTICS TAB -->
<div id="tab-analytics" class="hidden">
    <div class="cards" id="velocityCards"></div>
    <div class="charts">
        <div class="chart-box"><h2>Savings Rate Trend</h2><canvas id="savingsRateChart"></canvas></div>
        <div class="chart-box"><h2>Day-of-Week Spending</h2><canvas id="dowChart"></canvas></div>
    </div>
    <div class="trend-row">
        <div class="chart-box"><h2>Category Concentration</h2><canvas id="concentrationChart"></canvas></div>
        <div class="chart-box"><h2>Top Merchants by Visits</h2><canvas id="merchantsChart"></canvas></div>
    </div>
    <div id="momSection" class="section">
        <h2>Month-over-Month Changes</h2>
        <p class="subtitle" id="momSubtitle"></p>
        <div class="scroll-table"><table><thead><tr><th>Category</th><th>Previous</th><th>Current</th><th>Change</th></tr></thead><tbody id="momBody"></tbody></table></div>
    </div>
    <div id="recurringSection" class="section">
        <h2>Detected Recurring Charges</h2>
        <p class="subtitle">Subscriptions and regular payments (similar amount, monthly cadence)</p>
        <div class="scroll-table"><table><thead><tr><th>Store</th><th>Amount</th><th>Frequency</th><th>Annual Cost</th><th>Last Seen</th></tr></thead><tbody id="recurringBody"></tbody></table></div>
    </div>
    <div id="zscoreSection" class="section">
        <h2>Statistical Outliers <span style="font-size:12px;color:#fbbf24">(z-score &ge; 2.0, min 10 txns in category)</span></h2>
        <div class="scroll-table"><table><thead><tr><th>Date</th><th>Store</th><th>Amount</th><th>Category</th><th>Z-Score</th><th>Cat Mean &plusmn; Std</th></tr></thead><tbody id="zscoreBody"></tbody></table></div>
    </div>
</div>

<!-- IMPORT TAB -->
<div id="tab-import" class="hidden">
    <div class="section">
        <h2>Import CSV Files</h2>
        <div class="import-zone" id="importZone" data-testid="import-zone">
            <p>Drag & drop CSV files here, or click to browse</p>
            <input type="file" id="fileInput" accept=".csv" multiple data-testid="file-input">
        </div>
        <div id="importPreview" class="hidden" style="margin-top:16px"></div>
        <div id="importResult" style="margin-top:16px"></div>
    </div>
    <div class="section">
        <h2>Import Filters <span id="filtersCount" style="font-size:12px;color:#94a3b8"></span></h2>
        <p class="subtitle">Transactions matching these patterns are skipped during import. Remove a filter to start importing that type.</p>
        <div class="form-row">
            <input type="text" id="newFilterPattern" placeholder="Pattern (e.g. interac e-transfer)">
            <select id="newFilterType"><option value="substring">substring</option><option value="exact">exact</option></select>
            <input type="text" id="newFilterLabel" placeholder="Label (optional)">
            <button class="btn" id="addFilterBtn">Add Filter</button>
        </div>
        <div class="scroll-table" style="max-height:300px"><table><thead><tr><th>Pattern</th><th>Match</th><th>Label</th><th></th></tr></thead><tbody id="filtersBody"></tbody></table></div>
    </div>
    <div class="section">
        <h2>Import History</h2>
        <div class="scroll-table"><table><thead><tr><th>Date</th><th>File</th><th>Rows</th><th>New</th></tr></thead><tbody id="historyBody"></tbody></table></div>
    </div>
</div>

<datalist id="dl-expense-stores"></datalist>
<datalist id="dl-expense-raw"></datalist>
<datalist id="dl-income-stores"></datalist>
<datalist id="dl-all-stores"></datalist>

<script>
const COLORS = """
        + colors_json
        + """;
const FALLBACK_COLORS = ['#E11D48','#0891B2','#7C3AED','#EA580C','#2563EB','#16A34A','#CA8A04','#DC2626','#4F46E5','#0D9488'];
function getColor(cat, idx) { return COLORS[cat] || FALLBACK_COLORS[idx % FALLBACK_COLORS.length]; }
function catBadge(cat) {
    const bg = COLORS[cat] || '#475569';
    const cls = cat === 'Uncategorized' ? ' uncategorized' : '';
    return `<span class="cat-badge${cls}" style="background:${bg}22;color:${bg}">${cat}</span>`;
}
function toast(msg) {
    const el = document.createElement('div'); el.className = 'toast'; el.dataset.testid = 'toast'; el.textContent = msg;
    document.body.appendChild(el); setTimeout(() => el.remove(), 3000);
}
async function api(path, opts={}) {
    const r = await fetch(path, opts);
    return r.json();
}
async function apiPost(path, data) {
    return api(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
}

// --- App State ---
const App = {
    data: { transactions: [], summary: {}, budgets: [], rules: [], storePairs: {}, history: [], anomalies: [], uncategorized: [], suggestions: [], deleted: [], analytics: {}, trips: [] },
    charts: {},
    _currentTrip: null,

    _txnPage: 0,
    _txnPageSize: 500,
    _txnTotal: 0,

    async init() {
        const [txns, overview, budgets, rules, pairs, history, uncat, suggest, deleted, reimbursers, pendingReimb, reimburserPairs, stores, trips, importFilters] = await Promise.all([
            api(`/api/transactions?offset=0&limit=${this._txnPageSize}`),
            api('/api/overview'),
            api('/api/budgets'),
            api('/api/rules'), api('/api/store-pairs'), api('/api/history'),
            api('/api/uncategorized'), api('/api/suggest'),
            api('/api/transactions/deleted'),
            api('/api/reimbursers'), api('/api/reimbursements/pending'),
            api('/api/reimburser-pairs'), api('/api/stores'),
            api('/api/trips'),
            api('/api/import-filters'),
        ]);
        this.data.transactions = txns.transactions || [];
        this._txnTotal = txns.total || this.data.transactions.length;
        this._txnPage = 0;
        this.data.summary = overview.summary || {};
        this.data.anomalies = overview.anomalies || [];
        this.data.analytics = overview.analytics || {};
        this.data.budgets = budgets.budgets || [];
        this.data.rules = rules.rules || [];
        this.data.storePairs = pairs.store_pairs || {};
        this.data.history = history.history || [];
        this.data.uncategorized = uncat.merchants || [];
        this.data.suggestions = suggest.suggestions || [];
        this.data.deleted = deleted.transactions || [];
        this.data.reimbursers = reimbursers.reimbursers || [];
        this.data.pendingReimb = pendingReimb.pending || [];
        this.data.reimburserPairs = reimburserPairs.pairs || [];
        this.data.expenseStores = stores.expenses || [];
        this.data.incomeStores = stores.income || [];
        this.data.trips = trips.trips || [];
        this.data.importFilters = importFilters.filters || [];
        this.renderAll();
        this.populateDataLists();
    },

    async loadMoreTransactions() {
        this._txnPage++;
        const offset = this._txnPage * this._txnPageSize;
        if (offset >= this._txnTotal) return;
        const data = await api(`/api/transactions?offset=${offset}&limit=${this._txnPageSize}`);
        this.data.transactions = this.data.transactions.concat(data.transactions || []);
        this.renderTable();
    },

    populateDataLists() {
        const expNames = [...new Set(this.data.expenseStores.map(s => s.normalized))];
        const expRaw = [...new Set(this.data.expenseStores.map(s => s.raw))];
        const incNames = [...new Set(this.data.incomeStores.map(s => s.normalized))];
        const allNorm = [...new Set([...expNames, ...incNames])];
        document.getElementById('dl-expense-stores').innerHTML = expNames.slice(0,200).map(s => `<option value="${s}">`).join('');
        document.getElementById('dl-expense-raw').innerHTML = expRaw.slice(0,200).map(s => `<option value="${s}">`).join('');
        document.getElementById('dl-income-stores').innerHTML = incNames.slice(0,200).map(s => `<option value="${s}">`).join('');
        document.getElementById('dl-all-stores').innerHTML = allNorm.slice(0,300).map(s => `<option value="${s}">`).join('');
    },

    async refresh() { await this.init(); },

    renderAll() {
        this.renderHeader();
        this.renderCards();
        this.renderCharts();
        this.renderAnomalies();
        this.renderAnalytics();
        this.renderUncategorized();
        this.renderSuggestions();
        this.renderBudgets();
        this.renderRules();
        this.renderStorePairs();
        this.renderReimbursers();
        this.renderHistory();
        this.renderImportFilters();
        this.renderRecycleBin();
        this.renderTrips();
        this.renderTripCreateExcludes();
        this.populateFilters();
        this.renderTable();
    },

    renderTripCreateExcludes(currentExcluded) {
        const cats = this.data.summary.categories || [];
        const excluded = currentExcluded !== undefined ? currentExcluded : ['Investments'];
        const excludedSet = new Set(excluded);
        const container = document.getElementById('newTripExcludedCats');
        if (container) {
            container.innerHTML = cats.map(c =>
                `<label style="font-size:12px;color:#cbd5e1"><input type="checkbox" value="${c}" ${excludedSet.has(c)?'checked':''}> ${c}</label>`
            ).join(' ');
        }
    },

    renderHeader() {
        const s = this.data.summary;
        const net = (s.net_savings || 0);
        const netColor = net >= 0 ? '#4ade80' : '#f87171';
        const netStr = (net >= 0 ? '+' : '') + '$' + Math.abs(net).toLocaleString(undefined, {maximumFractionDigits: 0});
        document.getElementById('headerSub').innerHTML =
            `<span>${s.date_min || '?'} – ${s.date_max || '?'}</span>`
            + ` <span style="color:#334155">·</span> `
            + `<span style="color:${netColor};font-weight:600">${netStr}</span> net`
            + ` <span style="color:#334155">·</span> `
            + `<span>${s.total_transactions || 0} transactions</span>`;
    },

    renderCards() {
        const s = this.data.summary;
        const avgMonthly = s.num_months ? Math.round(s.total_expenses / s.num_months) : 0;
        document.getElementById('cards').innerHTML = `
            <div class="card"><div class="label">Total Expenses</div><div class="value red">$${(s.total_expenses||0).toLocaleString()}</div></div>
            <div class="card"><div class="label">Total Income</div><div class="value green">$${(s.total_income||0).toLocaleString()}</div></div>
            <div class="card"><div class="label">Net Savings</div><div class="value ${(s.net_savings||0)>=0?'green':'red'}">$${(s.net_savings||0).toLocaleString()}</div></div>
            <div class="card"><div class="label">Avg Monthly Spend</div><div class="value blue">$${avgMonthly.toLocaleString()}</div></div>
            <div class="card"><div class="label">Classification</div><div class="value purple">${(s.classification_rate||0).toFixed(0)}%</div></div>
            <div class="card"><div class="label">Transactions</div><div class="value blue">${this._txnTotal || 0}</div></div>
            <div class="card"><div class="label">Months Tracked</div><div class="value purple">${s.num_months || 0}</div></div>
        `;
    },

    renderCharts() {
        const s = this.data.summary;
        if (!s.months || !s.months.length) return;

        Object.values(this.charts).forEach(c => c.destroy());
        this.charts = {};

        Chart.defaults.color = '#94a3b8';
        Chart.defaults.borderColor = '#334155';

        const datasets = (s.categories || []).map((cat, i) => ({
            label: cat, data: s.monthly_data.map(m => m[cat] || 0),
            backgroundColor: getColor(cat, i), borderWidth: 0
        }));
        this.charts.monthly = new Chart(document.getElementById('monthlyChart'), {
            type:'bar', data:{labels:s.months, datasets},
            options:{responsive:true, plugins:{legend:{position:'bottom',labels:{boxWidth:12,padding:8,font:{size:11}}}}, scales:{x:{stacked:true,grid:{display:false}},y:{stacked:true,ticks:{callback:v=>'$'+v.toLocaleString()}}}}
        });

        const donutColors = (s.categories||[]).map((c,i) => getColor(c,i));
        this.charts.donut = new Chart(document.getElementById('donutChart'), {
            type:'doughnut', data:{labels:s.categories, datasets:[{data:(s.categories||[]).map(c=>s.category_totals[c]||0), backgroundColor:donutColors, borderWidth:0}]},
            options:{responsive:true, cutout:'60%', plugins:{legend:{position:'bottom',labels:{boxWidth:10,padding:6,font:{size:11}}}}}
        });

        this.charts.trend = new Chart(document.getElementById('trendChart'), {
            type:'line', data:{labels:s.months, datasets:[
                {label:'Expenses', data:s.monthly_data.map(m=>m._total_expense), borderColor:'#f87171', backgroundColor:'rgba(248,113,113,0.1)', fill:true, tension:0.3},
                {label:'Income', data:s.monthly_data.map(m=>m._income), borderColor:'#4ade80', backgroundColor:'rgba(74,222,128,0.1)', fill:true, tension:0.3}
            ]},
            options:{responsive:true, plugins:{legend:{position:'bottom',labels:{boxWidth:12,font:{size:11}}}}, scales:{y:{ticks:{callback:v=>'$'+v.toLocaleString()}}}}
        });

        this.charts.incExp = new Chart(document.getElementById('incExpChart'), {
            type:'bar', data:{labels:s.months, datasets:[
                {label:'Income', data:s.monthly_data.map(m=>m._income), backgroundColor:'#4ade80'},
                {label:'Expenses', data:s.monthly_data.map(m=>m._total_expense), backgroundColor:'#f87171'}
            ]},
            options:{responsive:true, plugins:{legend:{position:'bottom',labels:{boxWidth:12,font:{size:11}}}}, scales:{y:{ticks:{callback:v=>'$'+v.toLocaleString()}}}}
        });
    },

    _anomalyPage: 5,
    _anomalyFilter: '',
    renderAnomalies() {
        const sec = document.getElementById('anomaliesSection');
        const list = document.getElementById('anomaliesList');
        if (!this.data.anomalies.length) { sec.classList.add('hidden'); return; }
        sec.classList.remove('hidden');
        const q = this._anomalyFilter.toLowerCase();
        const filtered = q ? this.data.anomalies.filter(a => a.store.toLowerCase().includes(q) || a.category.toLowerCase().includes(q)) : this.data.anomalies;
        const showing = filtered.slice(0, this._anomalyPage);
        const hasMore = filtered.length > this._anomalyPage;
        list.innerHTML = (filtered.length === 0 ? '<p style="color:#94a3b8;font-size:13px">No matching anomalies</p>' : showing.map(a => `
            <div class="anomaly-card">
                <div><strong>${a.store}</strong> · ${a.date} · ${catBadge(a.category)}<br><span style="color:#94a3b8;font-size:12px">Avg: $${a.category_avg.toLocaleString()}</span></div>
                <div style="text-align:right"><div class="mult">${a.multiplier}x</div><div style="color:#f87171;font-weight:700">$${a.amount.toLocaleString()}</div></div>
            </div>
        `).join('')) + (hasMore ? `<button class="btn btn-sm btn-outline" onclick="App.showMoreAnomalies()" style="margin-top:8px">Show More (${filtered.length - this._anomalyPage} remaining)</button>` : '');
    },
    showMoreAnomalies() {
        this._anomalyPage += 10;
        this.renderAnomalies();
    },

    renderAnalytics() {
        const a = this.data.analytics;
        if (!a || !a.velocity) return;

        // Velocity cards
        const v = a.velocity;
        const paceClass = v.projected_total > v.prev_month_total ? 'red' : 'green';
        document.getElementById('velocityCards').innerHTML = `
            <div class="card"><div class="label">Spent This Month</div><div class="value red">$${v.spent_so_far.toLocaleString()}</div></div>
            <div class="card"><div class="label">Daily Rate</div><div class="value blue">$${v.daily_rate.toLocaleString()}/day</div></div>
            <div class="card"><div class="label">Projected Total</div><div class="value ${paceClass}">$${v.projected_total.toLocaleString()}</div></div>
            <div class="card"><div class="label">Last Month</div><div class="value purple">$${v.prev_month_total.toLocaleString()}</div></div>
            <div class="card"><div class="label">Day ${v.days_elapsed} of ${v.days_in_month}</div><div class="value blue">${Math.round(v.days_elapsed/v.days_in_month*100)}%</div></div>
        `;

        // Savings rate chart
        if (this.charts.savingsRate) this.charts.savingsRate.destroy();
        const sr = a.savings_rate || [];
        this.charts.savingsRate = new Chart(document.getElementById('savingsRateChart'), {
            type: 'line',
            data: { labels: sr.map(s=>s.month), datasets: [{
                label: 'Savings Rate %', data: sr.map(s=>s.rate),
                borderColor: '#4ade80', backgroundColor: 'rgba(74,222,128,0.1)',
                fill: true, tension: 0.3, pointRadius: 5
            }]},
            options: { responsive: true, plugins: { legend: { display: false } },
                scales: { y: { ticks: { callback: v => v + '%' }, suggestedMin: -50, suggestedMax: 100 } } }
        });

        // Day-of-week chart
        if (this.charts.dow) this.charts.dow.destroy();
        const dow = a.day_of_week || [];
        const dowColors = dow.map((d,i) => i >= 5 ? '#f87171' : '#3b82f6');
        this.charts.dow = new Chart(document.getElementById('dowChart'), {
            type: 'bar',
            data: { labels: dow.map(d=>d.day), datasets: [{
                label: 'Avg Spend', data: dow.map(d=>d.avg),
                backgroundColor: dowColors, borderWidth: 0
            }]},
            options: { responsive: true, plugins: { legend: { display: false } },
                scales: { y: { ticks: { callback: v => '$' + v } } } }
        });

        // Concentration donut
        if (this.charts.concentration) this.charts.concentration.destroy();
        const conc = a.concentration || {};
        const top3 = conc.top3_categories || [];
        const otherPct = 100 - (conc.top3_pct || 0);
        this.charts.concentration = new Chart(document.getElementById('concentrationChart'), {
            type: 'doughnut',
            data: { labels: [...top3.map(c=>c.category + ' (' + c.pct + '%)'), 'Others (' + otherPct.toFixed(1) + '%)'],
                datasets: [{ data: [...top3.map(c=>c.pct), otherPct],
                    backgroundColor: [...top3.map((c,i)=>getColor(c.category,i)), '#475569'], borderWidth: 0 }]},
            options: { responsive: true, cutout: '55%', plugins: { legend: { position: 'bottom', labels: { boxWidth: 10, font: { size: 11 } } } } }
        });

        // Top merchants bar
        if (this.charts.merchants) this.charts.merchants.destroy();
        const tm = (a.top_merchants || []).slice(0, 10);
        this.charts.merchants = new Chart(document.getElementById('merchantsChart'), {
            type: 'bar',
            data: { labels: tm.map(m=>m.store.substring(0,20)), datasets: [{
                label: 'Visits', data: tm.map(m=>m.visits),
                backgroundColor: '#8b5cf6', borderWidth: 0
            }]},
            options: { responsive: true, indexAxis: 'y', plugins: { legend: { display: false } } }
        });

        // MoM table
        const mom = a.mom_deltas || [];
        if (mom.length) {
            document.getElementById('momSubtitle').textContent = `${mom[0].previous_month} → ${mom[0].current_month}`;
            document.getElementById('momBody').innerHTML = mom.map(d => {
                const arrow = d.change_pct > 0 ? '↑' : d.change_pct < 0 ? '↓' : '→';
                const cls = d.change_pct > 20 ? 'red' : d.change_pct < -20 ? 'green' : '';
                return `<tr>
                    <td>${catBadge(d.category)}</td>
                    <td style="text-align:right">$${d.previous.toLocaleString()}</td>
                    <td style="text-align:right">$${d.current.toLocaleString()}</td>
                    <td style="text-align:right" class="${cls}"><strong>${arrow} ${d.change_pct > 0 ? '+' : ''}${d.change_pct}%</strong></td>
                </tr>`;
            }).join('');
        }

        // Recurring charges
        const rec = a.recurring || [];
        document.getElementById('recurringBody').innerHTML = rec.map(r => `
            <tr>
                <td><strong>${r.store}</strong></td>
                <td style="text-align:right">$${r.avg_amount.toFixed(2)}</td>
                <td>~${r.avg_gap_days} days (${r.occurrences} charges)</td>
                <td style="text-align:right;color:#f87171;font-weight:700">$${r.annual_cost.toLocaleString()}</td>
                <td>${r.last_date}</td>
            </tr>
        `).join('');

        // Z-score outliers
        const zs = a.zscore_outliers || [];
        document.getElementById('zscoreBody').innerHTML = zs.map(o => `
            <tr>
                <td>${o.date}</td>
                <td>${o.store}</td>
                <td style="text-align:right;color:#f87171;font-weight:700">$${o.amount.toFixed(2)}</td>
                <td>${catBadge(o.category)}</td>
                <td style="text-align:right"><strong>${o.z_score}σ</strong></td>
                <td style="color:#94a3b8">$${o.category_mean.toFixed(2)} ± $${o.category_std.toFixed(2)}</td>
            </tr>
        `).join('');
    },

    renderUncategorized() {
        const body = document.getElementById('uncatBody');
        const cats = this.data.summary.categories || [];
        body.innerHTML = this.data.uncategorized.map(m => `
            <tr>
                <td>${m.store}</td>
                <td>${m.count}</td>
                <td style="text-align:right">$${m.total_spend.toFixed(2)}</td>
                <td><select onchange="App.categorizeStore('${m.store.replace(/'/g,"\\'")}', this.value)">
                    <option value="">--</option>${cats.map(c=>`<option value="${c}">${c}</option>`).join('')}
                </select></td>
            </tr>
        `).join('');
    },

    async categorizeStore(store, category) {
        if (!category) return;
        const data = await apiPost('/api/rules', {pattern: store, category, match_type: 'exact'});
        toast(`"${store}" → ${category} (${data.updated || 0} transactions updated)`);
        await this.refresh();
    },

    renderSuggestions() {
        const body = document.getElementById('suggestBody');
        body.innerHTML = this.data.suggestions.map((s, i) => `
            <tr>
                <td>${s.store}</td>
                <td>${catBadge(s.category)}</td>
                <td style="text-align:right">$${s.amount.toFixed(2)}</td>
                <td>${s.count}</td>
                <td><button class="btn btn-sm btn-success" onclick="App.applySuggestion(${i})">Accept</button></td>
            </tr>
        `).join('');
    },

    async applySuggestion(idx) {
        const s = this.data.suggestions[idx];
        const data = await apiPost('/api/rules', {pattern: s.store, category: s.category, match_type: 'exact'});
        toast(`${s.store} → ${s.category} (${data.updated || 0} transactions updated)`);
        await this.refresh();
    },

    async applyAllSuggestions() {
        if (!this.data.suggestions.length) return;
        await apiPost('/api/suggest/apply', {suggestions: this.data.suggestions});
        toast(`Applied ${this.data.suggestions.length} suggestions`);
        await this.refresh();
    },

    renderBudgets() {
        const s = this.data.summary;
        const monthSel = document.getElementById('budgetMonth');
        const months = s.months || [];
        const latest = months.length ? months[months.length-1] : '';
        monthSel.innerHTML = months.map(m => `<option value="${m}"${m===latest?' selected':''}>${m}</option>`).join('');
        if (latest) this.renderBudgetChart(latest);

        const catSel = document.getElementById('newBudgetCat');
        catSel.innerHTML = (s.categories||[]).map(c=>`<option value="${c}">${c}</option>`).join('');

        const body = document.getElementById('budgetBody');
        const budgetMap = {};
        this.data.budgets.forEach(b => { budgetMap[b.month + '|' + b.category] = b.amount; });
        body.innerHTML = this.data.budgets.map(b => {
            const md = (s.monthly_data||[]).find(m => m.month === b.month) || {};
            const actual = md[b.category] || 0;
            const over = actual > b.amount;
            const pct = b.amount > 0 ? Math.round(actual / b.amount * 100) : 0;
            return `<tr class="${over ? 'over-budget' : ''}">
                <td>${b.month}</td><td>${b.category}</td>
                <td style="text-align:right">$${b.amount.toLocaleString()}</td>
                <td style="text-align:right;color:${over ? '#f87171' : '#4ade80'}">${pct}% ${over ? '⚠ OVER' : ''}</td>
            </tr>`;
        }).join('');
    },

    renderBudgetChart(month) {
        if (this.charts.budget) this.charts.budget.destroy();
        const s = this.data.summary;
        const md = (s.monthly_data||[]).find(m => m.month === month) || {};
        const cats = (s.categories||[]).filter(c => c !== 'Uncategorized');
        const budgetMap = {};
        this.data.budgets.filter(b => b.month === month).forEach(b => { budgetMap[b.category] = b.amount; });

        const actuals = cats.map(c => md[c] || 0);
        const budgets = cats.map(c => budgetMap[c] || 0);

        this.charts.budget = new Chart(document.getElementById('budgetChart'), {
            type: 'bar',
            data: { labels: cats, datasets: [
                { label: 'Actual', data: actuals, backgroundColor: cats.map((c,i) => getColor(c,i)) },
                { label: 'Budget', data: budgets, backgroundColor: 'rgba(59,130,246,0.3)', borderColor: '#3b82f6', borderWidth: 1 }
            ]},
            options: { responsive: true, plugins: { legend: { position: 'bottom' } }, scales: { y: { ticks: { callback: v => '$' + v.toLocaleString() } } } }
        });
    },

    renderRules(filter) {
        const search = (filter || document.getElementById('rulesSearch').value || '').toLowerCase();
        const filtered = this.data.rules.filter(r => !search || r.pattern.toLowerCase().includes(search) || r.category.toLowerCase().includes(search));
        document.getElementById('rulesCount').textContent = `(${filtered.length}/${this.data.rules.length})`;
        const body = document.getElementById('rulesBody');
        body.innerHTML = filtered.map(r => {
            const desc = r.match_type === 'exact'
                ? `store matches <code style="font-size:11px;background:#0f172a;padding:1px 4px;border-radius:3px">${r.pattern}</code>`
                : `store contains <code style="font-size:11px;background:#0f172a;padding:1px 4px;border-radius:3px">${r.pattern}</code>`;
            const count = r.txn_count != null ? `<span style="font-size:11px;color:#94a3b8">${r.txn_count} txn${r.txn_count===1?'':'s'}</span>` : '';
            return `<tr><td style="font-size:12px">${desc}</td><td>${catBadge(r.category)}</td><td>${count}</td></tr>`;
        }).join('');
        const catSel = document.getElementById('newRuleCat');
        catSel.innerHTML = (this.data.summary.categories||[]).map(c=>`<option value="${c}">${c}</option>`).join('');
    },

    renderStorePairs(filter) {
        const search = (filter || document.getElementById('pairsSearch').value || '').toLowerCase();
        const pairs = this.data.storePairs;
        const allEntries = Object.entries(pairs);

        // Group by normalized name
        const byNorm = {};
        for (const [raw, norm] of allEntries) {
            if (!byNorm[norm]) byNorm[norm] = [];
            byNorm[norm].push(raw);
        }

        // Filter groups: keep if norm or any raw matches search
        const filteredGroups = Object.entries(byNorm).filter(([norm, raws]) => {
            if (!search) return true;
            return norm.toLowerCase().includes(search) || raws.some(r => r.toLowerCase().includes(search));
        });
        filteredGroups.sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]));

        const matchedRaws = search
            ? filteredGroups.reduce((n, [, raws]) => n + raws.filter(r => r.toLowerCase().includes(search) || !search).length, 0)
            : allEntries.length;
        document.getElementById('pairsCount').textContent = `(${filteredGroups.length} groups / ${matchedRaws} pairs)`;

        const body = document.getElementById('pairsBody');
        body.innerHTML = filteredGroups.map(([norm, raws]) => {
            const rawsHtml = raws.map(raw => {
                const dimmed = search && !raw.toLowerCase().includes(search) && !norm.toLowerCase().includes(search) ? 'opacity:0.4;' : '';
                return `<tr style="${dimmed}background:#0c1929">
                    <td style="padding-left:24px;font-size:11px;color:#94a3b8;font-family:monospace">${raw}</td>
                    <td style="white-space:nowrap">
                        <button class="btn btn-sm btn-danger" onclick="App.deletePair('${raw.replace(/'/g,"\\'")}')">Del</button>
                    </td>
                </tr>`;
            }).join('');
            return `<tr style="background:#0f172a">
                <td>
                    <input type="text" value="${norm}" data-norm="${norm.replace(/"/g,'&quot;')}" class="norm-rename-input"
                        style="width:220px;background:#0f172a;border:1px solid #334155;color:#f8fafc;padding:3px 8px;border-radius:4px;font-size:13px;font-weight:500" list="dl-all-stores">
                    <span style="font-size:11px;color:#64748b;margin-left:8px">${raws.length} raw name${raws.length>1?'s':''}</span>
                </td>
                <td style="white-space:nowrap">
                    <button class="btn btn-sm btn-success" onclick="App.renameNorm(this)">Rename all</button>
                </td>
            </tr>${rawsHtml}`;
        }).join('');
    },

    async renameNorm(btn) {
        const input = btn.parentElement.parentElement.querySelector('.norm-rename-input');
        const oldNorm = input.dataset.norm;
        const newNorm = input.value.trim();
        if (!newNorm || newNorm === oldNorm) return;
        const pairs = this.data.storePairs;
        const raws = Object.entries(pairs).filter(([, n]) => n === oldNorm).map(([r]) => r);
        for (const raw of raws) {
            await apiPost('/api/store-pairs', {raw_name: raw, normalized_name: newNorm});
        }
        toast(`Renamed "${oldNorm}" → "${newNorm}" (${raws.length} pairs)`);
        await this.refresh();
    },

    async savePair(btn) {
        const input = btn.parentElement.parentElement.querySelector('.pair-norm-input');
        const raw = input.dataset.raw;
        const norm = input.value.trim();
        if (!norm) return;
        const data = await apiPost('/api/store-pairs', {raw_name: raw, normalized_name: norm});
        toast(`${raw} -> ${norm} (${data.normalized} normalized, ${data.recategorized} recategorized)`);
        await this.refresh();
    },

    async deletePair(raw) {
        await apiPost('/api/store-pairs/delete', {raw_name: raw});
        toast(`Removed pair: ${raw}`);
        await this.refresh();
    },

    async discoverStorePairs() {
        const data = await api('/api/store-pairs/discover');
        const suggestions = data.suggestions || [];
        if (!suggestions.length) { toast('No unpaired stores found'); return; }
        this._discoveredStorePairs = suggestions;
        document.getElementById('suggestedPairsCount').textContent = `(${suggestions.length})`;
        document.getElementById('suggestedPairsBody').innerHTML = suggestions.map((s, i) =>
            `<tr><td>${s.raw}</td><td><input type="text" value="${s.suggested_normalized}" id="sugNorm_${i}" style="width:100%;background:#0f172a;border:1px solid #334155;color:#f8fafc;padding:4px 8px;border-radius:4px" list="dl-all-stores"></td><td>${s.count}</td><td><button class="btn btn-sm btn-success" onclick="App.acceptStorePair(${i})">Accept</button></td></tr>`
        ).join('');
        document.getElementById('suggestedPairsSection').classList.remove('hidden');
    },

    async acceptStorePair(idx) {
        const s = this._discoveredStorePairs[idx];
        const edited = document.getElementById(`sugNorm_${idx}`).value.trim();
        const normalized = edited || s.suggested_normalized;
        const data = await apiPost('/api/store-pairs', {raw_name: s.raw, normalized_name: normalized});
        toast(`${s.raw} -> ${normalized} (${data.normalized} normalized, ${data.recategorized} recategorized)`);
        await this.refresh();
    },

    async acceptAllStorePairs() {
        if (!this._discoveredStorePairs || !this._discoveredStorePairs.length) return;
        let totalNorm = 0, totalRecat = 0;
        for (let i = 0; i < this._discoveredStorePairs.length; i++) {
            const s = this._discoveredStorePairs[i];
            const el = document.getElementById(`sugNorm_${i}`);
            const normalized = (el && el.value.trim()) || s.suggested_normalized;
            const data = await apiPost('/api/store-pairs', {raw_name: s.raw, normalized_name: normalized});
            totalNorm += data.normalized || 0;
            totalRecat += data.recategorized || 0;
        }
        toast(`Accepted ${this._discoveredStorePairs.length} pairs (${totalNorm} normalized, ${totalRecat} recategorized)`);
        document.getElementById('suggestedPairsSection').classList.add('hidden');
        await this.refresh();
    },

    async detectDuplicates() {
        const data = await api('/api/duplicates');
        const dupes = data.duplicates || [];
        if (!dupes.length) { toast('No duplicate store names found'); return; }
        this._duplicates = dupes;
        document.getElementById('duplicatesCount').textContent = `(${dupes.length} groups)`;
        document.getElementById('duplicatesBody').innerHTML = dupes.map((d, i) => {
            const variants = d.variants.map(v => `${v.name} (${v.count})`).join(', ');
            return `<tr><td><input type="text" value="${d.suggested_name}" id="dupName_${i}" style="width:100%;background:#0f172a;border:1px solid #334155;color:#f8fafc;padding:4px 8px;border-radius:4px" list="dl-all-stores"></td><td style="font-size:11px;color:#94a3b8">${variants}</td><td>${d.total_txns}</td><td><button class="btn btn-sm btn-success" onclick="App.consolidateDuplicate(${i})">Merge</button></td></tr>`;
        }).join('');
        document.getElementById('duplicatesSection').classList.remove('hidden');
    },

    async consolidateDuplicate(idx) {
        const d = this._duplicates[idx];
        const target = document.getElementById(`dupName_${idx}`).value.trim() || d.suggested_name;
        let totalNorm = 0, totalRecat = 0;
        for (const v of d.variants) {
            if (v.name !== target) {
                const data = await apiPost('/api/store-pairs', {raw_name: v.name, normalized_name: target});
                totalNorm += data.normalized || 0;
                totalRecat += data.recategorized || 0;
            }
        }
        toast(`Merged ${d.variants.length} variants -> ${target} (${totalNorm} normalized, ${totalRecat} recategorized)`);
        await this.refresh();
    },

    async consolidateAllDuplicates() {
        if (!this._duplicates || !this._duplicates.length) return;
        let totalNorm = 0, totalRecat = 0;
        for (let i = 0; i < this._duplicates.length; i++) {
            const d = this._duplicates[i];
            const el = document.getElementById(`dupName_${i}`);
            const target = (el && el.value.trim()) || d.suggested_name;
            for (const v of d.variants) {
                if (v.name !== target) {
                    const data = await apiPost('/api/store-pairs', {raw_name: v.name, normalized_name: target});
                    totalNorm += data.normalized || 0;
                    totalRecat += data.recategorized || 0;
                }
            }
        }
        toast(`Consolidated ${this._duplicates.length} groups (${totalNorm} normalized, ${totalRecat} recategorized)`);
        document.getElementById('duplicatesSection').classList.add('hidden');
        await this.refresh();
    },

    renderReimbursers() {
        const body = document.getElementById('reimbursersBody');
        document.getElementById('reimbursersCount').textContent = `(${this.data.reimbursers.length})`;
        body.innerHTML = this.data.reimbursers.map(r =>
            `<tr><td>${r.pattern}</td><td>${r.label || ''}</td><td>${r.match_type}</td><td><button class="btn btn-sm btn-danger" onclick="App.deleteReimburser('${r.pattern.replace(/'/g,"\\'")}')">Del</button></td></tr>`
        ).join('');
        // Pairs
        const pairsBody = document.getElementById('reimburserPairsBody');
        document.getElementById('reimbPairsCount').textContent = `(${this.data.reimburserPairs.length})`;
        pairsBody.innerHTML = this.data.reimburserPairs.map(p =>
            `<tr><td>${p.reimburser_pattern}</td><td>${p.expense_pattern}</td><td><button class="btn btn-sm btn-danger" onclick="App.deleteReimburserPair('${p.reimburser_pattern.replace(/'/g,"\\'")}','${p.expense_pattern.replace(/'/g,"\\'")}')">Del</button></td></tr>`
        ).join('');
        // Pending with suggestions
        const pending = document.getElementById('pendingReimbBody');
        pending.innerHTML = this.data.pendingReimb.map(p => {
            const sugg = (p.suggested_expenses || []);
            const suggHtml = sugg.length > 0
                ? `<select onchange="if(this.value)App.linkFromSuggestion('${p.uuid}',this.value)"><option value="">-- suggested --</option>${sugg.map(s => `<option value="${s.uuid}">${s.date} ${s.store} -$${s.amount.toFixed(2)}</option>`).join('')}</select>`
                : `<span style="color:#64748b;font-size:11px">No pair configured</span>`;
            return `<tr><td>${p.date}</td><td>${p.store}</td><td style="color:#4ade80">+$${p.amount.toFixed(2)}</td><td>${suggHtml}</td><td><button class="btn btn-sm btn-success" onclick="App.linkPending('${p.uuid}')">Browse All</button></td></tr>`;
        }).join('');
    },

    async deleteReimburser(pattern) {
        await api('/api/reimbursers/' + encodeURIComponent(pattern), {method:'DELETE'});
        toast('Reimburser removed');
        await this.refresh();
    },

    async deleteReimburserPair(reimburserPattern, expensePattern) {
        await apiPost('/api/reimburser-pairs/delete', {reimburser_pattern: reimburserPattern, expense_pattern: expensePattern});
        toast('Pair removed');
        await this.refresh();
    },

    async linkFromSuggestion(incomeUuid, expenseUuid) {
        await api('/api/link', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({expense_uuid:expenseUuid, income_uuid:incomeUuid})});
        toast('Reimbursement linked');
        await this.refresh();
    },

    async discoverPairs() {
        const data = await api('/api/reimburser-pairs/discover');
        const discovered = data.discovered || [];
        if (!discovered.length) { toast('No patterns discovered — link some income transactions to expenses first'); return; }
        this._discoveredPairs = discovered;
        const body = document.getElementById('discoveredPairsBody');
        body.innerHTML = discovered.map((d, i) => {
            const examples = (d.examples || []).map(e =>
                `<div style="font-size:11px;color:#94a3b8;padding:2px 0">
                    <span style="color:#4ade80">+$${e.income_amount.toFixed(2)}</span> ${e.income_store} on ${e.income_date}
                    &rarr; <span style="color:#f87171">-$${e.expense_amount.toFixed(2)}</span> ${e.expense_store} (${e.expense_category}) on ${e.expense_date}
                </div>`
            ).join('');
            const alreadySaved = (this.data.reimburserPairs||[]).some(p => p.reimburser_pattern === d.reimburser_pattern && p.expense_pattern === d.expense_pattern);
            return `<tr>
                <td>
                    <div style="font-weight:500">${d.reimburser_pattern} &rarr; ${d.expense_pattern}</div>
                    <div style="font-size:11px;color:#64748b">${d.link_count} linked transaction${d.link_count>1?'s':''}</div>
                    ${examples}
                </td>
                <td style="vertical-align:top;white-space:nowrap">
                    ${alreadySaved
                        ? `<span style="font-size:11px;color:#4ade80">&#x2713; Saved</span>`
                        : `<button class="btn btn-sm btn-success" onclick="App.acceptDiscovered(${i})">Save rule</button>`}
                </td>
            </tr>`;
        }).join('');
        document.getElementById('discoveredPairs').classList.remove('hidden');
    },

    async acceptDiscovered(idx) {
        const d = this._discoveredPairs[idx];
        await apiPost('/api/reimburser-pairs', {reimburser_pattern: d.reimburser_pattern, expense_pattern: d.expense_pattern});
        toast(`Pair saved: ${d.reimburser_pattern} -> ${d.expense_pattern}`);
        await this.refresh();
    },

    async acceptAllDiscovered() {
        if (!this._discoveredPairs || !this._discoveredPairs.length) return;
        await apiPost('/api/reimburser-pairs/accept', {pairs: this._discoveredPairs});
        toast(`Saved ${this._discoveredPairs.length} pairs`);
        document.getElementById('discoveredPairs').classList.add('hidden');
        await this.refresh();
    },

    linkPending(incomeUuid) {
        const expenses = this.data.transactions.filter(t => t.type === 'expense' && t.adjustment === 0);
        const body = document.getElementById('linkModalBody');
        this._linkPendingIncomeUuid = incomeUuid;
        body.innerHTML = `<p style="font-size:13px;color:#94a3b8;margin-bottom:12px">Select an expense to offset with this reimbursement:</p>` +
            `<table style="width:100%"><thead><tr><th>Date</th><th>Store</th><th>Amount</th><th></th></tr></thead><tbody>` +
            expenses.slice(0, 100).map(t => `<tr><td>${t.date}</td><td>${t.store_normalized}</td><td style="color:#f87171">-$${t.amount.toFixed(2)}</td><td><button class="btn btn-sm btn-success" onclick="App.linkPendingTo('${t.uuid}')">Link</button></td></tr>`).join('') +
            `</tbody></table>`;
        document.getElementById('linkModal').classList.remove('hidden');
    },

    async linkPendingTo(expenseUuid) {
        await api('/api/link', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({expense_uuid:expenseUuid, income_uuid:this._linkPendingIncomeUuid})});
        document.getElementById('linkModal').classList.add('hidden');
        toast('Reimbursement linked to expense');
        await this.refresh();
    },

    renderHistory() {
        const body = document.getElementById('historyBody');
        body.innerHTML = this.data.history.map(h =>
            `<tr><td>${h.imported_at}</td><td>${h.source_file}</td><td>${h.row_count}</td><td>${h.new_count}</td></tr>`
        ).join('');
    },

    renderImportFilters() {
        const filters = this.data.importFilters || [];
        document.getElementById('filtersCount').textContent = `(${filters.length})`;
        document.getElementById('filtersBody').innerHTML = filters.map(f =>
            `<tr>
                <td><code>${f.pattern}</code></td>
                <td>${f.match_type}</td>
                <td style="color:#94a3b8">${f.label || ''}</td>
                <td><button class="btn btn-sm btn-danger" onclick="App.removeImportFilter(${f.id})">Remove</button></td>
            </tr>`
        ).join('') || '<tr><td colspan="4" style="color:#94a3b8;padding:12px">No filters — all transactions imported</td></tr>';
    },

    async addImportFilter() {
        const pattern = document.getElementById('newFilterPattern').value.trim();
        const match_type = document.getElementById('newFilterType').value;
        const label = document.getElementById('newFilterLabel').value.trim();
        if (!pattern) return;
        await apiPost('/api/import-filters', {pattern, match_type, label});
        document.getElementById('newFilterPattern').value = '';
        document.getElementById('newFilterLabel').value = '';
        const data = await api('/api/import-filters');
        this.data.importFilters = data.filters || [];
        this.renderImportFilters();
        toast('Filter added');
    },

    async removeImportFilter(id) {
        await api(`/api/import-filters/${id}`, {method:'DELETE'});
        const data = await api('/api/import-filters');
        this.data.importFilters = data.filters || [];
        this.renderImportFilters();
        toast('Filter removed');
    },

    startStoreEdit(uuid, cell, currentName) {
        const input = document.createElement('input');
        input.value = currentName;
        input.style.cssText = 'width:100%;background:#0f172a;border:1px solid #3b82f6;color:#f8fafc;padding:2px 6px;border-radius:4px;font-size:13px';
        cell.innerHTML = '';
        cell.appendChild(input);
        input.focus();
        input.select();
        const save = async () => {
            const newName = input.value.trim();
            if (!newName || newName === currentName) { cell.textContent = currentName; return; }
            const r = await api(`/api/transactions/${uuid}/store`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({store_normalized: newName})});
            if (r.ok) {
                const txn = this.data.transactions.find(t => t.uuid === uuid);
                if (txn) txn.store_normalized = newName;
                toast(`Store renamed to "${newName}"`);
            }
            cell.textContent = newName;
        };
        input.addEventListener('blur', save);
        input.addEventListener('keydown', e => { if (e.key === 'Enter') input.blur(); if (e.key === 'Escape') { cell.textContent = currentName; input.removeEventListener('blur', save); } });
    },

    renderRecycleBin() {
        const body = document.getElementById('recycleBody');
        body.innerHTML = this.data.deleted.map(t => {
            const linkedInfo = t.linked_to
                ? `<span style="font-size:11px;color:#f59e0b" title="Has linked reimbursement — restoring won't restore the link">&#x26A0; was linked</span>`
                : '';
            const deletedWhen = t.deleted_at ? `<span style="font-size:11px;color:#64748b">deleted ${t.deleted_at}</span>` : '';
            return `<tr>
                <td>${t.date}</td>
                <td>
                    <div>${t.store_normalized}</div>
                    <div style="font-size:11px;color:#64748b">${t.category || 'Uncategorized'} · ${t.source_file}</div>
                </td>
                <td style="color:#f87171">-$${t.amount.toFixed(2)}</td>
                <td>${deletedWhen} ${linkedInfo}</td>
                <td><button class="btn btn-sm" onclick="App.restoreTxn('${t.uuid}')">Restore</button></td>
            </tr>`;
        }).join('');
    },

    async restoreTxn(uuid) {
        await api(`/api/transactions/${uuid}/restore`, {method:'POST'});
        toast('Transaction restored');
        await this.refresh();
    },

    async deleteTxn(uuid) {
        await api(`/api/transactions/${uuid}`, {method:'DELETE'});
        toast('Transaction deleted');
        await this.refresh();
    },

    renderTrips() {
        const body = document.getElementById('tripsBody');
        if (!body) return;
        if (!this.data.trips.length) {
            body.innerHTML = '<tr><td colspan="6" style="color:#94a3b8;text-align:center;padding:24px">No trips yet. Create one above.</td></tr>';
            return;
        }
        body.innerHTML = this.data.trips.map(t =>
            `<tr>
                <td><button class="btn btn-sm btn-outline" onclick="App.openTrip(${t.id})">${t.name}</button></td>
                <td style="font-size:12px;color:#94a3b8">${t.start_date} – ${t.end_date}</td>
                <td>${t.txn_count}</td>
                <td style="color:#f87171">$${(t.total_spend||0).toFixed(2)}</td>
                <td style="font-size:12px;color:#64748b">${t.notes||''}</td>
                <td><button class="btn btn-sm btn-danger" onclick="App.deleteTrip(${t.id})">Del</button></td>
            </tr>`
        ).join('');
    },

    async openTrip(tripId) {
        const data = await api(`/api/trips/${tripId}`);
        this._currentTrip = data;
        const sec = document.getElementById('tripDetailSection');
        document.getElementById('tripDetailName').textContent = data.trip.name;
        document.getElementById('tripDetailDates').textContent = `${data.trip.start_date} – ${data.trip.end_date}`;

        // Render excluded category checkboxes for this trip
        const cats = this.data.summary.categories || [];
        const excluded = data.trip.excluded_categories || [];
        const excludedSet = new Set(excluded);
        const excContainer = document.getElementById('tripExcludedCats');
        if (excContainer) {
            excContainer.innerHTML = cats.map(c =>
                `<label style="font-size:12px;color:#cbd5e1"><input type="checkbox" value="${c}" ${excludedSet.has(c)?'checked':''}> ${c}</label>`
            ).join(' ');
        }
        document.getElementById('saveTripExclusionsBtn').onclick = () => App.saveTripExclusions(tripId);

        const txns = data.transactions || [];
        this._renderTripSplit(txns);
        document.getElementById('tripTxnBody').innerHTML = txns.map(t =>
            `<tr${t.is_solo ? ' style="background:#1e2d1e"' : ''}>
                <td>${t.date}</td>
                <td>${t.store_normalized||t.store_raw}</td>
                <td>${catBadge(t.category)}</td>
                <td style="color:#f87171">-$${t.amount.toFixed(2)}</td>
                <td style="white-space:nowrap">
                    <button class="btn btn-sm ${t.is_solo?'btn-success':'btn-outline'}" title="${t.is_solo?'Just you — click to split':'Split — click to mark as just you'}" onclick="App.toggleTripSolo(${tripId},'${t.uuid}')">
                        ${t.is_solo ? '&#x1F464; Just me' : '&#x1F465; Split'}
                    </button>
                    <button class="btn btn-sm btn-danger" onclick="App.removeTripTxn(${tripId},'${t.uuid}')">Remove</button>
                </td>
            </tr>`
        ).join('');
        sec.classList.remove('hidden');
        document.getElementById('tripSplitPct').oninput = () => {
            this._renderTripSplit(this._currentTrip?.transactions || []);
        };
    },

    async saveTripExclusions(tripId) {
        const excluded = [...document.querySelectorAll('#tripExcludedCats input:checked')].map(el => el.value);
        const trip = this._currentTrip?.trip;
        if (!trip) return;
        await apiPost(`/api/trips/${tripId}`, {
            name: trip.name, start_date: trip.start_date,
            end_date: trip.end_date, notes: trip.notes || '',
            excluded_categories: excluded,
        });
        toast(`Exclusions saved. Reloading trip...`);
        await this.openTrip(tripId);
        const d = await api('/api/trips');
        this.data.trips = d.trips || [];
        this.renderTrips();
    },

    _renderTripSplit(txns) {
        const pct = parseFloat(document.getElementById('tripSplitPct').value) || 60;
        const total = txns.reduce((s, t) => s + t.amount, 0);
        const soloTotal = txns.filter(t => t.is_solo).reduce((s, t) => s + t.amount, 0);
        const splitTotal = txns.filter(t => !t.is_solo).reduce((s, t) => s + t.amount, 0);
        const mine = soloTotal + splitTotal * pct / 100;
        const theirs = splitTotal * (1 - pct / 100);
        document.getElementById('tripDetailTotal').textContent = `Total: $${total.toFixed(2)}`;
        document.getElementById('tripSplitResult').textContent =
            `You: $${mine.toFixed(2)} · Partner: $${theirs.toFixed(2)}`;
    },

    closeTripDetail() {
        document.getElementById('tripDetailSection').classList.add('hidden');
        this._currentTrip = null;
    },

    async deleteTrip(tripId) {
        await api(`/api/trips/${tripId}`, {method:'DELETE'});
        toast('Trip deleted');
        await this.refresh();
    },

    async toggleTripSolo(tripId, txnUuid) {
        await apiPost(`/api/trips/${tripId}/transactions/${txnUuid}/toggle-solo`, {});
        await this.openTrip(tripId);
    },

    async removeTripTxn(tripId, txnUuid) {
        await api(`/api/trips/${tripId}/transactions/${txnUuid}`, {method:'DELETE'});
        await this.openTrip(tripId);
        const data = await api('/api/trips');
        this.data.trips = data.trips || [];
        this.renderTrips();
    },

    showLinkModal(expenseUuid) {
        this._linkExpenseUuid = expenseUuid;
        const incomes = this.data.transactions.filter(t => t.type === 'income');
        const body = document.getElementById('linkModalBody');
        if (incomes.length === 0) {
            body.innerHTML = '<p>No income transactions available to link.</p>';
        } else {
            body.innerHTML = `<table style="width:100%"><thead><tr><th>Date</th><th>Store</th><th>Amount</th><th></th></tr></thead><tbody>` +
                incomes.map(t => `<tr><td>${t.date}</td><td>${t.store_normalized}</td><td>+$${t.amount.toFixed(2)}</td><td><button class="btn btn-sm btn-success" onclick="App.linkTo('${t.uuid}')">Link</button></td></tr>`).join('') +
                `</tbody></table>`;
        }
        document.getElementById('linkModal').classList.remove('hidden');
    },

    closeLinkModal() {
        document.getElementById('linkModal').classList.add('hidden');
    },

    async linkTo(incomeUuid) {
        await api('/api/link', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({expense_uuid:this._linkExpenseUuid, income_uuid:incomeUuid})});
        this.closeLinkModal();
        toast('Offset linked');
        await this.refresh();
    },

    async unlinkTxn(expenseUuid) {
        await api('/api/unlink', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({expense_uuid:expenseUuid})});
        toast('Offset removed');
        await this.refresh();
    },

    populateFilters() {
        const s = this.data.summary;
        const catFilter = document.getElementById('categoryFilter');
        const monthFilter = document.getElementById('monthFilter');
        catFilter.innerHTML = '<option value="">All</option>' + (s.categories||[]).map(c=>`<option value="${c}">${c}</option>`).join('');
        monthFilter.innerHTML = '<option value="">All</option>' + (s.months||[]).map(m=>`<option value="${m}">${m}</option>`).join('');
    },

    renderTable() {
        const search = document.getElementById('searchInput').value.toLowerCase();
        const cat = document.getElementById('categoryFilter').value;
        const month = document.getElementById('monthFilter').value;
        const type = document.getElementById('typeFilter').value;
        const minAmt = parseFloat(document.getElementById('minAmount').value) || 0;
        const maxAmt = parseFloat(document.getElementById('maxAmount').value) || Infinity;
        const dateFrom = document.getElementById('dateFrom').value;
        const dateTo = document.getElementById('dateTo').value;

        let filtered = this.data.transactions.filter(t => {
            if (search && !t.store_raw.toLowerCase().includes(search) && !t.store_normalized.toLowerCase().includes(search) && !t.category.toLowerCase().includes(search)) return false;
            if (cat && t.category !== cat) return false;
            if (month && t.month !== month) return false;
            if (type && t.type !== type) return false;
            if (t.amount < minAmt || t.amount > maxAmt) return false;
            if (dateFrom && t.date < dateFrom) return false;
            if (dateTo && t.date > dateTo) return false;
            return true;
        });

        if (App._sortCol) {
            filtered.sort((a, b) => {
                let va = a[App._sortCol], vb = b[App._sortCol];
                if (App._sortCol === 'amount') { va = +va; vb = +vb; }
                if (App._sortCol === 'store') { va = a.store_normalized; vb = b.store_normalized; }
                if (va < vb) return App._sortAsc ? -1 : 1;
                if (va > vb) return App._sortAsc ? 1 : -1;
                return 0;
            });
        }

        this._filtered = filtered;
        const hasMore = this.data.transactions.length < this._txnTotal;
        const loadedInfo = hasMore ? ` (${this.data.transactions.length}/${this._txnTotal} loaded)` : '';
        document.getElementById('txnCount').innerHTML =
            `Showing ${filtered.length} of ${this.data.transactions.length} transactions${loadedInfo}` +
            (filtered.length < this.data.transactions.length ? ` — $${filtered.reduce((s,t)=>s+t.amount,0).toLocaleString(undefined,{minimumFractionDigits:2})} total` : '') +
            (hasMore ? ` <button class="btn btn-sm btn-outline" onclick="App.loadMoreTransactions()">Load More</button>` : '');

        const cats = this.data.summary.categories || [];
        const catOpts = cats.map(c=>`<option value="${c}">${c}</option>`).join('');
        document.getElementById('txnBody').innerHTML = filtered.slice(0, 500).map(t => {
            const checked = this._selected.has(t.uuid) ? 'checked' : '';
            const hasOffset = t.adjustment > 0;
            const amtDisplay = hasOffset
                ? `<span style="text-decoration:line-through;opacity:0.5">$${t.amount.toFixed(2)}</span> $${t.effective_amount.toFixed(2)}`
                : `$${t.amount.toFixed(2)}`;
            const linkBtn = t.type === 'expense' && !hasOffset
                ? `<button class="btn btn-sm btn-outline" onclick="App.showLinkModal('${t.uuid}')" title="Link offset">Lnk</button>`
                : hasOffset
                ? `<button class="btn btn-sm btn-outline" onclick="App.unlinkTxn('${t.uuid}')" title="Remove offset" style="color:#f59e0b">Ulk</button>`
                : '';
            const tripNames = (this.data.trips||[]).filter(trip => {
                return trip.start_date <= t.date && t.date <= trip.end_date;
            }).map(trip => `<span style="font-size:11px;background:#1e3a5f;color:#93c5fd;padding:1px 6px;border-radius:4px;cursor:pointer" onclick="App.openTrip(${trip.id})">${trip.name}</span>`).join(' ');
            const isTransfer = t.type === 'transfer';
            const transferBtn = t.type !== 'income'
                ? `<button class="btn btn-sm btn-outline" style="${isTransfer?'color:#a78bfa':'color:#64748b'}" title="${isTransfer?'Mark as expense':'Mark as transfer (exclude from totals)'}" onclick="App.toggleTransfer('${t.uuid}','${t.type}')">${isTransfer?'Xfr&#x2713;':'Xfr'}</button>`
                : '';
            const rowBg = isTransfer ? ' style="opacity:0.5"' : hasOffset ? ' style="background:#1c2a1a"' : '';
            return `<tr${rowBg}>
                <td><input type="checkbox" data-uuid="${t.uuid}" ${checked} onchange="App.toggleSelect('${t.uuid}', this.checked)"></td>
                <td>${t.date}</td>
                <td title="${t.store_raw}" class="store-cell" onclick="App.startStoreEdit('${t.uuid}', this, '${(t.store_normalized||t.store_raw).replace(/'/g,"\\'")}')" style="cursor:pointer" data-uuid="${t.uuid}">${t.store_normalized}</td>
                <td>${catBadge(t.category)} <select class="inline-cat-select" onchange="App.inlineCategory('${t.uuid}', '${(t.store_normalized||t.store_raw).replace(/'/g,"\\'")}', this.value)"><option value="">edit</option>${catOpts}</select></td>
                <td style="text-align:right;font-variant-numeric:tabular-nums">${t.type==='income'?'+':'-'}${amtDisplay}</td>
                <td>${t.type}</td>
                <td>${tripNames}</td>
                <td style="display:flex;gap:4px">${transferBtn}${linkBtn}<button class="btn btn-sm btn-danger" onclick="App.deleteTxn('${t.uuid}')">Del</button></td>
            </tr>`;
        }).join('');
        document.getElementById('selectAll').checked = false;
        this.updateBulkBar();
    },
    _sortCol: 'date', _sortAsc: false,
    _selected: new Set(),
    _filtered: [],

    toggleSelect(uuid, checked) {
        if (checked) this._selected.add(uuid); else this._selected.delete(uuid);
        this.updateBulkBar();
    },

    toggleSelectAll(checked) {
        const checkboxes = document.querySelectorAll('#txnBody input[type="checkbox"]');
        checkboxes.forEach(cb => { cb.checked = checked; if (checked) this._selected.add(cb.dataset.uuid); else this._selected.delete(cb.dataset.uuid); });
        this.updateBulkBar();
    },

    updateBulkBar() {
        const bar = document.getElementById('bulkBar');
        const count = this._selected.size;
        document.getElementById('bulkCount').textContent = count;
        if (count > 0) bar.classList.remove('hidden'); else bar.classList.add('hidden');
        const catSel = document.getElementById('bulkCatSelect');
        const cats = this.data.summary.categories || [];
        catSel.innerHTML = '<option value="">Assign category...</option>' + cats.map(c=>`<option value="${c}">${c}</option>`).join('');
    },

    async bulkAssignCategory() {
        const cat = document.getElementById('bulkCatSelect').value;
        if (!cat || !this._selected.size) return;
        const uuids = [...this._selected];
        for (const uuid of uuids) {
            await api(`/api/transactions/${uuid}/category`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({category: cat})});
        }
        toast(`Assigned ${cat} to ${uuids.length} transactions`);
        this._selected.clear();
        await this.refresh();
    },

    async bulkDelete() {
        if (!this._selected.size) return;
        const uuids = [...this._selected];
        for (const uuid of uuids) { await api(`/api/transactions/${uuid}`, {method:'DELETE'}); }
        toast(`Deleted ${uuids.length} transactions`);
        this._selected.clear();
        await this.refresh();
    },

    async inlineCategory(uuid, store, category) {
        if (!category) return;
        await api(`/api/transactions/${uuid}/category`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({category})});
        if (store) {
            await apiPost('/api/rules', {pattern: store, category, match_type: 'exact'});
        }
        toast(`${store || uuid} → ${category} (rule saved)`);
        await this.refresh();
    },

    async toggleTransfer(uuid, currentType) {
        const newType = currentType === 'transfer' ? 'expense' : 'transfer';
        await api(`/api/transactions/${uuid}/type`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({type: newType})});
        toast(newType === 'transfer' ? 'Marked as transfer — excluded from totals' : 'Marked as expense');
        await this.refresh();
    },

    exportCsv() {
        const rows = this._filtered || this.data.transactions;
        const header = 'Date,Store,Category,Amount,Type\\n';
        const csv = header + rows.map(t => `${t.date},"${t.store_normalized.replace(/"/g,'""')}",${t.category},${t.amount},${t.type}`).join('\\n');
        const blob = new Blob([csv], {type:'text/csv'});
        const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'transactions.csv'; a.click();
    }
};

// --- Tab switching ---
const ALL_TABS = ['overview','transactions','organize','budgets','reimburse','trips','analytics','import'];
document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        ALL_TABS.forEach(name => {
            document.getElementById('tab-'+name).classList.toggle('hidden', name !== tab.dataset.tab);
        });
    });
});

// --- Table sorting ---
document.querySelectorAll('#txnTable th[data-col]').forEach(th => {
    th.addEventListener('click', () => {
        const col = th.dataset.col;
        if (App._sortCol === col) App._sortAsc = !App._sortAsc;
        else { App._sortCol = col; App._sortAsc = true; }
        App.renderTable();
    });
});

// --- Filter listeners ---
['searchInput','categoryFilter','monthFilter','typeFilter','minAmount','maxAmount','dateFrom','dateTo']
    .forEach(id => document.getElementById(id).addEventListener('input', () => App.renderTable()));

// --- Anomaly search ---
document.getElementById('anomalySearch').addEventListener('input', e => { App._anomalyFilter = e.target.value; App._anomalyPage = 5; App.renderAnomalies(); });

// --- Budget month change ---
document.getElementById('budgetMonth').addEventListener('change', e => App.renderBudgetChart(e.target.value));

// --- Set budget ---
document.getElementById('setBudgetBtn').addEventListener('click', async () => {
    const month = document.getElementById('newBudgetMonth').value;
    const cat = document.getElementById('newBudgetCat').value;
    const amt = parseFloat(document.getElementById('newBudgetAmt').value);
    if (!month || !cat || !amt) return;
    await apiPost('/api/budgets', {month, category: cat, amount: amt});
    toast(`Budget set: ${cat} = $${amt}`);
    await App.refresh();
});

// --- Copy budget ---
document.getElementById('copyBudgetBtn').addEventListener('click', async () => {
    const from = document.getElementById('copyFromMonth').value;
    const to = document.getElementById('copyToMonth').value;
    if (!from || !to) return;
    const r = await apiPost('/api/budgets/copy', {from_month: from, to_month: to});
    toast(`Copied ${r.count} budgets`);
    await App.refresh();
});

// --- Add rule ---
document.getElementById('addRuleBtn').addEventListener('click', async () => {
    const pattern = document.getElementById('newRulePattern').value.trim();
    const cat = document.getElementById('newRuleCat').value;
    const type = document.getElementById('newRuleType').value;
    if (!pattern || !cat) return;
    const data = await apiPost('/api/rules', {pattern, category: cat, match_type: type});
    toast(`Rule added: ${pattern} → ${cat} (${data.updated || 0} transactions updated)`);
    document.getElementById('newRulePattern').value = '';
    await App.refresh();
});

document.getElementById('normalizeRulesBtn').addEventListener('click', async () => {
    const data = await apiPost('/api/rules/normalize', {});
    toast(`Normalized ${data.normalized} rule pattern${data.normalized === 1 ? '' : 's'}`);
    await App.refresh();
});

// --- Add store pair ---
document.getElementById('addPairBtn').addEventListener('click', async () => {
    const raw = document.getElementById('newPairRaw').value.trim();
    const norm = document.getElementById('newPairNorm').value.trim();
    if (!raw || !norm) return;
    const data = await apiPost('/api/store-pairs', {raw_name: raw, normalized_name: norm});
    toast(`${raw} → ${norm} (${data.normalized || 0} normalized, ${data.recategorized || 0} recategorized)`);
    document.getElementById('newPairRaw').value = '';
    document.getElementById('newPairNorm').value = '';
    await App.refresh();
});

// --- Discover store pairs ---
document.getElementById('discoverStorePairsBtn').addEventListener('click', () => App.discoverStorePairs());
document.getElementById('acceptAllPairsBtn').addEventListener('click', () => App.acceptAllStorePairs());
document.getElementById('detectDuplicatesBtn').addEventListener('click', () => App.detectDuplicates());
document.getElementById('consolidateAllBtn').addEventListener('click', () => App.consolidateAllDuplicates());

// --- Add reimburser ---
document.getElementById('addReimburserBtn').addEventListener('click', async () => {
    const pattern = document.getElementById('newReimburserPattern').value.trim();
    const label = document.getElementById('newReimburserLabel').value.trim();
    const matchType = document.getElementById('newReimburserType').value;
    if (!pattern) return;
    await apiPost('/api/reimbursers', {pattern, label, match_type: matchType});
    toast(`Reimburser added: ${pattern}`);
    document.getElementById('newReimburserPattern').value = '';
    document.getElementById('newReimburserLabel').value = '';
    await App.refresh();
});

// --- Reimburser pairs ---
document.getElementById('addReimbPairBtn').addEventListener('click', async () => {
    const rp = document.getElementById('newPairReimburser').value.trim();
    const ep = document.getElementById('newPairExpense').value.trim();
    if (!rp || !ep) return;
    await apiPost('/api/reimburser-pairs', {reimburser_pattern: rp, expense_pattern: ep});
    toast(`Pair added: ${rp} -> ${ep}`);
    document.getElementById('newPairReimburser').value = '';
    document.getElementById('newPairExpense').value = '';
    await App.refresh();
});
document.getElementById('discoverPairsBtn').addEventListener('click', () => App.discoverPairs());
document.getElementById('acceptAllDiscoveredBtn').addEventListener('click', () => App.acceptAllDiscovered());

// --- Apply all suggestions ---
document.getElementById('applyAllSuggBtn').addEventListener('click', () => App.applyAllSuggestions());

// --- Re-categorize all ---
document.getElementById('recatAllBtn').addEventListener('click', async () => {
    const r = await apiPost('/api/recategorize', {});
    const msg = r.updated > 0 ? `Re-categorized ${r.updated} transactions` : 'No new matches found';
    document.getElementById('recatResult').textContent = msg;
    toast(msg);
    await App.refresh();
});

// --- Bulk actions ---
document.getElementById('selectAll').addEventListener('change', e => App.toggleSelectAll(e.target.checked));
document.getElementById('bulkCatBtn').addEventListener('click', () => App.bulkAssignCategory());
document.getElementById('bulkDeleteBtn').addEventListener('click', () => App.bulkDelete());
document.getElementById('bulkClearBtn').addEventListener('click', () => { App._selected.clear(); App.renderTable(); });

// --- CSV export ---
document.getElementById('exportCsvBtn').addEventListener('click', () => App.exportCsv());

// --- Import filters ---
document.getElementById('addFilterBtn').addEventListener('click', () => App.addImportFilter());

// --- Rules/pairs search ---
document.getElementById('rulesSearch').addEventListener('input', () => App.renderRules());
document.getElementById('pairsSearch').addEventListener('input', () => App.renderStorePairs());

// --- File import ---
const importZone = document.getElementById('importZone');
const fileInput = document.getElementById('fileInput');
importZone.addEventListener('click', () => fileInput.click());
importZone.addEventListener('dragover', e => { e.preventDefault(); importZone.classList.add('dragover'); });
importZone.addEventListener('dragleave', () => importZone.classList.remove('dragover'));
importZone.addEventListener('drop', e => { e.preventDefault(); importZone.classList.remove('dragover'); handleFiles(e.dataTransfer.files); });
fileInput.addEventListener('change', e => handleFiles(e.target.files));

async function handleFiles(files) {
    const preview = document.getElementById('importPreview');
    const result = document.getElementById('importResult');
    result.innerHTML = '';
    preview.classList.remove('hidden');
    let html = '';
    for (const file of files) {
        const form = new FormData(); form.append('file', file);
        const r = await fetch('/api/import/preview', {method:'POST', body: form});
        const data = await r.json();
        const p = data.preview;
        html += `<div class="section" style="margin-bottom:12px">
            <strong>${p.filename}</strong>: ${p.parsed} transactions (${p.expenses} expenses, ${p.income} income)<br>
            Classified: ${p.classified} | Unclassified: ${p.unclassified}<br>
            Date range: ${p.date_range.join(' to ')}<br>
            <button class="btn btn-success" style="margin-top:8px" onclick="doImport('${file.name}', this)">Confirm Import</button>
        </div>`;
    }
    preview.innerHTML = html;
    window._pendingFiles = files;
}

async function doImport(filename, btn) {
    const files = window._pendingFiles;
    for (const file of files) {
        if (file.name === filename) {
            const form = new FormData(); form.append('file', file);
            const r = await fetch('/api/import', {method:'POST', body: form});
            const data = await r.json();
            btn.textContent = `Imported: ${data.result.inserted || 0} new`;
            btn.disabled = true;
            toast(`Imported ${data.result.inserted || 0} transactions`);
            await App.refresh();
            break;
        }
    }
}

// --- Trips ---
document.getElementById('createTripBtn').addEventListener('click', async () => {
    const name = document.getElementById('newTripName').value.trim();
    const start = document.getElementById('newTripStart').value;
    const end = document.getElementById('newTripEnd').value;
    const notes = document.getElementById('newTripNotes').value.trim();
    if (!name || !start || !end) { toast('Name, start and end date required'); return; }
    const excluded = [...document.querySelectorAll('#newTripExcludedCats input:checked')].map(el => el.value);
    const r = await apiPost('/api/trips', {name, start_date: start, end_date: end, notes, auto_assign: true, excluded_categories: excluded});
    toast(`Trip created, ${r.assigned} transactions assigned`);
    document.getElementById('newTripName').value = '';
    document.getElementById('newTripNotes').value = '';
    await App.refresh();
});

// --- Init ---
App.init();
</script>

<div id="linkModal" class="hidden" style="position:fixed;inset:0;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:1000" onclick="if(event.target===this)App.closeLinkModal()">
    <div style="background:#1e293b;border-radius:12px;padding:24px;max-width:600px;width:90%;max-height:70vh;overflow-y:auto">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
            <h3 style="margin:0">Link Income Offset</h3>
            <button class="btn btn-sm btn-outline" onclick="App.closeLinkModal()">X</button>
        </div>
        <p style="font-size:13px;color:#94a3b8;margin-bottom:12px">Select an income transaction to offset this expense. The income amount will be subtracted from the expense total.</p>
        <div id="linkModalBody"></div>
    </div>
</div>

</body>
</html>"""
    )
