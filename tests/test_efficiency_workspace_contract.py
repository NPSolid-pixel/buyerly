from pathlib import Path
import unittest


class TestEfficiencyWorkspaceContract(unittest.TestCase):
    """Static product contract for the workspace served at /efficiency."""

    @classmethod
    def setUpClass(cls):
        root = Path(__file__).parents[1]
        cls.index = (root / "webapp" / "index.html").read_text()
        cls.script = (root / "webapp" / "js" / "app.js").read_text()
        cls.styles = "\n".join(
            path.read_text() for path in sorted((root / "webapp" / "css").glob("*.css"))
        )

    def test_first_load_empty_error_and_cached_refresh_are_explicit(self):
        for markup in (
            'id="tab-summary"',
            'aria-busy="false"',
            'id="summaryStatePanel"',
            'id="summaryStateTitle"',
            'id="summaryStateDescription"',
            'id="summaryStateSkeleton"',
            'id="summaryRetryButton"',
            'id="summaryOpenConnectionsButton"',
            'id="summaryResetScopeButton"',
            'id="summaryContent" class="summary-content hidden"',
        ):
            self.assertIn(markup, self.index)

        for state in ("loading", "refreshing", "ready", "empty", "error"):
            self.assertIn(f"'{state}'", self.script)
        self.assertIn("function setSummaryWorkspaceState(mode, options = {})", self.script)
        self.assertIn("setSummaryWorkspaceState(existingData ? 'refreshing' : 'loading')", self.script)
        self.assertIn("setSummaryWorkspaceState('error', { message: err.message })", self.script)
        self.assertNotIn('colspan="22"', self.script)

    def test_kpis_comparison_and_funnel_use_existing_summary_contract(self):
        for markup in (
            'id="summaryComparisonPanel"',
            'id="summaryComparisonContent"',
            'Изменение с предыдущего снимка',
            'id="summaryFunnelPanel"',
            'id="summaryFunnelChart"',
            'Воронка выбранного периода',
        ):
            self.assertIn(markup, self.index)

        for contract in (
            "function renderSummaryComparison(data)",
            "data.snapshot?.previous",
            "previousSummaryCost(previous, 'total_leads')",
            "function renderSummaryFunnel(data)",
            "data.total_impressions",
            "data.total_link_clicks",
            "data.total_landing_page_views",
            "data.total_leads",
            "data.total_regs",
            "data.total_purchases",
            "Сравниваем два обновления одного выбранного периода",
        ):
            self.assertIn(contract, self.script)

        self.assertIn("/api/summary?period=${period}", self.script)
        self.assertIn("apiRequest('/api/analytics-view'", self.script)
        self.assertNotIn("/api/efficiency", self.script)

    def test_period_view_filter_and_live_status_are_accessible(self):
        self.assertIn('role="status" aria-live="polite" aria-atomic="true"', self.index)
        self.assertIn('role="group" aria-labelledby="summaryPeriodLabel"', self.index)
        self.assertIn('aria-label="Поиск кабинета в сводке"', self.index)
        self.assertIn('aria-pressed="true">Сегодня</button>', self.index)
        self.assertIn('data-summary-view="all" aria-pressed="true"', self.index)
        self.assertIn('data-summary-status-filter="all" aria-pressed="true"', self.index)
        self.assertIn('id="summaryViewPresets" role="group"', self.index)
        self.assertIn('id="summaryStatusFilters" role="group"', self.index)
        self.assertIn('id="summaryRowsCount" class="summary-rows-count" role="status"', self.index)
        self.assertIn("function setSummaryControlsSelectionState()", self.script)
        self.assertIn("info.setAttribute('tabindex', '0')", self.script)
        self.assertIn("info.setAttribute('aria-label', info.title)", self.script)
        self.assertIn('class="summary-sort-button" type="button"', self.script)
        self.assertNotIn('data-summary-sort="${column.key}" tabindex="0"', self.script)
        self.assertIn('aria-labelledby="summaryColumnsTitle"', self.index)
        self.assertIn('aria-describedby="summaryColumnsDescription"', self.index)
        self.assertIn(".summary-column-visible:not([disabled])", self.script)
        self.assertIn("summaryColumnsTrigger.focus()", self.script)

    def test_partial_fresh_stale_and_account_statuses_have_semantics(self):
        for contract in (
            ".summary-freshness-badge.fresh",
            ".summary-freshness-badge.cached",
            ".summary-freshness-badge.stale",
            '.summary-quality-dot[data-status="complete"]',
            '.summary-quality-dot[data-status="partial"]',
            '.summary-quality-dot[data-status="unavailable"]',
            ".summary-quality-banner.error",
            ".summary-data-status.synced",
            ".summary-data-status.blocked",
            ".summary-data-status.error",
        ):
            self.assertIn(contract, self.styles)
        self.assertIn("statusDot.setAttribute('aria-label'", self.script)
        self.assertIn("banner.setAttribute('role'", self.script)
        self.assertIn("function normalizeSummaryQualityStatus(value)", self.script)
        self.assertIn("function normalizeSummaryCoverage(value)", self.script)
        self.assertIn("function normalizeSummaryCount(value)", self.script)
        self.assertIn("const SUMMARY_ACCOUNT_STATUSES = new Set(['synced', 'blocked', 'error'])", self.script)

    def test_mobile_breakdown_and_long_values_are_contained(self):
        for selector in (
            ".mob-summary-card",
            ".mob-card-head",
            ".mob-card-stats",
            ".stat-box",
            ".stat-box-val",
            ".summary-mobile-spend",
            ".summary-cards-list",
        ):
            self.assertIn(selector, self.styles)

        for containment in (
            "overflow-wrap: anywhere",
            "grid-template-columns: minmax(0, 1fr)",
            "overflow-x: auto",
            "max-width: 100%",
        ):
            self.assertIn(containment, self.styles)
        self.assertIn("@media (max-width: 480px)", self.styles)
        self.assertIn('title="${escapeHtml(displayName)}"', self.script)
        self.assertIn("escapeHtml(acc.account_id)", self.script)
        self.assertIn("escapeHtml(acc.note)", self.script)
        self.assertIn('[data-ui-pilot="efficiency"] .summary-mobile-name', self.styles)
        self.assertIn("text-overflow: clip", self.styles)

    def test_keyboard_resizer_does_not_trigger_table_sort(self):
        self.assertIn(
            "if (event.target.closest('[data-summary-column-resizer]')) {",
            self.script,
        )
        self.assertIn("resizeSummaryColumnWithKeyboard(event);", self.script)
        keydown_handler = self.script.split(
            "document.getElementById('summaryTableHead')?.addEventListener('keydown'",
            1,
        )[1].split("  });", 1)[0]
        self.assertNotIn("changeSummarySort", keydown_handler)


if __name__ == "__main__":
    unittest.main()
