"""Playwright-based PDF rendering of the dashboard."""

from playwright.sync_api import sync_playwright


def render_dashboard_pdf(base_url: str) -> bytes:
    """Render the dashboard as a multi-page PDF using headless Chromium.

    Captures each tab as a separate section in one continuous PDF.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(base_url, wait_until="networkidle")
        page.wait_for_selector("#cards", state="visible")
        page.wait_for_timeout(500)

        tabs = ["overview", "analytics", "budgets"]
        for tab in tabs:
            btn = page.get_by_test_id(f"tab-{tab}")
            btn.click()
            page.locator(f"#tab-{tab}").wait_for(state="visible")
            page.wait_for_timeout(300)

        page.get_by_test_id("tab-overview").click()
        page.locator("#tab-overview").wait_for(state="visible")
        page.wait_for_timeout(300)

        for tab in tabs:
            page.evaluate(f"""() => {{
                document.querySelectorAll('[data-tab]').forEach(el => {{
                    const panel = document.getElementById('tab-' + el.dataset.tab);
                    if (panel) {{
                        if (['{tab}', ...{tabs}].includes(el.dataset.tab)) {{
                            panel.classList.remove('hidden');
                        }}
                    }}
                }});
            }}""")

        page.evaluate("""() => {
            const tabs = ['overview', 'analytics', 'budgets'];
            document.querySelectorAll('.tab-bar').forEach(b => b.style.display = 'none');
            document.querySelectorAll('[id^="tab-"]').forEach(el => {
                const name = el.id.replace('tab-', '');
                if (tabs.includes(name)) {
                    el.classList.remove('hidden');
                    el.style.pageBreakBefore = name === 'overview' ? 'auto' : 'always';
                } else {
                    el.classList.add('hidden');
                }
            });
            // Hide interactive elements
            document.querySelectorAll('.bulk-bar, .filter-bar, #bulkBar, input[type="checkbox"], .inline-cat-select, .btn-danger, #exportCsvBtn').forEach(el => el.style.display = 'none');
            // Hide link modal
            const modal = document.getElementById('linkModal');
            if (modal) modal.style.display = 'none';
        }""")

        page.wait_for_timeout(200)

        pdf_bytes = page.pdf(
            format="Letter",
            print_background=True,
            margin={
                "top": "0.5in",
                "bottom": "0.5in",
                "left": "0.4in",
                "right": "0.4in",
            },
        )

        browser.close()

    return pdf_bytes
