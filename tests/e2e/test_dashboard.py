"""E2E tests for the interactive dashboard.

Design principles:
- No sleep/wait_for_timeout — use Playwright auto-waiting locators and expect()
- Tests are independent and parallel-safe (unique identifiers for mutations)
- Each test gets a fresh page but shares the session-scoped server
- Mutations use unique names so concurrent runs don't collide
"""

import re
import uuid as uuid_mod

import pytest
from playwright.sync_api import Page, expect

from .pages import DashboardPage

pytestmark = pytest.mark.e2e


@pytest.fixture
def dashboard(page: Page, base_url: str) -> DashboardPage:
    dp = DashboardPage(page, base_url)
    dp.navigate()
    return dp


def _uid() -> str:
    return uuid_mod.uuid4().hex[:8]


# --- Page Load & Rendering ---


class TestPageLoad:
    def test_has_title(self, dashboard: DashboardPage):
        expect(dashboard.page).to_have_title("show-me-the-money")

    def test_summary_cards_show_financial_data(self, dashboard: DashboardPage):
        cards = dashboard.cards
        expect(cards).to_contain_text("Total Expenses")
        expect(cards).to_contain_text("Total Income")
        expect(cards).to_contain_text("Net Savings")
        expect(cards).to_contain_text("$")

    def test_monthly_chart_renders(self, dashboard: DashboardPage):
        canvas = dashboard.page.locator("#monthlyChart")
        expect(canvas).to_be_visible()

    def test_donut_chart_renders(self, dashboard: DashboardPage):
        canvas = dashboard.page.locator("#donutChart")
        expect(canvas).to_be_visible()

    def test_trend_chart_renders(self, dashboard: DashboardPage):
        canvas = dashboard.page.locator("#trendChart")
        expect(canvas).to_be_visible()

    def test_income_expense_chart_renders(self, dashboard: DashboardPage):
        canvas = dashboard.page.locator("#incExpChart")
        expect(canvas).to_be_visible()

    def test_transaction_table_has_rows(self, dashboard: DashboardPage):
        expect(dashboard.txn_rows.first).to_be_visible()
        expect(dashboard.page.locator("#txnCount")).to_contain_text("Showing")

    def test_header_shows_date_range(self, dashboard: DashboardPage):
        header = dashboard.page.locator("#headerSub")
        expect(header).to_contain_text("2026")
        expect(header).to_contain_text("transactions")


# --- Tab Navigation ---


class TestTabs:
    def test_analytics_tab(self, dashboard: DashboardPage):
        dashboard.click_tab("analytics")
        expect(dashboard.page.locator("#tab-analytics")).to_be_visible()
        expect(dashboard.page.locator("#tab-overview")).to_be_hidden()

    def test_import_tab(self, dashboard: DashboardPage):
        dashboard.click_tab("import")
        expect(dashboard.page.locator("#tab-import")).to_be_visible()
        expect(dashboard.page.get_by_test_id("import-zone")).to_be_visible()

    def test_categorize_tab(self, dashboard: DashboardPage):
        dashboard.click_tab("categorize")
        expect(dashboard.page.locator("#tab-categorize")).to_be_visible()
        expect(dashboard.page.get_by_test_id("recategorize-btn")).to_be_visible()

    def test_budgets_tab(self, dashboard: DashboardPage):
        dashboard.click_tab("budgets")
        expect(dashboard.page.locator("#tab-budgets")).to_be_visible()
        expect(dashboard.page.locator("#budgetChart")).to_be_visible()

    def test_manage_tab(self, dashboard: DashboardPage):
        dashboard.click_tab("manage")
        expect(dashboard.page.locator("#tab-manage")).to_be_visible()
        expect(dashboard.page.locator("#rulesSearch")).to_be_visible()


# --- Transaction Filtering ---


class TestFiltering:
    def test_search_narrows_results(self, dashboard: DashboardPage):
        expect(dashboard.txn_rows.first).to_be_visible()
        initial = dashboard.txn_rows.count()
        dashboard.search_transactions("starbucks")
        expect(dashboard.page.locator("#txnCount")).to_contain_text("Showing")
        assert dashboard.txn_rows.count() < initial
        assert dashboard.txn_rows.count() > 0

    def test_category_filter(self, dashboard: DashboardPage):
        expect(dashboard.txn_rows.first).to_be_visible()
        initial = dashboard.txn_rows.count()
        dashboard.filter_category("Dining")
        assert dashboard.txn_rows.count() < initial
        assert dashboard.txn_rows.count() > 0

    def test_date_range_filter(self, dashboard: DashboardPage):
        expect(dashboard.txn_rows.first).to_be_visible()
        initial = dashboard.txn_rows.count()
        dashboard.filter_date_range("2026-01-01", "2026-01-31")
        assert dashboard.txn_rows.count() < initial
        assert dashboard.txn_rows.count() > 0

    def test_search_case_insensitive(self, dashboard: DashboardPage):
        dashboard.search_transactions("STARBUCKS")
        expect(dashboard.txn_rows.first).to_be_visible()
        assert dashboard.txn_rows.count() > 0

    def test_no_results_shows_zero(self, dashboard: DashboardPage):
        dashboard.search_transactions("zzz_nonexistent_store_zzz")
        expect(dashboard.page.locator("#txnCount")).to_contain_text("Showing 0")


# --- Bulk Select & Actions ---


class TestBulkActions:
    def test_select_shows_bulk_bar(self, dashboard: DashboardPage):
        expect(dashboard.bulk_bar).to_be_hidden()
        dashboard.select_transaction_checkbox(0)
        expect(dashboard.bulk_bar).to_be_visible()
        expect(dashboard.page.locator("#bulkCount")).to_have_text("1")

    def test_select_all_selects_visible(self, dashboard: DashboardPage):
        dashboard.search_transactions("starbucks")
        expect(dashboard.txn_rows.first).to_be_visible()
        count_before = dashboard.txn_rows.count()
        dashboard.select_all()
        expect(dashboard.bulk_bar).to_be_visible()
        expect(dashboard.page.locator("#bulkCount")).to_have_text(str(count_before))

    def test_clear_hides_bulk_bar(self, dashboard: DashboardPage):
        dashboard.select_transaction_checkbox(0)
        expect(dashboard.bulk_bar).to_be_visible()
        dashboard.bulk_clear()
        expect(dashboard.bulk_bar).to_be_hidden()

    def test_bulk_assign_category(self, dashboard: DashboardPage):
        dashboard.search_transactions("safeway")
        expect(dashboard.txn_rows.first).to_be_visible()
        dashboard.select_transaction_checkbox(0)
        expect(dashboard.bulk_bar).to_be_visible()
        dashboard.bulk_assign_category("Dining")
        expect(dashboard.toast).to_contain_text("Dining")

    def test_bulk_delete(self, dashboard: DashboardPage):
        dashboard.search_transactions("random place 123")
        expect(dashboard.txn_rows.first).to_be_visible()
        dashboard.select_transaction_checkbox(0)
        dashboard.bulk_delete()
        expect(dashboard.toast).to_contain_text("Deleted")


# --- Inline Category Edit ---


class TestInlineCategory:
    def test_inline_assign_updates_transaction(self, dashboard: DashboardPage):
        dashboard.search_transactions("unknown shop xyz")
        expect(dashboard.txn_rows.first).to_be_visible()
        dashboard.inline_set_category(0, "Health")
        expect(dashboard.toast).to_contain_text("Updated")


# --- Delete & Restore ---


class TestDeleteRestore:
    def test_delete_moves_to_recycle_bin(self, dashboard: DashboardPage):
        dashboard.search_transactions("shell gas station")
        expect(dashboard.txn_rows.first).to_be_visible()
        dashboard.delete_row(0)
        expect(dashboard.toast).to_contain_text("deleted")
        dashboard.click_tab("manage")
        expect(dashboard.recycle_bin_rows.first).to_be_visible()

    def test_restore_from_recycle_bin(self, dashboard: DashboardPage):
        dashboard.search_transactions("winners outlet")
        expect(dashboard.txn_rows.first).to_be_visible()
        dashboard.delete_row(0)
        expect(dashboard.toast).to_contain_text("deleted")
        dashboard.click_tab("manage")
        expect(dashboard.recycle_bin_rows.first).to_be_visible()
        count_before = dashboard.recycle_bin_rows.count()
        dashboard.restore_first_deleted()
        expect(dashboard.page.get_by_test_id("toast").last).to_contain_text("restored")


# --- Import ---


class TestImport:
    def test_csv_upload_shows_preview(self, dashboard: DashboardPage):
        uid = _uid()
        csv = (
            "Transaction Date,Description,Amount\n"
            f"01/05/2026,E2E_STORE_{uid},-25.50\n"
            f"01/06/2026,E2E_INCOME_{uid},1000.00\n"
        )
        dashboard.upload_csv(f"e2e_test_{uid}.csv", csv)
        preview = dashboard.page.locator("#importPreview")
        expect(preview).to_contain_text("transactions")

    def test_confirm_import_inserts_data(self, dashboard: DashboardPage):
        uid = _uid()
        csv = (
            "Transaction Date,Description,Amount\n"
            f"02/10/2026,IMPORT_TEST_{uid},-99.99\n"
        )
        dashboard.upload_csv(f"import_{uid}.csv", csv)
        expect(dashboard.page.locator("#importPreview")).to_contain_text(
            "1 transactions"
        )
        dashboard.confirm_import()
        expect(dashboard.toast).to_contain_text("Imported")


# --- Categorize ---


class TestCategorize:
    def test_recategorize_button_runs(self, dashboard: DashboardPage):
        dashboard.click_recategorize()
        expect(dashboard.recategorize_result).to_have_text(
            re.compile(r"(categorized|no new)", re.IGNORECASE)
        )

    def test_uncategorized_section_exists(self, dashboard: DashboardPage):
        dashboard.click_tab("categorize")
        section = dashboard.page.locator("#uncategorizedSection")
        expect(section).to_be_visible()


# --- Budgets ---


class TestBudgets:
    def test_budget_chart_renders(self, dashboard: DashboardPage):
        dashboard.click_tab("budgets")
        expect(dashboard.page.locator("#budgetChart")).to_be_visible()

    def test_set_budget(self, dashboard: DashboardPage):
        dashboard.set_budget("2026-06", "Dining", "999")
        expect(dashboard.toast).to_contain_text("Budget set")

    def test_over_budget_highlighted(self, dashboard: DashboardPage):
        dashboard.click_tab("budgets")
        over = dashboard.page.locator(".over-budget")
        expect(over.first).to_be_visible()

    def test_budget_table_shows_status(self, dashboard: DashboardPage):
        dashboard.click_tab("budgets")
        expect(dashboard.budget_rows.first).to_be_visible()
        expect(dashboard.budget_rows.first).to_contain_text("%")


# --- Manage: Rules ---


class TestRules:
    def test_add_rule(self, dashboard: DashboardPage):
        uid = _uid()
        dashboard.add_rule(f"e2e_pattern_{uid}", "Dining")
        expect(dashboard.toast).to_contain_text("Rule added")

    def test_search_rules_filters(self, dashboard: DashboardPage):
        dashboard.click_tab("manage")
        count_el = dashboard.page.locator("#rulesCount")
        expect(count_el).to_have_text(re.compile(r"\(\d+/\d+\)"))
        dashboard.search_rules("starbucks")
        expect(count_el).to_have_text(re.compile(r"\(\d+/\d+\)"))

    def test_rules_count_shows_total(self, dashboard: DashboardPage):
        dashboard.click_tab("manage")
        count_el = dashboard.page.locator("#rulesCount")
        expect(count_el).to_have_text(re.compile(r"\(\d+/\d+\)"))


# --- Manage: Store Pairs ---


class TestStorePairs:
    def test_add_store_pair(self, dashboard: DashboardPage):
        uid = _uid()
        dashboard.add_store_pair(f"raw_{uid}", f"norm_{uid}")
        expect(dashboard.toast).to_contain_text("Pair added")

    def test_search_pairs_filters(self, dashboard: DashboardPage):
        uid = _uid()
        dashboard.add_store_pair(f"searchable_{uid}", f"target_{uid}")
        expect(dashboard.toast).to_be_visible()
        dashboard.search_pairs(f"searchable_{uid}")
        expect(dashboard.page.locator("#pairsCount")).to_contain_text("1/")


# --- Analytics ---


class TestAnalytics:
    def test_velocity_cards_render(self, dashboard: DashboardPage):
        dashboard.click_tab("analytics")
        expect(dashboard.velocity_cards).to_contain_text("$")
        expect(dashboard.velocity_cards).to_contain_text("day")

    def test_savings_rate_chart(self, dashboard: DashboardPage):
        dashboard.click_tab("analytics")
        expect(dashboard.page.locator("#savingsRateChart")).to_be_visible()

    def test_dow_chart(self, dashboard: DashboardPage):
        dashboard.click_tab("analytics")
        expect(dashboard.page.locator("#dowChart")).to_be_visible()

    def test_merchants_chart(self, dashboard: DashboardPage):
        dashboard.click_tab("analytics")
        expect(dashboard.page.locator("#merchantsChart")).to_be_visible()

    def test_mom_table_populated(self, dashboard: DashboardPage):
        dashboard.click_tab("analytics")
        expect(dashboard.mom_rows.first).to_be_visible()

    def test_recurring_charges_detected(self, dashboard: DashboardPage):
        dashboard.click_tab("analytics")
        # Seed data has recurring stores
        expect(dashboard.recurring_rows.first).to_be_visible()

    def test_zscore_outliers_table(self, dashboard: DashboardPage):
        dashboard.click_tab("analytics")
        table = dashboard.page.locator("#zscoreBody")
        expect(table).to_be_visible()


# --- CSV Export ---


class TestExport:
    def test_export_button_visible(self, dashboard: DashboardPage):
        expect(dashboard.export_btn).to_be_visible()
        expect(dashboard.export_btn).to_have_text("Export CSV")


# --- Table Sorting ---


class TestSorting:
    def test_sort_by_amount(self, dashboard: DashboardPage):
        expect(dashboard.txn_rows.first).to_be_visible()
        dashboard.page.locator("th[data-col='amount']").click()
        expect(dashboard.txn_rows.first).to_be_visible()

    def test_sort_by_date(self, dashboard: DashboardPage):
        expect(dashboard.txn_rows.first).to_be_visible()
        dashboard.page.locator("th[data-col='date']").click()
        expect(dashboard.txn_rows.first).to_be_visible()

    def test_sort_by_store(self, dashboard: DashboardPage):
        expect(dashboard.txn_rows.first).to_be_visible()
        dashboard.page.locator("th[data-col='store']").click()
        expect(dashboard.txn_rows.first).to_be_visible()
