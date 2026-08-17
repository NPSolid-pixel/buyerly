from pathlib import Path
import unittest


class TestFrontendRuleContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        webapp = Path(__file__).parents[1] / "webapp"
        cls.script = (webapp / "js" / "app.js").read_text()
        cls.index = (webapp / "index.html").read_text()
        cls.styles = (webapp / "css" / "styles.css").read_text()
        cls.server = (Path(__file__).parents[1] / "api" / "server.py").read_text()

    def test_frontend_uses_current_rule_endpoints(self):
        self.assertNotIn("/apply-preset", self.script)
        self.assertIn("/assign-rule", self.script)
        self.assertIn("/detach-rule/${presetId}", self.script)

    def test_account_cards_do_not_use_removed_single_rule_fields(self):
        for removed_field in (
            "acc.rule_conditions",
            "acc.preset_name",
            "acc.rule_action",
            "acc.preset_id",
        ):
            self.assertNotIn(removed_field, self.script)

    def test_account_import_does_not_offer_or_send_automatic_rules(self):
        for removed_contract in (
            "addEnableRulesSwitch",
            "selectedPreset",
            "max_spend_0_leads",
            "max_spend_1_lead",
            "max_cpa_multiple_leads",
            "Начальные лимиты автоправил",
        ):
            self.assertNotIn(removed_contract, self.script + self.index)

        self.assertIn("btnBatchOpenRules", self.index)
        self.assertIn("Перейти к правилам", self.index)

    def test_telegram_mini_app_sends_signed_init_data(self):
        sdk_position = self.index.index("telegram-web-app.js")
        app_position = self.index.index("/static/js/app.js")

        self.assertLess(sdk_position, app_position)
        self.assertIn("window.Telegram?.WebApp?.initData", self.script)
        self.assertIn("`tma ${telegramInitData}`", self.script)
        self.assertIn("!authToken && !telegramInitData", self.script)

    def test_desktop_shell_uses_quiet_palette_and_sidebar_grid(self):
        self.assertIn("QUIET GRAPHITE PALETTE", self.styles)
        self.assertIn("grid-template-columns: 232px minmax(0, 1fr)", self.styles)
        self.assertIn("--tg-bg: #0d0e11", self.styles)
        self.assertNotIn("TOKYO NIGHT COLOR PALETTE", self.styles)
        self.assertNotIn("appEl.style.display = 'block'", self.script)

    def test_logs_are_a_first_class_section_not_a_summary_table(self):
        self.assertIn('data-tab="logs"', self.index)
        self.assertIn('id="tab-logs"', self.index)
        self.assertIn('id="logsAttentionBanner"', self.index)
        self.assertNotIn('id="stoppedAdsetsSection"', self.index)
        self.assertIn('/api/audit-events?', self.script)
        self.assertIn('window.openLogDetails', self.script)
        self.assertIn('window.undoAuditEvent', self.script)
        self.assertIn('/api/audit-events/${eventId}/undo', self.script)
        self.assertIn('event.can_undo', self.script)
        for contract in ('Выполнено', 'Пропущено', 'Отменено', 'Скрыть'):
            self.assertIn(contract, self.index + self.script)
        for obsolete in ('Ждут решения', 'Нужна проверка', 'Подтвердить остановку'):
            self.assertNotIn(obsolete, self.index)

    def test_mobile_settings_remain_reachable_from_profile_badge(self):
        self.assertIn('id="userBadge"', self.index)
        self.assertIn('role="button" tabindex="0"', self.index)
        self.assertIn("window.switchTab('settings')", self.script)
        self.assertNotIn('class="mobile-nav-item" data-tab="settings"', self.index)

    def test_sections_have_stable_urls_and_restore_after_reload(self):
        for contract in (
            "accounts: '/accounts'",
            "rules: '/rules'",
            "summary: '/summary'",
            "logs: '/logs'",
            "add: '/add-accounts'",
            "settings: '/settings'",
            'TAB_ROUTES',
            'ROUTE_TABS',
            'tabFromLocation',
            'syncBrowserRoute',
            "window.addEventListener('popstate'",
            "historyMode: 'replace'",
            "historyMode: 'none'",
            "btn.setAttribute('aria-current', 'page')",
        ):
            self.assertIn(contract, self.script)

        for route in ('accounts', 'rules', 'summary', 'logs', 'add-accounts', 'settings'):
            self.assertIn(f'@app.get("/{route}")', self.server)

    def test_rule_groups_can_be_managed_and_assigned_from_the_ui(self):
        for contract in (
            'id="ruleGroupsContainer"',
            'id="modalRuleGroup"',
            'id="ruleGroupRulesList"',
            'id="assignGroupsList"',
            'Новая группа',
        ):
            self.assertIn(contract, self.index)

        self.assertIn("apiRequest('/api/rule-groups')", self.script)
        self.assertIn('/assign-rule-group/${groupId}', self.script)
        self.assertIn('window.pickRuleGroupForAccount', self.script)
        self.assertIn('rule-groups-grid', self.styles)

    def test_summary_separates_funnel_metrics_and_data_sync(self):
        for contract in (
            'id="kpiLeads"',
            'id="kpiCpl"',
            'id="kpiRegs"',
            'id="kpiCpreg"',
            'id="kpiPurchases"',
            'id="kpiCpp"',
            'id="kpiCoverage"',
            'id="summaryQualityBanner"',
            'id="summaryDefinitionsList"',
            'Синхронизация',
        ):
            self.assertIn(contract, self.index)

        self.assertIn('data.data_quality', self.script)
        self.assertIn('data.metric_definitions', self.script)
        self.assertIn('data.cache?.is_cached', self.script)
        self.assertNotIn('id="kpiResults"', self.index)
        self.assertNotIn('id="kpiCostPerResult"', self.index)
        self.assertNotIn('Стоимость результата', self.index)
        self.assertNotIn('data.avg_cost_per_result', self.script)

    def test_summary_restores_snapshots_and_auto_refreshes(self):
        for contract in (
            'id="kpiSpendPrevious"',
            'Автообновление · каждые 3 мин',
        ):
            self.assertIn(contract, self.index)

        for contract in (
            'SUMMARY_AUTO_REFRESH_MS = 3 * 60 * 1000',
            'startSummaryAutoRefresh()',
            "loadSummary(state.currentPeriod, false, { silent: true, refreshIfStale: true })",
            'refreshSummaryIfStale',
            'renderSpendComparison',
            'показываем данные от',
        ):
            self.assertIn(contract, self.script)

    def test_summary_separates_delivery_and_traffic_metrics(self):
        for contract in (
            'id="kpiImpressions"',
            'id="kpiReach"',
            'id="kpiFrequency"',
            'id="kpiCpm"',
            'id="kpiLinkClicks"',
            'id="kpiOutboundClicks"',
            'id="kpiLandingPageViews"',
            'id="kpiUniqueClicks"',
            'class="data-table summary-metrics-table"',
            'CTR All',
            'CTR Link',
        ):
            self.assertIn(contract, self.index + self.script)

        for contract in (
            'data.total_reach',
            'data.avg_frequency',
            'data.avg_cpm',
            'data.total_link_clicks',
            'data.total_outbound_clicks',
            'data.total_landing_page_views',
        ):
            self.assertIn(contract, self.script)

        self.assertIn('.metric-category-header', self.styles)
        self.assertIn('.summary-metrics-table', self.styles)

    def test_summary_table_views_are_configurable_and_persisted(self):
        for contract in (
            'id="summaryViewPresets"',
            'id="btnOpenSummaryColumns"',
            'id="modalSummaryColumns"',
            'id="summaryColumnOptions"',
            'id="summaryTableColumns"',
            'id="summaryTableHead"',
            'id="summaryAccountSearch"',
            'id="summaryStatusFilters"',
            'id="summaryRowsCount"',
            'data-summary-view="overview"',
            'data-summary-view="delivery"',
            'data-summary-view="traffic"',
            'data-summary-view="funnel"',
            'data-summary-column="account"',
            'data-summary-column="cpp"',
        ):
            self.assertIn(contract, self.index + self.script)

        for contract in (
            "apiRequest('/api/analytics-view')",
            "apiRequest('/api/analytics-view', {",
            'loadSummaryViewPreference()',
            'applySummaryColumnVisibility()',
            'SUMMARY_VIEW_PRESETS',
            'column_order',
            'column_widths',
            'sort_column',
            'sort_direction',
            'filters',
            'period',
            'data-summary-sort',
            'initializeSummaryTab',
            'renderSummaryAccountRows',
            'summaryFilterSaveTimer',
            'data-summary-column-width-input',
            'data-summary-column-resizer',
            'setupSummaryColumnEditor',
            'startSummaryColumnResize',
            'moveSummaryColumnResize',
            'finishSummaryColumnResize',
            'resetSummaryColumnWidth',
        ):
            self.assertIn(contract, self.script)

        self.assertIn('.summary-view-toolbar', self.styles)
        self.assertIn('.summary-column-hidden', self.styles)
        self.assertIn('.summary-column-drag', self.styles)
        self.assertIn('.summary-table-filterbar', self.styles)
        self.assertIn('.summary-sortable-header', self.styles)
        self.assertIn('.summary-column-resizer', self.styles)
        self.assertIn('.summary-column-resizing', self.styles)
        self.assertIn('table-layout: fixed', self.styles)
        self.assertIn('v=9.14.0', self.index)

    def test_money_is_currency_aware_and_mixed_totals_are_separated(self):
        for contract in (
            'id="summaryCurrencyBreakdown"',
            'data.currency_totals',
            'data.mixed_currencies',
            'currencyDisplay: \'code\'',
            'Валюта Meta',
            'валюте конкретного кабинета',
        ):
            self.assertIn(contract, self.index + self.script)
        self.assertNotIn('Спенд ($)', self.script)
        self.assertNotIn('Потолок ($/день)', self.index)

    def test_rule_builder_uses_independent_cost_metrics_and_exact_operators(self):
        for contract in (
            'value="cpl"',
            'value="cpreg"',
            'value="cpp"',
            'value="gt"',
            'value="gte"',
            'value="lt"',
            'value="lte"',
            'value="eq"',
        ):
            self.assertIn(contract, self.script)

        self.assertNotIn('<option value="cpa"', self.script)
        self.assertNotIn('<option value="cpr"', self.script)

    def test_account_cards_separate_meta_automation_and_rule_state(self):
        for contract in (
            'id="accountsHealthActive"',
            'id="accountsHealthAutomation"',
            'id="accountsHealthIssues"',
            'id="modalAccountDetails"',
            'Статус Meta',
            'Автоматика',
        ):
            self.assertIn(contract, self.index + self.script)

        self.assertIn('getAccountMetaState', self.script)
        self.assertIn('window.openAccountDetails', self.script)
        self.assertIn('window.openAccountLogs', self.script)
        self.assertIn('state.pendingLogsAccountId', self.script)
        self.assertIn('account-state-grid', self.styles)
        self.assertIn('account-detail-grid', self.styles)
