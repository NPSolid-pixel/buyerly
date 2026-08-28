from pathlib import Path
import re
import unittest


class TestFrontendRuleContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        webapp = Path(__file__).parents[1] / "webapp"
        cls.scripts = {
            path.name: path.read_text()
            for path in sorted((webapp / "js").glob("*.js"))
        }
        cls.app_script = cls.scripts["app.js"]
        cls.script = "\n".join(cls.scripts.values())
        cls.index = (webapp / "index.html").read_text()
        cls.styles = (webapp / "css" / "styles.css").read_text()
        cls.server = (Path(__file__).parents[1] / "api" / "server.py").read_text()
        cls.workflow = (Path(__file__).parents[1] / ".github" / "workflows" / "deploy.yml").read_text()

    def test_frontend_foundations_are_split_and_loaded_before_app(self):
        script_paths = (
            "/static/js/browser-preferences.js",
            "/static/js/workspace-slugs.js",
            "/static/js/security.js",
            "/static/js/app.js",
        )
        positions = [self.index.index(path) for path in script_paths]
        self.assertEqual(positions, sorted(positions))
        cache_versions = [
            re.search(rf'{re.escape(path)}\?v=([^"\s]+)', self.index).group(1)
            for path in script_paths
        ]
        self.assertEqual(len(set(cache_versions)), 1)

        for filename, namespace in (
            ("browser-preferences.js", "BuyerlyBrowserPreferences"),
            ("workspace-slugs.js", "BuyerlyWorkspaceSlugs"),
            ("security.js", "BuyerlySecurity"),
        ):
            self.assertIn(namespace, self.scripts[filename])
            self.assertIn(f"window.{namespace}", self.app_script)

        for extracted_definition in (
            "function readBrowserPreference(",
            "function slugifyText(",
            "function sanitizeUrl(",
        ):
            self.assertNotIn(extracted_definition, self.app_script)

        self.assertIn("find webapp/js -type f -name '*.js'", self.workflow)
        self.assertIn("node --check", self.workflow)

    def test_frontend_uses_current_rule_endpoints(self):
        self.assertNotIn("/apply-preset", self.script)
        self.assertIn("/assign-rule", self.script)
        self.assertIn("/detach-rule/${presetId}", self.script)

    def test_account_health_dashboard_contract(self):
        for contract in (
            'id="accountHealthDashboard"',
            'id="accountHealthSignals"',
            "/api/health/overview",
            "function loadHealthOverview()",
            "account.health ||",
            "Последний успех",
            "api_synthetic_availability_percent",
            "api_synthetic_latency_p95_ms",
        ):
            self.assertIn(contract, self.index + self.script)
        self.assertIn(".account-health-row", self.styles)

    def test_visible_mutations_use_supported_routes_and_partial_results(self):
        self.assertIn("/api/meta/connections/${connectionId}", self.script)
        self.assertIn("idsToDelete.map(id => apiRequest(`/api/presets/${id}`", self.script)
        self.assertIn("Promise.allSettled", self.script)
        self.assertIn("failedResults.length", self.script)
        self.assertIn("state.selectedRuleIds.delete(id)", self.script)
        self.assertNotIn("/api/rules/presets/", self.script)
        self.assertNotIn("/api/worker/run-now", self.script)
        self.assertNotIn("window.runRuleCheckNow", self.script + self.index)

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
            'id="fbAccountsTable"',
            'id="modalMetaAssets"',
            'id="metaAssetGroups"',
            'id="metaSelectAll"',
            'id="btnImportMetaAssets"',
            'id="modalManualToken"',
        ):
            self.assertIn(contract, self.index)
        for contract in (
            'window.startMetaOAuthFlow',
            'window.openManualTokenModal',
            'window.migrateAccountFromDetails',
            'Миграция на OAuth',
        ):
            self.assertIn(contract, self.script)
        for endpoint in (
            "/api/meta/oauth/start",
            "/api/meta/connections",
            "/discover",
            "/import",
        ):
            self.assertIn(endpoint, self.script)
        self.assertIn("meta_connection", self.script)
        self.assertIn("meta-asset-row", self.styles)
        self.assertIn("badge-migration", self.styles)
        self.assertIn("account-detail-migration-callout", self.styles)

    def test_telegram_mini_app_sends_signed_init_data(self):
        sdk_position = self.index.index("telegram-web-app.js")
        app_position = self.index.index("/static/js/app.js")

        self.assertLess(sdk_position, app_position)
        self.assertIn("window.Telegram?.WebApp?.initData", self.script)
        self.assertIn("`tma ${telegramInitData}`", self.script)
        self.assertIn("const user = await apiRequest('/api/me')", self.script)

    def test_browser_auth_uses_cookie_csrf_and_removes_legacy_storage(self):
        self.assertIn("credentials: 'same-origin'", self.script)
        self.assertIn("'X-CSRF-Token': csrfToken", self.script)
        self.assertIn("/api/auth/sessions", self.script)
        self.assertIn("/api/auth/logout-all", self.script)
        self.assertIn('id="settingsSessionsList"', self.index)
        self.assertNotIn("localStorage.setItem('buyerly_auth_token'", self.script)
        self.assertNotIn("sessionStorage.setItem('buyerly_auth_token'", self.script)

    def test_password_login_accepts_username_without_browser_email_validation(self):
        self.assertIn(
            'type="text" id="onboardingSignInEmail"',
            self.index,
        )
        self.assertIn('placeholder="Email or username"', self.index)
        self.assertIn('autocomplete="username"', self.index)
        self.assertNotIn(
            'type="email" id="onboardingSignInEmail"',
            self.index,
        )
        self.assertIn("username: email, password", self.script)

    def test_corrupted_browser_preferences_recover_to_valid_defaults(self):
        for contract in (
            "function readBrowserPreference(key, fallback, options = {})",
            "function writeBrowserPreference(key, value, options = {})",
            "const value = options.json ? JSON.parse(raw) : raw",
            "resetBrowserPreference(key)",
            "localStorage.removeItem(key)",
            "validate: isStringArray",
            "validate: isIdArray",
            "validate: isWidthRecord",
            "validate: isStringRecord",
            "['relevant', 'custom'].includes(value)",
            "['asc', 'desc'].includes(value)",
        ):
            self.assertIn(contract, self.script)

        for unsafe_read in (
            "JSON.parse(localStorage.getItem('buyerly_collapsed_sections')",
            "JSON.parse(localStorage.getItem('buyerly_groups_custom_order')",
            "JSON.parse(localStorage.getItem('buyerly_accounts_col_order_v2')",
            "JSON.parse(localStorage.getItem('buyerly_accounts_col_widths')",
            "JSON.parse(localStorage.getItem('buyerly_collapsed_rule_groups')",
            "JSON.parse(localStorage.getItem('buyerly_rule_group_colors')",
        ):
            self.assertNotIn(unsafe_read, self.script)

        for key in (
            "buyerly_collapsed_sections",
            "buyerly_groups_sort_mode",
            "buyerly_groups_custom_order",
            "buyerly_accounts_col_order_v2",
            "buyerly_accounts_sort_col",
            "buyerly_accounts_sort_dir",
            "buyerly_accounts_col_widths",
            "buyerly_collapsed_rule_groups",
            "buyerly_rule_group_colors",
            "buyerly_accounts_col_calcs",
        ):
            self.assertRegex(
                self.script,
                rf"readBrowserPreference\(\s*{re.escape(repr(key))}",
            )

    def test_desktop_shell_uses_quiet_palette_and_sidebar_grid(self):
        self.assertIn("QUIET GRAPHITE PALETTE", self.styles)
        self.assertIn("grid-template-columns: 232px minmax(0, 1fr)", self.styles)
        self.assertIn("--tg-bg: #0d0e11", self.styles)
        self.assertNotIn("TOKYO NIGHT COLOR PALETTE", self.styles)
        self.assertNotIn("appEl.style.display = 'block'", self.script)

    def test_design_system_foundation_and_three_pilots(self):
        for token in (
            "--font-size-sm: 14px",
            "--action-primary:",
            "--warning-bg:",
            "--focus-ring:",
            "--layer-popover:",
            "--motion-standard:",
        ):
            self.assertIn(token, self.styles)

        for component in (
            ".ui-button",
            ".ui-icon-button",
            ".ui-input",
            ".ui-select",
            ".ui-tabs",
            ".ui-badge",
            ".ui-tooltip",
            ".ui-popover",
            ".ui-modal",
            ".ui-drawer",
            ".ui-table",
            ".ui-kpi-value",
            ".ui-chart",
            ".ui-empty-state",
            ".ui-alert",
            ".ui-skeleton",
        ):
            self.assertIn(component, self.styles)

        for pilot in ("today", "automations", "connections"):
            self.assertIn(f'data-ui-pilot="{pilot}"', self.index)

        self.assertNotIn('placeholder="Ask anything..."', self.index)
        self.assertNotIn('class="ai-prompt-card"', self.index)
        self.assertIn("onclick=\"window.switchTab('fb_accounts')\"", self.index)
        self.assertIn("onclick=\"window.switchTab('rules')\"", self.index)
        self.assertIn("onclick=\"window.switchTab('logs')\"", self.index)

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
            "fb_accounts: '/facebook-accounts'",
            "settings: '/settings'",
            'TAB_ROUTES',
            'ROUTE_TABS',
            'tabFromLocation',
            'syncBrowserRoute',
            "window.addEventListener('popstate'",
            "historyMode: 'replace'",
            "historyMode: 'none'",
            "btn.setAttribute('aria-current', 'page')",
            "pushState",
            "replaceState",
        ):
            self.assertIn(contract, self.script)

        self.assertNotIn("window.history[method]", self.script)

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

    def test_rules_kanban_board_contract(self):
        for contract in (
            'id="modalChooseRule"',
            'id="chooseRuleSearchInput"',
            'id="chooseRuleList"',
            'id="chooseRuleCreateBtn"',
            'id="btnConfirmChooseRule"',
            'id="ruleGroupMenuPopover"',
            'id="ruleAddColumnPopover"',
            'id="ruleGroupPopoverNameInput"',
            'id="ruleGroupColorPalette"',
            'id="newColumnColorPalette"',
            'id="ruleGroupSelect"',
            'id="modalLinkRuleAccounts"',
            'id="btnOpenLinkAccounts"',
            'rules-kanban-board',
        ):
            self.assertIn(contract, self.index)

        for script_contract in (
            'window.onRulesFilterChange',
            'isPresetMatchingFilter',
            'buildKanbanRuleCard',
            'window.onRuleDragStart',
            'window.onRuleDragEnd',
            'window.onRuleColumnDragOver',
            'window.onRuleColumnDragLeave',
            'window.onRuleColumnDrop',
            'window.onRuleGroupColumnDragStart',
            'window.onRuleGroupColumnDragEnd',
            'window.onRuleGroupColumnDragOver',
            'window.onRuleGroupColumnDrop',
            '/api/rule-groups/reorder',
            'window.movePresetToGroup',
            'window.openChooseRuleModal',
            'window.openGroupMenuPopover',
            'window.openAddColumnPopover',
            'window.toggleGroupCollapse',
            'window.selectGroupColor',
            'window.toggleSelectRule',
            'window.clearRuleSelection',
            'window.bulkDeleteSelectedRules',
            'window.openLinkRuleAccountsModal',
            'window.saveLinkRuleAccounts',
            'window.detachRuleFromAccountDirectly',
        ):
            self.assertIn(script_contract, self.script)

        for style_contract in (
            '.rules-kanban-board',
            '.rules-column',
            '.rules-column.collapsed',
            '.rules-column-collapsed-strip',
            '.rules-column-header',
            '.rules-column-drag-handle',
            '.rules-column.column-dragging',
            '.rules-column.drag-over-left',
            '.rules-column.drag-over-right',
            '.rules-column-body',
            '.rules-column-body.drop-target-active',
            '.rules-kanban-card',
            '.rules-add-column-btn',
            '.rule-action-badge',
            '.rule-meta-tag',
            '.rule-card-checkbox',
            '.rule-conditions-inline',
            '.rules-bulk-bar',
            '.attio-popover',
            '.choose-rule-card',
            '.color-swatch',
            '.record-accounts-toolbar',
            '.record-account-detach-btn',
        ):
            self.assertIn(style_contract, self.styles)

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
            'id="createWorkspaceScreen"',
            'id="createWsLogoFileInput"',
            'id="createWsLogoBadge"',
            'id="createWsNameInput"',
            'id="createWsSlugInput"',
            'id="btnCreateWsSubmit"',
            'id="modalWorkspaceSettings"',
            'id="editWorkspaceNameInput"',
            'id="btnDeleteCurrentWorkspace"',
            'id="modalInviteMembers"',
            'id="inviteMemberEmailInput"',
            'id="inviteMemberRoleSelect"',
            'id="inviteAcceptScreen"',
        ):
            self.assertIn(contract, self.index)

        for obsolete in (
            'Apps and integrations',
            '+ + New workspace',
            'id="pageWorkspaceColorPicker"',
        ):
            self.assertNotIn(obsolete, self.index)

    def test_sidebar_user_profile_and_localization_contract(self):
        for contract in (
            'id="sidebarFooter"',
            'id="userBadge"',
            'id="userAvatar"',
            'id="userName"',
            'class="sidebar-user-card"',
            'class="sidebar-user-avatar"',
            'class="sidebar-user-name"',
            'class="help-btn"',
            '<span>Справка</span>',
            'id="homeGreetingTitle"',
            'Доброе утро, Buyerly.',
        ):
            self.assertIn(contract, self.index)

        self.assertIn("home: 'Главная — Buyerly'", self.script)
        self.assertIn('<span>Главная</span>', self.script)
        self.assertIn("let greeting = 'Доброе утро';", self.script)
        self.assertIn("greeting = 'Добрый день';", self.script)
        self.assertIn("greeting = 'Добрый вечер';", self.script)
        self.assertIn("greeting = 'Доброй ночи';", self.script)
        self.assertIn('.sidebar-footer', self.styles)
        self.assertIn('.sidebar-user-card', self.styles)

        for script_contract in (
            'renderWorkspacesDropdown',
            'window.toggleWorkspaceDropdown',
            'window.switchWorkspace',
            'resetWorkspaceState',
            'window.resetWorkspaceState',
            'workspaceEpoch',
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

        self.assertNotIn('id="sidebarFbAccountsCount"', self.index)
        self.assertNotIn('id="sidebarTotalCount"', self.index)
        self.assertNotIn('id="headerFbSection"', self.index)
        self.assertNotIn('id="sidebarFbAccountsList"', self.index)

        home_pos = self.index.index('id="navDashboard"')
        fb_pos = self.index.index('id="navFbAccounts"')
        rules_pos = self.index.index('id="navRules"')
        summary_pos = self.index.index('id="navSummary"')
        logs_pos = self.index.index('id="navLogs"')
        self.assertLess(home_pos, fb_pos)
        self.assertLess(fb_pos, rules_pos)
        self.assertLess(rules_pos, summary_pos)
        self.assertLess(summary_pos, logs_pos)

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
            'window.copyCurrentGroupLink',
            'window.toggleAccountSelection',
            'window.toggleSelectAllAccounts',
            'window.clearAccountSelection',
            'window.toggleBulkGroupDropdown',
            'window.assignSelectedAccountsToGroup',
            'window.reconnectMetaConnection',
            'window.validateMetaConnection',
            'window.toggleSidebarSection',
            'applySidebarSectionsCollapsedState',
        ):
            self.assertIn(script_contract, self.script)

        self.assertIn('id="btnGroupShare"', self.index)
        for sort_label in ('По релевантности', 'Недавние', 'По алфавиту', 'Кастомные', 'Сортировка'):
            self.assertIn(sort_label, self.index)

        # FB Accounts tab cleanup assertions
        self.assertNotIn('id="fbAccountSearchInput"', self.index)
        self.assertNotIn('data-fb-filter', self.index)
        self.assertNotIn('<th>Страницы</th>', self.index)
        self.assertNotIn('>Страницы</th>', self.index)
        self.assertIn('<span>Подключить Facebook</span>', self.index)
        self.assertNotIn('+ +', self.index)

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
            '.nav-section-actions',
            '.sidebar .list-count',
        ):
            self.assertIn(style_contract, self.styles)

    def test_search_and_notifications_contract(self):
        for contract in (
            'id="sidebarSearchBox"',
            'id="sidebarNotificationsBtn"',
            'id="notificationsPopover"',
            'id="notificationsTabPaneNotifications"',
            'id="notificationsTabPaneRequests"',
            'id="quickSearchModal"',
            'id="quickSearchInput"',
            'id="quickSearchResults"',
            'Уведомления (0)',
            'Запросы (0)',
            'Нет уведомлений',
            'Нет запросов',
            'Поиск по разделам, аккаунтам и правилам...',
            'Навигация',
            'Выбрать',
            'Закрыть',
        ):
            self.assertIn(contract, self.index)

        # Confirm pure search without Ask Attio or Quick actions
        for forbidden in ('Ask Attio', 'ask attio', 'Quick Action', 'quick action'):
            self.assertNotIn(forbidden, self.index + self.script)

        for script_contract in (
            'window.openQuickSearchModal',
            'window.closeQuickSearchModal',
            'window.clearQuickSearchInput',
            'window.selectQuickSearchResult',
            'window.toggleNotificationsPopover',
            'window.switchNotificationsTab',
            'getQuickSearchEntities',
            'renderQuickSearchResults',
            'setupQuickSearchListeners',
            'quickSearchSelectedIndex',
        ):
            self.assertIn(script_contract, self.script)

        for style_contract in (
            '.sidebar-search-row',
            '.sidebar-notifications-btn',
            '.notifications-popover',
            '.notifications-tabs',
            '.notifications-tab-btn',
            '.notifications-bell-art',
            '.notifications-grid-backdrop',
            '.quick-search-overlay',
            '.quick-search-dialog',
            '.quick-search-header',
            '.quick-search-input',
            '.quick-search-results',
            '.quick-search-item',
            '.quick-search-item.is-selected',
            '.quick-search-footer',
            '.quick-search-kbd',
        ):
            self.assertIn(style_contract, self.styles)

    def test_sidebar_and_workspace_navigation_contract(self):
        # Verify no undefined fetch helpers exist
        for undefined_helper in (
            'fetchAccounts()',
            'fetchRulePresets()',
            'fetchRuleGroups()',
            'fetchAccountGroups()',
        ):
            self.assertNotIn(undefined_helper, self.script)

        # Verify sidebar account groups and counters contract
        for contract in (
            'renderSidebarAccountGroups',
            'loadAccountsInFlightPromise',
            'sidebarAccountGroupsContainer',
            'sidebarTotalCount',
            'sidebarFbAccountsCount',
        ):
            self.assertIn(contract, self.script + self.index)

        # Verify sidebar typography and contrast hierarchy (Attio style)
        self.assertIn('color: #5A5E66;', self.styles)
        self.assertIn('.list-item.active .list-count', self.styles)
        self.assertIn('.nav-item.active svg', self.styles)
        self.assertIn('.list-item.active svg', self.styles)

    def test_toast_and_error_sanitization_contract(self):
        # 1. showToast must not use raw unescaped string interpolation into innerHTML
        self.assertNotIn("toast.innerHTML = `<span>${iconSvg}</span> <span>${message}</span>`", self.script)

        # 2. showToast must use messageSpan.textContent to safely render toast text
        self.assertIn("messageSpan.textContent = text", self.script)

        # 3. No undefined showNotification() calls in frontend script
        self.assertNotIn("showNotification(", self.script)

        # 4. Empty-state error rendering in accounts list must escape error message
        self.assertIn("${escapeHtml(err.message)}", self.script)

    def test_workspace_multi_tenancy_bleed_isolation_contract(self):
        # 1. State must track workspaceEpoch
        self.assertIn("workspaceEpoch: 0", self.script)

        # 2. resetWorkspaceState must clear all transient selections, summary caches and in-flight promises
        self.assertIn("function resetWorkspaceState()", self.script)
        self.assertIn("state.selectedAccounts.clear()", self.script)
        self.assertIn("state.selectedRuleIds.clear()", self.script)
        self.assertIn("state.linkRuleSelectedAccountIds.clear()", self.script)
        self.assertIn("state.summaryCache = {}", self.script)
        self.assertIn("state.summary = null", self.script)
        self.assertIn("loadAccountsInFlightPromise = null", self.script)

        # 3. switchWorkspace and submitCreateWorkspaceFromPage must invoke resetWorkspaceState
        self.assertIn("resetWorkspaceState();", self.script)

        # 4. Critical loaders must guard against workspace epoch changes
        self.assertIn("if (state.workspaceEpoch !== epoch) return;", self.script)

    def test_template_field_sanitization_and_url_security_contracts(self):
        # 1. sanitizeUrl helper must exist and be exposed to window
        self.assertIn("function sanitizeUrl(url)", self.script)
        self.assertIn("window.sanitizeUrl = sanitizeUrl", self.script)

        # 2. avatar_url must never be directly interpolated without sanitizeUrl and escapeHtml
        self.assertNotIn('${m.avatar_url ? `<img src="${m.avatar_url}"', self.script)
        self.assertNotIn('uAvatar.innerHTML = `<img src="${user.avatar_url}"', self.script)
        self.assertIn("${safeAvatar ? `<img src=\"${escapeHtml(safeAvatar)}\" alt=\"\">` : initial}", self.script)
        self.assertIn("uAvatar.innerHTML = `<img src=\"${escapeHtml(safeAvatar)}\"", self.script)

        # 3. account_id must be escaped in summary table, mobile cards, parsed chips and batch results
        self.assertNotIn("(${acc.account_id})", self.script)
        self.assertIn("(${escapeHtml(acc.account_id)})", self.script)
        self.assertNotIn("<code>${p.account_id}</code>", self.script)
        self.assertIn("<code>${escapeHtml(p.account_id)}</code>", self.script)
        self.assertNotIn("(${item.account_id})", self.script)
        self.assertNotIn("<b>${item.account_id}</b>", self.script)
        self.assertIn("(${escapeHtml(item.account_id)})", self.script)
        self.assertIn("<b>${escapeHtml(item.account_id)}</b>", self.script)

    def test_workspace_logo_upload_is_persisted_before_workspace_creation(self):
        self.assertIn("state.pageWorkspaceLogoFile = file", self.script)
        self.assertIn("/api/onboarding/workspace/logo", self.script)
        self.assertIn("logo_url: logoUrl", self.script)
        self.assertIn("5 * 1024 * 1024", self.script)
        self.assertNotIn("image/gif", self.index)
        self.assertNotIn("10 МБ", self.index)
        self.assertNotIn("10MB", self.index)
