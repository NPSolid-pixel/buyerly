export const LONG_LABEL = 'Очень длинное название workspace и рекламного кабинета — '.repeat(8).trim();

const workspace = {
  id: 109,
  name: 'Buyerly Visual QA',
  slug: 'quality-workspace',
  badge_text: 'Q',
  badge_color: '#9A5B00',
  is_active: true
};

const account = (long = false) => ({
  id: 501,
  account_id: 'act_123456789012345678901234567890',
  name: long ? LONG_LABEL : 'Nordic Growth Account',
  custom_name: long ? `${LONG_LABEL} custom` : 'Nordic Growth',
  note: long ? LONG_LABEL : 'Основной кабинет команды',
  account_status: 1,
  is_active: true,
  rules_enabled: true,
  timezone_name: 'Europe/Stockholm',
  currency: 'USD',
  today_spend: 1284.42,
  today_leads: 73,
  group_ids: [801],
  active_rules: [{ preset_id: 701, name: 'Stop high CPL' }],
  latest_metrics: { spend: 1284.42, leads: 73, cpl: 17.59, ctr: 2.81 }
});

const preset = (long = false) => ({
  id: 701,
  name: long ? LONG_LABEL : 'Stop high CPL',
  action: 'turn_off',
  check_interval_minutes: 5,
  conditions: [{ metric: 'cpl', operator: 'gt', value: 25 }]
});

const auditEvent = (long = false) => ({
  id: 901,
  created_at: '2026-08-29T12:00:00Z',
  status: 'SUCCESS',
  display_status: 'SUCCESS',
  category: 'rule_action',
  event_type: 'rule_action',
  account_id: 'act_123456789012345678901234567890',
  account_name: long ? LONG_LABEL : 'Nordic Growth',
  adset_id: '23851234567890123',
  adset_name: long ? `${LONG_LABEL} ad set` : 'Prospecting · Broad',
  rule_name: long ? `${LONG_LABEL} rule` : 'Stop high CPL',
  action: 'turn_off',
  message: long ? LONG_LABEL : 'Ad set остановлен после подтверждённого превышения CPL.',
  actor_type: 'worker',
  details: {},
  before_state: {},
  after_state: {}
});

function summaryFixture({ empty = false, long = false, partial = false } = {}) {
  const accounts = empty ? [] : [{
    ...account(long),
    short_name: long ? LONG_LABEL : 'Nordic Growth',
    data_status: partial ? 'error' : 'synced',
    data_status_label: partial ? 'Meta временно не вернула метрики' : 'Данные получены',
    spend: partial ? 0 : 1284.42,
    impressions: partial ? 0 : 184200,
    reach: partial ? 0 : 93400,
    frequency: partial ? null : 1.97,
    clicks: partial ? 0 : 5170,
    unique_clicks: partial ? 0 : 4310,
    link_clicks: partial ? 0 : 3920,
    outbound_clicks: partial ? 0 : 3210,
    landing_page_views: partial ? 0 : 2890,
    leads: partial ? 0 : 73,
    registrations: partial ? 0 : 21,
    purchases: partial ? 0 : 8,
    cpm: partial ? null : 6.97,
    ctr: partial ? null : 2.81,
    cpc: partial ? null : 0.25,
    cpl: partial ? null : 17.59,
    cpreg: partial ? null : 61.16,
    cpp: partial ? null : 160.55
  }];
  const synced = partial || empty ? 0 : accounts.length;
  return {
    source: 'Meta Marketing API',
    generated_at: '2026-08-29T12:00:00Z',
    accounts_count: accounts.length,
    total_spend: partial ? 0 : 1284.42,
    total_leads: partial ? 0 : 73,
    total_regs: partial ? 0 : 21,
    total_purchases: partial ? 0 : 8,
    total_impressions: partial ? 0 : 184200,
    total_reach: partial ? 0 : 93400,
    total_clicks: partial ? 0 : 5170,
    total_unique_clicks: partial ? 0 : 4310,
    total_link_clicks: partial ? 0 : 3920,
    total_outbound_clicks: partial ? 0 : 3210,
    total_landing_page_views: partial ? 0 : 2890,
    avg_frequency: partial ? null : 1.97,
    avg_cpm: partial ? null : 6.97,
    avg_ctr: partial ? null : 2.81,
    avg_ctr_link: partial ? null : 2.13,
    avg_ctr_outbound: partial ? null : 1.74,
    avg_cpc: partial ? null : 0.25,
    avg_cpc_link: partial ? null : 0.33,
    cost_per_lead: partial ? null : 17.59,
    cost_per_registration: partial ? null : 61.16,
    cost_per_purchase: partial ? null : 160.55,
    cost_per_landing_page_view: partial ? null : 0.44,
    display_currency: 'USD',
    mixed_currencies: false,
    currency_totals: empty ? [] : [{ currency: 'USD', accounts_count: synced, spend: partial ? 0 : 1284.42 }],
    cache: { origin: 'database', is_cached: true },
    data_quality: {
      status: partial ? 'partial' : empty ? 'unavailable' : 'complete',
      metrics_coverage_percent: empty ? 0 : partial ? 0 : 100,
      accounts_synced: synced,
      accounts_total: accounts.length,
      accounts_failed: partial ? 1 : 0,
      accounts_blocked: 0
    },
    metric_definitions: { spend: 'Расход по данным Meta.', leads: 'Лиды из выбранного attribution window.' },
    accounts
  };
}

export function fixtureFor(pathname, scenario = 'populated') {
  const empty = scenario === 'empty';
  const long = scenario === 'long';
  const partial = scenario === 'partial';
  const accounts = empty ? [] : [account(long)];
  const events = empty ? [] : [auditEvent(long)];
  const presets = empty ? [] : [preset(long)];
  const groups = empty ? [] : [{
    id: 801,
    name: long ? LONG_LABEL : 'Scale protected',
    description: long ? LONG_LABEL : 'Production safeguards',
    color: 'purple',
    preset_ids: [701],
    account_ids: ['act_123456789012345678901234567890']
  }];

  if (pathname === '/api/me') return {
    id: 42,
    username: 'visual.qa',
    full_name: long ? LONG_LABEL : 'Visual QA',
    first_name: 'Visual',
    role: 'admin',
    onboarding_completed: true,
    workspaces: [{ ...workspace, name: long ? LONG_LABEL : workspace.name }],
    active_workspace: { ...workspace, name: long ? LONG_LABEL : workspace.name }
  };
  if (pathname === '/api/accounts') return accounts;
  if (pathname === '/api/account-groups') return groups;
  if (pathname === '/api/presets') return presets;
  if (pathname === '/api/rule-groups') return groups;
  if (pathname === '/api/meta/connections') return empty ? [] : [{
    id: 601,
    provider_user_name: long ? LONG_LABEL : 'Meta Operations Profile',
    provider_user_id: '123456789012345678901234567890',
    status: 'active',
    days_until_expiration: 42,
    token_expires_at: '2026-10-10T12:00:00Z',
    last_validated_at: '2026-08-29T12:00:00Z'
  }];
  if (pathname === '/api/health/overview') return {
    overall_status: 'healthy',
    counts: { healthy: accounts.length, degraded: 0, critical: 0, unknown: 0 },
    signals: {
      api_synthetic_availability_percent: 99.99,
      api_synthetic_latency_p95_ms: 184,
      worker_cycle_lag_seconds: 60,
      action_error_rate_24h_percent: 0,
      action_error_rate_warning_percent: 2,
      action_error_rate_critical_percent: 5,
      meta_quota_percent: 18,
      token_problem_count: 0
    },
    accounts: accounts.map((item) => ({
      account_id: item.account_id,
      name: item.name,
      status: 'healthy',
      cause: 'none',
      last_success_at: '2026-08-29T12:00:00Z'
    }))
  };
  if (pathname === '/api/audit-events') return {
    items: events,
    page: 1,
    page_size: 25,
    total: events.length,
    total_pages: 1,
    status_counts: empty ? {} : { SUCCESS: 1, ERROR: 0, SKIPPED: 0 }
  };
  if (pathname === '/api/summary') return summaryFixture({ empty, long, partial });
  if (pathname === '/api/analytics-view') return {
    view_mode: 'overview',
    visible_columns: ['account', 'data', 'spend', 'leads', 'cpl'],
    column_order: ['account', 'data', 'spend', 'leads', 'cpl'],
    column_widths: {},
    sort_column: '',
    sort_direction: 'desc',
    filters: { query: '', status: 'all', group_id: 'all' },
    period: 'today'
  };
  if (pathname === '/api/adsets/stopped') return [];
  if (pathname === '/api/settings') return {
    user_role: 'admin',
    poll_interval_minutes: 5,
    critical_rule_interval_minutes: 1,
    stop_confirmation_minutes: 3,
    inventory_cache_minutes: 15,
    account_health_interval_minutes: 5,
    max_concurrent_accounts: 4,
    max_concurrent_actions: 2,
    usage_soft_limit_percent: 60,
    usage_hard_limit_percent: 80,
    adaptive_polling_enabled: true,
    runtime: { finished_at: '2026-08-29T12:00:00Z', duration_ms: 1280, errors_count: 0, usage_percent: 18 }
  };
  if (pathname === '/api/auth/sessions') return empty ? [] : [{
    id: 'session-visual-qa',
    current: true,
    user_agent: long ? LONG_LABEL : 'Chromium · Linux',
    ip_address: '127.0.0.1',
    last_seen_at: '2026-08-29T12:00:00Z'
  }];
  return {};
}

export const routeContracts = [
  { tab: 'home', path: '/quality-workspace/today', states: ['loading', 'partial', 'long'] },
  { tab: 'fb_accounts', path: '/quality-workspace/connections', states: ['loading', 'empty', 'error', 'partial', 'long'] },
  { tab: 'accounts', path: '/quality-workspace/accounts', states: ['loading', 'empty', 'error', 'long'] },
  { tab: 'rules', path: '/quality-workspace/automations', states: ['loading', 'empty', 'long'] },
  { tab: 'summary', path: '/quality-workspace/efficiency', states: ['loading', 'empty', 'error', 'partial', 'long'] },
  { tab: 'logs', path: '/quality-workspace/action-history', states: ['loading', 'empty', 'error', 'long'] },
  { tab: 'settings', path: '/quality-workspace/settings', states: ['loading', 'error', 'long'] }
];
