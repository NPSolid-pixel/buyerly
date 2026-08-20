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

    def test_meta_oauth_is_primary_and_manual_token_is_advanced(self):
        for contract in (
            'id="btnStartMetaOAuth"',
            'id="metaConnectionsList"',
            'id="metaAssetGroups"',
            'id="metaSelectAll"',
            'id="btnImportMetaAssets"',
            'class="form-card legacy-import-card"',
        ):
            self.assertIn(contract, self.index)
        for endpoint in (
            "/api/meta/oauth/start",
            "/api/meta/connections",
            "/discover",
            "/import",
        ):
            self.assertIn(endpoint, self.script)
        self.assertIn("meta_connection", self.script)
        self.assertIn("meta-asset-row", self.styles)

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

    def test_automation_settings_are_separate_and_password_protected(self):
        for contract in (
            'aria-label="Разделы настроек"',
            'data-settings-nav="automation"',
            'data-settings-page="automation"',
            'id="automationSettingsPassword"',
            'id="btnSaveAutomationSettings"',
            'id="automationRuntimeBadge"',
            'id="automationUsagePercent"',
            'id="settingStopConfirmation"',
            'id="funnelProtectionNotice"',
        ):
            self.assertIn(contract, self.index)

        for contract in (
            "window.switchSettingsPage",
            "window.saveAutomationSettings",
            "apiRequest('/api/settings/automation'",
            "current_password: password",
            "stop_confirmation_minutes: readNumber('settingStopConfirmation')",
        ):
            self.assertIn(contract, self.script)

        self.assertIn('.automation-settings-grid', self.styles)
        self.assertIn('.settings-subnav', self.styles)

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
        self.assertIn('v=9.21.0', self.index)

    def test_account_groups_scope_the_whole_summary_and_keep_profile_columns_configurable(self):
        for contract in (
            'id="sidebarAccountGroupsContainer"',
            'id="modalAccountGroup"',
            'id="accountGroupMembers"',
            'id="summaryAccountGroupSelect"',
            'data-summary-column="custom_name"',
            'data-summary-column="note"',
        ):
            self.assertIn(contract, self.index + self.script)
        self.assertNotIn('id="summaryGroupScopeLabel"', self.index)

        for contract in (
            "apiRequest('/api/account-groups')",
            "apiRequest(groupId ? `/api/account-groups/${groupId}` : '/api/account-groups'",
            'buildScopedSummaryData',
            "updateSummaryFilters({ group_id:",
            "{ key: 'custom_name', label: 'Моё название'",
            "{ key: 'note', label: 'Заметка'",
            'renderAccountGroupTags',
        ):
            self.assertIn(contract, self.script)

        self.assertIn('.account-groups-panel', self.styles)
        self.assertIn('.summary-scope-control', self.styles)
        self.assertIn('.summary-note-cell', self.styles)

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

    def test_rule_builder_explains_and_validates_rules_in_plain_russian(self):
        for contract in (
            'id="rulePlainPreview"',
            'id="rulePlainText"',
            'id="ruleValidationMessages"',
            'id="btnCopyRulePlainText"',
            'id="btnUseRulePlainName"',
            'Правило простым языком',
            'validateRuleDraft',
            'buildPlainRuleTextFromValues',
            'renderRuleDraftSummary',
            'Выключить группу объявлений',
        ):
            self.assertIn(contract, self.index + self.script)

        for style_contract in (
            '.rule-plain-preview',
            '.rule-validation-message.error',
            '.rule-card-plain-summary',
        ):
            self.assertIn(style_contract, self.styles)

    def test_account_cards_separate_meta_automation_and_rule_state(self):
        for contract in (
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

    def test_account_cards_show_connection_annotations_and_saved_activity(self):
        for contract in (
            'id="modalAccountProfile"',
            'id="accountCustomNameInput"',
            'id="accountNoteInput"',
            'id="btnSaveAccountProfile"',
            'Facebook Login',
            'System User',
            'Spend сегодня',
            'Снимок за сегодня',
            'Внутренняя заметка',
        ):
            self.assertIn(contract, self.index + self.script)

        for contract in (
            'account?.connection_type',
            'account?.latest_metrics',
            'account.custom_name',
            'account.note',
            '/api/accounts/${accountId}/profile',
            'method: \'PATCH\'',
            'accountDisplayName',
            'getAccountActivityState',
        ):
            self.assertIn(contract, self.script)

        for contract in (
            '.account-connection-badge',
            '.account-note-preview',
            '.account-metrics-grid',
            '.account-activity-line',
        ):
            self.assertIn(contract, self.styles)

    def test_new_rule_uses_saved_rule_as_a_copy_not_an_edit(self):
        select_start = self.script.index('window.selectPreset = function (presetId)')
        select_end = self.script.index('window.newPresetMode = function ()', select_start)
        select_contract = self.script[select_start:select_end]

        self.assertIn("ruleBuilderMode: 'create'", self.script)
        self.assertIn('templatePresetId: null', self.script)
        self.assertIn("const isEditing = state.ruleBuilderMode === 'edit'", select_contract)
        self.assertIn("value = isEditing ? preset.id : ''", select_contract)
        self.assertIn('будет создано новое правило', select_contract)
        self.assertIn("state.ruleBuilderMode = 'create'", self.script)
        self.assertIn("state.ruleBuilderMode = 'edit'", self.script)

    def test_workspace_switcher_and_modals_contract(self):
        for contract in (
            'id="workspaceBtn"',
            'id="currentWorkspaceBadge"',
            'id="currentWorkspaceName"',
            'id="workspaceDropdown"',
            'id="workspaceDropdownList"',
            'Новый воркспейс',
            'Account settings',
            'Workspace settings',
            'Invite team members',
            'id="createWorkspaceScreen"',
            'id="createWsLogoFileInput"',
            'id="createWsLogoBadge"',
            'id="createWsNameInput"',
            'id="createWsSlugInput"',
            'id="btnCreateWsSubmit"',
            'id="modalWorkspaceSettings"',
            'id="editWorkspaceNameInput"',
            'id="btnDeleteCurrentWorkspace"',
        ):
            self.assertIn(contract, self.index)

        self.assertNotIn('Apps and integrations', self.index)
        self.assertNotIn('+ + New workspace', self.index)
        self.assertNotIn('id="pageWorkspaceColorPicker"', self.index)

        for script_contract in (
            'renderWorkspacesDropdown',
            'window.toggleWorkspaceDropdown',
            'window.switchWorkspace',
            'window.openCreateWorkspacePage',
            'window.closeCreateWorkspacePage',
            'window.handleWorkspaceLogoUpload',
            'window.submitCreateWorkspaceFromPage',
            'window.openWorkspaceSettings',
            'window.submitSaveWorkspaceSettings',
            'window.submitDeleteCurrentWorkspace',
            '/api/workspaces/switch',
            '/api/workspaces',
        ):
            self.assertIn(script_contract, self.script)

        for style_contract in (
            '.workspace-dropdown',
            '.ws-create-screen-wrapper',
            '.ws-create-card-container',
            '.ws-logo-avatar',
            '.ws-logo-upload-overlay',
            '.btn-ws-continue',
            '.color-picker-row',
            '.color-swatch',
        ):
            self.assertIn(style_contract, self.styles)

    def test_fb_accounts_and_attio_lists_contract(self):
        for contract in (
            'data-tab="fb_accounts"',
            'id="navFbAccounts"',
            'id="sidebarFbAccountsCount"',
            'id="navGroupAll"',
            'id="sidebarAccountGroupsContainer"',
            'id="tab-fb_accounts"',
            'id="fbAccountsTable"',
            'id="accountBulkActionBar"',
            'id="bulkSelectedCount"',
            'id="bulkGroupDropdown"',
            'id="btnListsSortMenu"',
            'id="listsSortDropdown"',
        ):
            self.assertIn(contract, self.index)

        for script_contract in (
            "fb_accounts: '/facebook-accounts'",
            'loadFacebookAccounts',
            'renderFacebookAccounts',
            'renderSidebarAccountGroups',
            'getSortedAccountGroups',
            'window.onGroupDragStart',
            'window.onGroupDrop',
            'window.toggleListsSortMenu',
            'window.setGroupsSortMode',
            'window.switchAccountGroup',
            'window.toggleAccountSelection',
            'window.toggleSelectAllAccounts',
            'window.clearAccountSelection',
            'window.toggleBulkGroupDropdown',
            'window.assignSelectedAccountsToGroup',
            'window.toggleSidebarSection',
            'applySidebarSectionsCollapsedState',
        ):
            self.assertIn(script_contract, self.script)

        for style_contract in (
            '.floating-action-bar',
            '.selected-badge',
            '.fb-accounts-table',
            '.fb-profile-cell',
            '.token-chip',
            '.nav-section-chevron',
            '.nav-section-content',
            '.lists-sort-dropdown',
            '.sort-menu-item',
            '.sort-check-icon',
            '.list-item.dragging',
        ):
            self.assertIn(style_contract, self.styles)

