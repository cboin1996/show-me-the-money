"""Page Object Model for the SMTM dashboard."""

from playwright.sync_api import Page, expect


class DashboardPage:
    """POM for the single-page dashboard app."""

    def __init__(self, page: Page, base_url: str):
        self.page = page
        self.base_url = base_url

    def navigate(self):
        self.page.goto(self.base_url)
        self.page.wait_for_load_state("networkidle")

    # --- Tabs ---

    def click_tab(self, name: str):
        self.page.get_by_test_id(f"tab-{name}").click()
        self.page.locator(f"#tab-{name}").wait_for(state="visible")

    # --- Summary Cards ---

    @property
    def cards(self):
        return self.page.get_by_test_id("summary-cards")

    # --- Transaction Table ---

    @property
    def txn_body(self):
        return self.page.get_by_test_id("txn-body")

    @property
    def txn_rows(self):
        return self.txn_body.locator("tr")

    @property
    def txn_count_text(self) -> str:
        return self.page.locator("#txnCount").inner_text()

    def search_transactions(self, query: str):
        inp = self.page.locator("#searchInput")
        inp.fill(query)
        expect(self.page.locator("#txnCount")).to_contain_text("Showing")

    def filter_category(self, category: str):
        self.page.locator("#categoryFilter").select_option(category)
        expect(self.page.locator("#txnCount")).to_contain_text("Showing")

    def filter_date_range(self, from_date: str, to_date: str):
        self.page.locator("#dateFrom").fill(from_date)
        self.page.locator("#dateTo").fill(to_date)
        expect(self.page.locator("#txnCount")).to_contain_text("Showing")

    def clear_filters(self):
        self.page.locator("#searchInput").fill("")
        self.page.locator("#categoryFilter").select_option("")
        self.page.locator("#dateFrom").fill("")
        self.page.locator("#dateTo").fill("")

    def select_transaction_checkbox(self, index: int):
        self.txn_body.locator("input[type='checkbox']").nth(index).check()

    def select_all(self):
        self.page.get_by_test_id("select-all").check()

    @property
    def bulk_bar(self):
        return self.page.get_by_test_id("bulk-bar")

    def bulk_assign_category(self, category: str):
        self.page.locator("#bulkCatSelect").select_option(category)
        self.page.locator("#bulkCatBtn").click()

    def bulk_delete(self):
        self.page.locator("#bulkDeleteBtn").click()

    def bulk_clear(self):
        self.page.locator("#bulkClearBtn").click()

    def inline_set_category(self, row_index: int, category: str):
        self.txn_body.locator("select.inline-cat-select").nth(row_index).select_option(
            category
        )

    def delete_row(self, row_index: int):
        self.txn_body.locator(".btn-danger").nth(row_index).click()

    # --- Import ---

    def upload_csv(self, filename: str, content: str):
        self.click_tab("import")
        self.page.get_by_test_id("file-input").set_input_files(
            {"name": filename, "mimeType": "text/csv", "buffer": content.encode()}
        )

    def confirm_import(self):
        self.page.get_by_text("Confirm Import").click()

    # --- Categorize ---

    def click_recategorize(self):
        self.click_tab("categorize")
        self.page.get_by_test_id("recategorize-btn").click()

    @property
    def recategorize_result(self):
        return self.page.get_by_test_id("recategorize-result")

    def categorize_merchant_row(self, row_index: int, category: str):
        self.page.locator("#uncatBody select").nth(row_index).select_option(category)

    @property
    def uncategorized_rows(self):
        return self.page.locator("#uncatBody tr")

    # --- Budgets ---

    def set_budget(self, month: str, category: str, amount: str):
        self.click_tab("budgets")
        self.page.locator("#newBudgetMonth").fill(month)
        self.page.locator("#newBudgetCat").select_option(category)
        self.page.locator("#newBudgetAmt").fill(amount)
        self.page.locator("#setBudgetBtn").click()

    @property
    def budget_rows(self):
        return self.page.locator("#budgetBody tr")

    # --- Manage ---

    def add_rule(self, pattern: str, category: str, match_type: str = "exact"):
        self.click_tab("manage")
        self.page.locator("#newRulePattern").fill(pattern)
        self.page.locator("#newRuleCat").select_option(category)
        self.page.locator("#newRuleType").select_option(match_type)
        self.page.locator("#addRuleBtn").click()

    def search_rules(self, query: str):
        self.page.locator("#rulesSearch").fill(query)

    def add_store_pair(self, raw: str, normalized: str):
        self.click_tab("manage")
        self.page.locator("#newPairRaw").fill(raw)
        self.page.locator("#newPairNorm").fill(normalized)
        self.page.locator("#addPairBtn").click()

    def search_pairs(self, query: str):
        self.page.locator("#pairsSearch").fill(query)

    def restore_first_deleted(self):
        self.page.locator("#recycleBody .btn").first.click()

    @property
    def recycle_bin_rows(self):
        return self.page.locator("#recycleBody tr")

    # --- Analytics ---

    @property
    def velocity_cards(self):
        return self.page.locator("#velocityCards")

    @property
    def recurring_rows(self):
        return self.page.locator("#recurringBody tr")

    @property
    def mom_rows(self):
        return self.page.locator("#momBody tr")

    # --- Toast ---

    @property
    def toast(self):
        return self.page.get_by_test_id("toast")

    # --- Export ---

    @property
    def export_btn(self):
        return self.page.get_by_test_id("export-csv-btn")
