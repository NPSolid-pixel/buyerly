/**
 * Buyerly Web App — Core Frontend Application Logic (Standalone SaaS)
 */

(function () {
  'use strict';

  const SUMMARY_AUTO_REFRESH_MS = 3 * 60 * 1000;
  const SUMMARY_COLUMNS = [
    { key: 'account', label: 'Кабинет', group: 'base', required: true },
    { key: 'custom_name', label: 'Моё название', group: 'base' },
    { key: 'note', label: 'Заметка', group: 'base' },
    { key: 'data', label: 'Статус данных', group: 'base', required: true },
    { key: 'spend', label: 'Spend', group: 'base' },
    { key: 'impressions', label: 'Показы', group: 'delivery' },
    { key: 'reach', label: 'Охват', group: 'delivery' },
    { key: 'frequency', label: 'Частота', group: 'delivery' },
    { key: 'cpm', label: 'CPM', group: 'delivery' },
    { key: 'clicks', label: 'Все клики', group: 'traffic' },
    { key: 'link_clicks', label: 'Link Clicks', group: 'traffic' },
    { key: 'unique_clicks', label: 'Unique Clicks', group: 'traffic' },
    { key: 'outbound_clicks', label: 'Outbound Clicks', group: 'traffic' },
    { key: 'landing_page_views', label: 'Landing Page Views', group: 'traffic' },
    { key: 'ctr', label: 'CTR All', group: 'traffic' },
    { key: 'ctr_link', label: 'CTR Link', group: 'traffic' },
    { key: 'cpc', label: 'CPC All', group: 'traffic' },
    { key: 'cpc_link', label: 'CPC Link', group: 'traffic' },
    { key: 'leads', label: 'Лиды', group: 'funnel' },
    { key: 'registrations', label: 'Регистрации', group: 'funnel' },
    { key: 'purchases', label: 'Покупки', group: 'funnel' },
    { key: 'cpl', label: 'CPL', group: 'funnel' },
    { key: 'cpreg', label: 'CPReg', group: 'funnel' },
    { key: 'cpp', label: 'CPP', group: 'funnel' }
  ];
  const SUMMARY_VIEW_PRESETS = {
    overview: ['account', 'custom_name', 'note', 'data', 'spend', 'impressions', 'clicks', 'link_clicks', 'leads', 'registrations', 'purchases', 'cpl', 'cpreg', 'cpp'],
    delivery: ['account', 'custom_name', 'data', 'spend', 'impressions', 'reach', 'frequency', 'cpm'],
    traffic: ['account', 'custom_name', 'data', 'spend', 'impressions', 'clicks', 'link_clicks', 'unique_clicks', 'outbound_clicks', 'landing_page_views', 'ctr', 'ctr_link', 'cpc', 'cpc_link'],
    funnel: ['account', 'custom_name', 'data', 'spend', 'leads', 'registrations', 'purchases', 'cpl', 'cpreg', 'cpp'],
    all: SUMMARY_COLUMNS.map(column => column.key)
  };
  const SUMMARY_COLUMN_GROUPS = {
    base: { label: 'Основное', columns: ['account', 'custom_name', 'note', 'data', 'spend'] },
    delivery: { label: 'Доставка', columns: ['impressions', 'reach', 'frequency', 'cpm'] },
    traffic: { label: 'Трафик', columns: ['clicks', 'link_clicks', 'unique_clicks', 'outbound_clicks', 'landing_page_views', 'ctr', 'ctr_link', 'cpc', 'cpc_link'] },
    funnel: { label: 'Воронка', columns: ['leads', 'registrations', 'purchases', 'cpl', 'cpreg', 'cpp'] }
  };
  const SUMMARY_DEFAULT_COLUMN_WIDTHS = {
    account: 260, custom_name: 180, note: 280, data: 120, spend: 112,
    impressions: 104, reach: 104, frequency: 96, cpm: 96,
    clicks: 104, link_clicks: 104, unique_clicks: 104, outbound_clicks: 112,
    landing_page_views: 120, ctr: 96, ctr_link: 96, cpc: 96, cpc_link: 96,
    leads: 88, registrations: 96, purchases: 96, cpl: 96, cpreg: 96, cpp: 96
  };
  const SUMMARY_COLUMN_MIN_WIDTH = 72;
  const SUMMARY_COLUMN_MAX_WIDTH = 420;
  const TAB_ROUTES = Object.freeze({
    home: '/home',
    fb_accounts: '/facebook-accounts',
    accounts: '/accounts',
    rules: '/rules',
    summary: '/summary',
    logs: '/logs',
    add: '/add-accounts',
    settings: '/settings'
  });
  const ROUTE_TABS = Object.freeze({
    ...Object.fromEntries(Object.entries(TAB_ROUTES).map(([tab, route]) => [route, tab])),
    '/fb-accounts': 'fb_accounts',
    '/fb_accounts': 'fb_accounts',
    '/add': 'add',
    '/main': 'home',
    '/dashboard': 'home'
  });
  const LEGACY_ROUTE_TABS = Object.freeze({ '/': 'home', '/dashboard': 'home' });
  const TAB_PAGE_TITLES = Object.freeze({
    home: 'Главная — Buyerly',
    fb_accounts: 'FB Аккаунты — Buyerly',
    accounts: 'Все кабинеты — Buyerly',
    rules: 'Правила — Buyerly',
    summary: 'Сводка — Buyerly',
    logs: 'Логи — Buyerly',
    add: 'Добавить кабинеты — Buyerly',
    settings: 'Настройки — Buyerly'
  });
  let summaryAutoRefreshTimer = null;
  let summaryViewSaveQueue = Promise.resolve();
  let summaryViewChangeVersion = 0;
  let summaryFilterSaveTimer = null;
  let summaryColumnResizeState = null;

  // Application State
  const state = {
    user: null,
    workspaces: [],
    activeWorkspace: null,
    newWorkspaceSelectedColor: '#F5A300',
    editWorkspaceSelectedColor: '#F5A300',
    collapsedSections: new Set(JSON.parse(localStorage.getItem('buyerly_collapsed_sections') || '[]')),
    accountGroupsSortMode: localStorage.getItem('buyerly_groups_sort_mode') || 'relevant',
    accountGroupsCustomOrder: JSON.parse(localStorage.getItem('buyerly_groups_custom_order') || '[]'),
    selectedAccounts: new Set(),
    accountsColumnOrder: JSON.parse(localStorage.getItem('buyerly_accounts_col_order_v2') || 'null') || [
      'name', 'status', 'timezone', 'spend', 'cpm', 'cpc', 'ctr', 'leads', 'cpl', 'registrations', 'cpreg', 'purchases', 'cpp', 'automation'
    ],
    accountsSortColumn: localStorage.getItem('buyerly_accounts_sort_col') || 'name',
    accountsSortDirection: localStorage.getItem('buyerly_accounts_sort_dir') || 'asc',
    accountsColumnWidths: JSON.parse(localStorage.getItem('buyerly_accounts_col_widths') || '{}'),
    fbConnections: [],
    accounts: [],
    accountGroups: [],
    accountGroupFilter: 'all',
    summary: null,
    summaryCache: {},
    summaryLoading: false,
    summaryQueuedRequest: null,
    summaryView: {
      view_mode: 'all',
      visible_columns: [...SUMMARY_VIEW_PRESETS.all],
      column_order: [...SUMMARY_VIEW_PRESETS.all],
      column_widths: { ...SUMMARY_DEFAULT_COLUMN_WIDTHS },
      sort_column: '',
      sort_direction: 'desc',
      filters: { query: '', status: 'all', group_id: 'all' },
      period: 'today'
    },
    summaryViewLoaded: false,
    presets: [],
    ruleGroups: [],
    collapsedRuleGroups: new Set(JSON.parse(localStorage.getItem('buyerly_collapsed_rule_groups') || '[]')),
    ruleGroupColors: JSON.parse(localStorage.getItem('buyerly_rule_group_colors') || '{}'),
    selectedRuleIds: new Set(),
    chooseRuleTargetGroupId: null,
    chooseRuleSelectedIndex: 0,
    chooseRuleFilteredList: [],
    linkRuleModalPresetId: null,
    linkRuleSelectedAccountIds: new Set(),
    activePresetId: null,
    templatePresetId: null,
    ruleBuilderMode: 'create',
    currentPeriod: 'today',
    activeTab: 'accounts',
    filter: 'all',
    searchQuery: '',
    parsedAccounts: [],
    metaOAuth: {
      configured: false,
      connections: [],
      activeConnectionId: null,
      assets: [],
      selectedAccountIds: new Set(),
      callbackHandled: false
    },
    auditEvents: [],
    auditPage: 1,
    auditTotalPages: 1,
    pendingLogsAccountId: '',
    stoppedAdsets: [],
    settings: {
      poll_interval_minutes: 10,
      critical_rule_interval_minutes: 2,
      stop_confirmation_minutes: 10,
      inventory_cache_minutes: 5,
      account_health_interval_minutes: 15,
      max_concurrent_accounts: 3,
      max_concurrent_actions: 3,
      usage_soft_limit_percent: 60,
      usage_hard_limit_percent: 80,
      adaptive_polling_enabled: true,
      runtime: {}
    }
  };

  // Helper to get Web Auth Token
  function getWebAuthToken() {
    try {
      return localStorage.getItem('buyerly_auth_token') || sessionStorage.getItem('buyerly_auth_token') || '';
    } catch (e) {
      return '';
    }
  }

  function setWebAuthToken(token) {
    try {
      if (token) {
        localStorage.setItem('buyerly_auth_token', token);
        sessionStorage.setItem('buyerly_auth_token', token);
      } else {
        localStorage.removeItem('buyerly_auth_token');
        sessionStorage.removeItem('buyerly_auth_token');
      }
    } catch (e) {}
  }

  function getTelegramInitData() {
    try {
      return window.Telegram?.WebApp?.initData || '';
    } catch (e) {
      return '';
    }
  }

  // API Client with Bearer Token Authentication
  async function apiRequest(endpoint, options = {}) {
    const headers = {
      'Content-Type': 'application/json',
      ...(options.headers || {})
    };

    const telegramInitData = getTelegramInitData();
    const authToken = getWebAuthToken();
    if (telegramInitData) {
      headers['Authorization'] = `tma ${telegramInitData}`;
    } else if (authToken) {
      headers['Authorization'] = `Bearer ${authToken}`;
    }

    try {
      const response = await fetch(endpoint, {
        ...options,
        headers
      });

      if (!response.ok) {
        if (response.status === 401 && endpoint !== '/api/auth/login') {
          setWebAuthToken('');
          const loginScreen = document.getElementById('loginScreen');
          const appEl = document.getElementById('app');
          if (appEl) appEl.style.display = 'none';
          if (loginScreen) {
            loginScreen.style.display = 'flex';
            loginScreen.classList.remove('hidden');
          }
        }
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `Ошибка сервера (${response.status})`);
      }

      return await response.json();
    } catch (err) {
      console.error(`API Error on ${endpoint}:`, err);
      throw err;
    }
  }

  // Haptic Feedback (Safe no-op in browser)
  function haptic(type = 'impact', style = 'medium') {
    if (navigator.vibrate) {
      try { navigator.vibrate(20); } catch (e) {}
    }
  }

  // Toast Notification System
  function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    
    let iconSvg = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>';
    if (type === 'success') {
      iconSvg = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>';
    } else if (type === 'error') {
      iconSvg = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>';
    }

    toast.innerHTML = `<span>${iconSvg}</span> <span>${message}</span>`;
    container.appendChild(toast);

    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateY(-10px)';
      toast.style.transition = 'all 0.3s ease';
      setTimeout(() => toast.remove(), 300);
    }, 3200);
  }

  // ==========================================================
  // TAB NAVIGATION & ROUTING
  // ==========================================================
  function normalizeAppPath(pathname = '/') {
    const path = String(pathname || '/').replace(/\/+$/, '');
    return path || '/';
  }

  function parsePathLocation(pathname = window.location.pathname, search = window.location.search) {
    const raw = normalizeAppPath(pathname);
    const trimmed = raw.replace(/^\/+|\/+$/g, '');
    let groupFilter = 'all';

    // Parse search parameters if provided e.g. ?group=1 or ?group_id=1
    if (search) {
      try {
        const params = new URLSearchParams(search);
        if (params.has('group')) groupFilter = params.get('group') || 'all';
        else if (params.has('group_id')) groupFilter = params.get('group_id') || 'all';
      } catch (e) {}
    }

    if (!trimmed) {
      return { workspaceSlug: '', tab: 'home', groupFilter: groupFilter };
    }

    const parts = trimmed.split('/');

    // Paths with workspace slug e.g. /buyerly/groups/1 or /buyerly/accounts or /buyerly/facebook-accounts
    if (parts.length >= 2) {
      const slug = parts[0];
      const segment = parts[1];
      const subSegment = parts[2] || '';

      if (segment === 'groups' || segment === 'lists' || segment === 'collection') {
        return { workspaceSlug: slug, tab: 'accounts', groupFilter: subSegment || groupFilter || 'all' };
      }
      if (segment === 'facebook-groups') {
        return { workspaceSlug: slug, tab: 'fb_accounts', groupFilter: subSegment || groupFilter || 'all' };
      }
      if (segment === 'rule-groups') {
        return { workspaceSlug: slug, tab: 'rules', groupFilter: subSegment || groupFilter || 'all' };
      }
      if (segment === 'rules') {
        const ruleId = subSegment && /^\d+$/.test(subSegment) ? Number(subSegment) : null;
        return { workspaceSlug: slug, tab: 'rules', groupFilter: groupFilter, ruleId: ruleId };
      }
      if (segment === 'chats') {
        return { workspaceSlug: slug, tab: 'home', chatId: subSegment, groupFilter: 'all' };
      }
      if (segment === 'accounts' && subSegment) {
        return { workspaceSlug: slug, tab: 'accounts', groupFilter: subSegment };
      }

      const segmentTab = ROUTE_TABS['/' + segment] || (Object.hasOwn(TAB_ROUTES, segment) ? segment : null);
      if (segmentTab) {
        return { workspaceSlug: slug, tab: segmentTab, groupFilter: groupFilter };
      }
      return { workspaceSlug: slug, tab: 'home', groupFilter: groupFilter };
    }

    // Paths without workspace slug e.g. /groups/1 or /accounts or /rules or /rules/12
    if (parts.length === 2) {
      if (parts[0] === 'rules' && /^\d+$/.test(parts[1])) {
        return { workspaceSlug: '', tab: 'rules', groupFilter: groupFilter, ruleId: Number(parts[1]) };
      }
    }

    if (parts.length === 1) {
      const candidate = parts[0];
      if (candidate === 'groups' || candidate === 'lists' || candidate === 'collection') {
        return { workspaceSlug: '', tab: 'accounts', groupFilter: groupFilter };
      }
      if (candidate === 'facebook-groups') {
        return { workspaceSlug: '', tab: 'fb_accounts', groupFilter: groupFilter };
      }
      if (candidate === 'rule-groups') {
        return { workspaceSlug: '', tab: 'rules', groupFilter: groupFilter };
      }
      if (candidate === 'chats') {
        return { workspaceSlug: '', tab: 'home', groupFilter: 'all' };
      }
      const candidateTab = ROUTE_TABS['/' + candidate] || (Object.hasOwn(TAB_ROUTES, candidate) ? candidate : null);
      if (candidateTab) {
        return { workspaceSlug: '', tab: candidateTab, groupFilter: groupFilter };
      }
      if (LEGACY_ROUTE_TABS['/' + candidate]) {
        return { workspaceSlug: '', tab: LEGACY_ROUTE_TABS['/' + candidate], groupFilter: groupFilter };
      }
      return { workspaceSlug: candidate, tab: 'home', groupFilter: groupFilter };
    }

    return { workspaceSlug: '', tab: 'home', groupFilter: groupFilter };
  }

  function tabFromLocation(pathname = window.location.pathname) {
    const parsed = parsePathLocation(pathname);
    return parsed.tab;
  }

  function workspaceSlugFromLocation(pathname = window.location.pathname) {
    const parsed = parsePathLocation(pathname);
    return parsed.workspaceSlug;
  }

  function isKnownAppPath(pathname = window.location.pathname) {
    const path = normalizeAppPath(pathname);
    if (path === '/' || path === '/sign-in' || path === '/login') return true;
    if (ROUTE_TABS[path] || LEGACY_ROUTE_TABS[path]) return true;
    const parsed = parsePathLocation(pathname);
    return Boolean(parsed.tab && (Object.hasOwn(TAB_ROUTES, parsed.tab) || ROUTE_TABS['/' + parsed.tab]));
  }

  function rememberReturnRoute(pathname = window.location.pathname, search = window.location.search) {
    if (!isKnownAppPath(pathname)) return;
    try {
      sessionStorage.setItem('buyerly_return_route', pathname + (search || ''));
    } catch (e) {}
  }

  function consumeReturnRoute() {
    try {
      const route = sessionStorage.getItem('buyerly_return_route') || '';
      sessionStorage.removeItem('buyerly_return_route');
      return route;
    } catch (e) {
      return '';
    }
  }

  function syncBrowserRoute(tab, method = 'push') {
    if (method === 'none') return;
    try {
      const historyFn = method === 'replace' ? 'replaceState' : 'pushState';
      const slug = state.activeWorkspace ? state.activeWorkspace.slug : '';
      const tabPath = (TAB_ROUTES[tab] || `/${tab}`).replace(/^\//, '');
      let route = `/${tabPath}`;
      if (slug) {
        if (tab === 'accounts' && state.accountGroupFilter && state.accountGroupFilter !== 'all') {
          route = `/${slug}/groups/${encodeURIComponent(state.accountGroupFilter)}`;
        } else if (tab === 'rules' && state.currentRecordPresetId) {
          route = `/${slug}/rules/${state.currentRecordPresetId}`;
        } else {
          route = `/${slug}/${tabPath}`;
        }
      } else {
        if (tab === 'rules' && state.currentRecordPresetId) {
          route = `/rules/${state.currentRecordPresetId}`;
        } else {
          route = `/${tabPath}`;
        }
      }
      if (typeof window.history[historyFn] === 'function') {
        window.history[historyFn]({ 
          tab: tab, 
          workspace: slug, 
          groupFilter: state.accountGroupFilter || 'all',
          ruleId: (tab === 'rules') ? state.currentRecordPresetId : null 
        }, '', route);
      }
    } catch (e) {
      console.warn('syncBrowserRoute error:', e);
    }
  }

  window.updateSidebarActiveState = function () {
    const tabName = state.activeTab;
    document.querySelectorAll('.nav-tab, .mobile-nav-item, .nav-item, .list-item').forEach(btn => {
      let isActive = false;
      if (btn.dataset.tab === tabName) {
        if (tabName === 'accounts') {
          const groupFilter = btn.dataset.groupFilter;
          if (groupFilter !== undefined) {
            isActive = String(groupFilter) === String(state.accountGroupFilter || 'all');
          } else {
            isActive = (state.accountGroupFilter === 'all');
          }
        } else {
          isActive = true;
        }
      }
      btn.classList.toggle('active', isActive);
      if (isActive) btn.setAttribute('aria-current', 'page');
      else btn.removeAttribute('aria-current');
    });
  };

  window.toggleSidebarSection = function (sectionKey) {
    if (state.collapsedSections.has(sectionKey)) {
      state.collapsedSections.delete(sectionKey);
    } else {
      state.collapsedSections.add(sectionKey);
    }
    try {
      localStorage.setItem('buyerly_collapsed_sections', JSON.stringify(Array.from(state.collapsedSections)));
    } catch (e) {}
    applySidebarSectionsCollapsedState();
  };

  function applySidebarSectionsCollapsedState() {
    const isAccountsCollapsed = state.collapsedSections.has('accounts');

    const headerAccounts = document.getElementById('headerAccountsSection');
    const listAccounts = document.getElementById('sidebarAccountsListContainer');
    if (headerAccounts) headerAccounts.classList.toggle('collapsed', isAccountsCollapsed);
    if (listAccounts) listAccounts.classList.toggle('collapsed', isAccountsCollapsed);
  }

  window.switchTab = function (requestedTab, options = {}) {
    const tabName = Object.hasOwn(TAB_ROUTES, requestedTab) ? requestedTab : 'accounts';
    state.activeTab = tabName;
    if (tabName !== 'accounts') {
      state.accountGroupFilter = 'all';
    }
    if (options.haptic !== false) haptic('selection');
    syncBrowserRoute(tabName, options.historyMode || 'push');

    // Update active tab buttons (Desktop & Mobile & Sidebar)
    window.updateSidebarActiveState();

    // Update Header Breadcrumbs
    const breadcrumbArea = document.getElementById('headerBreadcrumbArea');
    if (breadcrumbArea) {
      if (tabName === 'accounts' && state.accountGroupFilter && state.accountGroupFilter !== 'all') {
        const group = (state.accountGroups || []).find(g => 
          String(g.id) === String(state.accountGroupFilter) || 
          String(g.name || '').toLowerCase() === String(state.accountGroupFilter).toLowerCase()
        );
        const groupTitle = group ? escapeHtml(group.name) : 'Группа кабинетов';
        breadcrumbArea.innerHTML = `
          <div class="breadcrumb-title">
            <svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor" style="margin-right:6px; color:var(--text-muted);"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"/></svg>
            <span>${groupTitle}</span>
          </div>
        `;
        document.title = `${group ? group.name : 'Группа'} — Buyerly`;
      } else {
        const titles = {
          home: '<svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor" style="margin-right:6px;"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6"/></svg><span>Главная</span>',
          fb_accounts: '<svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor" style="margin-right:6px;"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"/></svg><span>FB Аккаунты</span>',
          accounts: '<svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor" style="margin-right:6px;"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"/></svg><span>Все кабинеты</span>',
          rules: '<svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor" style="margin-right:6px;"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/></svg><span>Правила</span>',
          summary: '<svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor" style="margin-right:6px;"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/></svg><span>Сводка</span>',
          logs: '<svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor" style="margin-right:6px;"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"/></svg><span>Логи</span>',
          add: '<svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor" style="margin-right:6px;"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M12 4v16m8-8H4"/></svg><span>Добавить кабинеты</span>',
          settings: '<svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor" style="margin-right:6px;"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37.996.608 2.296.07 2.572-1.065z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/></svg><span>Настройки</span>'
        };
        breadcrumbArea.innerHTML = `<div class="breadcrumb-title">${titles[tabName] || 'Buyerly'}</div>`;
        document.title = TAB_PAGE_TITLES[tabName] || 'Buyerly — AI Media Buyer';
      }
    }

    // Show active tab section
    document.querySelectorAll('.tab-content').forEach(section => {
      section.classList.toggle('active', section.id === `tab-${tabName}`);
    });

    // Auto-fetch data on tab switch
    if (tabName === 'home') {
      updateHomeGreeting();
    } else if (tabName === 'fb_accounts') {
      loadFacebookAccounts();
    } else if (tabName === 'accounts') {
      loadAccounts();
    } else if (tabName === 'rules') {
      loadRulesTab();
    } else if (tabName === 'summary') {
      initializeSummaryTab();
    } else if (tabName === 'logs') {
      loadLogsTab(1);
    } else if (tabName === 'add') {
      loadMetaConnections();
    } else if (tabName === 'settings') {
      loadSettings();
    }

    // Scroll to top of tab content
    const activeSection = document.getElementById(`tab-${tabName}`);
    if (activeSection) {
      activeSection.scrollTo({ top: 0, behavior: options.scrollBehavior || 'smooth' });
    }
    window.scrollTo({ top: 0, behavior: options.scrollBehavior || 'smooth' });
  };

  // ==========================================================
  // WORKSPACE MANAGEMENT
  // ==========================================================
  function renderWorkspacesDropdown() {
    const badgeEl = document.getElementById('currentWorkspaceBadge');
    const nameEl = document.getElementById('currentWorkspaceName');
    const listEl = document.getElementById('workspaceDropdownList');

    const activeWs = state.activeWorkspace;
    if (activeWs) {
      if (badgeEl) {
        badgeEl.textContent = activeWs.badge_text || (activeWs.name ? activeWs.name.charAt(0).toUpperCase() : 'B');
        badgeEl.style.backgroundColor = activeWs.badge_color || '#F5A300';
      }
      if (nameEl) {
        nameEl.innerHTML = `${escapeHtml(activeWs.name)} <svg fill="none" viewBox="0 0 16 16" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M4 6l4 4 4-4"/></svg>`;
      }
    }

    if (listEl && state.workspaces) {
      listEl.innerHTML = state.workspaces.map(w => {
        const isActive = activeWs && (activeWs.id === w.id || activeWs.slug === w.slug);
        const bText = w.badge_text || (w.name ? w.name.charAt(0).toUpperCase() : 'B');
        const bColor = w.badge_color || '#F5A300';
        return `
          <div class="dropdown-item ${isActive ? 'active' : ''}" onclick="window.switchWorkspace(${w.id});">
            <div class="dropdown-item-left">
              <div class="workspace-badge" style="width:20px;height:20px;font-size:11px;background-color:${escapeHtml(bColor)};">${escapeHtml(bText)}</div>
              <span style="${isActive ? 'font-weight:600;' : ''}">${escapeHtml(w.name)}</span>
            </div>
            ${isActive ? '<svg class="dropdown-check-icon" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd"/></svg>' : ''}
          </div>
        `;
      }).join('');
    }
  }

  window.toggleWorkspaceDropdown = function (event) {
    if (event) event.stopPropagation();
    const dropdown = document.getElementById('workspaceDropdown');
    if (!dropdown) return;
    const isShowing = dropdown.classList.contains('show');
    if (isShowing) {
      dropdown.classList.remove('show');
    } else {
      renderWorkspacesDropdown();
      dropdown.classList.add('show');
    }
  };

  window.closeWorkspaceDropdown = function () {
    const dropdown = document.getElementById('workspaceDropdown');
    if (dropdown) dropdown.classList.remove('show');
  };

  window.switchWorkspace = async function (workspaceId) {
    window.closeWorkspaceDropdown();
    try {
      showLoading();
      const res = await apiRequest('/api/workspaces/switch', {
        method: 'POST',
        body: JSON.stringify({ workspace_id: workspaceId })
      });
      state.activeWorkspace = res.active_workspace;
      state.workspaces = res.workspaces || state.workspaces;
      renderWorkspacesDropdown();
      syncBrowserRoute(state.activeTab, 'push');
      showToast(`Воркспейс: ${state.activeWorkspace.name}`);

      // Refresh data for new active workspace
      await Promise.allSettled([
        loadAccounts(),
        loadFacebookAccounts(),
        loadPresets(),
        loadRuleGroups()
      ]);
    } catch (e) {
      showToast(e.message || 'Ошибка переключения воркспейса', 'error');
    } finally {
      hideLoading();
    }
  };

  window.openCreateWorkspacePage = function () {
    window.closeWorkspaceDropdown();
    const appEl = document.getElementById('app');
    const wsScreen = document.getElementById('createWorkspaceScreen');
    const loginScreen = document.getElementById('loginScreen');
    if (loginScreen) {
      loginScreen.style.display = 'none';
      loginScreen.classList.add('hidden');
    }
    if (appEl) {
      appEl.style.display = 'none';
    }
    if (wsScreen) {
      wsScreen.style.display = 'flex';
      wsScreen.classList.remove('hidden');
    }

    const nameInput = document.getElementById('createWsNameInput');
    const slugInput = document.getElementById('createWsSlugInput');
    if (nameInput) nameInput.value = '';
    if (slugInput) slugInput.value = '';

    state.pageWorkspaceLogoDataUrl = null;
    const fileInput = document.getElementById('createWsLogoFileInput');
    if (fileInput) fileInput.value = '';

    const img = document.getElementById('createWsLogoImg');
    if (img) {
      img.src = '';
      img.classList.add('hidden');
    }

    const badgeLetter = document.getElementById('createWsBadgeLetter');
    if (badgeLetter) {
      badgeLetter.style.display = 'block';
      badgeLetter.textContent = 'W';
    }

    setTimeout(() => nameInput?.focus(), 150);
  };

  window.handleWorkspaceLogoUpload = function (event) {
    const file = event.target.files && event.target.files[0];
    if (!file) return;

    if (file.size > 10 * 1024 * 1024) {
      showToast('Размер файла не должен превышать 10 МБ', 'error');
      event.target.value = '';
      return;
    }

    const reader = new FileReader();
    reader.onload = function (e) {
      const dataUrl = e.target.result;
      state.pageWorkspaceLogoDataUrl = dataUrl;
      const img = document.getElementById('createWsLogoImg');
      const letter = document.getElementById('createWsBadgeLetter');
      if (img) {
        img.src = dataUrl;
        img.classList.remove('hidden');
      }
      if (letter) {
        letter.style.display = 'none';
      }
      showToast('Логотип загружен');
    };
    reader.onerror = function () {
      showToast('Ошибка чтения файла изображения', 'error');
    };
    reader.readAsDataURL(file);
  };

  window.closeCreateWorkspacePage = function () {
    const wsScreen = document.getElementById('createWorkspaceScreen');
    const appEl = document.getElementById('app');
    if (wsScreen) {
      wsScreen.style.display = 'none';
      wsScreen.classList.add('hidden');
    }
    if (appEl) {
      appEl.style.display = 'flex';
    }
    if (state.activeTab) {
      window.switchTab(state.activeTab, { historyMode: 'none', haptic: false });
    }
  };

  window.submitCreateWorkspaceFromPage = async function () {
    const nameInput = document.getElementById('createWsNameInput');
    const name = nameInput ? nameInput.value.trim() : '';
    if (!name) {
      showToast('Введите название воркспейса', 'error');
      nameInput?.focus();
      return;
    }

    const btn = document.getElementById('btnCreateWsSubmit');
    if (btn) btn.disabled = true;

    try {
      const created = await apiRequest('/api/workspaces', {
        method: 'POST',
        body: JSON.stringify({
          name: name,
          badge_color: '#F5A300'
        })
      });

      window.closeCreateWorkspacePage();
      state.activeWorkspace = created;
      const workspacesList = await apiRequest('/api/workspaces');
      state.workspaces = workspacesList;
      renderWorkspacesDropdown();
      syncBrowserRoute('home', 'push');
      window.switchTab('home');
      showToast(`Воркспейс "${created.name}" создан!`);

      // Refresh accounts & rules (will be empty in new workspace)
      await Promise.allSettled([
        loadAccounts(),
        loadFacebookAccounts(),
        loadPresets(),
        loadRuleGroups()
      ]);
    } catch (e) {
      showToast(e.message || 'Ошибка создания воркспейса', 'error');
    } finally {
      if (btn) btn.disabled = false;
    }
  };

  window.openNewWorkspaceModal = function () {
    window.openCreateWorkspacePage();
  };

  window.updateNewWorkspaceSlugPreview = function () {
    const input = document.getElementById('newWorkspaceNameInput');
    const preview = document.getElementById('newWorkspaceSlugPreview');
    const val = input ? input.value : '';
    const slug = slugifyText(val) || 'your-workspace';
    if (preview) preview.textContent = slug;
  };

  window.selectWorkspaceColor = function (el, type) {
    const color = el?.dataset?.color || '#F5A300';
    const containerId = type === 'edit' ? 'editWorkspaceColorPicker' : 'newWorkspaceColorPicker';
    document.querySelectorAll(`#${containerId} .color-swatch`).forEach(sw => {
      sw.classList.toggle('active', sw === el);
    });
    if (type === 'edit') {
      state.editWorkspaceSelectedColor = color;
    } else {
      state.newWorkspaceSelectedColor = color;
    }
  };

  window.submitCreateWorkspace = async function () {
    return window.submitCreateWorkspaceFromPage();
  };

  window.openWorkspaceSettings = function () {
    window.closeWorkspaceDropdown();
    const ws = state.activeWorkspace;
    if (!ws) return;

    const input = document.getElementById('editWorkspaceNameInput');
    if (input) input.value = ws.name || '';
    state.editWorkspaceSelectedColor = ws.badge_color || '#F5A300';

    document.querySelectorAll('#editWorkspaceColorPicker .color-swatch').forEach(sw => {
      sw.classList.toggle('active', sw.dataset.color === (ws.badge_color || '#F5A300'));
    });

    const deleteBtn = document.getElementById('btnDeleteCurrentWorkspace');
    if (deleteBtn) {
      deleteBtn.style.display = (state.workspaces && state.workspaces.length > 1) ? 'inline-flex' : 'none';
    }

    window.openModal('modalWorkspaceSettings');
  };

  window.submitSaveWorkspaceSettings = async function () {
    const ws = state.activeWorkspace;
    if (!ws) return;

    const input = document.getElementById('editWorkspaceNameInput');
    const name = input ? input.value.trim() : '';
    if (!name) {
      showToast('Название не может быть пустым', 'error');
      input?.focus();
      return;
    }

    const btn = document.getElementById('btnSaveWorkspaceSettings');
    if (btn) btn.disabled = true;

    try {
      const updated = await apiRequest(`/api/workspaces/${ws.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          name: name,
          badge_color: state.editWorkspaceSelectedColor || '#F5A300'
        })
      });

      window.closeModal('modalWorkspaceSettings');
      state.activeWorkspace = updated;
      const workspacesList = await apiRequest('/api/workspaces');
      state.workspaces = workspacesList;
      renderWorkspacesDropdown();
      showToast('Настройки воркспейса сохранены');
    } catch (e) {
      showToast(e.message || 'Ошибка сохранения настроек', 'error');
    } finally {
      if (btn) btn.disabled = false;
    }
  };

  window.submitDeleteCurrentWorkspace = async function () {
    const ws = state.activeWorkspace;
    if (!ws) return;

    if (!confirm(`Вы действительно хотите удалить воркспейс "${ws.name}"?`)) {
      return;
    }

    const btn = document.getElementById('btnDeleteCurrentWorkspace');
    if (btn) btn.disabled = true;

    try {
      const res = await apiRequest(`/api/workspaces/${ws.id}`, {
        method: 'DELETE'
      });

      window.closeModal('modalWorkspaceSettings');
      showToast('Воркспейс удален');

      // Reload workspaces and switch
      const workspacesList = await apiRequest('/api/workspaces');
      state.workspaces = workspacesList;
      state.activeWorkspace = workspacesList.find(w => w.is_active) || workspacesList[0];
      renderWorkspacesDropdown();
      syncBrowserRoute('home', 'push');
      window.switchTab('home');

      await Promise.allSettled([
        loadAccounts(),
        loadFacebookAccounts(),
        loadPresets(),
        loadRuleGroups()
      ]);
    } catch (e) {
      showToast(e.message || 'Ошибка удаления воркспейса', 'error');
    } finally {
      if (btn) btn.disabled = false;
    }
  };

  window.openAccountSettings = function () {
    window.closeWorkspaceDropdown();
    window.switchTab('settings');
  };
  // ==========================================================
  // TAB: HOME (ГЛАВНАЯ)
  // ==========================================================
  function updateHomeGreeting() {
    const el = document.getElementById('homeGreetingTitle');
    if (!el) return;
    const hour = new Date().getHours();
    let greeting = 'Доброе утро';
    if (hour >= 12 && hour < 18) {
      greeting = 'Добрый день';
    } else if (hour >= 18 && hour < 23) {
      greeting = 'Добрый вечер';
    } else if (hour >= 23 || hour < 5) {
      greeting = 'Доброй ночи';
    }
    const name = state.user?.full_name || state.user?.username || 'Buyerly';
    el.textContent = `${greeting}, ${name}.`;
  }

  // ==========================================================
  // TAB 1: ACCOUNTS (МОИ КАБИНЕТЫ & СПИСКИ)
  // ==========================================================
  let loadAccountsInFlightPromise = null;

  async function loadAccounts() {
    const listEl = document.getElementById('accountsList');
    const emptyEl = document.getElementById('accountsEmptyState');

    if (loadAccountsInFlightPromise) {
      return loadAccountsInFlightPromise;
    }

    loadAccountsInFlightPromise = (async () => {
      try {
        const [accounts, groups] = await Promise.all([
          apiRequest('/api/accounts'),
          apiRequest('/api/account-groups')
        ]);
        state.accounts = accounts;
        state.accountGroups = groups;
        if (state.accountGroupFilter !== 'all') {
          const matched = groups.find(group => 
            String(group.id) === state.accountGroupFilter || 
            String(group.name || '').toLowerCase() === state.accountGroupFilter.toLowerCase()
          );
          if (matched) {
            state.accountGroupFilter = String(matched.id);
          } else {
            state.accountGroupFilter = 'all';
          }
        }
        renderAccountGroups();
        renderSidebarAccountGroups();
        updateAccountsPageHeader();
        renderAccounts();
        return { accounts, groups };
      } catch (err) {
        if (listEl && state.activeTab === 'accounts') {
          listEl.innerHTML = `<div class="empty-state"><p class="text-danger">${err.message}</p></div>`;
        }
      } finally {
        loadAccountsInFlightPromise = null;
      }
    })();

    return loadAccountsInFlightPromise;
  }

  function accountDisplayName(account) {
    return String(account?.custom_name || '').trim() || account?.name || account?.account_id || 'Кабинет';
  }

  function accountGroupsFor(account) {
    const groupIds = new Set((account?.group_ids || []).map(String));
    return state.accountGroups.filter(group => groupIds.has(String(group.id)));
  }

  function renderAccountGroupTags(account, options = {}) {
    const groups = accountGroupsFor(account);
    if (!groups.length) {
      return options.empty
        ? '<span class="account-group-tag empty">Без группы</span>'
        : '';
    }
    return groups.map(group => (
      `<button class="account-group-tag" type="button" onclick="window.openAccountGroupEditor(${group.id})" title="${escapeHtml(group.description || 'Изменить группу')}">${escapeHtml(group.name)}</button>`
    )).join('');
  }

  function renderAccountGroups() {
    const container = document.getElementById('accountGroupsBar');
    if (!container) return;
    const buttons = state.accountGroups.map(group => (
      `<span class="account-group-filter-wrap">
        <button type="button" class="account-group-filter ${state.accountGroupFilter === String(group.id) ? 'active' : ''}" data-account-group-filter="${group.id}" onclick="window.switchAccountGroup('${group.id}')" title="${escapeHtml(group.description || '')}"><span>${escapeHtml(group.name)}</span><b>${group.accounts_count || 0}</b></button>
        <button type="button" class="account-group-edit" onclick="window.openAccountGroupEditor(${group.id})" aria-label="Изменить группу ${escapeHtml(group.name)}" title="Изменить состав группы"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg></button>
      </span>`
    ));
    container.innerHTML = buttons.join('');
  }

  function getSortedAccountGroups() {
    if (!state.accountGroups || state.accountGroups.length === 0) return [];
    const groups = [...state.accountGroups];
    const mode = state.accountGroupsSortMode || 'relevant';

    if (mode === 'recent') {
      return groups.sort((a, b) => (b.id || 0) - (a.id || 0));
    } else if (mode === 'alpha') {
      return groups.sort((a, b) => (a.name || '').localeCompare(b.name || '', 'ru', { sensitivity: 'base' }));
    } else if (mode === 'custom') {
      const order = state.accountGroupsCustomOrder || [];
      return groups.sort((a, b) => {
        const idxA = order.indexOf(a.id);
        const idxB = order.indexOf(b.id);
        if (idxA !== -1 && idxB !== -1) return idxA - idxB;
        if (idxA !== -1) return -1;
        if (idxB !== -1) return 1;
        return (a.id || 0) - (b.id || 0);
      });
    } else {
      // 'relevant' - sort by active spend / accounts_count desc, then id
      return groups.sort((a, b) => (b.accounts_count || 0) - (a.accounts_count || 0) || (a.id || 0) - (b.id || 0));
    }
  }

  function renderSidebarAccountGroups() {
    const container = document.getElementById('sidebarAccountGroupsContainer');
    if (!container) return;
    const sortedGroups = getSortedAccountGroups();
    if (!sortedGroups || sortedGroups.length === 0) {
      container.innerHTML = '';
      return;
    }
    const html = sortedGroups.map(group => {
      const isActive = state.activeTab === 'accounts' && String(state.accountGroupFilter) === String(group.id);
      return `
        <div class="list-item nav-tab ${isActive ? 'active' : ''}" 
             draggable="true"
             data-group-id="${group.id}"
             data-tab="accounts" 
             data-group-filter="${group.id}" 
             id="navGroup-${group.id}" 
             onclick="window.switchAccountGroup('${group.id}');"
             ondragstart="window.onGroupDragStart(event, ${group.id})"
             ondragover="window.onGroupDragOver(event)"
             ondragenter="window.onGroupDragEnter(event, ${group.id})"
             ondragleave="window.onGroupDragLeave(event)"
             ondrop="window.onGroupDrop(event, ${group.id})"
             ondragend="window.onGroupDragEnd(event)">
          <svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"/></svg>
          <span style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap; flex:1;">${escapeHtml(group.name)}</span>
        </div>
      `;
    }).join('');
    container.innerHTML = html;
  }

  let draggedGroupId = null;

  window.onGroupDragStart = function (e, groupId) {
    draggedGroupId = groupId;
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', String(groupId));
    const target = e.currentTarget;
    setTimeout(() => {
      if (target) target.classList.add('dragging');
    }, 0);
  };

  window.onGroupDragOver = function (e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
  };

  window.onGroupDragEnter = function (e, targetGroupId) {
    e.preventDefault();
    if (!draggedGroupId || draggedGroupId === targetGroupId) return;
    const target = e.currentTarget;
    if (!target) return;
    const rect = target.getBoundingClientRect();
    const midY = rect.top + rect.height / 2;
    if (e.clientY < midY) {
      target.classList.add('drag-over-top');
      target.classList.remove('drag-over-bottom');
    } else {
      target.classList.add('drag-over-bottom');
      target.classList.remove('drag-over-top');
    }
  };

  window.onGroupDragLeave = function (e) {
    const target = e.currentTarget;
    if (target) {
      target.classList.remove('drag-over-top', 'drag-over-bottom');
    }
  };

  window.onGroupDrop = function (e, targetGroupId) {
    e.preventDefault();
    const target = e.currentTarget;
    if (target) {
      target.classList.remove('drag-over-top', 'drag-over-bottom');
    }
    if (!draggedGroupId || draggedGroupId === targetGroupId) return;

    const currentSorted = getSortedAccountGroups();
    const currentIds = currentSorted.map(g => g.id);
    const fromIndex = currentIds.indexOf(draggedGroupId);
    const toIndex = currentIds.indexOf(targetGroupId);

    if (fromIndex === -1 || toIndex === -1) return;

    const rect = target.getBoundingClientRect();
    const midY = rect.top + rect.height / 2;

    currentIds.splice(fromIndex, 1);
    const newTargetIndex = currentIds.indexOf(targetGroupId);
    if (e.clientY < midY) {
      currentIds.splice(newTargetIndex, 0, draggedGroupId);
    } else {
      currentIds.splice(newTargetIndex + 1, 0, draggedGroupId);
    }

    state.accountGroupsCustomOrder = currentIds;
    state.accountGroupsSortMode = 'custom';

    try {
      localStorage.setItem('buyerly_groups_custom_order', JSON.stringify(currentIds));
      localStorage.setItem('buyerly_groups_sort_mode', 'custom');
    } catch (err) {}

    updateSortDropdownUI();
    renderSidebarAccountGroups();
    haptic('impact', 'light');
  };

  window.onGroupDragEnd = function (e) {
    draggedGroupId = null;
    document.querySelectorAll('#sidebarAccountGroupsContainer .list-item').forEach(el => {
      el.classList.remove('dragging', 'drag-over-top', 'drag-over-bottom');
    });
  };

  window.toggleListsSortMenu = function (e) {
    if (e) e.stopPropagation();
    const dropdown = document.getElementById('listsSortDropdown');
    const btnSort = document.getElementById('btnListsSortMenu');
    const header = document.getElementById('headerAccountsSection');
    if (!dropdown) return;
    const isHidden = dropdown.classList.contains('hidden');
    document.getElementById('workspaceDropdown')?.classList.remove('show');
    document.getElementById('bulkGroupDropdown')?.classList.add('hidden');
    dropdown.classList.toggle('hidden', !isHidden);
    if (btnSort) btnSort.classList.toggle('active', isHidden);
    if (header) header.classList.toggle('has-open-menu', isHidden);
    if (isHidden) {
      updateSortDropdownUI();
    }
  };

  window.setGroupsSortMode = function (mode) {
    state.accountGroupsSortMode = mode;
    try {
      localStorage.setItem('buyerly_groups_sort_mode', mode);
    } catch (e) {}

    if (mode === 'custom' && (!state.accountGroupsCustomOrder || state.accountGroupsCustomOrder.length === 0)) {
      state.accountGroupsCustomOrder = (state.accountGroups || []).map(g => g.id);
      try {
        localStorage.setItem('buyerly_groups_custom_order', JSON.stringify(state.accountGroupsCustomOrder));
      } catch (e) {}
    }

    updateSortDropdownUI();
    renderSidebarAccountGroups();
    const dropdown = document.getElementById('listsSortDropdown');
    if (dropdown) dropdown.classList.add('hidden');
    document.getElementById('btnListsSortMenu')?.classList.remove('active');
    document.getElementById('headerAccountsSection')?.classList.remove('has-open-menu');
    haptic('selection');
  };

  function updateSortDropdownUI() {
    const currentMode = state.accountGroupsSortMode || 'relevant';
    document.querySelectorAll('#listsSortDropdown .sort-menu-item').forEach(item => {
      const sort = item.dataset.sort;
      item.classList.toggle('active', sort === currentMode);
    });
  }

  window.copyCurrentGroupLink = function () {
    const slug = (state.activeWorkspace && state.activeWorkspace.slug) ? state.activeWorkspace.slug : 'buyerly';
    const group = (state.accountGroups || []).find(g => 
      String(g.id) === String(state.accountGroupFilter) || 
      String(g.name || '').toLowerCase() === String(state.accountGroupFilter).toLowerCase()
    );
    const idOrSlug = group ? group.id : state.accountGroupFilter;
    const url = `${window.location.origin}/${slug}/groups/${encodeURIComponent(idOrSlug)}`;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(() => {
        showToast('Ссылка на группу скопирована', 'success');
      }).catch(() => {
        prompt('Ссылка на группу:', url);
      });
    } else {
      prompt('Ссылка на группу:', url);
    }
  };

  window.switchAccountGroup = function (groupId, options = {}) {
    state.accountGroupFilter = String(groupId);
    state.activeTab = 'accounts';
    window.switchTab('accounts', {
      historyMode: options.historyMode || 'push',
      haptic: options.haptic,
      scrollBehavior: options.scrollBehavior
    });
    updateAccountsPageHeader();
    renderAccounts();
    window.updateSidebarActiveState();
  };

  function updateAccountsPageHeader() {
    const eyebrow = document.getElementById('accountsPageEyebrow');
    const title = document.getElementById('accountsPageTitle');
    const subtitle = document.getElementById('accountsPageSubtitle');
    const btnSettings = document.getElementById('btnGroupSettings');
    const btnShare = document.getElementById('btnGroupShare');
    const viewTitle = document.getElementById('accountsViewCurrentTitle');

    if (state.accountGroupFilter === 'all') {
      if (eyebrow) eyebrow.textContent = 'Рабочее пространство';
      if (title) title.textContent = 'Рекламные кабинеты';
      if (viewTitle) viewTitle.textContent = 'Все кабинеты';
      if (subtitle) subtitle.textContent = 'Статус Meta, автоматика и назначенные правила — отдельно и без скрытых состояний.';
      if (btnSettings) btnSettings.classList.add('hidden');
      if (btnShare) btnShare.classList.add('hidden');
    } else {
      const group = (state.accountGroups || []).find(g => 
        String(g.id) === String(state.accountGroupFilter) || 
        String(g.name || '').toLowerCase() === String(state.accountGroupFilter).toLowerCase()
      );
      if (group) {
        if (eyebrow) eyebrow.textContent = `Группа кабинетов (${group.accounts_count || 0} шт.)`;
        if (title) title.textContent = group.name;
        if (viewTitle) viewTitle.textContent = group.name;
        if (subtitle) subtitle.textContent = group.description || 'Кабинеты, входящие в эту группу.';
        if (btnSettings) {
          btnSettings.classList.remove('hidden');
          btnSettings.onclick = () => window.openAccountGroupEditor(group.id);
        }
        if (btnShare) {
          btnShare.classList.remove('hidden');
        }
      }
    }
  }

  window.openEditCurrentGroup = function () {
    if (state.accountGroupFilter !== 'all') {
      window.openAccountGroupEditor(parseInt(state.accountGroupFilter, 10));
    }
  };

  function getAccountConnectionState(account) {
    return account?.connection_type === 'facebook_login'
      ? {
          key: 'oauth',
          label: 'Facebook Login',
          detail: 'Подключён через авторизацию Facebook'
        }
      : {
          key: 'system',
          label: 'System User',
          detail: 'Подключён вручную токеном System User'
        };
  }

  function getAccountActivityState(account) {
    const metrics = account?.latest_metrics;
    if (!metrics) {
      return { key: 'missing', label: 'Нет снимка', detail: 'Откройте Сводку и обновите данные' };
    }
    if (metrics.data_status === 'error') {
      return { key: 'error', label: 'Ошибка данных', detail: metrics.data_status_label || 'Meta не вернула метрики' };
    }
    if (metrics.data_status === 'blocked') {
      return { key: 'blocked', label: 'Недоступен', detail: metrics.data_status_label || 'Метрики кабинета недоступны' };
    }
    if (typeof metrics.spend !== 'number' || !Number.isFinite(metrics.spend)) {
      return { key: 'missing', label: 'Нет Spend', detail: 'В сохранённом снимке нет денежного показателя' };
    }
    return metrics.spend > 0
      ? { key: 'spending', label: 'Есть расход', detail: 'В последнем снимке за сегодня Spend больше нуля' }
      : { key: 'idle', label: 'Без расхода', detail: 'В последнем снимке за сегодня Spend равен нулю' };
  }

  function renderAccountLatestMetrics(account, context = 'card') {
    const metrics = account?.latest_metrics;
    const activity = getAccountActivityState(account);
    const metricValue = (key) => metrics ? formatNumber(metrics[key]) : '—';
    const spend = metrics && typeof metrics.spend === 'number'
      ? formatMoneyOrDash(metrics.spend, account.currency)
      : '—';
    const updatedAt = metrics?.generated_at || metrics?.saved_at;
    const updatedLabel = updatedAt ? formatSummaryTime(updatedAt) : 'обновлений ещё не было';

    return `
      <div class="account-metrics-grid ${context === 'details' ? 'details' : ''}">
        <div class="account-metric account-metric-spend"><span>Spend сегодня</span><b>${escapeHtml(spend)}</b></div>
        <div class="account-metric"><span>Лиды</span><b>${metricValue('leads')}</b></div>
        <div class="account-metric"><span>Реги</span><b>${metricValue('registrations')}</b></div>
        <div class="account-metric"><span>Покупки</span><b>${metricValue('purchases')}</b></div>
      </div>
      <div class="account-activity-line ${activity.key}" title="${escapeHtml(activity.detail)}">
        <span><span class="status-dot"></span><b>${escapeHtml(activity.label)}</b></span>
        <span>Снимок за сегодня · ${escapeHtml(updatedLabel)}</span>
      </div>`;
  }

  function pluralize(n, one, few, many) {
    const abs = Math.abs(n) % 100;
    const rem = abs % 10;
    if (abs > 10 && abs < 20) return many;
    if (rem > 1 && rem < 5) return few;
    if (rem === 1) return one;
    return many;
  }
  // ATTIO ACCOUNTS DATA GRID & COLUMN DEFINITIONS
  // ==========================================================
  const DEFAULT_ACCOUNTS_COLUMN_ORDER = [
    'name',
    'status',
    'timezone',
    'spend',
    'cpm',
    'cpc',
    'ctr',
    'leads',
    'cpl',
    'registrations',
    'cpreg',
    'purchases',
    'cpp',
    'automation'
  ];

  const ACCOUNTS_COLUMNS_DEF = {
    name: {
      id: 'name',
      label: 'Кабинет / Имя',
      category: 'account',
      type: 'entity',
      iconSvg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 21V9"/></svg>`,
      minWidth: 40,
      defaultWidth: 260,
      sticky: true,
      sortable: true
    },
    status: {
      id: 'status',
      label: 'Статус Meta',
      category: 'account',
      type: 'status',
      iconSvg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4l3 3"/></svg>`,
      minWidth: 28,
      defaultWidth: 130,
      sortable: true
    },
    timezone: {
      id: 'timezone',
      label: 'Таймзона',
      category: 'account',
      type: 'text',
      iconSvg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`,
      minWidth: 28,
      defaultWidth: 160,
      sortable: true
    },
    spend: {
      id: 'spend',
      label: 'Spend',
      category: 'traffic',
      type: 'currency',
      iconSvg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>`,
      minWidth: 28,
      defaultWidth: 120,
      sortable: true
    },
    cpm: {
      id: 'cpm',
      label: 'CPM',
      category: 'traffic',
      type: 'currency',
      iconSvg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>`,
      minWidth: 28,
      defaultWidth: 100,
      sortable: true
    },
    cpc: {
      id: 'cpc',
      label: 'CPC',
      category: 'traffic',
      type: 'currency',
      iconSvg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M15 15l6 6m-11-4a7 7 0 110-14 7 7 0 010 14z"/></svg>`,
      minWidth: 28,
      defaultWidth: 100,
      sortable: true
    },
    ctr: {
      id: 'ctr',
      label: 'CTR',
      category: 'traffic',
      type: 'percent',
      iconSvg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="19" y1="5" x2="5" y2="19"/><circle cx="6.5" cy="6.5" r="2.5"/><circle cx="17.5" cy="17.5" r="2.5"/></svg>`,
      minWidth: 28,
      defaultWidth: 95,
      sortable: true
    },
    leads: {
      id: 'leads',
      label: 'Лиды',
      category: 'conversions',
      type: 'number',
      iconSvg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="4" y1="9" x2="20" y2="9"/><line x1="4" y1="15" x2="20" y2="15"/><line x1="10" y1="3" x2="8" y2="21"/><line x1="16" y1="3" x2="14" y2="21"/></svg>`,
      minWidth: 28,
      defaultWidth: 95,
      sortable: true
    },
    cpl: {
      id: 'cpl',
      label: 'CPL',
      category: 'conversions',
      type: 'currency',
      iconSvg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>`,
      minWidth: 28,
      defaultWidth: 100,
      sortable: true
    },
    registrations: {
      id: 'registrations',
      label: 'Реги (Regs)',
      category: 'conversions',
      type: 'number',
      iconSvg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>`,
      minWidth: 28,
      defaultWidth: 105,
      sortable: true
    },
    cpreg: {
      id: 'cpreg',
      label: 'CPReg',
      category: 'conversions',
      type: 'currency',
      iconSvg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>`,
      minWidth: 28,
      defaultWidth: 105,
      sortable: true
    },
    purchases: {
      id: 'purchases',
      label: 'Покупки (Purch)',
      category: 'conversions',
      type: 'number',
      iconSvg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"/></svg>`,
      minWidth: 28,
      defaultWidth: 115,
      sortable: true
    },
    cpp: {
      id: 'cpp',
      label: 'CPP',
      category: 'conversions',
      type: 'currency',
      iconSvg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>`,
      minWidth: 28,
      defaultWidth: 105,
      sortable: true
    },
    automation: {
      id: 'automation',
      label: 'Автоматика',
      category: 'account',
      type: 'toggle',
      iconSvg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>`,
      minWidth: 28,
      defaultWidth: 135,
      sortable: true
    },
    // Optional catalog columns:
    currency: {
      id: 'currency',
      label: 'Валюта',
      category: 'account',
      type: 'text',
      iconSvg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M16 8h-6a2 2 0 1 0 0 4h4a2 2 0 1 1 0 4H8"/><path d="M12 18V6"/></svg>`,
      minWidth: 28,
      defaultWidth: 95,
      sortable: true
    },
    business_name: {
      id: 'business_name',
      label: 'Business Manager',
      category: 'account',
      type: 'text',
      iconSvg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/></svg>`,
      minWidth: 28,
      defaultWidth: 160,
      sortable: true
    },
    note: {
      id: 'note',
      label: 'Заметка',
      category: 'account',
      type: 'text',
      iconSvg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="17" y1="10" x2="3" y2="10"/><line x1="21" y1="6" x2="3" y2="6"/><line x1="21" y1="14" x2="3" y2="14"/><line x1="17" y1="18" x2="3" y2="18"/></svg>`,
      minWidth: 28,
      defaultWidth: 140,
      sortable: true
    },
    spend_cap: {
      id: 'spend_cap',
      label: 'Лимит затрат',
      category: 'account',
      type: 'currency',
      iconSvg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>`,
      minWidth: 28,
      defaultWidth: 130,
      sortable: true
    },
    amount_spent: {
      id: 'amount_spent',
      label: 'Lifetime Spend',
      category: 'account',
      type: 'currency',
      iconSvg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.21 15.89A10 10 0 1 1 8 2.83"/><path d="M22 12A10 10 0 0 0 12 2v10z"/></svg>`,
      minWidth: 28,
      defaultWidth: 130,
      sortable: true
    },
    impressions: {
      id: 'impressions',
      label: 'Показы',
      category: 'traffic',
      type: 'number',
      iconSvg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>`,
      minWidth: 28,
      defaultWidth: 110,
      sortable: true
    },
    reach: {
      id: 'reach',
      label: 'Охват',
      category: 'traffic',
      type: 'number',
      iconSvg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><path d="M2 12h20"/></svg>`,
      minWidth: 28,
      defaultWidth: 110,
      sortable: true
    },
    frequency: {
      id: 'frequency',
      label: 'Частота',
      category: 'traffic',
      type: 'number',
      iconSvg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>`,
      minWidth: 28,
      defaultWidth: 95,
      sortable: true
    },
    clicks: {
      id: 'clicks',
      label: 'Все клики',
      category: 'traffic',
      type: 'number',
      iconSvg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M15 15l6 6m-11-4a7 7 0 110-14 7 7 0 010 14z"/></svg>`,
      minWidth: 28,
      defaultWidth: 105,
      sortable: true
    },
    link_clicks: {
      id: 'link_clicks',
      label: 'Клики по ссылке',
      category: 'traffic',
      type: 'number',
      iconSvg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>`,
      minWidth: 28,
      defaultWidth: 130,
      sortable: true
    },
    link_ctr: {
      id: 'link_ctr',
      label: 'Link CTR',
      category: 'traffic',
      type: 'percent',
      iconSvg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="19" y1="5" x2="5" y2="19"/><circle cx="6.5" cy="6.5" r="2.5"/><circle cx="17.5" cy="17.5" r="2.5"/></svg>`,
      minWidth: 28,
      defaultWidth: 100,
      sortable: true
    },
    roas: {
      id: 'roas',
      label: 'ROAS',
      category: 'conversions',
      type: 'number',
      iconSvg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>`,
      minWidth: 28,
      defaultWidth: 95,
      sortable: true
    },
    landing_page_views: {
      id: 'landing_page_views',
      label: 'LPV (Просмотры)',
      category: 'conversions',
      type: 'number',
      iconSvg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3h18v18H3z"/><path d="M3 9h18"/></svg>`,
      minWidth: 28,
      defaultWidth: 125,
      sortable: true
    },
    cost_per_lpv: {
      id: 'cost_per_lpv',
      label: 'Cost per LPV',
      category: 'conversions',
      type: 'currency',
      iconSvg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>`,
      minWidth: 28,
      defaultWidth: 115,
      sortable: true
    }
  };

  // ==========================================================
  // DRAG & DROP COLUMN REORDERING & RESIZING
  // ==========================================================
  let draggedColumnId = null;

  window.handleHeaderPointerDown = function (e, colId) {
    if (e.button !== 0) return; // Only left-click
    // Do not intercept if clicking resizer or checkbox
    if (e.target.closest('.attio-resizer') || e.target.closest('.attio-checkbox') || e.target.closest('button')) {
      return;
    }

    const startX = e.clientX;
    const startY = e.clientY;
    const th = document.getElementById(`th-col-${colId}`) || e.currentTarget.closest('th');
    let isDragging = false;
    let dragGhost = null;

    const onMouseMove = (moveEvent) => {
      const distX = moveEvent.clientX - startX;
      const distY = moveEvent.clientY - startY;

      if (!isDragging) {
        if (Math.hypot(distX, distY) > 4) {
          if (colId === 'name') return; // Name column is fixed
          isDragging = true;
          document.body.classList.add('is-dragging-column');
          if (th) th.classList.add('is-dragging');

          dragGhost = document.createElement('div');
          dragGhost.className = 'attio-header-drag-ghost';
          const colDef = ACCOUNTS_COLUMNS_DEF[colId] || {};
          dragGhost.innerHTML = `
            <span class="attio-th-type-icon">${colDef.iconSvg || ''}</span>
            <span>${escapeHtml(colDef.label || colId)}</span>
          `;
          document.body.appendChild(dragGhost);
        }
      }

      if (isDragging && dragGhost) {
        dragGhost.style.left = `${moveEvent.clientX + 14}px`;
        dragGhost.style.top = `${moveEvent.clientY + 14}px`;

        const targetTh = document.elementFromPoint(moveEvent.clientX, moveEvent.clientY)?.closest('.attio-th');
        document.querySelectorAll('.attio-th').forEach(el => el.classList.remove('drag-over-left', 'drag-over-right'));

        if (targetTh && targetTh.dataset.colId && targetTh.dataset.colId !== colId && targetTh.dataset.colId !== 'name' && !targetTh.classList.contains('attio-th-add-col') && !targetTh.classList.contains('attio-th-spacer')) {
          const rect = targetTh.getBoundingClientRect();
          const midX = rect.left + rect.width / 2;
          if (moveEvent.clientX < midX) {
            targetTh.classList.add('drag-over-left');
          } else {
            targetTh.classList.add('drag-over-right');
          }
        }
      }
    };

    const onMouseUp = (upEvent) => {
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);

      if (dragGhost) {
        dragGhost.remove();
        dragGhost = null;
      }
      document.body.classList.remove('is-dragging-column');
      if (th) th.classList.remove('is-dragging');

      if (isDragging) {
        const targetTh = document.elementFromPoint(upEvent.clientX, upEvent.clientY)?.closest('.attio-th');
        const targetColId = targetTh ? targetTh.dataset.colId : null;
        const isRight = targetTh && targetTh.classList.contains('drag-over-right');
        document.querySelectorAll('.attio-th').forEach(el => el.classList.remove('drag-over-left', 'drag-over-right'));

        if (targetColId && targetColId !== colId && targetColId !== 'name' && targetColId !== 'add_column') {
          const currentOrder = [...(state.accountsColumnOrder || DEFAULT_ACCOUNTS_COLUMN_ORDER)];
          const fromIdx = currentOrder.indexOf(colId);
          if (fromIdx !== -1) {
            currentOrder.splice(fromIdx, 1);
            let toIdx = currentOrder.indexOf(targetColId);
            if (isRight) toIdx += 1;
            currentOrder.splice(toIdx, 0, colId);
            state.accountsColumnOrder = currentOrder;
            localStorage.setItem('buyerly_accounts_col_order_v2', JSON.stringify(currentOrder));
            renderAccounts();
          }
        }
      } else {
        // Clean Click -> Sort Column!
        const colDef = ACCOUNTS_COLUMNS_DEF[colId] || {};
        if (colDef.sortable) {
          window.setAccountsSort(colId);
        }
      }
    };

    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
  };


  let lastResizerClick = { time: 0, colId: null };

  window.resetAccountsColumnOrder = function () {
    state.accountsColumnOrder = [...DEFAULT_ACCOUNTS_COLUMN_ORDER];
    state.accountsColumnWidths = {};
    localStorage.removeItem('buyerly_accounts_col_order_v2');
    localStorage.removeItem('buyerly_accounts_col_widths');
    showToast('Порядок и ширина колонок сброшены', 'info');
    renderAccounts();
  };

  window.resetSingleColumnWidth = function (e, colId) {
    if (e) {
      e.preventDefault();
      e.stopPropagation();
    }
    if (state.accountsColumnWidths) {
      delete state.accountsColumnWidths[colId];
      localStorage.setItem('buyerly_accounts_col_widths', JSON.stringify(state.accountsColumnWidths));
      const colDef = ACCOUNTS_COLUMNS_DEF[colId] || {};
      showToast(`Ширина колонки «${colDef.label || colId}» сброшена`, 'info');
      renderAccounts();
    }
  };

  window.initColumnResize = function (e, colId) {
    if (e.button !== 0) return; // Only left-click
    e.preventDefault();
    e.stopPropagation();

    // Programmatic double-click detector (guaranteed to trigger)
    const now = Date.now();
    if (lastResizerClick.colId === colId && (now - lastResizerClick.time) < 350) {
      lastResizerClick = { time: 0, colId: null };
      window.resetSingleColumnWidth(e, colId);
      return;
    }
    lastResizerClick = { time: now, colId: colId };

    const startX = e.clientX;
    const th = document.getElementById(`th-col-${colId}`) || e.currentTarget.closest('th');
    const colDef = ACCOUNTS_COLUMNS_DEF[colId] || {};
    const startWidth = th ? th.offsetWidth : (state.accountsColumnWidths[colId] || colDef.defaultWidth || 120);
    const minWidth = 20; // Zero-floor minimum: 20px
    const colTrack = document.getElementById(`col-track-${colId}`);
    const resizer = e.currentTarget;
    const viewport = document.querySelector('.attio-table-viewport');
    
    // Create or reuse full-height guide line
    let guide = document.getElementById('attioResizeGuide');
    if (!guide && viewport) {
      guide = document.createElement('div');
      guide.id = 'attioResizeGuide';
      guide.className = 'attio-table-resize-guide';
      viewport.appendChild(guide);
    }

    const viewportRect = viewport ? viewport.getBoundingClientRect() : { left: 0, top: 0 };
    const thRect = th ? th.getBoundingClientRect() : { right: startX };
    
    if (guide && viewport) {
      const initialGuideLeft = thRect.right - viewportRect.left + viewport.scrollLeft;
      guide.style.left = `${initialGuideLeft}px`;
      guide.classList.add('is-active');
    }

    resizer.classList.add('is-resizing');
    document.body.classList.add('is-resizing-column');

    let currentWidth = startWidth;
    let rafId = null;

    const onMouseMove = (moveEvent) => {
      moveEvent.preventDefault();
      const diff = moveEvent.clientX - startX;
      currentWidth = Math.max(minWidth, startWidth + diff);

      if (rafId) cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(() => {
        if (colTrack) {
          colTrack.style.width = `${currentWidth}px`;
          colTrack.style.maxWidth = `${currentWidth}px`;
          colTrack.style.minWidth = `${currentWidth}px`;
        }
        if (th) {
          th.style.width = `${currentWidth}px`;
          th.style.maxWidth = `${currentWidth}px`;
          th.style.minWidth = `${currentWidth}px`;
        }
        if (guide && th && viewport) {
          const updatedThRect = th.getBoundingClientRect();
          const guideLeft = updatedThRect.right - viewportRect.left + viewport.scrollLeft;
          guide.style.left = `${guideLeft}px`;
        }
      });
    };

    const onMouseUp = (upEvent) => {
      upEvent.preventDefault();
      if (rafId) cancelAnimationFrame(rafId);

      if (guide) {
        guide.classList.remove('is-active');
      }

      resizer.classList.remove('is-resizing');
      document.body.classList.remove('is-resizing-column');
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);

      state.accountsColumnWidths[colId] = currentWidth;
      localStorage.setItem('buyerly_accounts_col_widths', JSON.stringify(state.accountsColumnWidths));
      renderAccounts();
    };

    document.addEventListener('mousemove', onMouseMove, { passive: false });
    document.addEventListener('mouseup', onMouseUp, { passive: false });
  };

  // ==========================================================
  // ATTIO COLUMN PICKER POPOVER & VISIBILITY MANAGEMENT
  // ==========================================================
  window.toggleAddColumnPopover = function (event) {
    if (event) {
      event.preventDefault();
      event.stopPropagation();
    }
    const popover = document.getElementById('accountsAddColumnPopover');
    if (!popover) return;
    const isHidden = popover.classList.contains('hidden');
    document.querySelectorAll('.attio-dropdown-menu, .attio-column-picker-popover').forEach(m => m.classList.add('hidden'));

    if (isHidden) {
      renderColumnPickerList('');
      popover.classList.remove('hidden');

      const btn = event ? (event.currentTarget || event.target.closest('.attio-add-col-btn')) : document.querySelector('.attio-add-col-btn');
      if (btn) {
        const rect = btn.getBoundingClientRect();
        popover.style.top = `${rect.bottom + 6}px`;
        popover.style.left = `${Math.max(10, rect.right - 270)}px`;
      }

      const input = document.getElementById('columnPickerSearchInput');
      if (input) {
        input.value = '';
        setTimeout(() => input.focus(), 50);
      }

      const closeHandler = (e) => {
        if (!popover.contains(e.target) && !e.target.closest('.attio-add-col-btn')) {
          popover.classList.add('hidden');
          document.removeEventListener('click', closeHandler);
        }
      };
      setTimeout(() => document.addEventListener('click', closeHandler), 10);
    }
  };

  window.handleColumnPickerSearch = function (query) {
    renderColumnPickerList(query);
  };

  function renderColumnPickerList(filterQuery = '') {
    const listEl = document.getElementById('columnPickerList');
    if (!listEl) return;
    const query = String(filterQuery || '').trim().toLowerCase();
    const currentOrder = new Set(state.accountsColumnOrder || DEFAULT_ACCOUNTS_COLUMN_ORDER);

    const categories = [
      { id: 'account', label: 'Свойства кабинета' },
      { id: 'traffic', label: 'Трафик и вовлеченность' },
      { id: 'conversions', label: 'Конверсии и E-commerce' }
    ];

    let html = '';
    categories.forEach(cat => {
      const items = Object.values(ACCOUNTS_COLUMNS_DEF).filter(col => {
        if (col.id === 'name') return false; // Name is sticky and required
        if (col.category !== cat.id) return false;
        if (!query) return true;
        return col.label.toLowerCase().includes(query) || col.id.toLowerCase().includes(query);
      });

      if (items.length > 0) {
        html += `<div class="attio-picker-section-title">${escapeHtml(cat.label)}</div>`;
        items.forEach(col => {
          const isActive = currentOrder.has(col.id);
          html += `
            <div class="attio-picker-item ${isActive ? 'is-active' : ''}" onclick="window.toggleColumnVisibility('${col.id}')">
              <div class="attio-picker-item-left">
                <span class="attio-picker-item-icon">${col.iconSvg || ''}</span>
                <span class="attio-picker-item-label">${escapeHtml(col.label)}</span>
              </div>
              <div class="attio-picker-item-right">
                ${isActive ? `
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" class="attio-picker-check-icon">
                    <polyline points="20 6 9 17 4 12"/>
                  </svg>
                ` : ''}
              </div>
            </div>
          `;
        });
      }
    });

    if (!html) {
      html = '<div class="attio-picker-empty">Колонка не найдена</div>';
    }

    listEl.innerHTML = html;
  }

  window.toggleColumnVisibility = function (colId) {
    if (colId === 'name') return;
    let currentOrder = [...(state.accountsColumnOrder || DEFAULT_ACCOUNTS_COLUMN_ORDER)];
    const idx = currentOrder.indexOf(colId);

    if (idx >= 0) {
      currentOrder.splice(idx, 1);
      showToast(`Колонка «${ACCOUNTS_COLUMNS_DEF[colId]?.label || colId}» скрыта`, 'info');
    } else {
      const autoIdx = currentOrder.indexOf('automation');
      if (autoIdx >= 0 && colId !== 'automation') {
        currentOrder.splice(autoIdx, 0, colId);
      } else {
        currentOrder.push(colId);
      }
      showToast(`Колонка «${ACCOUNTS_COLUMNS_DEF[colId]?.label || colId}» добавлена`, 'success');
    }

    state.accountsColumnOrder = currentOrder;
    localStorage.setItem('buyerly_accounts_col_order_v2', JSON.stringify(currentOrder));
    renderColumnPickerList(document.getElementById('columnPickerSearchInput')?.value || '');
    renderAccounts();
  };

  // ==========================================================
  // VIEWS, SORT & FILTER POPOVERS
  // ==========================================================
  window.toggleAccountsViewDropdown = function (event) {
    if (event) event.stopPropagation();
    const dd = document.getElementById('accountsViewDropdown');
    if (!dd) return;
    const isHidden = dd.classList.contains('hidden');
    document.querySelectorAll('.attio-dropdown-menu').forEach(m => m.classList.add('hidden'));
    if (isHidden) {
      renderAccountsViewGroupItems();
      dd.classList.remove('hidden');
      const closeHandler = (e) => {
        if (!dd.contains(e.target)) {
          dd.classList.add('hidden');
          document.removeEventListener('click', closeHandler);
        }
      };
      setTimeout(() => document.addEventListener('click', closeHandler), 10);
    }
  };

  function renderAccountsViewGroupItems() {
    const container = document.getElementById('accountsViewGroupItems');
    if (!container) return;
    const groups = state.accountGroups || [];
    if (!groups.length) {
      container.innerHTML = '';
      return;
    }
    container.innerHTML = groups.map(g => {
      const isActive = state.accountGroupFilter === String(g.id);
      return `
        <div class="attio-dropdown-item ${isActive ? 'active' : ''}" onclick="window.selectAccountGroupView('${g.id}')">
          <div class="attio-dropdown-item-left">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"/></svg>
            <span>${escapeHtml(g.name)}</span>
          </div>
          <span class="attio-dropdown-count">${g.accounts_count || 0}</span>
        </div>
      `;
    }).join('');
  }

  window.selectAccountGroupView = function (groupId) {
    state.accountGroupFilter = String(groupId);
    const dd = document.getElementById('accountsViewDropdown');
    if (dd) dd.classList.add('hidden');
    
    const titleEl = document.getElementById('accountsViewCurrentTitle');
    const shareBtn = document.getElementById('btnGroupShare');
    const settingsBtn = document.getElementById('btnGroupSettings');

    if (state.accountGroupFilter === 'all') {
      if (titleEl) titleEl.textContent = 'Все кабинеты';
      if (shareBtn) shareBtn.classList.add('hidden');
      if (settingsBtn) settingsBtn.classList.add('hidden');
    } else {
      const group = (state.accountGroups || []).find(g => String(g.id) === state.accountGroupFilter);
      if (titleEl) titleEl.textContent = group ? group.name : 'Группа';
      if (shareBtn) shareBtn.classList.remove('hidden');
      if (settingsBtn) settingsBtn.classList.remove('hidden');
    }
    renderAccounts();
  };

  window.toggleAccountsSortDropdown = function (event) {
    if (event) event.stopPropagation();
    const dd = document.getElementById('accountsSortDropdown');
    if (!dd) return;
    const isHidden = dd.classList.contains('hidden');
    document.querySelectorAll('.attio-dropdown-menu').forEach(m => m.classList.add('hidden'));
    if (isHidden) {
      dd.classList.remove('hidden');
      const closeHandler = (e) => {
        if (!dd.contains(e.target)) {
          dd.classList.add('hidden');
          document.removeEventListener('click', closeHandler);
        }
      };
      setTimeout(() => document.addEventListener('click', closeHandler), 10);
    }
  };

  window.setAccountsSort = function (colKey) {
    if (state.accountsSortColumn === colKey) {
      state.accountsSortDirection = state.accountsSortDirection === 'asc' ? 'desc' : 'asc';
    } else {
      state.accountsSortColumn = colKey;
      state.accountsSortDirection = (colKey === 'spend' || colKey === 'leads' || colKey === 'cpl') ? 'desc' : 'asc';
    }
    localStorage.setItem('buyerly_accounts_sort_col', state.accountsSortColumn);
    localStorage.setItem('buyerly_accounts_sort_dir', state.accountsSortDirection);
    
    const sortBtn = document.getElementById('btnAccountsSort');
    const sortLabel = document.getElementById('accountsSortLabel');
    const colDef = ACCOUNTS_COLUMNS_DEF[colKey];
    if (sortLabel && colDef) {
      sortLabel.textContent = `${colDef.label} (${state.accountsSortDirection === 'asc' ? '↑' : '↓'})`;
    }
    if (sortBtn) sortBtn.classList.add('active');

    const dd = document.getElementById('accountsSortDropdown');
    if (dd) {
      dd.classList.add('hidden');
      dd.querySelectorAll('.attio-dropdown-item').forEach(item => {
        item.classList.toggle('active', item.getAttribute('data-sort-key') === colKey);
      });
    }
    renderAccounts();
  };

  window.toggleAccountsFilterDropdown = function (event) {
    if (event) event.stopPropagation();
    const dd = document.getElementById('accountsFilterDropdown');
    if (!dd) return;
    const isHidden = dd.classList.contains('hidden');
    document.querySelectorAll('.attio-dropdown-menu').forEach(m => m.classList.add('hidden'));
    if (isHidden) {
      dd.classList.remove('hidden');
      const closeHandler = (e) => {
        if (!dd.contains(e.target)) {
          dd.classList.add('hidden');
          document.removeEventListener('click', closeHandler);
        }
      };
      setTimeout(() => document.addEventListener('click', closeHandler), 10);
    }
  };

  window.setAccountsFilter = function (filterVal) {
    state.filter = filterVal;
    const filterBtn = document.getElementById('btnAccountsFilter');
    const filterLabel = document.getElementById('accountsFilterLabel');
    const labels = {
      all: 'Filter',
      active: 'Активные',
      rules: 'С автоматикой',
      issue: 'Ошибки'
    };
    if (filterLabel) filterLabel.textContent = labels[filterVal] || 'Filter';
    if (filterBtn) filterBtn.classList.toggle('active', filterVal !== 'all');

    const dd = document.getElementById('accountsFilterDropdown');
    if (dd) {
      dd.classList.add('hidden');
      dd.querySelectorAll('.attio-dropdown-item').forEach(item => {
        item.classList.toggle('active', item.getAttribute('data-filter-val') === filterVal);
      });
    }
    renderAccounts();
  };

  window.handleAccountsSearch = function (query) {
    state.searchQuery = query || '';
    const clearBtn = document.getElementById('accountsSearchClearBtn');
    if (clearBtn) clearBtn.classList.toggle('hidden', !state.searchQuery);
    renderAccounts();
  };

  window.clearAccountsSearch = function () {
    state.searchQuery = '';
    const input = document.getElementById('accountsSearchInput');
    if (input) input.value = '';
    const clearBtn = document.getElementById('accountsSearchClearBtn');
    if (clearBtn) clearBtn.classList.add('hidden');
    renderAccounts();
  };

  function sortAccountsList(list) {
    const col = state.accountsSortColumn;
    const dir = state.accountsSortDirection === 'desc' ? -1 : 1;
    if (!col) return list;

    return [...list].sort((a, b) => {
      const mA = a.latest_metrics || a.insights || {};
      const mB = b.latest_metrics || b.insights || {};
      let valA, valB;
      switch (col) {
        case 'name':
          valA = accountDisplayName(a).toLowerCase();
          valB = accountDisplayName(b).toLowerCase();
          return valA.localeCompare(valB) * dir;
        case 'timezone':
          valA = (a.timezone_name || '').toLowerCase();
          valB = (b.timezone_name || '').toLowerCase();
          return valA.localeCompare(valB) * dir;
        case 'currency':
          valA = (a.currency || '').toLowerCase();
          valB = (b.currency || '').toLowerCase();
          return valA.localeCompare(valB) * dir;
        case 'business_name':
          valA = (a.business_name || '').toLowerCase();
          valB = (b.business_name || '').toLowerCase();
          return valA.localeCompare(valB) * dir;
        case 'note':
          valA = (a.note || '').toLowerCase();
          valB = (b.note || '').toLowerCase();
          return valA.localeCompare(valB) * dir;
        case 'spend':
          valA = a.today_spend || mA.spend || 0;
          valB = b.today_spend || mB.spend || 0;
          return (valA - valB) * dir;
        case 'cpm':
          valA = mA.cpm || 0;
          valB = mB.cpm || 0;
          return (valA - valB) * dir;
        case 'cpc':
          valA = mA.cpc || 0;
          valB = mB.cpc || 0;
          return (valA - valB) * dir;
        case 'ctr':
          valA = mA.ctr || 0;
          valB = mB.ctr || 0;
          return (valA - valB) * dir;
        case 'leads':
          valA = a.today_leads !== undefined ? a.today_leads : (mA.leads || 0);
          valB = b.today_leads !== undefined ? b.today_leads : (mB.leads || 0);
          return (valA - valB) * dir;
        case 'cpl':
          valA = a.today_cpl !== undefined ? a.today_cpl : (mA.cpl || 0);
          valB = b.today_cpl !== undefined ? b.today_cpl : (mB.cpl || 0);
          return (valA - valB) * dir;
        case 'registrations':
          valA = mA.registrations || 0;
          valB = mB.registrations || 0;
          return (valA - valB) * dir;
        case 'cpreg':
          valA = mA.cpreg || 0;
          valB = mB.cpreg || 0;
          return (valA - valB) * dir;
        case 'purchases':
          valA = mA.purchases || 0;
          valB = mB.purchases || 0;
          return (valA - valB) * dir;
        case 'cpp':
          valA = mA.cpp || 0;
          valB = mB.cpp || 0;
          return (valA - valB) * dir;
        case 'impressions':
          valA = mA.impressions || 0;
          valB = mB.impressions || 0;
          return (valA - valB) * dir;
        case 'reach':
          valA = mA.reach || 0;
          valB = mB.reach || 0;
          return (valA - valB) * dir;
        case 'frequency':
          valA = mA.frequency || 0;
          valB = mB.frequency || 0;
          return (valA - valB) * dir;
        case 'clicks':
          valA = mA.clicks || 0;
          valB = mB.clicks || 0;
          return (valA - valB) * dir;
        case 'link_clicks':
          valA = mA.link_clicks || 0;
          valB = mB.link_clicks || 0;
          return (valA - valB) * dir;
        case 'link_ctr':
          valA = mA.link_ctr || mA.ctr_link || 0;
          valB = mB.link_ctr || mB.ctr_link || 0;
          return (valA - valB) * dir;
        case 'roas':
          valA = mA.roas || 0;
          valB = mB.roas || 0;
          return (valA - valB) * dir;
        case 'landing_page_views':
          valA = mA.landing_page_views || 0;
          valB = mB.landing_page_views || 0;
          return (valA - valB) * dir;
        case 'cost_per_lpv':
          valA = mA.cost_per_lpv || 0;
          valB = mB.cost_per_lpv || 0;
          return (valA - valB) * dir;
        case 'spend_cap':
          valA = a.spend_cap || 0;
          valB = b.spend_cap || 0;
          return (valA - valB) * dir;
        case 'amount_spent':
          valA = a.amount_spent || 0;
          valB = b.amount_spent || 0;
          return (valA - valB) * dir;
        case 'status':
          valA = a.account_status || 0;
          valB = b.account_status || 0;
          return (valA - valB) * dir;
        case 'automation':
          valA = a.rules_enabled ? 1 : 0;
          valB = b.rules_enabled ? 1 : 0;
          return (valA - valB) * dir;
        default:
          return 0;
      }
    });
  }

  // ==========================================================
  // BULK ACTIONS & ATTIO FLOATING BAR
  // ==========================================================
  window.toggleAccountSelection = function (accountId, isChecked) {
    if (isChecked) {
      state.selectedAccounts.add(accountId);
    } else {
      state.selectedAccounts.delete(accountId);
    }
    const rowEl = document.getElementById(`row-${accountId}`);
    if (rowEl) rowEl.classList.toggle('is-selected', isChecked);
    updateBulkActionBar();
  };

  window.toggleSelectAllAccounts = function (isChecked) {
    const query = state.searchQuery.toLowerCase().trim();
    const filtered = state.accounts.filter(acc => {
      const searchText = [acc.name, acc.custom_name, acc.note, acc.account_id].filter(Boolean).join(' ').toLowerCase();
      if (query && !searchText.includes(query)) return false;
      if (state.accountGroupFilter !== 'all' && !(acc.group_ids || []).map(String).includes(state.accountGroupFilter)) return false;
      if (state.filter === 'active') return acc.account_status === 1 && acc.is_active;
      if (state.filter === 'rules') return acc.rules_enabled;
      if (state.filter === 'issue') return acc.account_status !== 1 || !acc.is_active;
      return true;
    });

    if (isChecked) {
      filtered.forEach(acc => state.selectedAccounts.add(acc.account_id));
    } else {
      state.selectedAccounts.clear();
    }
    
    document.querySelectorAll('.attio-row-checkbox').forEach(cb => {
      cb.checked = isChecked;
    });
    document.querySelectorAll('.attio-table tr.attio-row').forEach(row => {
      row.classList.toggle('is-selected', isChecked);
    });
    updateBulkActionBar();
  };

  window.clearAccountSelection = function () {
    state.selectedAccounts.clear();
    document.querySelectorAll('.attio-row-checkbox, #selectAllAccountsCheckbox').forEach(cb => {
      cb.checked = false;
    });
    document.querySelectorAll('.attio-table tr.attio-row').forEach(row => {
      row.classList.remove('is-selected');
    });
    updateBulkActionBar();
  };

  function updateBulkActionBar() {
    const bar = document.getElementById('accountBulkActionBar');
    const countEl = document.getElementById('bulkSelectedCount');
    const selectAllCb = document.getElementById('selectAllAccountsCheckbox');
    const thName = document.getElementById('th-col-name');
    if (!state.selectedAccounts) state.selectedAccounts = new Set();
    if (!bar) return;

    const count = state.selectedAccounts.size;
    if (count > 0) {
      if (countEl) countEl.textContent = count;
      bar.classList.remove('hidden');
    } else {
      bar.classList.add('hidden');
      window.closeBulkGroupDropdown();
      const moreDd = document.getElementById('bulkMoreDropdown');
      if (moreDd) moreDd.classList.add('hidden');
    }

    if (thName) {
      thName.classList.toggle('has-selected', count > 0);
    }

    if (selectAllCb) {
      const visibleCheckboxes = document.querySelectorAll('.attio-row-checkbox');
      const filteredCount = visibleCheckboxes.length;
      if (count === 0 || filteredCount === 0) {
        selectAllCb.checked = false;
        selectAllCb.indeterminate = false;
      } else if (count >= filteredCount) {
        selectAllCb.checked = true;
        selectAllCb.indeterminate = false;
      } else {
        selectAllCb.checked = false;
        selectAllCb.indeterminate = true;
      }
    }
  }

  window.toggleBulkGroupDropdown = function (event) {
    if (event) event.stopPropagation();
    const dropdown = document.getElementById('bulkGroupDropdown');
    if (!dropdown) return;
    const isShowing = !dropdown.classList.contains('hidden');
    document.querySelectorAll('.attio-dropdown-menu').forEach(m => m.classList.add('hidden'));
    if (!isShowing) {
      renderBulkGroupDropdownList();
      dropdown.classList.remove('hidden');
      const closeHandler = (e) => {
        if (!dropdown.contains(e.target)) {
          dropdown.classList.add('hidden');
          document.removeEventListener('click', closeHandler);
        }
      };
      setTimeout(() => document.addEventListener('click', closeHandler), 10);
    }
  };

  window.closeBulkGroupDropdown = function () {
    const dropdown = document.getElementById('bulkGroupDropdown');
    if (dropdown) dropdown.classList.add('hidden');
  };

  function renderBulkGroupDropdownList() {
    const listEl = document.getElementById('bulkGroupDropdownList');
    if (!listEl) return;
    if (!state.accountGroups || state.accountGroups.length === 0) {
      listEl.innerHTML = '<div style="padding: 6px 10px; font-size: 12px; color: var(--text-muted);">Нет созданных групп</div>';
      return;
    }
    listEl.innerHTML = state.accountGroups.map(group => `
      <div class="attio-dropdown-item" onclick="window.assignSelectedAccountsToGroup(${group.id});">
        <div class="attio-dropdown-item-left">
          <svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"/></svg>
          <span>${escapeHtml(group.name)}</span>
        </div>
        <span class="attio-dropdown-count">${group.accounts_count || 0}</span>
      </div>
    `).join('');
  }

  window.assignSelectedAccountsToGroup = async function (groupId) {
    window.closeBulkGroupDropdown();
    const selectedIds = Array.from(state.selectedAccounts);
    if (!selectedIds.length) return;

    const group = state.accountGroups.find(g => g.id === groupId);
    if (!group) return;

    try {
      showLoading();
      const accountsInWorkspace = state.accounts || [];
      const matchingAccountDbIds = accountsInWorkspace
        .filter(acc => selectedIds.includes(acc.account_id) || selectedIds.includes(String(acc.id)))
        .map(acc => acc.id);

      const existingMemberIds = (group.account_ids || []);
      const combined = Array.from(new Set([...existingMemberIds, ...matchingAccountDbIds]));

      await apiRequest(`/api/account-groups/${groupId}`, {
        method: 'PUT',
        body: JSON.stringify({
          name: group.name,
          description: group.description || '',
          account_ids: combined
        })
      });

      showToast(`Кабинеты добавлены в группу "${group.name}"`, 'success');
      window.clearAccountSelection();
      await loadAccounts();
    } catch (e) {
      showToast(e.message || 'Ошибка добавления в группу', 'error');
    } finally {
      hideLoading();
    }
  };

  window.openCreateGroupWithSelected = function () {
    window.closeBulkGroupDropdown();
    const selectedAccountDbIds = (state.accounts || [])
      .filter(acc => state.selectedAccounts.has(acc.account_id))
      .map(acc => acc.id);
    window.openCreateAccountGroup();
    if (selectedAccountDbIds.length) {
      renderAccountGroupMemberOptions(selectedAccountDbIds);
    }
  };

  window.openBulkAssignRules = function () {
    const selected = Array.from(state.selectedAccounts);
    if (!selected.length) return;
    window.openAssignRuleModal(selected[0]);
  };

  window.toggleBulkMoreDropdown = function (event) {
    if (event) event.stopPropagation();
    const dd = document.getElementById('bulkMoreDropdown');
    if (!dd) return;
    const isHidden = dd.classList.contains('hidden');
    document.querySelectorAll('.attio-dropdown-menu').forEach(m => m.classList.add('hidden'));
    if (isHidden) {
      dd.classList.remove('hidden');
      const closeHandler = (e) => {
        if (!dd.contains(e.target)) {
          dd.classList.add('hidden');
          document.removeEventListener('click', closeHandler);
        }
      };
      setTimeout(() => document.addEventListener('click', closeHandler), 10);
    }
  };

  window.bulkCopyAccountIds = function () {
    const selected = Array.from(state.selectedAccounts);
    if (!selected.length) return;
    const text = selected.join(', ');
    navigator.clipboard.writeText(text).then(() => {
      showToast(`Скопировано ${selected.length} ID кабинетов`, 'success');
    }).catch(() => {
      showToast('Ошибка копирования', 'error');
    });
    const dd = document.getElementById('bulkMoreDropdown');
    if (dd) dd.classList.add('hidden');
  };

  window.bulkToggleAutomationPrompt = async function () {
    const selected = Array.from(state.selectedAccounts);
    if (!selected.length) return;
    
    const selectedAccs = state.accounts.filter(a => state.selectedAccounts.has(a.account_id));
    const anyEnabled = selectedAccs.some(a => a.rules_enabled);
    const targetState = !anyEnabled;
    const actionName = targetState ? 'включить' : 'приостановить';
    
    if (!confirm(`Вы действительно хотите ${actionName} автоматику для ${selected.length} выбранных кабинетов?`)) return;

    try {
      showLoading();
      for (const acc of selectedAccs) {
        await apiRequest(`/api/accounts/${acc.account_id}/toggle-rules`, {
          method: 'POST',
          body: JSON.stringify({ enabled: targetState })
        }).catch(() => {});
      }
      showToast(`Автоматика ${targetState ? 'включена' : 'приостановлена'} для ${selected.length} кабинетов`, 'success');
      await loadAccounts();
    } catch (e) {
      showToast(e.message || 'Ошибка изменения автоматики', 'error');
    } finally {
      hideLoading();
    }
  };

  window.bulkDeleteSelectedAccounts = async function () {
    const selected = Array.from(state.selectedAccounts);
    if (!selected.length) return;
    if (!confirm(`Удалить выбранные рекламные кабинеты (${selected.length} шт.)?`)) return;

    try {
      showLoading();
      for (const accId of selected) {
        await apiRequest(`/api/accounts/${accId}`, { method: 'DELETE' }).catch(() => {});
      }
      showToast(`Удалено ${selected.length} кабинетов`, 'success');
      window.clearAccountSelection();
      await loadAccounts();
    } catch (e) {
      showToast(e.message || 'Ошибка удаления кабинетов', 'error');
    } finally {
      hideLoading();
    }
  };

  // ==========================================================
  // RENDER CELL HELPER FOR DYNAMIC ACCOUNTS TABLE
  // ==========================================================
  function renderAccountCell(acc, colId, isSelected, displayName, metaState, activeRules, autoPillClass, autoPillText) {
    const m = acc.latest_metrics || acc.insights || {};
    const currency = acc.currency || 'USD';
    
    switch (colId) {
      case 'name':
        return `
          <td class="attio-td sticky-col">
            <div class="cell-entity-wrapper">
              <div style="display: flex; align-items: center; flex-shrink: 0;" onclick="event.stopPropagation();">
                <input type="checkbox" class="attio-checkbox attio-row-checkbox" ${isSelected ? 'checked' : ''} onchange="window.toggleAccountSelection('${escapeHtml(acc.account_id)}', this.checked)" title="Выбрать кабинет">
              </div>
              <div style="min-width: 0; overflow: hidden; flex: 1;">
                <div class="account-text-name" title="${escapeHtml(displayName)}">${escapeHtml(displayName)}</div>
                <div class="account-text-id" onclick="window.copyToClipboard('${escapeHtml(acc.account_id)}', this)" title="Нажмите, чтобы скопировать ID" style="cursor:pointer; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                  ${escapeHtml(acc.account_id)}
                </div>
              </div>
            </div>
          </td>
        `;
      case 'status':
        const metaPillClass = metaState.key === 'active' ? 'green' : (metaState.key === 'paused' ? 'amber' : 'red');
        return `
          <td class="attio-td">
            <span class="status-pill ${metaPillClass}" style="max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
              <span class="status-dot"></span>
              <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(metaState.label)}</span>
            </span>
          </td>
        `;
      case 'timezone':
        const tz = acc.timezone_name || 'UTC';
        return `
          <td class="attio-td">
            <span style="font-size: 12px; color: var(--text-secondary); display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${escapeHtml(tz)}">
              ${escapeHtml(tz)}
            </span>
          </td>
        `;
      case 'spend':
        const rawSpend = acc.today_spend !== undefined ? acc.today_spend : (m.spend !== undefined ? m.spend : 0);
        const spendStr = formatMoneyOrDash(rawSpend, currency);
        return `
          <td class="attio-td">
            <span class="num-bold" style="display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(spendStr)}</span>
          </td>
        `;
      case 'cpm':
        const cpmVal = m.cpm !== undefined && m.cpm !== null ? formatMoneyOrDash(m.cpm, currency) : '—';
        return `
          <td class="attio-td">
            <span class="num-bold" style="display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(cpmVal)}</span>
          </td>
        `;
      case 'cpc':
        const cpcVal = m.cpc !== undefined && m.cpc !== null ? formatMoneyOrDash(m.cpc, currency) : '—';
        return `
          <td class="attio-td">
            <span class="num-bold" style="display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(cpcVal)}</span>
          </td>
        `;
      case 'ctr':
        const ctrVal = m.ctr !== undefined && m.ctr !== null ? `${Number(m.ctr).toFixed(2)}%` : '—';
        return `
          <td class="attio-td">
            <span class="num-bold" style="display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(ctrVal)}</span>
          </td>
        `;
      case 'leads':
        const rawLeads = acc.today_leads !== undefined ? acc.today_leads : (m.leads !== undefined ? m.leads : '—');
        return `
          <td class="attio-td">
            <span class="num-bold" style="display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(String(rawLeads))}</span>
          </td>
        `;
      case 'cpl':
        const rawCpl = acc.today_cpl !== undefined ? acc.today_cpl : (m.cpl !== undefined ? m.cpl : null);
        const cplStr = rawCpl !== null && rawCpl !== undefined ? formatMoneyOrDash(rawCpl, currency) : '—';
        return `
          <td class="attio-td">
            <span style="display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(cplStr)}</span>
          </td>
        `;
      case 'registrations':
        const regsVal = m.registrations !== undefined ? m.registrations : '—';
        return `
          <td class="attio-td">
            <span class="num-bold" style="display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(String(regsVal))}</span>
          </td>
        `;
      case 'cpreg':
        const cpregVal = m.cpreg !== undefined && m.cpreg !== null ? formatMoneyOrDash(m.cpreg, currency) : '—';
        return `
          <td class="attio-td">
            <span style="display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(cpregVal)}</span>
          </td>
        `;
      case 'purchases':
        const purchVal = m.purchases !== undefined ? m.purchases : '—';
        return `
          <td class="attio-td">
            <span class="num-bold" style="display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(String(purchVal))}</span>
          </td>
        `;
      case 'cpp':
        const cppVal = m.cpp !== undefined && m.cpp !== null ? formatMoneyOrDash(m.cpp, currency) : '—';
        return `
          <td class="attio-td">
            <span style="display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(cppVal)}</span>
          </td>
        `;
      case 'automation':
        return `
          <td class="attio-td">
            <button class="status-pill ${autoPillClass}" type="button" onclick="window.toggleRules('${escapeHtml(acc.account_id)}', ${!acc.rules_enabled})" style="cursor: pointer; border: none; font-family: inherit; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="Нажмите, чтобы переключить автоматику">
              <span class="status-dot"></span>
              <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${autoPillText}</span>
            </button>
          </td>
        `;
      case 'currency':
        return `
          <td class="attio-td">
            <span class="mono" style="font-size: 12px; font-weight: 500; display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
              ${escapeHtml(currency)}
            </span>
          </td>
        `;
      case 'business_name':
        const bmName = acc.business_name || acc.batch_name || '—';
        return `
          <td class="attio-td">
            <span style="font-size: 12px; color: var(--text-secondary); display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${escapeHtml(bmName)}">
              ${escapeHtml(bmName)}
            </span>
          </td>
        `;
      case 'note':
        const noteText = String(acc.note || '').trim();
        return `
          <td class="attio-td">
            <span style="color: ${noteText ? 'var(--text-primary)' : 'var(--text-muted)'}; font-size: 12px; display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${escapeHtml(noteText || '')}">
              ${escapeHtml(noteText || '—')}
            </span>
          </td>
        `;
      case 'spend_cap':
        const spendCapVal = acc.spend_cap ? formatMoneyOrDash(acc.spend_cap, currency) : '—';
        return `
          <td class="attio-td">
            <span style="display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(spendCapVal)}</span>
          </td>
        `;
      case 'amount_spent':
        const amountSpentVal = acc.amount_spent ? formatMoneyOrDash(acc.amount_spent, currency) : '—';
        return `
          <td class="attio-td">
            <span style="display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(amountSpentVal)}</span>
          </td>
        `;
      case 'impressions':
        const impVal = m.impressions !== undefined ? m.impressions : '—';
        return `
          <td class="attio-td">
            <span class="num-bold" style="display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(String(impVal))}</span>
          </td>
        `;
      case 'reach':
        const reachVal = m.reach !== undefined ? m.reach : '—';
        return `
          <td class="attio-td">
            <span class="num-bold" style="display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(String(reachVal))}</span>
          </td>
        `;
      case 'frequency':
        const freqVal = m.frequency !== undefined && m.frequency !== null ? Number(m.frequency).toFixed(2) : '—';
        return `
          <td class="attio-td">
            <span style="display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(freqVal)}</span>
          </td>
        `;
      case 'clicks':
        const clicksVal = m.clicks !== undefined ? m.clicks : '—';
        return `
          <td class="attio-td">
            <span class="num-bold" style="display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(String(clicksVal))}</span>
          </td>
        `;
      case 'link_clicks':
        const linkClicksVal = m.link_clicks !== undefined ? m.link_clicks : '—';
        return `
          <td class="attio-td">
            <span class="num-bold" style="display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(String(linkClicksVal))}</span>
          </td>
        `;
      case 'link_ctr':
        const lCtr = m.link_ctr !== undefined ? m.link_ctr : (m.ctr_link !== undefined ? m.ctr_link : null);
        const lCtrStr = lCtr !== null ? `${Number(lCtr).toFixed(2)}%` : '—';
        return `
          <td class="attio-td">
            <span style="display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(lCtrStr)}</span>
          </td>
        `;
      case 'roas':
        const roasVal = m.roas !== undefined && m.roas !== null ? `${Number(m.roas).toFixed(2)}x` : '—';
        return `
          <td class="attio-td">
            <span class="num-bold" style="display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(roasVal)}</span>
          </td>
        `;
      case 'landing_page_views':
        const lpvVal = m.landing_page_views !== undefined ? m.landing_page_views : '—';
        return `
          <td class="attio-td">
            <span class="num-bold" style="display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(String(lpvVal))}</span>
          </td>
        `;
      case 'cost_per_lpv':
        const costLpv = m.cost_per_lpv !== undefined && m.cost_per_lpv !== null ? formatMoneyOrDash(m.cost_per_lpv, currency) : '—';
        return `
          <td class="attio-td">
            <span style="display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(costLpv)}</span>
          </td>
        `;
      case 'rules':
        return `
          <td class="attio-td">
            <div style="display: flex; align-items: center; gap: 4px; overflow: hidden; min-width: 0;">
              <span class="status-pill ${activeRules.length > 0 ? 'green' : 'amber'}" style="font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100%;">
                ${activeRules.length > 0 ? `${activeRules.length} ${pluralize(activeRules.length, 'правило', 'правила', 'правил')}` : 'Без правил'}
              </span>
              <button class="btn btn-secondary btn-xs" type="button" onclick="window.openAssignRuleModal('${escapeHtml(acc.account_id)}')" style="padding: 2px 5px; font-size: 10.5px; flex-shrink: 0;">
                Настроить
              </button>
            </div>
          </td>
        `;
      default:
        return `<td class="attio-td">—</td>`;
    }
  }

  // ==========================================================
  // RENDER ACCOUNTS (ATTIO GRID WITH DRAG & DROP AND SORTING)
  // ==========================================================
  function renderAccounts() {
    const listEl = document.getElementById('accountsList');
    const emptyEl = document.getElementById('accountsEmptyState');
    const query = state.searchQuery.toLowerCase().trim();

    // Filter by search, chips, and group
    let filtered = state.accounts.filter(acc => {
      const searchText = [acc.name, acc.custom_name, acc.note, acc.account_id]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      const matchSearch = !query || searchText.includes(query);

      if (!matchSearch) return false;
      if (state.accountGroupFilter !== 'all' && !(acc.group_ids || []).map(String).includes(state.accountGroupFilter)) return false;

      if (state.filter === 'active') return acc.account_status === 1 && acc.is_active;
      if (state.filter === 'rules') return acc.rules_enabled;
      if (state.filter === 'issue') return acc.account_status !== 1 || !acc.is_active;
      return true;
    });

    // Apply sorting
    filtered = sortAccountsList(filtered);

    // Update chip counters
    const totalCount = state.accounts.length;
    const rulesCount = state.accounts.filter(a => a.rules_enabled).length;
    const activeCount = state.accounts.filter(a => a.account_status === 1 && a.is_active).length;
    const issueCount = state.accounts.filter(a => a.account_status !== 1 || !a.is_active).length;

    const elCountAll = document.getElementById('countAll');
    const elCountActive = document.getElementById('countActive');
    const elCountRules = document.getElementById('countRules');
    const elCountIssue = document.getElementById('countIssue');
    const elViewCountAll = document.getElementById('viewCountAll');
    const sbTotal = document.getElementById('sidebarTotalCount');

    if (elCountAll) elCountAll.textContent = totalCount;
    if (elCountActive) elCountActive.textContent = activeCount;
    if (elCountRules) elCountRules.textContent = rulesCount;
    if (elCountIssue) elCountIssue.textContent = issueCount;
    if (elViewCountAll) elViewCountAll.textContent = totalCount;
    if (sbTotal) sbTotal.textContent = totalCount;

    if (filtered.length === 0) {
      if (listEl) listEl.innerHTML = '';
      if (emptyEl) emptyEl.classList.remove('hidden');
      updateBulkActionBar();
      return;
    }

    if (emptyEl) emptyEl.classList.add('hidden');

    if (!state.selectedAccounts) state.selectedAccounts = new Set();

    // Build Table Headers dynamically based on state.accountsColumnOrder
    const colOrder = state.accountsColumnOrder || DEFAULT_ACCOUNTS_COLUMN_ORDER;
    const totalFiltered = filtered.length;
    const selectedCount = state.selectedAccounts.size;
    const allSelected = totalFiltered > 0 && selectedCount >= totalFiltered;
    
    const theadHtml = colOrder.map(colId => {
      const colDef = ACCOUNTS_COLUMNS_DEF[colId] || { id: colId, label: colId, type: 'text', minWidth: 20, defaultWidth: 130 };
      const isSticky = colDef.sticky;
      const width = state.accountsColumnWidths[colId] || colDef.defaultWidth || 130;
      const isSorted = state.accountsSortColumn === colId;
      const sortArrow = isSorted ? (state.accountsSortDirection === 'asc' ? ' ↑' : ' ↓') : '';

      if (isSticky && colId === 'name') {
        const isHeaderSelectedClass = selectedCount > 0 ? 'has-selected' : '';
        return `
          <th class="attio-th sticky-col ${isHeaderSelectedClass}" 
              data-col-id="${colId}" 
              id="th-col-${colId}"
              style="width: ${width}px; max-width: ${width}px; min-width: ${width}px;">
            <div class="attio-th-content" onclick="window.setAccountsSort('name')" style="cursor: pointer;" title="Нажмите для сортировки">
              <div class="attio-th-left">
                <div style="display: flex; align-items: center; flex-shrink: 0;" onclick="event.stopPropagation();">
                  <input type="checkbox" id="selectAllAccountsCheckbox" class="attio-checkbox" ${allSelected ? 'checked' : ''} onchange="window.toggleSelectAllAccounts(this.checked)" title="Выбрать все кабинеты">
                </div>
                <span class="attio-th-type-icon">${colDef.iconSvg || ''}</span>
                <span class="attio-th-title">${escapeHtml(colDef.label)}${sortArrow}</span>
              </div>
            </div>
            <div class="attio-resizer" onmousedown="window.initColumnResize(event, '${colId}')" title="Потяните для изменения ширины (двойной клик — сброс)"></div>
          </th>
        `;
      }

      return `
        <th class="attio-th" 
            data-col-id="${colId}" 
            id="th-col-${colId}"
            style="width: ${width}px; max-width: ${width}px; min-width: ${width}px;"
            onmousedown="window.handleHeaderPointerDown(event, '${colId}')">
          <div class="attio-th-content">
            <div class="attio-th-left">
              <span class="attio-th-type-icon">${colDef.iconSvg || ''}</span>
              <span class="attio-th-title">${escapeHtml(colDef.label)}${sortArrow}</span>
            </div>
          </div>
          <div class="attio-resizer" onmousedown="window.initColumnResize(event, '${colId}')" title="Потяните для изменения ширины (двойной клик — сброс)"></div>
        </th>
      `;
    }).join('');

    // Build Rows
    const rowsHtml = filtered.map(acc => {
      const metaState = getAccountMetaState(acc);
      const activeRules = Array.isArray(acc.active_rules) ? acc.active_rules : [];
      const displayName = accountDisplayName(acc);
      const autoPillClass = acc.rules_enabled ? 'green' : 'amber';
      const autoPillText = acc.rules_enabled ? 'Включена' : 'На паузе';
      const isSelected = state.selectedAccounts.has(acc.account_id);

      const cellsHtml = colOrder.map(colId => 
        renderAccountCell(acc, colId, isSelected, displayName, metaState, activeRules, autoPillClass, autoPillText)
      ).join('');

      return `
        <tr id="row-${escapeHtml(acc.account_id)}" class="attio-row ${isSelected ? 'is-selected' : ''}">
          ${cellsHtml}
          <td class="attio-td attio-td-add-col-spacer"></td>
          <td class="attio-td attio-td-spacer"></td>
        </tr>
      `;
    }).join('');

    if (listEl) {
      const colgroupHtml = colOrder.map(colId => {
        const colDef = ACCOUNTS_COLUMNS_DEF[colId] || {};
        const width = state.accountsColumnWidths[colId] || colDef.defaultWidth || 130;
        return `<col id="col-track-${colId}" style="width: ${width}px; max-width: ${width}px; min-width: ${width}px;">`;
      }).join('') + '<col style="width: 120px; max-width: 120px; min-width: 120px;"><col class="col-track-spacer" style="width: 100%;">';

      listEl.innerHTML = `
        <div class="attio-table-viewport">
          <table class="attio-table">
            <colgroup>
              ${colgroupHtml}
            </colgroup>
            <thead>
              <tr>
                ${theadHtml}
                <th class="attio-th attio-th-add-col" style="width: 120px; max-width: 120px; min-width: 120px;">
                  <button type="button" class="attio-add-col-btn" onclick="window.toggleAddColumnPopover(event)" title="Добавить колонку в таблицу">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                    <span>Add column</span>
                  </button>
                </th>
                <th class="attio-th attio-th-spacer"></th>
              </tr>
            </thead>
            <tbody>
              ${rowsHtml}
            </tbody>
          </table>
        </div>
      `;
    }
    updateBulkActionBar();
  }

  // ==========================================================
  // TAB: FACEBOOK ACCOUNTS (PROFILES, BMS, TOKENS - ATTIO STYLE)
  // ==========================================================
  async function loadFacebookAccounts() {
    const tableBody = document.getElementById('fbAccountsTableBody');
    const emptyEl = document.getElementById('fbAccountsEmptyState');
    if (!tableBody) return;
    
    try {
      const [connections, accounts] = await Promise.all([
        apiRequest('/api/meta/connections').catch(() => []),
        state.accounts.length ? Promise.resolve(state.accounts) : apiRequest('/api/accounts').catch(() => [])
      ]);
      
      state.fbConnections = connections || [];
      if (accounts && accounts.length) {
        state.accounts = accounts;
      }
      renderFacebookAccounts();
    } catch (e) {
      console.error('Error loading facebook accounts:', e);
      if (emptyEl) emptyEl.classList.remove('hidden');
    }
  }

  function renderFacebookAccounts() {
    const tableBody = document.getElementById('fbAccountsTableBody');
    const emptyEl = document.getElementById('fbAccountsEmptyState');
    if (!tableBody) return;

    const connections = state.fbConnections || [];

    // Update sidebar counter
    const totalCount = connections.length;
    const elSidebarCount = document.getElementById('sidebarFbAccountsCount');
    if (elSidebarCount) elSidebarCount.textContent = totalCount;

    if (connections.length === 0) {
      tableBody.innerHTML = '';
      if (emptyEl) emptyEl.classList.remove('hidden');
      return;
    }

    if (emptyEl) emptyEl.classList.add('hidden');

    const html = connections.map(conn => {
      const name = conn.provider_user_name || 'Facebook User';
      const initial = name.charAt(0).toUpperCase();
      const uid = conn.provider_user_id || '—';
      
      const linkedAccounts = state.accounts || [];
      const rkCount = linkedAccounts.length;
      
      let totalSpend = 0;
      linkedAccounts.forEach(acc => {
        const sp = acc.today_spend || acc.insights?.spend || 0;
        totalSpend += (typeof sp === 'number' ? sp : 0);
      });
      const spendFormatted = totalSpend > 0 ? `$${totalSpend.toFixed(2)}` : '$0.00';

      const isTokenActive = conn.status === 'active';
      const tokenClass = isTokenActive ? 'green' : 'amber';
      const tokenText = isTokenActive ? 'Активен' : 'Требует внимания';

      return `
        <tr class="attio-row">
          <td class="attio-td sticky-col" style="width: 280px;">
            <div class="cell-entity-wrapper">
              <div class="cell-icon-container">
                <div class="cell-entity-icon fb-icon" title="Facebook Профиль">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/></svg>
                </div>
              </div>
              <div style="min-width: 0; overflow: hidden; flex: 1;">
                <div class="account-text-name" title="${escapeHtml(name)}">${escapeHtml(name)}</div>
                <div class="account-text-id">UID: ${escapeHtml(uid)} · <span class="fb-rk-badge" style="display:inline-block; margin-left:2px;">${rkCount} рк</span></div>
              </div>
            </div>
          </td>
          <td class="attio-td" style="width: 220px;">
            <div style="font-weight: 500; font-size: 12.5px; color: var(--text-primary);">${escapeHtml(name)}</div>
            <div style="font-size: 11px; color: var(--text-muted); font-family: var(--font-mono); margin-top: 1px;">
              ID: ${escapeHtml(uid)}
            </div>
          </td>
          <td class="attio-td" style="width: 220px;">
            <div>
              <span class="fb-bm-chip">${escapeHtml(name)} <b style="color: var(--text-muted); font-size: 10px; margin-left: 2px;">${rkCount} рк</b></span>
            </div>
          </td>
          <td class="attio-td" style="width: 140px;">
            <span class="num-bold">${escapeHtml(spendFormatted)}</span>
          </td>
          <td class="attio-td" style="width: 160px;">
            <span class="status-pill ${tokenClass}">
              <span class="status-dot"></span>
              ${tokenText}
            </span>
          </td>
          <td class="attio-td" style="width: 110px; text-align: right;">
            <div style="display: inline-flex; align-items: center; gap: 4px;">
              <button class="btn btn-secondary btn-xs" onclick="window.discoverMetaConnectionAssets(${conn.id})" title="Синхронизировать кабинеты" style="padding: 2px 6px;"><svg width="12" height="12" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg></button>
              <button class="btn btn-secondary btn-xs" onclick="window.deleteMetaConnectionPrompt(${conn.id})" title="Удалить подключение" style="padding: 2px 6px; color: #BA2525;"><svg width="12" height="12" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg></button>
            </div>
          </td>
          <td class="attio-td attio-td-spacer"></td>
        </tr>
      `;
    }).join('');

    tableBody.innerHTML = html;
  }

  window.discoverMetaConnectionAssets = async function (connectionId) {
    try {
      showLoading();
      await discoverMetaAssets(connectionId);
      showToast('Кабинеты синхронизированы', 'success');
      await loadAccounts();
    } catch (e) {
      showToast(e.message || 'Ошибка синхронизации', 'error');
    } finally {
      hideLoading();
    }
  };

  window.deleteMetaConnectionPrompt = async function (connectionId) {
    if (!confirm('Отключить этот профиль Facebook? Все привязанные токены будут удалены.')) return;
    try {
      showLoading();
      await apiRequest(`/api/meta/connections/${connectionId}`, { method: 'DELETE' });
      showToast('Подключение Facebook удалено', 'success');
      await loadFacebookAccounts();
    } catch (e) {
      showToast(e.message || 'Ошибка удаления', 'error');
    } finally {
      hideLoading();
    }
  };

  window.startMetaOAuthFlow = function () {
    window.switchTab('add');
  };

  function getAccountMetaState(account) {
    if (!account.is_active) return { key: 'inactive', label: 'Выключен в Buyerly', dot: 'muted' };
    if ([2, 101].includes(account.account_status)) return { key: 'blocked', label: 'Заблокирован', dot: 'danger' };
    if (account.account_status === 3) return { key: 'unsettled', label: 'Проблема оплаты', dot: 'warning' };
    if (account.account_status !== 1) return { key: 'unknown', label: 'Нужна проверка', dot: 'warning' };
    return { key: 'active', label: 'Доступен', dot: 'success' };
  }

  // Toggle Auto-Rules via API
  window.toggleRules = async function (accountId, isEnabled) {
    haptic('impact', 'light');
    try {
      const res = await apiRequest(`/api/accounts/${accountId}/toggle-rules`, {
        method: 'POST'
      });
      showToast(res.message, 'success');
      
      const acc = state.accounts.find(a => a.account_id === accountId);
      if (acc) {
        acc.rules_enabled = res.rules_enabled;
        renderAccounts();
      }
    } catch (err) {
      showToast(`Ошибка: ${err.message}`, 'error');
      loadAccounts();
    }
  };

  // Copy Account ID
  window.copyToClipboard = function (text, el) {
    haptic('impact', 'light');
    navigator.clipboard.writeText(text).then(() => {
      showToast(`ID ${text} скопирован в буфер!`, 'info');
    });
  };

  window.openAccountDetails = function (accountId) {
    const account = state.accounts.find(item => item.account_id === accountId);
    const content = document.getElementById('accountDetailsContent');
    if (!account || !content) return;
    const metaState = getAccountMetaState(account);
    const activeRules = Array.isArray(account.active_rules) ? account.active_rules : [];
    const actionLabels = {
      turn_off: 'Выключить ad set', notify_only: 'Только уведомить', turn_on: 'Включить ad set',
      increase_budget: 'Увеличить бюджет', decrease_budget: 'Уменьшить бюджет'
    };
    const rulesHtml = activeRules.length
      ? activeRules.map(rule => `
          <div class="account-detail-rule">
            <div>
              <b>${escapeHtml(rule.name || `Правило #${rule.preset_id}`)}</b>
              <span>${escapeHtml(actionLabels[rule.action] || rule.action)} · проверка каждые ${rule.check_interval || 5} мин · cooldown ${rule.cooldown_minutes || 0} мин</span>
            </div>
            <span class="account-detail-rule-state ${account.rules_enabled ? 'active' : 'paused'}">${account.rules_enabled ? 'Работает' : 'Пауза'}</span>
          </div>`).join('')
      : '<div class="account-detail-rules-empty">Правила не назначены. Автоматика не может быть включена.</div>';
    const ownerHtml = state.user?.role === 'admin'
      ? `<div class="account-detail-field"><span>Владелец</span><b class="mono">${escapeHtml(account.owner_id || '—')}</b></div>`
      : '';
    const connectionState = getAccountConnectionState(account);
    const displayName = accountDisplayName(account);
    const hasCustomName = String(account.custom_name || '').trim();
    const note = String(account.note || '').trim();

    content.innerHTML = `
      <div class="account-detail-hero">
        <div>
          <span class="eyebrow">Рекламный кабинет</span>
          <h2>${escapeHtml(displayName)}</h2>
          ${hasCustomName ? `<span class="account-detail-source-name">Название Meta: ${escapeHtml(account.name)}</span>` : ''}
          <button type="button" class="account-detail-copy mono" onclick="window.copyToClipboard('${escapeHtml(account.account_id)}', this)">${escapeHtml(account.account_id)} · копировать</button>
        </div>
        <span class="account-meta-state ${metaState.key}"><span class="status-dot dot-${metaState.dot}"></span>${metaState.label}</span>
      </div>

      <div class="account-detail-grid">
        <div class="account-detail-field"><span>Статус Meta</span><b>${escapeHtml(account.status_label || metaState.label)}</b></div>
        <div class="account-detail-field"><span>Часовой пояс</span><b class="mono">${escapeHtml(account.timezone_name || 'UTC')}</b></div>
        <div class="account-detail-field"><span>Валюта Meta</span><b class="mono">${escapeHtml(account.currency || 'UNKNOWN')}</b></div>
        <div class="account-detail-field"><span>Подключение</span><b>${escapeHtml(connectionState.label)}</b></div>
        <div class="account-detail-field"><span>Автоматика</span><b>${account.rules_enabled ? 'Включена' : 'Выключена'}</b></div>
        <div class="account-detail-field"><span>Назначено правил</span><b>${activeRules.length}</b></div>
        ${ownerHtml}
        <div class="account-detail-field"><span>Добавлен</span><b>${escapeHtml(account.created_at || '—')}</b></div>
      </div>

      <section class="account-detail-profile">
        <div class="account-detail-section-head"><h3>Внутренняя заметка</h3><button type="button" onclick="window.openAccountProfileEditor('${escapeHtml(account.account_id)}')">Изменить</button></div>
        <p class="${note ? '' : 'empty'}">${escapeHtml(note || 'Заметка пока не заполнена. Здесь можно хранить гео, оффер или текущий статус работы.')}</p>
      </section>

      <section class="account-detail-profile">
        <div class="account-detail-section-head"><h3>Группы кабинета</h3><button type="button" onclick="window.openAccountGroupForAccount('${escapeHtml(account.account_id)}')">Новая группа</button></div>
        <div class="account-group-tags">${renderAccountGroupTags(account, { empty: true })}</div>
      </section>

      <section class="account-detail-performance">
        <div class="account-detail-section-head"><h3>Последний сохранённый снимок</h3><span>Сегодня</span></div>
        ${renderAccountLatestMetrics(account, 'details')}
      </section>

      <section class="account-detail-rules">
        <div class="account-detail-section-head"><h3>Правила автоматики</h3><span>${activeRules.length}</span></div>
        ${rulesHtml}
      </section>

      <div class="account-detail-actions">
        <button class="btn btn-primary" type="button" onclick="window.manageRulesFromAccountDetails('${escapeHtml(account.account_id)}')">Управлять правилами</button>
        <button class="btn btn-secondary" type="button" onclick="window.openAccountLogs('${escapeHtml(account.account_id)}')">История действий</button>
        <button class="btn btn-danger" type="button" onclick="window.deleteAccountFromDetails('${escapeHtml(account.account_id)}')">Удалить</button>
      </div>`;
    window.openModal('modalAccountDetails');
  };

  window.manageRulesFromAccountDetails = function (accountId) {
    window.closeModal('modalAccountDetails');
    window.openAssignRuleModal(accountId);
  };

  window.openAccountLogs = function (accountId) {
    state.pendingLogsAccountId = accountId;
    window.closeModal('modalAccountDetails');
    window.switchTab('logs');
  };

  window.deleteAccountFromDetails = function (accountId) {
    const account = state.accounts.find(item => item.account_id === accountId);
    if (!account) return;
    window.closeModal('modalAccountDetails');
    window.openDeleteConfirmModal(account.account_id, accountDisplayName(account));
  };

  window.openAccountProfileEditor = function (accountId) {
    const account = state.accounts.find(item => item.account_id === accountId);
    if (!account) return;
    const connectionState = getAccountConnectionState(account);
    document.getElementById('accountProfileId').value = account.account_id;
    document.getElementById('accountCustomNameInput').value = account.custom_name || '';
    document.getElementById('accountNoteInput').value = account.note || '';
    document.getElementById('accountProfileMetaName').textContent = account.name || account.account_id;
    document.getElementById('accountProfileMeta').textContent = `${account.account_id} · ${connectionState.label}`;
    window.openModal('modalAccountProfile');
    window.setTimeout(() => document.getElementById('accountCustomNameInput')?.focus(), 50);
  };

  document.getElementById('btnSaveAccountProfile')?.addEventListener('click', async () => {
    const accountId = document.getElementById('accountProfileId')?.value;
    const customName = document.getElementById('accountCustomNameInput')?.value || '';
    const note = document.getElementById('accountNoteInput')?.value || '';
    const button = document.getElementById('btnSaveAccountProfile');
    if (!accountId || !button) return;
    button.disabled = true;
    try {
      const saved = await apiRequest(`/api/accounts/${accountId}/profile`, {
        method: 'PATCH',
        body: JSON.stringify({ custom_name: customName, note })
      });
      const account = state.accounts.find(item => item.account_id === accountId);
      if (account) {
        account.custom_name = saved.custom_name || '';
        account.note = saved.note || '';
      }
      const detailsWasOpen = !document.getElementById('modalAccountDetails')?.classList.contains('hidden');
      window.closeModal('modalAccountProfile');
      renderAccounts();
      if (state.activeTab === 'summary') rerenderSummaryForTableControls();
      if (detailsWasOpen) window.openAccountDetails(accountId);
      showToast(saved.message || 'Название и заметка сохранены', 'success');
    } catch (err) {
      showToast(`Ошибка сохранения: ${err.message}`, 'error');
    } finally {
      button.disabled = false;
    }
  });

  function renderAccountGroupMemberOptions(selectedIds = []) {
    const container = document.getElementById('accountGroupMembers');
    if (!container) return;
    const selected = new Set(selectedIds);
    container.innerHTML = state.accounts.map(account => `
      <label class="account-group-member-option">
        <input type="checkbox" value="${escapeHtml(account.account_id)}" ${selected.has(account.account_id) ? 'checked' : ''}>
        <span>
          <b>${escapeHtml(accountDisplayName(account))}</b>
          <small>${escapeHtml(account.name)} · ${escapeHtml(account.account_id)}</small>
        </span>
      </label>`).join('') || '<p class="text-hint">Сначала добавьте хотя бы один рекламный кабинет.</p>';
    updateAccountGroupSelectionCount();
  }

  function updateAccountGroupSelectionCount() {
    const count = document.querySelectorAll('#accountGroupMembers input:checked').length;
    const label = document.getElementById('accountGroupSelectionCount');
    if (label) label.textContent = `Выбрано: ${count}`;
  }

  window.openCreateAccountGroup = function (preselectedAccountId = '') {
    document.getElementById('accountGroupId').value = '';
    document.getElementById('accountGroupName').value = '';
    document.getElementById('accountGroupDescription').value = '';
    document.getElementById('accountGroupModalTitle').textContent = 'Новая группа кабинетов';
    document.getElementById('btnDeleteAccountGroup')?.classList.add('hidden');
    renderAccountGroupMemberOptions(preselectedAccountId ? [preselectedAccountId] : []);
    window.openModal('modalAccountGroup');
    window.setTimeout(() => document.getElementById('accountGroupName')?.focus(), 50);
  };

  window.openAccountGroupForAccount = function (accountId) {
    window.openCreateAccountGroup(accountId);
  };

  window.openAccountGroupEditor = function (groupId) {
    const group = state.accountGroups.find(item => Number(item.id) === Number(groupId));
    if (!group) return;
    document.getElementById('accountGroupId').value = String(group.id);
    document.getElementById('accountGroupName').value = group.name || '';
    document.getElementById('accountGroupDescription').value = group.description || '';
    document.getElementById('accountGroupModalTitle').textContent = 'Изменить группу кабинетов';
    document.getElementById('btnDeleteAccountGroup')?.classList.remove('hidden');
    renderAccountGroupMemberOptions(group.account_ids || []);
    window.openModal('modalAccountGroup');
  };

  document.getElementById('accountGroupMembers')?.addEventListener('change', updateAccountGroupSelectionCount);

  document.getElementById('btnSaveAccountGroup')?.addEventListener('click', async () => {
    const groupId = document.getElementById('accountGroupId')?.value || '';
    const name = document.getElementById('accountGroupName')?.value.trim() || '';
    const description = document.getElementById('accountGroupDescription')?.value.trim() || '';
    const accountIds = Array.from(document.querySelectorAll('#accountGroupMembers input:checked')).map(input => input.value);
    const button = document.getElementById('btnSaveAccountGroup');
    if (!name) {
      showToast('Введите название группы', 'error');
      document.getElementById('accountGroupName')?.focus();
      return;
    }
    button.disabled = true;
    try {
      await apiRequest(groupId ? `/api/account-groups/${groupId}` : '/api/account-groups', {
        method: groupId ? 'PUT' : 'POST',
        body: JSON.stringify({ name, description, account_ids: accountIds })
      });
      window.closeModal('modalAccountGroup');
      await loadAccounts();
      if (state.activeTab === 'summary') rerenderSummaryForTableControls();
      showToast(groupId ? 'Группа обновлена' : 'Группа создана', 'success');
    } catch (err) {
      showToast(`Ошибка: ${err.message}`, 'error');
    } finally {
      button.disabled = false;
    }
  });

  document.getElementById('btnDeleteAccountGroup')?.addEventListener('click', async () => {
    const groupId = document.getElementById('accountGroupId')?.value || '';
    const group = state.accountGroups.find(item => String(item.id) === groupId);
    if (!groupId || !group) return;
    if (!window.confirm(`Удалить группу «${group.name}»? Кабинеты останутся подключёнными.`)) return;
    try {
      await apiRequest(`/api/account-groups/${groupId}`, { method: 'DELETE' });
      if (state.summaryView.filters.group_id === groupId) {
        state.summaryView.filters.group_id = 'all';
        await persistSummaryView(state.summaryView);
      }
      window.closeModal('modalAccountGroup');
      await loadAccounts();
      if (state.activeTab === 'summary') rerenderSummaryForTableControls();
      showToast('Группа удалена', 'success');
    } catch (err) {
      showToast(`Ошибка удаления: ${err.message}`, 'error');
    }
  });

  // ==========================================================
  // TAB: RULES & PRESETS MANAGEMENT (ATTIO KANBAN BOARD)
  // ==========================================================
  let draggedRuleInfo = null;

  async function loadRulesTab() {
    await Promise.all([loadPresets(), loadRuleGroups(), loadAccounts()]);
    renderRulesTab();
  }

  window.onRulesFilterChange = function () {
    const searchInput = document.getElementById('rulesSearchInput');
    const actionFilter = document.getElementById('rulesActionFilter');
    state.rulesSearchQuery = (searchInput?.value || '').toLowerCase().trim();
    state.rulesActionFilter = actionFilter?.value || 'all';
    renderRulesTab();
  };

  function isPresetMatchingFilter(preset) {
    const query = (state.rulesSearchQuery || '').toLowerCase();
    const filter = state.rulesActionFilter || 'all';

    if (filter !== 'all' && preset.action !== filter) {
      return false;
    }
    if (query) {
      const nameMatch = (preset.name || '').toLowerCase().includes(query);
      const actionMatch = (preset.action || '').toLowerCase().includes(query);
      const condMatch = (preset.conditions || []).some(c => (c.metric || '').toLowerCase().includes(query));
      if (!nameMatch && !actionMatch && !condMatch) return false;
    }
    return true;
  }

  function buildKanbanRuleCard(p, groupId = null) {
    const actionBadgeMap = {
      'turn_off': { label: 'Стоп', class: 'rule-action-turn_off' },
      'notify_only': { label: 'Пуш', class: 'rule-action-notify_only' },
      'turn_on': { label: 'Старт', class: 'rule-action-turn_on' },
      'increase_budget': { label: '+Бюджет', class: 'rule-action-increase_budget' },
      'decrease_budget': { label: '-Бюджет', class: 'rule-action-decrease_budget' }
    };
    const act = actionBadgeMap[p.action] || { label: p.action, class: '' };
    const condList = p.conditions || [];
    const metricLabels = {
      'spend': 'Спенд', 'cpl': 'CPL', 'cpreg': 'CPReg', 'cpp': 'CPP',
      'legacy_cpa': 'CPA', 'leads': 'Лиды', 'registrations': 'Реги',
      'purchases': 'Покупки', 'ctr': 'CTR', 'cpc': 'CPC'
    };
    const opLabels = { gt: '>', gte: '≥', lt: '<', lte: '≤', eq: '=' };

    const conditionsSummary = condList.map(c => {
      const mLabel = metricLabels[c.metric] || c.metric;
      const op = opLabels[c.operator] || c.operator;
      const unit = (c.metric === 'leads' || c.metric === 'registrations' || c.metric === 'purchases') ? ' шт' : (c.metric === 'ctr' ? '%' : ' вал.');
      return `${mLabel} ${op} ${Number(c.value || 0)}${unit}`;
    }).join(' · ');

    let linkedCount = 0;
    (state.accounts || []).forEach(acc => {
      if (acc.rules_enabled && (acc.active_rules || []).some(r => r.preset_id === p.id)) {
        linkedCount++;
      }
    });

    const stepInfo = (p.action === 'increase_budget' || p.action === 'decrease_budget')
      ? ` ${p.action === 'increase_budget' ? '+' : '-'}${p.budget_change_percent || 20}%`
      : '';

    const isSelected = state.selectedRuleIds && state.selectedRuleIds.has(p.id);

    return `
      <div class="rules-kanban-card rule-card ${isSelected ? 'selected' : ''}"
           draggable="true"
           data-preset-id="${p.id}"
           data-group-id="${groupId !== null ? groupId : ''}"
           ondragstart="window.onRuleDragStart(event, ${p.id}, ${groupId !== null ? groupId : 'null'})"
           ondragend="window.onRuleDragEnd(event)"
           onclick="window.openRuleRecordPage(${p.id})">
        <div class="rule-card-top">
          <div class="rule-card-top-left">
            <span class="rule-card-checkbox ${isSelected ? 'selected' : ''}" onclick="event.stopPropagation(); window.toggleSelectRule(${p.id})" title="Выбрать правило">
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"></polyline></svg>
            </span>
            <span class="rule-card-title" title="${escapeHtml(p.name)}">${escapeHtml(p.name)}</span>
          </div>
          <div class="rule-card-top-right">
            <span class="rule-action-badge ${act.class}">${act.label}${stepInfo}</span>
          </div>
        </div>
        
        <div class="rule-card-meta-bar">
          <div class="rule-card-meta-left" title="${escapeHtml(conditionsSummary || 'Без условий')}">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
            <span class="rule-conditions-text">${escapeHtml(conditionsSummary || 'Без условий')}</span>
          </div>
          <div class="rule-card-meta-right">
            ${linkedCount > 0 ? `<span class="rule-link-badge active" title="Привязано кабинетов"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right:2px; vertical-align:-1px;"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"></path><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"></path></svg>${linkedCount}</span>` : ''}
            <span class="rule-meta-tag" title="Интервал проверки">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
              ${p.check_interval_minutes || 5}м
            </span>
          </div>
        </div>
      </div>
    `;
  }

  window.toggleSelectRule = function(ruleId) {
    if (!state.selectedRuleIds) state.selectedRuleIds = new Set();
    if (state.selectedRuleIds.has(ruleId)) {
      state.selectedRuleIds.delete(ruleId);
    } else {
      state.selectedRuleIds.add(ruleId);
    }
    updateRulesBulkActionsBar();
    renderRulesTab();
  };

  window.clearRuleSelection = function() {
    if (state.selectedRuleIds) state.selectedRuleIds.clear();
    updateRulesBulkActionsBar();
    renderRulesTab();
  };

  function updateRulesBulkActionsBar() {
    const bar = document.getElementById('rulesBulkActionsBar');
    const countEl = document.getElementById('rulesBulkCount');
    const count = state.selectedRuleIds ? state.selectedRuleIds.size : 0;
    if (!bar) return;
    if (count > 0) {
      if (countEl) countEl.textContent = count;
      bar.classList.remove('hidden');
    } else {
      bar.classList.add('hidden');
    }
  }

  window.bulkDeleteSelectedRules = async function() {
    const count = state.selectedRuleIds ? state.selectedRuleIds.size : 0;
    if (count === 0) return;
    if (!confirm(`Удалить выбранные правила (${count} шт.)?`)) return;

    const idsToDelete = [...state.selectedRuleIds];
    showGlobalLoading(`Удаление правил (${count})...`);
    try {
      for (const id of idsToDelete) {
        await apiFetch(`/api/rules/presets/${id}`, { method: 'DELETE' });
      }
      state.selectedRuleIds.clear();
      updateRulesBulkActionsBar();
      await loadRulePresets();
      await loadRuleGroups();
      renderRulesTab();
      showNotification(`Успешно удалено правил: ${count}`, 'success');
    } catch (err) {
      showNotification(`Ошибка удаления: ${err.message}`, 'error');
    } finally {
      hideGlobalLoading();
    }
  };

  let chooseGroupSelectedIndex = 0;
  let chooseGroupActiveColor = 'purple';
  let chooseGroupFilteredItems = [];

  function renderChooseGroupItems(query = '') {
    const listEl = document.getElementById('chooseGroupList');
    const createRow = document.getElementById('chooseGroupCreateActionRow');
    if (!listEl) return;

    const q = (query || '').toLowerCase().trim();
    const matchingGroups = (state.ruleGroups || []).filter(g => {
      if (!q) return true;
      return (g.name || '').toLowerCase().includes(q) || (g.description || '').toLowerCase().includes(q);
    });

    chooseGroupFilteredItems = [];

    let groupsHtml = '';
    if (matchingGroups.length === 0) {
      groupsHtml = '<div class="rules-column-empty" style="padding: 14px 0; text-align: center; color: #898a8d; font-size: 12px;">Группы не найдены</div>';
    } else {
      groupsHtml = matchingGroups.map((group) => {
        const colorName = group.color || 'purple';
        const count = (group.preset_ids || []).length;
        const itemIdx = chooseGroupFilteredItems.length;
        chooseGroupFilteredItems.push({ type: 'group', id: group.id, name: group.name });
        const isSelected = itemIdx === chooseGroupSelectedIndex;

        return `
          <div class="choose-group-item ${isSelected ? 'selected' : ''}" 
               data-item-index="${itemIdx}"
               data-group-id="${group.id}"
               onclick="window.selectGroupFromModal(${group.id})">
            <div class="choose-group-item-left">
              <span class="choose-group-dot swatch-${colorName}"></span>
              <span class="choose-group-item-name">${escapeHtml(group.name)}</span>
            </div>
            <div class="choose-group-item-right">
              <span class="choose-group-count-badge">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                ${count}
              </span>
            </div>
          </div>
        `;
      }).join('');
    }

    listEl.innerHTML = groupsHtml;

    // Add create item to items list
    const createIndex = chooseGroupFilteredItems.length;
    chooseGroupFilteredItems.push({ type: 'create' });
    if (createRow) {
      createRow.classList.toggle('selected', chooseGroupSelectedIndex === createIndex);
      createRow.setAttribute('data-item-index', String(createIndex));
    }

    if (chooseGroupSelectedIndex >= chooseGroupFilteredItems.length) {
      chooseGroupSelectedIndex = 0;
    }
    updateChooseGroupSelectionHighlight();
  }

  function updateChooseGroupSelectionHighlight() {
    document.querySelectorAll('.choose-group-item, #chooseGroupCreateActionRow').forEach(el => {
      const idx = Number(el.getAttribute('data-item-index'));
      el.classList.toggle('selected', idx === chooseGroupSelectedIndex);
      if (idx === chooseGroupSelectedIndex) {
        el.scrollIntoView({ block: 'nearest' });
      }
    });
  }

  window.openBulkMoveModal = function (event) {
    const count = state.selectedRuleIds ? state.selectedRuleIds.size : 0;
    if (count === 0) return;

    const countBadge = document.getElementById('chooseGroupSelectedCountBadge');
    if (countBadge) {
      countBadge.textContent = `${count} выбрано`;
    }

    const searchInput = document.getElementById('chooseGroupSearchInput');
    if (searchInput) {
      searchInput.value = '';
    }

    document.getElementById('chooseGroupViewSelect')?.classList.remove('hidden');
    document.getElementById('chooseGroupViewCreate')?.classList.add('hidden');

    chooseGroupSelectedIndex = 0;
    renderChooseGroupItems('');

    window.openModal('modalChooseGroup');
    setTimeout(() => {
      searchInput?.focus();
    }, 60);
  };

  window.filterChooseGroupList = function (query) {
    chooseGroupSelectedIndex = 0;
    renderChooseGroupItems(query);
  };

  window.onChooseGroupKeydown = function (event) {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      if (chooseGroupFilteredItems.length > 0) {
        chooseGroupSelectedIndex = (chooseGroupSelectedIndex + 1) % chooseGroupFilteredItems.length;
        updateChooseGroupSelectionHighlight();
      }
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      if (chooseGroupFilteredItems.length > 0) {
        chooseGroupSelectedIndex = (chooseGroupSelectedIndex - 1 + chooseGroupFilteredItems.length) % chooseGroupFilteredItems.length;
        updateChooseGroupSelectionHighlight();
      }
    } else if (event.key === 'Enter') {
      event.preventDefault();
      window.confirmSelectedGroupChoice();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      window.closeModal('modalChooseGroup');
    }
  };

  window.confirmSelectedGroupChoice = function () {
    const item = chooseGroupFilteredItems[chooseGroupSelectedIndex];
    if (!item) return;
    if (item.type === 'group') {
      window.selectGroupFromModal(item.id);
    } else if (item.type === 'create') {
      window.openCreateGroupFromChooseModal();
    }
  };

  window.selectGroupFromModal = async function (groupId) {
    const targetGroup = state.ruleGroups.find(g => g.id === groupId);
    if (!targetGroup) return;

    const presetIds = Array.from(state.selectedRuleIds || []);
    if (presetIds.length === 0) return;

    window.closeModal('modalChooseGroup');
    showGlobalLoading(`Перемещение правил (${presetIds.length})...`);
    try {
      await window.moveMultiplePresetsToGroup(presetIds, groupId);
      state.selectedRuleIds.clear();
      updateRulesBulkActionsBar();
      showToast(`Правила перемещены в группу «${targetGroup.name}»`, 'success');
    } catch (err) {
      showToast(`Ошибка перемещения: ${err.message}`, 'error');
    } finally {
      hideGlobalLoading();
    }
  };

  window.moveMultiplePresetsToGroup = async function (presetIds, targetGroupId) {
    const idsToMove = Array.from(presetIds);
    if (idsToMove.length === 0) return;

    // 1. Remove from all other groups
    for (const group of state.ruleGroups) {
      if (group.id === targetGroupId) continue;
      const currentIds = group.preset_ids || [];
      const hasAny = currentIds.some(id => idsToMove.includes(id));
      if (hasAny) {
        const newIds = currentIds.filter(id => !idsToMove.includes(id));
        await apiRequest(`/api/rule-groups/${group.id}`, {
          method: 'PUT',
          body: JSON.stringify({
            name: group.name,
            description: group.description || '',
            preset_ids: newIds
          })
        });
        group.preset_ids = newIds;
      }
    }

    // 2. Add to target group (if specified)
    if (targetGroupId !== null) {
      const tgtGroup = state.ruleGroups.find(g => g.id === targetGroupId);
      if (tgtGroup) {
        const currentIds = tgtGroup.preset_ids || [];
        const combinedIds = Array.from(new Set([...currentIds, ...idsToMove]));
        await apiRequest(`/api/rule-groups/${targetGroupId}`, {
          method: 'PUT',
          body: JSON.stringify({
            name: tgtGroup.name,
            description: tgtGroup.description || '',
            preset_ids: combinedIds
          })
        });
        tgtGroup.preset_ids = combinedIds;
      }
    }

    await loadRuleGroups();
    await loadRulePresets();
    renderRulesTab();
  };

  window.openCreateGroupFromChooseModal = function () {
    document.getElementById('chooseGroupViewSelect')?.classList.add('hidden');
    document.getElementById('chooseGroupViewCreate')?.classList.remove('hidden');

    const searchVal = document.getElementById('chooseGroupSearchInput')?.value.trim() || '';
    const nameInput = document.getElementById('newGroupNameModalInput');
    const descInput = document.getElementById('newGroupDescModalInput');
    const dot = document.getElementById('chooseGroupColorDot');
    const palette = document.getElementById('chooseGroupColorPalette');

    chooseGroupActiveColor = 'purple';
    if (dot) dot.className = 'attio-popover-dot swatch-purple';
    palette?.classList.add('hidden');

    if (nameInput) nameInput.value = searchVal;
    if (descInput) descInput.value = '';

    setTimeout(() => {
      nameInput?.focus();
      nameInput?.select();
    }, 60);
  };

  window.backToChooseGroupList = function () {
    document.getElementById('chooseGroupViewCreate')?.classList.add('hidden');
    document.getElementById('chooseGroupViewSelect')?.classList.remove('hidden');

    const searchInput = document.getElementById('chooseGroupSearchInput');
    setTimeout(() => {
      searchInput?.focus();
    }, 60);
  };

  window.toggleModalNewGroupColorPalette = function (event) {
    if (event) event.stopPropagation();
    document.getElementById('chooseGroupColorPalette')?.classList.toggle('hidden');
  };

  window.selectModalNewGroupColor = function (colorName) {
    chooseGroupActiveColor = colorName;
    const dot = document.getElementById('chooseGroupColorDot');
    if (dot) dot.className = `attio-popover-dot swatch-${colorName}`;
    document.getElementById('chooseGroupColorPalette')?.classList.add('hidden');
  };

  window.onNewGroupNameModalKeydown = function (event) {
    if (event.key === 'Enter') {
      event.preventDefault();
      window.submitCreateGroupFromModal();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      window.backToChooseGroupList();
    }
  };

  window.submitCreateGroupFromModal = async function () {
    const nameInput = document.getElementById('newGroupNameModalInput');
    const descInput = document.getElementById('newGroupDescModalInput');
    const name = nameInput ? nameInput.value.trim() : '';
    const description = descInput ? descInput.value.trim() : '';

    if (!name) {
      showToast('Введите название группы', 'error');
      nameInput?.focus();
      return;
    }

    const presetIds = Array.from(state.selectedRuleIds || []);
    const color = chooseGroupActiveColor || 'purple';

    showGlobalLoading('Создание группы и перемещение...');
    try {
      // 1. Remove rules from their current source groups
      for (const group of state.ruleGroups) {
        const currentIds = group.preset_ids || [];
        const hasAny = currentIds.some(id => presetIds.includes(id));
        if (hasAny) {
          const newIds = currentIds.filter(id => !presetIds.includes(id));
          await apiRequest(`/api/rule-groups/${group.id}`, {
            method: 'PUT',
            body: JSON.stringify({
              name: group.name,
              description: group.description || '',
              preset_ids: newIds
            })
          });
          group.preset_ids = newIds;
        }
      }

      // 2. Create the new group with preset_ids
      await apiRequest('/api/rule-groups', {
        method: 'POST',
        body: JSON.stringify({
          name,
          description,
          color,
          preset_ids: presetIds
        })
      });

      window.closeModal('modalChooseGroup');
      state.selectedRuleIds.clear();
      updateRulesBulkActionsBar();
      await loadRuleGroups();
      await loadRulePresets();
      renderRulesTab();
      showToast(`Группа «${name}» создана, правила перемещены`, 'success');
    } catch (err) {
      showToast(`Ошибка: ${err.message}`, 'error');
    } finally {
      hideGlobalLoading();
    }
  };

  window.onChooseGroupOverlayClick = function (event) {
    if (event.target && event.target.id === 'modalChooseGroup') {
      window.closeModal('modalChooseGroup');
    }
  };

  function renderRulesTab() {
    const boardContainer = document.getElementById('ruleGroupsContainer');
    const emptyEl = document.getElementById('rulesEmptyState');
    const activeCountEl = document.getElementById('rulesActiveCount');
    const groupsCountEl = document.getElementById('rulesGroupsCount');
    const linkedCountEl = document.getElementById('rulesLinkedAccsCount');
    if (!boardContainer || !emptyEl) return;

    updateRulesBulkActionsBar();

    const totalPresets = state.presets.length;
    let linkedAccountsCount = 0;
    state.accounts.forEach(a => {
      if (a.rules_enabled && a.active_rules && a.active_rules.length > 0) {
        linkedAccountsCount++;
      }
    });

    if (activeCountEl) activeCountEl.textContent = totalPresets;
    if (groupsCountEl) groupsCountEl.textContent = state.ruleGroups.length;
    if (linkedCountEl) linkedCountEl.textContent = linkedAccountsCount;

    if (totalPresets === 0 && state.ruleGroups.length === 0) {
      boardContainer.innerHTML = '';
      emptyEl.classList.remove('hidden');
      return;
    }

    emptyEl.classList.add('hidden');

    const defaultDotColors = ['dot-purple', 'dot-blue', 'dot-emerald', 'dot-amber', 'dot-orange', 'dot-cyan', 'dot-magenta', 'dot-rose', 'dot-lime', 'dot-yellow', 'dot-red', 'dot-gray'];
    const allGroupedPresetIds = new Set();
    state.ruleGroups.forEach(g => {
      (g.preset_ids || []).forEach(id => allGroupedPresetIds.add(id));
    });

    // 1. Ungrouped Rules Column ("Без группы") - Fixed first as "No stage" in Attio
    const ungroupedPresets = state.presets
      .filter(p => !allGroupedPresetIds.has(p.id))
      .filter(isPresetMatchingFilter);

    let ungroupedColumnHtml = '';
    if (ungroupedPresets.length > 0 || state.ruleGroups.length === 0) {
      const ungroupedCardsHtml = ungroupedPresets.map(p => buildKanbanRuleCard(p, null)).join('');
      ungroupedColumnHtml = `
        <div class="rules-column fixed-column" data-group-id="ungrouped">
          <div class="rules-column-header">
            <div class="rules-column-title-wrap">
              <span class="rules-column-dot dot-gray"></span>
              <span class="rules-column-title">Без группы</span>
              <span class="rules-column-count">${ungroupedPresets.length}</span>
            </div>
            <div class="rules-column-actions">
              <button class="rules-column-btn" title="Добавить правило" onclick="window.openChooseRuleModal(null)">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
              </button>
            </div>
          </div>
          <div class="rules-column-body"
               ondragover="window.onRuleColumnDragOver(event)"
               ondragleave="window.onRuleColumnDragLeave(event)"
               ondrop="window.onRuleColumnDrop(event, null)">
            ${ungroupedCardsHtml || '<div class="rules-column-empty">Нет одиночных правил</div>'}
          </div>
          <div class="rules-column-footer">
            <button type="button" class="rules-column-add-calc-btn" onclick="window.openCreateRuleFromTab()">
              <span>+ Add calculation</span>
            </button>
          </div>
        </div>
      `;
    }

    // 2. Custom Group Columns (Draggable horizontally in Attio style)
    const groupColumnsHtml = state.ruleGroups.map((group, idx) => {
      const savedColor = state.ruleGroupColors[group.id];
      const dotColor = savedColor ? `dot-${savedColor}` : defaultDotColors[idx % defaultDotColors.length];
      const groupPresetIds = group.preset_ids || [];
      const presetsInGroup = groupPresetIds
        .map(id => state.presets.find(p => p.id === id))
        .filter(Boolean)
        .filter(isPresetMatchingFilter);

      const isCollapsed = state.collapsedRuleGroups.has(group.id);

      if (isCollapsed) {
        return `
          <div class="rules-column collapsed"
               data-group-id="${group.id}"
               ondragover="window.onRuleGroupColumnDragOver(event, ${group.id})"
               ondragenter="window.onRuleGroupColumnDragEnter(event, ${group.id})"
               ondragleave="window.onRuleGroupColumnDragLeave(event, ${group.id})"
               ondrop="window.onRuleGroupColumnDrop(event, ${group.id})"
               onclick="window.toggleGroupCollapse(${group.id})"
               title="Нажмите, чтобы развернуть колонку">
            <div class="rules-column-collapsed-strip"
                 draggable="true"
                 ondragstart="window.onRuleGroupColumnDragStart(event, ${group.id})"
                 ondragend="window.onRuleGroupColumnDragEnd(event)">
              <span class="rules-column-collapsed-dot ${dotColor}"></span>
              <span class="rules-column-collapsed-count">${presetsInGroup.length}</span>
              <span class="rules-column-collapsed-title">${escapeHtml(group.name)}</span>
            </div>
          </div>
        `;
      }

      const cardsHtml = presetsInGroup.map(p => buildKanbanRuleCard(p, group.id)).join('');

      return `
        <div class="rules-column"
             data-group-id="${group.id}"
             ondragover="window.onRuleGroupColumnDragOver(event, ${group.id})"
             ondragenter="window.onRuleGroupColumnDragEnter(event, ${group.id})"
             ondragleave="window.onRuleGroupColumnDragLeave(event, ${group.id})"
             ondrop="window.onRuleGroupColumnDrop(event, ${group.id})">
          <div class="rules-column-header"
               draggable="true"
               ondragstart="window.onRuleGroupColumnDragStart(event, ${group.id})"
               ondragend="window.onRuleGroupColumnDragEnd(event)">
            <div class="rules-column-title-wrap" onclick="window.openGroupMenuPopover(event, ${group.id})" title="Настройки группы" draggable="false">
              <span class="rules-column-dot ${dotColor}"></span>
              <span class="rules-column-title" title="${escapeHtml(group.name)}">${escapeHtml(group.name)}</span>
              <span class="rules-column-count">${presetsInGroup.length}</span>
            </div>
            <div class="rules-column-actions" draggable="false">
              <button class="rules-column-btn" title="Добавить правило в группу" onclick="window.openChooseRuleModal(${group.id})">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
              </button>
              <button class="rules-column-drag-handle"
                      type="button"
                      title="Переместить группу"
                      draggable="true"
                      ondragstart="window.onRuleGroupColumnDragStart(event, ${group.id})"
                      ondragend="window.onRuleGroupColumnDragEnd(event)">
                <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
                  <circle cx="5" cy="3" r="1.25"/>
                  <circle cx="11" cy="3" r="1.25"/>
                  <circle cx="5" cy="8" r="1.25"/>
                  <circle cx="11" cy="8" r="1.25"/>
                  <circle cx="5" cy="13" r="1.25"/>
                  <circle cx="11" cy="13" r="1.25"/>
                </svg>
              </button>
            </div>
          </div>
          <div class="rules-column-body"
               ondragover="window.onRuleColumnDragOver(event)"
               ondragleave="window.onRuleColumnDragLeave(event)"
               ondrop="window.onRuleColumnDrop(event, ${group.id})">
            ${cardsHtml || '<div class="rules-column-empty">Нет правил</div>'}
          </div>
          <div class="rules-column-footer">
            <button type="button" class="rules-column-add-calc-btn" onclick="window.openCreateRuleFromTab()">
              <span>+ Add calculation</span>
            </button>
          </div>
        </div>
      `;
    }).join('');

    // 3. Add Group Column Button (Attio dashed square [ + ])
    const addGroupColumnCard = `
      <button class="rules-add-column-btn" type="button" onclick="window.openAddColumnPopover(event)" title="Добавить группу правил">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
      </button>
    `;

    boardContainer.innerHTML = ungroupedColumnHtml + groupColumnsHtml + addGroupColumnCard;
  }

  // Group Collapse handler
  window.toggleGroupCollapse = function (groupId) {
    if (state.collapsedRuleGroups.has(groupId)) {
      state.collapsedRuleGroups.delete(groupId);
    } else {
      state.collapsedRuleGroups.add(groupId);
    }
    localStorage.setItem('buyerly_collapsed_rule_groups', JSON.stringify([...state.collapsedRuleGroups]));
    renderRulesTab();
  };

  // Group Menu Popover Logic
  let activePopoverGroupId = null;
  let activeNewColumnColor = 'purple';

  window.openGroupMenuPopover = function (event, groupId) {
    if (event) {
      event.stopPropagation();
      event.preventDefault();
    }
    const group = state.ruleGroups.find(g => g.id === groupId);
    if (!group) return;

    // Close any other open popovers
    document.getElementById('ruleAddColumnPopover')?.classList.add('hidden');

    activePopoverGroupId = groupId;
    const popover = document.getElementById('ruleGroupMenuPopover');
    const input = document.getElementById('ruleGroupPopoverNameInput');
    const dot = document.getElementById('ruleGroupPopoverDot');
    const collapseText = document.getElementById('popoverCollapseText');
    const palette = document.getElementById('ruleGroupColorPalette');

    if (!popover || !input || !dot) return;

    palette?.classList.add('hidden');
    input.value = group.name;

    const currentColor = state.ruleGroupColors[groupId] || 'purple';
    dot.className = `attio-popover-dot swatch-${currentColor}`;

    if (palette) {
      palette.querySelectorAll('.color-swatch').forEach(swatch => {
        swatch.classList.toggle('active', swatch.dataset.color === currentColor);
      });
    }

    if (collapseText) {
      collapseText.textContent = state.collapsedRuleGroups.has(groupId) ? 'Развернуть колонку' : 'Свернуть колонку';
    }

    const target = event.currentTarget || event.target;
    const rect = target.getBoundingClientRect();
    popover.style.top = `${rect.bottom + window.scrollY + 6}px`;
    popover.style.left = `${Math.max(10, Math.min(window.innerWidth - 265, rect.left + window.scrollX))}px`;
    popover.classList.remove('hidden');

    setTimeout(() => {
      input.focus();
      const len = input.value.length;
      input.setSelectionRange(len, len);
    }, 50);
  };

  window.hideCurrentGroupFromPopover = function () {
    if (!activePopoverGroupId) return;
    state.collapsedRuleGroups.add(activePopoverGroupId);
    localStorage.setItem('buyerly_collapsed_rule_groups', JSON.stringify([...state.collapsedRuleGroups]));
    document.getElementById('ruleGroupMenuPopover')?.classList.add('hidden');
    renderRulesTab();
  };

  window.saveGroupNameFromPopover = async function () {
    if (!activePopoverGroupId) return;
    const input = document.getElementById('ruleGroupPopoverNameInput');
    const newName = input?.value.trim();
    const group = state.ruleGroups.find(g => g.id === activePopoverGroupId);
    if (!group || !newName || newName === group.name) return;

    try {
      await apiRequest(`/api/rule-groups/${activePopoverGroupId}`, {
        method: 'PUT',
        body: JSON.stringify({
          name: newName,
          description: group.description || '',
          preset_ids: group.preset_ids || []
        })
      });
      group.name = newName;
      renderRulesTab();
    } catch (e) {
      showToast(`Ошибка сохранения имени: ${e.message}`, 'error');
    }
  };

  window.onGroupNameInputKeydown = function (event) {
    if (event.key === 'Enter') {
      event.preventDefault();
      window.saveGroupNameFromPopover();
      document.getElementById('ruleGroupMenuPopover')?.classList.add('hidden');
    } else if (event.key === 'Escape') {
      document.getElementById('ruleGroupMenuPopover')?.classList.add('hidden');
    }
  };

  window.toggleGroupColorPalette = function (event) {
    if (event) {
      event.stopPropagation();
      event.preventDefault();
    }
    document.getElementById('ruleGroupColorPalette')?.classList.toggle('hidden');
  };

  window.selectGroupColor = function (colorName) {
    if (!activePopoverGroupId) return;
    state.ruleGroupColors[activePopoverGroupId] = colorName;
    localStorage.setItem('buyerly_rule_group_colors', JSON.stringify(state.ruleGroupColors));
    const dot = document.getElementById('ruleGroupPopoverDot');
    if (dot) dot.className = `attio-popover-dot swatch-${colorName}`;
    const palette = document.getElementById('ruleGroupColorPalette');
    if (palette) {
      palette.querySelectorAll('.color-swatch').forEach(swatch => {
        swatch.classList.toggle('active', swatch.dataset.color === colorName);
      });
      palette.classList.add('hidden');
    }
    renderRulesTab();
  };

  window.toggleCollapseCurrentGroup = function () {
    if (!activePopoverGroupId) return;
    window.toggleGroupCollapse(activePopoverGroupId);
    document.getElementById('ruleGroupMenuPopover')?.classList.add('hidden');
  };

  window.deleteCurrentGroupFromPopover = async function () {
    if (!activePopoverGroupId) return;
    const id = activePopoverGroupId;
    document.getElementById('ruleGroupMenuPopover')?.classList.add('hidden');
    await window.deleteRuleGroup(id);
  };

  // Quick Add Column Popover Logic
  window.openAddColumnPopover = function (event) {
    if (event) {
      event.stopPropagation();
      event.preventDefault();
    }
    document.getElementById('ruleGroupMenuPopover')?.classList.add('hidden');

    const popover = document.getElementById('ruleAddColumnPopover');
    const input = document.getElementById('newColumnPopoverNameInput');
    const dot = document.getElementById('newColumnPopoverDot');
    const palette = document.getElementById('newColumnColorPalette');

    if (!popover || !input || !dot) return;

    activeNewColumnColor = 'purple';
    dot.className = 'attio-popover-dot swatch-purple';
    palette?.classList.add('hidden');
    if (palette) {
      palette.querySelectorAll('.color-swatch').forEach(swatch => {
        swatch.classList.toggle('active', swatch.dataset.color === 'purple');
      });
    }
    input.value = '';

    const target = event.currentTarget || event.target;
    const rect = target.getBoundingClientRect();
    popover.style.top = `${rect.top + window.scrollY}px`;
    popover.style.left = `${Math.max(10, Math.min(window.innerWidth - 265, rect.left + window.scrollX))}px`;
    popover.classList.remove('hidden');

    setTimeout(() => input.focus(), 50);
  };

  window.closeAddColumnPopover = function () {
    document.getElementById('ruleAddColumnPopover')?.classList.add('hidden');
  };

  window.toggleNewColumnColorPalette = function (event) {
    if (event) {
      event.stopPropagation();
      event.preventDefault();
    }
    document.getElementById('newColumnColorPalette')?.classList.toggle('hidden');
  };

  window.selectNewColumnColor = function (colorName) {
    activeNewColumnColor = colorName;
    const dot = document.getElementById('newColumnPopoverDot');
    if (dot) dot.className = `attio-popover-dot swatch-${colorName}`;
    const palette = document.getElementById('newColumnColorPalette');
    if (palette) {
      palette.querySelectorAll('.color-swatch').forEach(swatch => {
        swatch.classList.toggle('active', swatch.dataset.color === colorName);
      });
      palette.classList.add('hidden');
    }
  };

  window.onNewColumnNameKeydown = function (event) {
    if (event.key === 'Enter') {
      event.preventDefault();
      window.submitNewColumnFromPopover();
    } else if (event.key === 'Escape') {
      window.closeAddColumnPopover();
    }
  };

  window.submitNewColumnFromPopover = async function () {
    const input = document.getElementById('newColumnPopoverNameInput');
    const name = input?.value.trim();
    if (!name) {
      showToast('Введите название группы', 'error');
      input?.focus();
      return;
    }
    try {
      const created = await apiRequest('/api/rule-groups', {
        method: 'POST',
        body: JSON.stringify({ name, description: '', preset_ids: [] })
      });
      if (created && created.id) {
        state.ruleGroupColors[created.id] = activeNewColumnColor;
        localStorage.setItem('buyerly_rule_group_colors', JSON.stringify(state.ruleGroupColors));
      }
      showToast(`Группа «${name}» создана`, 'success');
      window.closeAddColumnPopover();
      await loadRuleGroups();
      renderRulesTab();
    } catch (e) {
      showToast(`Ошибка создания группы: ${e.message}`, 'error');
    }
  };

  // Choose Rule Modal (Photo 4)
  window.openChooseRuleModal = function (targetGroupId = null) {
    haptic('selection');
    state.chooseRuleTargetGroupId = targetGroupId !== null && targetGroupId !== undefined ? Number(targetGroupId) : null;
    state.chooseRuleSelectedIndex = 0;

    const group = targetGroupId !== null ? state.ruleGroups.find(g => g.id === Number(targetGroupId)) : null;
    const badge = document.getElementById('chooseRuleTargetGroupBadge');
    if (badge) {
      if (group) {
        badge.textContent = group.name;
        badge.classList.remove('hidden');
      } else {
        badge.classList.add('hidden');
      }
    }

    const searchInput = document.getElementById('chooseRuleSearchInput');
    if (searchInput) searchInput.value = '';

    window.renderChooseRuleList('');
    window.openModal('modalChooseRule');
    setTimeout(() => searchInput?.focus(), 50);
  };

  window.onChooseRuleSearchInput = function (query) {
    window.renderChooseRuleList(query);
  };

  window.renderChooseRuleList = function (query = '') {
    const container = document.getElementById('chooseRuleList');
    if (!container) return;

    const q = (query || '').toLowerCase().trim();
    state.chooseRuleFilteredList = state.presets.filter(p => {
      if (!q) return true;
      const nameMatch = (p.name || '').toLowerCase().includes(q);
      const actMatch = (p.action || '').toLowerCase().includes(q);
      return nameMatch || actMatch;
    });

    if (state.chooseRuleSelectedIndex >= state.chooseRuleFilteredList.length) {
      state.chooseRuleSelectedIndex = Math.max(0, state.chooseRuleFilteredList.length - 1);
    }

    const actionBadgeMap = {
      'turn_off': { label: 'Стоп', class: 'rule-action-turn_off' },
      'notify_only': { label: 'Пуш', class: 'rule-action-notify_only' },
      'turn_on': { label: 'Старт', class: 'rule-action-turn_on' },
      'increase_budget': { label: '+Бюджет', class: 'rule-action-increase_budget' },
      'decrease_budget': { label: '-Бюджет', class: 'rule-action-decrease_budget' }
    };

    if (state.chooseRuleFilteredList.length === 0) {
      container.innerHTML = `
        <div style="text-align:center; padding: 18px 10px; color: var(--text-muted); font-size: 12.5px;">
          Правил не найдено. Нажмите «Создать новое правило» ниже.
        </div>
      `;
      return;
    }

    container.innerHTML = state.chooseRuleFilteredList.map((preset, idx) => {
      const act = actionBadgeMap[preset.action] || { label: preset.action, class: '' };
      const isSelected = idx === state.chooseRuleSelectedIndex;

      const parentGroups = state.ruleGroups.filter(g => (g.preset_ids || []).includes(preset.id));
      const groupNames = parentGroups.map(g => g.name).join(', ');
      const subInfo = groupNames ? `Группа: ${escapeHtml(groupNames)}` : 'Без группы';

      return `
        <div class="choose-rule-item ${isSelected ? 'selected' : ''}" data-index="${idx}" onclick="window.selectAndConfirmRule(${preset.id})">
          <div class="choose-rule-item-left">
            <div class="choose-rule-item-icon">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"></path><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path></svg>
            </div>
            <div class="choose-rule-item-info">
              <span class="choose-rule-item-name">${escapeHtml(preset.name)}</span>
              <span class="choose-rule-item-sub">${subInfo} · ${(preset.conditions || []).length} усл. · ${preset.check_interval_minutes || 5}м</span>
            </div>
          </div>
          <div class="choose-rule-item-right">
            <span class="rule-action-badge ${act.class}">${act.label}</span>
          </div>
        </div>
      `;
    }).join('');
  };

  window.onChooseRuleKeydown = function (event) {
    const list = state.chooseRuleFilteredList || [];
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      if (list.length > 0) {
        state.chooseRuleSelectedIndex = (state.chooseRuleSelectedIndex + 1) % list.length;
        window.renderChooseRuleList(document.getElementById('chooseRuleSearchInput')?.value || '');
      }
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      if (list.length > 0) {
        state.chooseRuleSelectedIndex = (state.chooseRuleSelectedIndex - 1 + list.length) % list.length;
        window.renderChooseRuleList(document.getElementById('chooseRuleSearchInput')?.value || '');
      }
    } else if (event.key === 'Enter') {
      event.preventDefault();
      window.confirmSelectedRuleChoice();
    } else if (event.key === 'Escape') {
      window.closeModal('modalChooseRule');
    }
  };

  window.selectAndConfirmRule = async function (presetId) {
    const targetGroupId = state.chooseRuleTargetGroupId;
    window.closeModal('modalChooseRule');

    if (targetGroupId !== null) {
      const sourceGroup = state.ruleGroups.find(g => (g.preset_ids || []).includes(presetId));
      const sourceGroupId = sourceGroup ? sourceGroup.id : null;
      await window.movePresetToGroup(presetId, sourceGroupId, targetGroupId);
    } else {
      window.editPresetFromTab(presetId);
    }
  };

  window.confirmSelectedRuleChoice = async function () {
    const list = state.chooseRuleFilteredList || [];
    if (list.length > 0 && list[state.chooseRuleSelectedIndex]) {
      const chosenPreset = list[state.chooseRuleSelectedIndex];
      await window.selectAndConfirmRule(chosenPreset.id);
    } else {
      window.openCreateRuleFromChooser();
    }
  };

  // ==========================================================
  // ATTIO CREATE RULE CONTROLLER (MODAL 2)
  // ==========================================================
  window.openCreateRuleFromChooser = function () {
    const targetGroupId = state.chooseRuleTargetGroupId;
    window.closeModal('modalChooseRule');
    window.openCreateRuleModal(targetGroupId);
  };

  window.openCreateRuleForGroup = function (targetGroupId = null) {
    window.openCreateRuleModal(targetGroupId);
  };

  window.openCreateRuleModal = function (targetGroupId = null) {
    haptic('selection');
    state.createRuleTargetGroupId = targetGroupId !== null && targetGroupId !== undefined ? Number(targetGroupId) : null;
    
    // Reset form fields
    const nameInput = document.getElementById('createRuleNameInput');
    if (nameInput) nameInput.value = '';
    
    window.populateCreateRuleGroupSelect(state.createRuleTargetGroupId);
    
    const actionSelect = document.getElementById('createRuleActionSelect');
    if (actionSelect) actionSelect.value = 'turn_off';
    window.onCreateRuleActionChange('turn_off');
    
    const budgetPercent = document.getElementById('createRuleBudgetPercentInput');
    if (budgetPercent) budgetPercent.value = 20;
    const budgetCeiling = document.getElementById('createRuleBudgetCeilingInput');
    if (budgetCeiling) budgetCeiling.value = 0;
    
    window.setCreateRuleLogic('and');
    
    // Cooldown & Telegram
    const cooldownSelect = document.getElementById('createRuleCooldownSelect');
    if (cooldownSelect) cooldownSelect.value = '0';
    const customCooldown = document.getElementById('createRuleCustomCooldownInput');
    if (customCooldown) {
      customCooldown.classList.add('hidden');
      customCooldown.value = '';
    }
    const notifyToggle = document.getElementById('createRuleNotifyTgToggle');
    if (notifyToggle) notifyToggle.checked = true;

    // Reset More options spoiler
    const moreBody = document.getElementById('createRuleMoreBody');
    const moreArrow = document.getElementById('createRuleMoreArrow');
    if (moreBody) moreBody.classList.add('hidden');
    if (moreArrow) moreArrow.classList.remove('open');

    // Default condition: Spend >= 2.0 (today)
    const container = document.getElementById('createRuleConditionsList');
    if (container) container.innerHTML = '';
    window.addCreateRuleConditionRow('spend', 'gte', 2.0, 'today');

    window.renderCreateRuleDraftSummary();
    window.openModal('modalCreateRule');
    setTimeout(() => nameInput?.focus(), 50);
  };

  window.backToRuleChooser = function () {
    window.closeModal('modalCreateRule');
    window.openChooseRuleModal(state.chooseRuleTargetGroupId || state.createRuleTargetGroupId);
  };

  window.onCreateRuleOverlayClick = function (event) {
    if (event.target.id === 'modalCreateRule') {
      window.closeModal('modalCreateRule');
    }
  };

  window.onChooseRuleOverlayClick = function (event) {
    if (event.target.id === 'modalChooseRule') {
      window.closeModal('modalChooseRule');
    }
  };

  window.populateCreateRuleGroupSelect = function (selectedGroupId = null) {
    const select = document.getElementById('createRuleGroupSelect');
    if (!select) return;
    
    let html = '<option value="">Без группы</option>';
    (state.ruleGroups || []).forEach(g => {
      const isSel = selectedGroupId !== null && Number(selectedGroupId) === g.id;
      html += `<option value="${g.id}" ${isSel ? 'selected' : ''}>${escapeHtml(g.name)}</option>`;
    });
    select.innerHTML = html;
  };

  window.onCreateRuleActionChange = function (action) {
    const budgetSection = document.getElementById('createRuleBudgetSection');
    const ceilingField = document.getElementById('createRuleBudgetCeilingField');
    if (action === 'increase_budget' || action === 'decrease_budget') {
      budgetSection?.classList.remove('hidden');
      if (ceilingField) {
        ceilingField.style.display = action === 'increase_budget' ? 'flex' : 'none';
      }
    } else {
      budgetSection?.classList.add('hidden');
    }
    window.renderCreateRuleDraftSummary();
  };

  window.setCreateRuleLogic = function (logic = 'and') {
    document.querySelectorAll('#createRuleLogicGroup .attio-logic-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.logic === (logic || 'and'));
    });
    window.renderCreateRuleDraftSummary();
  };

  window.getCreateRuleLogic = function () {
    const activeBtn = document.querySelector('#createRuleLogicGroup .attio-logic-btn.active');
    return activeBtn?.dataset.logic || 'and';
  };

  window.toggleCreateRuleMoreSettings = function () {
    const moreBody = document.getElementById('createRuleMoreBody');
    const moreArrow = document.getElementById('createRuleMoreArrow');
    if (!moreBody || !moreArrow) return;
    const isHidden = moreBody.classList.contains('hidden');
    moreBody.classList.toggle('hidden', !isHidden);
    moreArrow.classList.toggle('open', isHidden);
  };

  window.onCreateRuleCooldownChange = function (val) {
    const customInput = document.getElementById('createRuleCustomCooldownInput');
    if (val === 'custom') {
      customInput?.classList.remove('hidden');
      customInput?.focus();
    } else {
      customInput?.classList.add('hidden');
      if (customInput) customInput.value = '';
    }
    window.renderCreateRuleDraftSummary();
  };

  window.getCreateRuleCooldownFromUI = function () {
    const select = document.getElementById('createRuleCooldownSelect');
    const customInput = document.getElementById('createRuleCustomCooldownInput');
    if (!select) return 0;
    if (select.value === 'custom') {
      return parseInt(customInput?.value) || 0;
    }
    return parseInt(select.value) || 0;
  };

  window.addCreateRuleConditionRow = function (metric = 'spend', operator = 'gte', value = '2.0', timeWindow = 'today') {
    haptic('selection');
    const container = document.getElementById('createRuleConditionsList');
    if (!container) return;

    const row = document.createElement('div');
    row.className = 'attio-cond-row';
    row.innerHTML = `
      <select class="attio-cond-metric attio-field-select" onchange="window.renderCreateRuleDraftSummary()">
        <option value="spend" ${metric === 'spend' ? 'selected' : ''}>Спенд (валюта кабинета)</option>
        <option value="cpl" ${metric === 'cpl' ? 'selected' : ''}>CPL (Цена лида)</option>
        <option value="cpreg" ${metric === 'cpreg' ? 'selected' : ''}>CPReg (Цена регистрации)</option>
        <option value="cpp" ${metric === 'cpp' ? 'selected' : ''}>CPP (Цена покупки)</option>
        <option value="leads" ${metric === 'leads' ? 'selected' : ''}>Лиды (шт)</option>
        <option value="registrations" ${metric === 'registrations' ? 'selected' : ''}>Регистрации (шт)</option>
        <option value="purchases" ${metric === 'purchases' ? 'selected' : ''}>Покупки (шт)</option>
        <option value="ctr" ${metric === 'ctr' ? 'selected' : ''}>CTR All (%)</option>
        <option value="cpc" ${metric === 'cpc' ? 'selected' : ''}>CPC All (валюта кабинета)</option>
      </select>
      <select class="attio-cond-op attio-field-select" onchange="window.renderCreateRuleDraftSummary()">
        <option value="gt" ${operator === 'gt' ? 'selected' : ''}>&gt; (больше)</option>
        <option value="gte" ${operator === 'gte' ? 'selected' : ''}>&ge; (не меньше)</option>
        <option value="lt" ${operator === 'lt' ? 'selected' : ''}>&lt; (меньше)</option>
        <option value="lte" ${operator === 'lte' ? 'selected' : ''}>&le; (не больше)</option>
        <option value="eq" ${operator === 'eq' ? 'selected' : ''}>= (равно)</option>
      </select>
      <input type="number" class="attio-cond-val attio-field-input text-center" placeholder="0.0" step="0.5" min="0" inputmode="decimal" value="${value}" oninput="window.renderCreateRuleDraftSummary()">
      <select class="attio-cond-win attio-field-select" onchange="window.renderCreateRuleDraftSummary()">
        <option value="today" ${timeWindow === 'today' ? 'selected' : ''}>Сегодня</option>
        <option value="yesterday" ${timeWindow === 'yesterday' ? 'selected' : ''}>Вчера</option>
        <option value="last_3d" ${timeWindow === 'last_3d' ? 'selected' : ''}>3 дня</option>
        <option value="last_7d" ${timeWindow === 'last_7d' ? 'selected' : ''}>7 дней</option>
      </select>
      <button type="button" class="attio-cond-del-btn" onclick="window.removeCreateRuleConditionRow(this)" title="Удалить условие">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
      </button>
    `;
    container.appendChild(row);
    window.renderCreateRuleDraftSummary();
  };

  window.removeCreateRuleConditionRow = function (button) {
    haptic('selection');
    const container = document.getElementById('createRuleConditionsList');
    button?.closest('.attio-cond-row')?.remove();
    if (container && container.children.length === 0) {
      window.addCreateRuleConditionRow('spend', 'gte', 2.0, 'today');
    } else {
      window.renderCreateRuleDraftSummary();
    }
  };

  window.getCreateRuleConditionsFromUI = function () {
    const rows = document.querySelectorAll('#createRuleConditionsList .attio-cond-row');
    const conditions = [];
    rows.forEach(r => {
      const metric = r.querySelector('.attio-cond-metric')?.value || 'spend';
      const operator = r.querySelector('.attio-cond-op')?.value || 'gte';
      const valInput = r.querySelector('.attio-cond-val')?.value;
      const timeWindow = r.querySelector('.attio-cond-win')?.value || 'today';
      const value = parseFloat(valInput);
      if (!isNaN(value)) {
        conditions.push({ metric, operator, value, time_window: timeWindow });
      }
    });
    return conditions;
  };

  window.renderCreateRuleDraftSummary = function () {
    const textElement = document.getElementById('createRulePlainText');
    const errorsElement = document.getElementById('createRuleValidationErrors');
    const saveButton = document.getElementById('btnSubmitCreateRule');
    if (!textElement || !errorsElement) return [];

    const action = document.getElementById('createRuleActionSelect')?.value || 'turn_off';
    const logic = window.getCreateRuleLogic();
    const conditions = window.getCreateRuleConditionsFromUI();
    const budgetPercent = parseFloat(document.getElementById('createRuleBudgetPercentInput')?.value) || 0;
    const budgetCeiling = parseFloat(document.getElementById('createRuleBudgetCeilingInput')?.value) || 0;

    const plainText = buildPlainRuleTextFromValues(action, logic, conditions, budgetPercent, budgetCeiling);
    textElement.textContent = plainText;
    textElement.dataset.plainName = fitPlainRuleName(plainText);

    const errors = [];
    if (conditions.length === 0) {
      errors.push('Добавьте хотя бы одно корректное условие.');
    }
    if (conditions.some(c => ['leads', 'registrations', 'purchases'].includes(c.metric) && !Number.isInteger(c.value))) {
      errors.push('Лиды, регистрации и покупки указываются только целыми числами.');
    }
    if ((action === 'increase_budget' || action === 'decrease_budget') && (budgetPercent <= 0 || budgetPercent > 100)) {
      errors.push('Изменение бюджета должно быть от 1% до 100%.');
    }
    if (action === 'increase_budget' && (budgetCeiling <= 0 || budgetCeiling > 10000000)) {
      errors.push('Для увеличения бюджета укажите безопасный дневной потолок (> 0).');
    }

    errorsElement.innerHTML = errors.map(e => `
      <div class="attio-val-item error">
        <svg width="12" height="12" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
        <span>${escapeHtml(e)}</span>
      </div>
    `).join('');

    if (saveButton) saveButton.disabled = errors.length > 0;
    return errors;
  };

  window.useCreateRulePlainName = function () {
    const input = document.getElementById('createRuleNameInput');
    const name = document.getElementById('createRulePlainText')?.dataset.plainName || '';
    if (input && name) {
      input.value = name;
      input.focus();
      showToast('Понятное название вставлено', 'success');
    }
  };

  window.submitCreateRule = async function () {
    const nameInput = document.getElementById('createRuleNameInput');
    const actionSelect = document.getElementById('createRuleActionSelect');
    const groupSelect = document.getElementById('createRuleGroupSelect');
    const saveButton = document.getElementById('btnSubmitCreateRule');

    const name = nameInput?.value.trim() || document.getElementById('createRulePlainText')?.dataset.plainName || 'Новое правило';
    const action = actionSelect?.value || 'turn_off';
    const selectedGroupId = groupSelect?.value ? Number(groupSelect.value) : null;
    const logic = window.getCreateRuleLogic();
    const conditions = window.getCreateRuleConditionsFromUI();
    const cooldownMins = window.getCreateRuleCooldownFromUI();
    const notifyTg = document.getElementById('createRuleNotifyTgToggle')?.checked !== false;
    const budgetPercent = parseFloat(document.getElementById('createRuleBudgetPercentInput')?.value) || 0;
    const budgetCeiling = parseFloat(document.getElementById('createRuleBudgetCeilingInput')?.value) || 0;

    const errors = window.renderCreateRuleDraftSummary();
    if (errors && errors.length > 0) {
      showToast(errors[0], 'error');
      return;
    }

    const payload = {
      name,
      action,
      conditions,
      condition_logic: logic,
      cooldown_minutes: cooldownMins,
      check_interval_minutes: 5,
      notify_tg: notifyTg,
      budget_change_percent: (action === 'increase_budget' || action === 'decrease_budget') ? budgetPercent : 0.0,
      budget_max_daily: action === 'increase_budget' ? budgetCeiling : 0.0
    };

    if (saveButton) saveButton.disabled = true;
    try {
      const newPreset = await apiRequest('/api/presets', {
        method: 'POST',
        body: JSON.stringify(payload)
      });

      if (selectedGroupId && newPreset && newPreset.id) {
        const group = state.ruleGroups.find(g => g.id === selectedGroupId);
        if (group) {
          const currentIds = group.preset_ids || [];
          if (!currentIds.includes(newPreset.id)) {
            await apiRequest(`/api/rule-groups/${selectedGroupId}`, {
              method: 'PUT',
              body: JSON.stringify({
                name: group.name,
                description: group.description || '',
                preset_ids: [...currentIds, newPreset.id]
              })
            });
          }
        }
      }

      haptic('notification', 'success');
      showToast(`Правило «${newPreset.name || name}» успешно создано!`, 'success');
      window.closeModal('modalCreateRule');

      await Promise.all([loadPresets(), loadRuleGroups(), loadAccounts()]);
      if (state.activeTab === 'rules') renderRulesTab();
    } catch (err) {
      showToast(`Ошибка создания правила: ${err.message}`, 'error');
    } finally {
      if (saveButton) saveButton.disabled = false;
    }
  };

  window.populateRuleGroupSelect = function (selectedGroupId = null) {
    const select = document.getElementById('ruleGroupSelect');
    if (!select) return;
    select.innerHTML = '<option value="">Без группы</option>' + state.ruleGroups.map(g => {
      const isSel = selectedGroupId !== null && Number(selectedGroupId) === g.id;
      return `<option value="${g.id}" ${isSel ? 'selected' : ''}>${escapeHtml(g.name)}</option>`;
    }).join('');
  };

  // Drag & Drop handlers for rules Kanban board
  let draggedRuleGroupId = null;

  window.onRuleGroupColumnDragStart = function (event, groupId) {
    if (event.target.closest('button:not(.rules-column-drag-handle), .rules-column-title-wrap')) {
      if (!event.target.closest('.rules-column-drag-handle')) {
        event.preventDefault();
        return;
      }
    }
    draggedRuleGroupId = Number(groupId);
    try {
      event.dataTransfer.setData('application/x-rule-group-id', String(groupId));
      event.dataTransfer.setData('text/plain', `rule-group-${groupId}`);
      event.dataTransfer.effectAllowed = 'move';
    } catch (e) {}

    const col = event.currentTarget.closest('.rules-column');
    if (col) {
      setTimeout(() => {
        if (draggedRuleGroupId !== null) {
          col.classList.add('column-dragging');
        }
      }, 0);
    }
  };

  window.onRuleGroupColumnDragEnd = function (event) {
    draggedRuleGroupId = null;
    document.querySelectorAll('.rules-column').forEach(el => {
      el.classList.remove('column-dragging', 'drag-over-left', 'drag-over-right');
    });
  };

  window.onRuleGroupColumnDragOver = function (event, targetGroupId) {
    if (!draggedRuleGroupId || draggedRuleGroupId === Number(targetGroupId)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';

    const col = event.currentTarget.closest('.rules-column');
    if (!col) return;

    const rect = col.getBoundingClientRect();
    const midX = rect.left + rect.width / 2;
    if (event.clientX < midX) {
      col.classList.add('drag-over-left');
      col.classList.remove('drag-over-right');
    } else {
      col.classList.add('drag-over-right');
      col.classList.remove('drag-over-left');
    }
  };

  window.onRuleGroupColumnDragEnter = function (event, targetGroupId) {
    if (!draggedRuleGroupId || draggedRuleGroupId === Number(targetGroupId)) return;
    event.preventDefault();
  };

  window.onRuleGroupColumnDragLeave = function (event, targetGroupId) {
    const col = event.currentTarget.closest('.rules-column');
    if (col && !col.contains(event.relatedTarget)) {
      col.classList.remove('drag-over-left', 'drag-over-right');
    }
  };

  window.onRuleGroupColumnDrop = async function (event, targetGroupId) {
    if (!draggedRuleGroupId) return;
    event.preventDefault();
    event.stopPropagation();

    const fromId = Number(draggedRuleGroupId);
    const toId = Number(targetGroupId);

    const col = event.currentTarget.closest('.rules-column');
    const isRight = col ? col.classList.contains('drag-over-right') : false;

    document.querySelectorAll('.rules-column').forEach(el => {
      el.classList.remove('column-dragging', 'drag-over-left', 'drag-over-right');
    });

    draggedRuleGroupId = null;

    if (fromId === toId) return;

    const fromIdx = state.ruleGroups.findIndex(g => g.id === fromId);
    let toIdx = state.ruleGroups.findIndex(g => g.id === toId);

    if (fromIdx === -1 || toIdx === -1) return;

    const [movedGroup] = state.ruleGroups.splice(fromIdx, 1);
    toIdx = state.ruleGroups.findIndex(g => g.id === toId);
    const insertIdx = isRight ? toIdx + 1 : toIdx;
    state.ruleGroups.splice(insertIdx, 0, movedGroup);

    renderRulesTab();

    try {
      const groupIds = state.ruleGroups.map(g => g.id);
      const updated = await apiRequest('/api/rule-groups/reorder', {
        method: 'PUT',
        body: JSON.stringify({ group_ids: groupIds })
      });
      if (Array.isArray(updated) && updated.length > 0) {
        state.ruleGroups = updated;
      }
    } catch (err) {
      console.error('Failed to save rule groups order:', err);
      showToast(`Ошибка сохранения порядка групп: ${err.message}`, 'error');
      await loadRuleGroups();
      renderRulesTab();
    }
  };

  window.onRuleDragStart = function (event, presetId, sourceGroupId) {
    draggedRuleInfo = {
      presetId: Number(presetId),
      sourceGroupId: sourceGroupId !== null ? Number(sourceGroupId) : null
    };
    try {
      event.dataTransfer.setData('text/plain', JSON.stringify(draggedRuleInfo));
      event.dataTransfer.effectAllowed = 'move';
    } catch (e) {}
    event.currentTarget.classList.add('dragging');
  };

  window.onRuleDragEnd = function (event) {
    event.currentTarget.classList.remove('dragging');
    draggedRuleInfo = null;
    document.querySelectorAll('.rules-column-body').forEach(el => el.classList.remove('drop-target-active'));
  };

  window.onRuleColumnDragOver = function (event) {
    if (draggedRuleGroupId) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    const body = event.currentTarget.classList.contains('rules-column-body') ? event.currentTarget : event.currentTarget.querySelector('.rules-column-body');
    if (body && !body.classList.contains('drop-target-active')) {
      body.classList.add('drop-target-active');
    }
  };

  window.onRuleColumnDragLeave = function (event) {
    const body = event.currentTarget.classList.contains('rules-column-body') ? event.currentTarget : event.currentTarget.querySelector('.rules-column-body');
    if (body && !body.contains(event.relatedTarget)) {
      body.classList.remove('drop-target-active');
    }
  };

  window.onRuleColumnDrop = async function (event, targetGroupId) {
    event.preventDefault();
    document.querySelectorAll('.rules-column-body').forEach(el => el.classList.remove('drop-target-active'));

    let info = draggedRuleInfo;
    if (!info) {
      try {
        const raw = event.dataTransfer.getData('text/plain');
        if (raw) info = JSON.parse(raw);
      } catch (e) {}
    }
    if (!info || !info.presetId) return;

    const presetId = Number(info.presetId);
    const sourceGroupId = info.sourceGroupId !== null ? Number(info.sourceGroupId) : null;
    const targetId = targetGroupId !== null ? Number(targetGroupId) : null;

    if (sourceGroupId === targetId) return;

    await window.movePresetToGroup(presetId, sourceGroupId, targetId);
  };

  window.movePresetToGroup = async function (presetId, sourceGroupId, targetGroupId) {
    const preset = state.presets.find(p => p.id === presetId);
    const presetName = preset ? preset.name : `Правило #${presetId}`;

    try {
      if (sourceGroupId !== null) {
        const srcGroup = state.ruleGroups.find(g => g.id === sourceGroupId);
        if (srcGroup) {
          const newPresetIds = (srcGroup.preset_ids || []).filter(id => id !== presetId);
          await apiRequest(`/api/rule-groups/${sourceGroupId}`, {
            method: 'PUT',
            body: JSON.stringify({
              name: srcGroup.name,
              description: srcGroup.description || '',
              preset_ids: newPresetIds
            })
          });
          srcGroup.preset_ids = newPresetIds;
        }
      }

      if (targetGroupId !== null) {
        const tgtGroup = state.ruleGroups.find(g => g.id === targetGroupId);
        if (tgtGroup) {
          const currentIds = tgtGroup.preset_ids || [];
          if (!currentIds.includes(presetId)) {
            const newPresetIds = [...currentIds, presetId];
            await apiRequest(`/api/rule-groups/${targetGroupId}`, {
              method: 'PUT',
              body: JSON.stringify({
                name: tgtGroup.name,
                description: tgtGroup.description || '',
                preset_ids: newPresetIds
              })
            });
            tgtGroup.preset_ids = newPresetIds;
          }
        }
      }

      const destName = targetGroupId !== null
        ? `группу «${(state.ruleGroups.find(g => g.id === targetGroupId)?.name || 'Группа')}»`
        : 'колонку «Без группы»';

      showToast(`«${presetName}» перемещено в ${destName}`, 'success');
      await loadRuleGroups();
      renderRulesTab();
    } catch (err) {
      showToast(`Ошибка перемещения: ${err.message}`, 'error');
      await loadRuleGroups();
      renderRulesTab();
    }
  };

  // ==========================================================
  // ATTIO RULE RECORD SCREEN (EMBEDDED FULL-PAGE VIEW / PHOTO 1)
  // ==========================================================
  state.currentRecordPresetId = null;
  state.recordActiveTab = 'overview';

  window.editPresetFromTab = function (presetId) {
    window.openRuleRecordPage(presetId);
  };

  window.openRuleRecordOverlay = function (presetId) {
    window.openRuleRecordPage(presetId);
  };

  window.closeRuleRecordPage = function (historyMode = 'push') {
    haptic('selection');
    state.currentRecordPresetId = null;
    const kanbanView = document.getElementById('rulesKanbanView');
    const recordView = document.getElementById('rulesRecordView');
    if (recordView) recordView.classList.add('hidden');
    if (kanbanView) kanbanView.classList.remove('hidden');

    syncBrowserRoute('rules', historyMode);

    const breadcrumbArea = document.getElementById('headerBreadcrumbArea');
    if (breadcrumbArea) {
      breadcrumbArea.innerHTML = `<div class="breadcrumb-title"><svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor" style="margin-right:6px;"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/></svg><span>Правила</span></div>`;
    }
    document.title = 'Правила — Buyerly';
  };

  window.openRuleRecordPage = async function (presetId, historyMode = 'push') {
    haptic('selection');
    state.currentRecordPresetId = Number(presetId);
    
    // Ensure tab-rules is visible
    if (state.activeTab !== 'rules') {
      state.activeTab = 'rules';
      document.querySelectorAll('.tab-content').forEach(section => {
        section.classList.toggle('active', section.id === 'tab-rules');
      });
      window.updateSidebarActiveState();
    }

    const kanbanView = document.getElementById('rulesKanbanView');
    const recordView = document.getElementById('rulesRecordView');
    if (kanbanView) kanbanView.classList.add('hidden');
    if (recordView) recordView.classList.remove('hidden');

    let preset = state.presets.find(p => p.id === Number(presetId));
    if (!preset) {
      await loadPresets();
      preset = state.presets.find(p => p.id === Number(presetId));
    }
    if (!preset) {
      showToast('Правило не найдено', 'error');
      window.closeRuleRecordPage('none');
      return;
    }

    syncBrowserRoute('rules', historyMode);

    // Update Header Breadcrumbs
    const breadcrumbArea = document.getElementById('headerBreadcrumbArea');
    if (breadcrumbArea) {
      breadcrumbArea.innerHTML = `
        <div class="breadcrumb-title" style="display:flex; align-items:center; gap:6px;">
          <span style="cursor:pointer; color:var(--text-muted);" onclick="window.closeRuleRecordPage()">Правила</span>
          <span style="color:var(--text-muted);">/</span>
          <span>${escapeHtml(preset.name || 'Правило')}</span>
        </div>
      `;
    }
    document.title = `${preset.name || 'Правило'} — Buyerly`;

    // Determine current group & sibling rules for navigation
    const parentGroup = state.ruleGroups.find(g => (g.preset_ids || []).includes(preset.id));
    const groupName = parentGroup ? parentGroup.name : 'Без группы';
    const groupPresets = parentGroup
      ? (parentGroup.preset_ids || []).map(id => state.presets.find(p => p.id === id)).filter(Boolean)
      : state.presets;

    const currentIndex = groupPresets.findIndex(p => p.id === preset.id);
    const totalCount = groupPresets.length || 1;
    const posIndex = currentIndex >= 0 ? currentIndex + 1 : 1;

    // Navigation arrows & pill
    const pill = document.getElementById('recordNavPositionPill');
    if (pill) {
      pill.innerHTML = `${posIndex} из ${totalCount} в rules &bull; <span style="font-weight:600;">${escapeHtml(groupName)}</span>`;
    }
    const btnPrev = document.getElementById('btnPrevRecord');
    const btnNext = document.getElementById('btnNextRecord');
    if (btnPrev) btnPrev.disabled = currentIndex <= 0;
    if (btnNext) btnNext.disabled = currentIndex < 0 || currentIndex >= totalCount - 1;

    // Title & Icon
    const titleText = document.getElementById('recordTitleText');
    const titleInput = document.getElementById('recordTitleInput');
    const titleDisplay = document.getElementById('recordTitleDisplay');
    if (titleText) titleText.textContent = preset.name || 'Без названия';
    if (titleInput) {
      titleInput.value = preset.name || '';
      titleInput.classList.add('hidden');
    }
    if (titleDisplay) titleDisplay.classList.remove('hidden');

    const avatarIcon = document.getElementById('recordAvatarIcon');
    if (avatarIcon) {
      const actionIcons = {
        'turn_off': '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="6" y="4" width="4" height="16"></rect><rect x="14" y="4" width="4" height="16"></rect></svg>',
        'notify_only': '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>',
        'turn_on': '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>',
        'increase_budget': '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"></polyline><polyline points="17 6 23 6 23 12"></polyline></svg>',
        'decrease_budget': '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 18 13.5 8.5 8.5 13.5 1 6"></polyline><polyline points="17 18 23 18 23 12"></polyline></svg>'
      };
      avatarIcon.innerHTML = actionIcons[preset.action] || '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>';
    }

    // Populate Left Column: Record Details
    const detailsGrid = document.getElementById('recordDetailsGrid');
    if (detailsGrid) {
      const actionLabels = {
        'turn_off': 'Выключить адсет (STOP)',
        'notify_only': 'Прислать уведомление (NOTIFY)',
        'turn_on': 'Включить адсет (START)',
        'increase_budget': `+${preset.budget_change_percent || 20}% к бюджету`,
        'decrease_budget': `-${preset.budget_change_percent || 20}% от бюджета`
      };

      const metricLabels = {
        spend: 'Спенд', cpl: 'CPL', cpreg: 'CPReg', cpp: 'CPP',
        leads: 'Лиды', registrations: 'Регистрации', purchases: 'Покупки',
        ctr: 'CTR', cpc: 'CPC'
      };
      const opLabels = { gt: '>', gte: '≥', lt: '<', lte: '≤', eq: '=' };
      const winLabels = { today: 'сегодня', yesterday: 'вчера', last_3d: '3 дня', last_7d: '7 дней' };

      const conditionsFormatted = (preset.conditions || []).map(c => {
        const m = metricLabels[c.metric] || c.metric;
        const op = opLabels[c.operator] || c.operator;
        const w = winLabels[c.time_window] || c.time_window;
        const val = ['spend', 'cpl', 'cpreg', 'cpp', 'cpc'].includes(c.metric) ? `$${c.value}` : (c.metric === 'ctr' ? `${c.value}%` : `${c.value} шт`);
        return `${m} ${op} ${val} (${w})`;
      }).join((preset.condition_logic || 'and').toUpperCase() === 'OR' ? ' <b style="color:var(--accent-primary)">ИЛИ</b> ' : ' <b style="color:var(--accent-primary)">И</b> ');

      const groupColor = parentGroup ? (state.ruleGroupColors[parentGroup.id] || 'purple') : 'gray';

      detailsGrid.innerHTML = `
        <div class="record-detail-row">
          <span class="record-detail-key">Действие</span>
          <span class="record-detail-val">${actionLabels[preset.action] || preset.action}</span>
        </div>
        <div class="record-detail-row">
          <span class="record-detail-key">Группа</span>
          <span class="record-detail-val" style="display:inline-flex;align-items:center;gap:6px;"><span class="rules-column-dot dot-${groupColor}"></span> <span>${escapeHtml(groupName)}</span></span>
        </div>
        <div class="record-detail-row">
          <span class="record-detail-key">Логика</span>
          <span class="record-detail-val">${(preset.condition_logic || 'and').toUpperCase() === 'OR' ? 'OR (Любое)' : 'AND (Все)'}</span>
        </div>
        <div class="record-detail-row">
          <span class="record-detail-key">Условия</span>
          <span class="record-detail-val" style="font-size: 12px; line-height: 1.4;">${conditionsFormatted || '—'}</span>
        </div>
        ${preset.action === 'increase_budget' && preset.budget_max_daily ? `
        <div class="record-detail-row">
          <span class="record-detail-key">Потолок</span>
          <span class="record-detail-val">$${preset.budget_max_daily} / день</span>
        </div>` : ''}
        <div class="record-detail-row">
          <span class="record-detail-key">Кулдаун</span>
          <span class="record-detail-val">${preset.cooldown_minutes ? `${preset.cooldown_minutes} мин` : 'Без паузы'}</span>
        </div>
        <div class="record-detail-row">
          <span class="record-detail-key">Telegram</span>
          <span class="record-detail-val"><span class="status-pill ${preset.notify_tg !== false ? 'green' : 'gray'}">${preset.notify_tg !== false ? 'Включены' : 'Выключены'}</span></span>
        </div>
      `;
    }

    // Populate Left Column: Groups
    const groupsList = document.getElementById('recordGroupsList');
    if (groupsList) {
      if (parentGroup) {
        const groupColor = state.ruleGroupColors[parentGroup.id] || 'purple';
        groupsList.innerHTML = `
          <div class="record-group-pill">
            <span class="record-group-dot-label">
              <span class="rules-column-dot dot-${groupColor}"></span>
              <span>${escapeHtml(parentGroup.name)}</span>
            </span>
            <span class="record-group-time">Активна</span>
          </div>
        `;
      } else {
        groupsList.innerHTML = `
          <div class="record-group-pill" style="color:var(--text-muted);">
            <span class="rules-column-dot dot-gray"></span>
            <span>Без группы (не в колонке)</span>
          </div>
        `;
      }
    }

    // Calculate Linked Accounts
    let linkedAccounts = [];
    (state.accounts || []).forEach(acc => {
      if ((acc.active_rules || []).some(r => r.preset_id === preset.id)) {
        linkedAccounts.push(acc);
      }
    });

    const accountsBadge = document.getElementById('recordAccountsCountBadge');
    if (accountsBadge) accountsBadge.textContent = String(linkedAccounts.length);

    // Populate Accounts Tab
    const accountsContainer = document.getElementById('recordAccountsList');
    if (accountsContainer) {
      if (linkedAccounts.length === 0) {
        accountsContainer.innerHTML = `
          <div class="record-empty-state" style="text-align:center; padding: 40px 20px;">
            <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="var(--text-muted)" stroke-width="1.5" style="margin-bottom: 10px; opacity: 0.6;"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18"/></svg>
            <div style="font-size: 13.5px; font-weight: 600; color: var(--text-primary); margin-bottom: 4px;">Правило не привязано к кабинетам</div>
            <div style="font-size: 12px; color: var(--text-muted); margin-bottom: 16px;">Привяжите правило к рекламным кабинетам, чтобы воркер начал проверять условия.</div>
            <button type="button" class="btn btn-primary btn-sm" onclick="window.openLinkRuleAccountsModal(${preset.id})">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
              <span>Привязать к кабинетам</span>
            </button>
          </div>
        `;
      } else {
        accountsContainer.innerHTML = linkedAccounts.map(acc => `
          <div class="record-account-row" id="recordAccRow_${escapeHtml(acc.account_id)}">
            <div class="record-account-left">
              <div class="record-account-name">
                <span>${escapeHtml(acc.custom_name || acc.name)}</span>
                ${acc.custom_name ? `<span style="font-size:11px; color:var(--text-muted); font-weight:400;">(${escapeHtml(acc.name)})</span>` : ''}
              </div>
              <div class="record-account-id">
                ID: ${escapeHtml(acc.account_id)} &bull; ${acc.currency || 'USD'} ${acc.batch_name ? `&bull; ${escapeHtml(acc.batch_name)}` : ''}
              </div>
            </div>
            <div class="record-account-right">
              <span class="badge ${acc.rules_enabled ? 'badge-success' : 'badge-neutral'}">
                ${acc.rules_enabled ? 'Автоматика вкл.' : 'На паузе'}
              </span>
              <button type="button" class="record-account-detach-btn" title="Отвязать правило от кабинета" onclick="window.detachRuleFromAccountDirectly('${escapeHtml(acc.account_id)}', ${preset.id})">
                Отвязать
              </button>
            </div>
          </div>
        `).join('');
      }
    }

    // Load Activity Events
    await window.loadRuleRecordActivity(preset.id, preset.name);

    // Render Highlights
    window.renderRecordHighlights(preset, linkedAccounts.length);

    window.setRecordActiveTab('overview');
  };

  // Direct Detach from Rule Record View
  window.detachRuleFromAccountDirectly = async function (accountId, presetId) {
    if (!confirm('Отвязать это правило от кабинета?')) return;
    try {
      await apiRequest(`/api/accounts/${accountId}/detach-rule/${presetId}`, {
        method: 'POST'
      });
      // Update local state
      const acc = state.accounts.find(a => a.account_id === accountId);
      if (acc && acc.active_rules) {
        acc.active_rules = acc.active_rules.filter(r => r.preset_id !== presetId);
      }
      showToast('Правило отвязано от кабинета', 'success');
      haptic('notification', 'success');
      const preset = state.presets.find(p => p.id === presetId);
      if (preset) populateRecordView(preset);
      if (state.activeTab === 'accounts') renderAccounts();
      if (state.activeTab === 'rules') renderRulesTab();
    } catch (e) {
      showToast(`Ошибка отвязки: ${e.message}`, 'error');
    }
  };

  // Open Link Rule to Accounts Modal
  window.openLinkRuleAccountsModal = function (presetId) {
    const targetPresetId = presetId || state.currentRecordPresetId;
    const preset = state.presets.find(p => p.id === targetPresetId);
    if (!preset) return;

    state.linkRuleModalPresetId = preset.id;
    state.linkRuleSelectedAccountIds = new Set(
      (state.accounts || [])
        .filter(acc => (acc.active_rules || []).some(r => r.preset_id === preset.id))
        .map(acc => acc.account_id)
    );

    const searchInput = document.getElementById('linkRuleAccountsSearchInput');
    if (searchInput) searchInput.value = '';

    window.renderLinkRuleAccountsList('');
    window.openModal('modalLinkRuleAccounts');
  };

  window.renderLinkRuleAccountsList = function (query) {
    const listEl = document.getElementById('linkRuleAccountsList');
    const badgeEl = document.getElementById('linkRuleAccountsSelectedBadge');
    if (!listEl) return;

    const q = (query || '').toLowerCase().trim();
    const filteredAccounts = (state.accounts || []).filter(acc => {
      if (!q) return true;
      const name = (acc.name || '').toLowerCase();
      const customName = (acc.custom_name || '').toLowerCase();
      const accId = (acc.account_id || '').toLowerCase();
      const batch = (acc.batch_name || '').toLowerCase();
      return name.includes(q) || customName.includes(q) || accId.includes(q) || batch.includes(q);
    });

    if (badgeEl) {
      badgeEl.textContent = `${state.linkRuleSelectedAccountIds.size} выбрано`;
    }

    if (filteredAccounts.length === 0) {
      listEl.innerHTML = `
        <div style="padding: 24px; text-align: center; color: var(--text-muted); font-size: 13px;">
          ${q ? 'Кабинеты не найдены' : 'В этом воркспейсе пока нет рекламных кабинетов'}
        </div>
      `;
      return;
    }

    listEl.innerHTML = filteredAccounts.map(acc => {
      const isSelected = state.linkRuleSelectedAccountIds.has(acc.account_id);
      const attachedRulesCount = (acc.active_rules || []).length;
      return `
        <div class="choose-group-item ${isSelected ? 'selected' : ''}" style="cursor: pointer;" onclick="window.toggleLinkAccountSelection('${escapeHtml(acc.account_id)}')">
          <div class="choose-group-item-left" style="gap: 12px;">
            <input type="checkbox" class="link-account-checkbox" ${isSelected ? 'checked' : ''} onclick="event.stopPropagation(); window.toggleLinkAccountSelection('${escapeHtml(acc.account_id)}')">
            <div>
              <div class="choose-group-item-name" style="font-weight: 600;">
                ${escapeHtml(acc.custom_name || acc.name)}
                ${acc.custom_name ? `<span style="font-size:11px; color:var(--text-muted); font-weight:400;">(${escapeHtml(acc.name)})</span>` : ''}
              </div>
              <div style="font-size: 11px; color: var(--text-muted);">
                ${escapeHtml(acc.account_id)} &bull; ${acc.currency || 'USD'} ${acc.batch_name ? `&bull; ${escapeHtml(acc.batch_name)}` : ''}
              </div>
            </div>
          </div>
          <div class="choose-group-item-right">
            <span class="choose-group-count-badge" title="Всего правил привязано к кабинету">
              ${attachedRulesCount} ${attachedRulesCount === 1 ? 'правило' : 'правил'}
            </span>
          </div>
        </div>
      `;
    }).join('');
  };

  window.filterLinkRuleAccountsList = function (query) {
    window.renderLinkRuleAccountsList(query);
  };

  window.toggleLinkAccountSelection = function (accountId) {
    if (state.linkRuleSelectedAccountIds.has(accountId)) {
      state.linkRuleSelectedAccountIds.delete(accountId);
    } else {
      state.linkRuleSelectedAccountIds.add(accountId);
    }
    const searchInput = document.getElementById('linkRuleAccountsSearchInput');
    window.renderLinkRuleAccountsList(searchInput ? searchInput.value : '');
  };

  window.toggleSelectAllLinkRuleAccounts = function () {
    const searchInput = document.getElementById('linkRuleAccountsSearchInput');
    const q = (searchInput ? searchInput.value : '').toLowerCase().trim();
    const visibleAccounts = (state.accounts || []).filter(acc => {
      if (!q) return true;
      const name = (acc.name || '').toLowerCase();
      const customName = (acc.custom_name || '').toLowerCase();
      const accId = (acc.account_id || '').toLowerCase();
      const batch = (acc.batch_name || '').toLowerCase();
      return name.includes(q) || customName.includes(q) || accId.includes(q) || batch.includes(q);
    });

    const allVisibleSelected = visibleAccounts.every(acc => state.linkRuleSelectedAccountIds.has(acc.account_id));
    if (allVisibleSelected) {
      visibleAccounts.forEach(acc => state.linkRuleSelectedAccountIds.delete(acc.account_id));
    } else {
      visibleAccounts.forEach(acc => state.linkRuleSelectedAccountIds.add(acc.account_id));
    }
    window.renderLinkRuleAccountsList(searchInput ? searchInput.value : '');
  };

  window.saveLinkRuleAccounts = async function () {
    const presetId = state.linkRuleModalPresetId;
    const preset = state.presets.find(p => p.id === presetId);
    if (!preset) return;

    const previouslyAttached = new Set(
      (state.accounts || [])
        .filter(acc => (acc.active_rules || []).some(r => r.preset_id === preset.id))
        .map(acc => acc.account_id)
    );

    const toAttach = [...state.linkRuleSelectedAccountIds].filter(id => !previouslyAttached.has(id));
    const toDetach = [...previouslyAttached].filter(id => !state.linkRuleSelectedAccountIds.has(id));

    window.closeModal('modalLinkRuleAccounts');

    if (toAttach.length === 0 && toDetach.length === 0) {
      showToast('Привязки не изменились', 'info');
      return;
    }

    const btnSave = document.getElementById('btnSaveLinkRuleAccounts');
    if (btnSave) btnSave.disabled = true;

    try {
      const promises = [
        ...toAttach.map(accId => apiRequest(`/api/accounts/${accId}/assign-rule`, {
          method: 'POST',
          body: JSON.stringify({ preset_id: preset.id })
        })),
        ...toDetach.map(accId => apiRequest(`/api/accounts/${accId}/detach-rule/${preset.id}`, {
          method: 'POST'
        }))
      ];

      await Promise.allSettled(promises);

      // Reload fresh accounts
      await loadAccounts(false);

      populateRecordView(preset);
      if (state.activeTab === 'accounts') renderAccounts();
      if (state.activeTab === 'rules') renderRulesTab();

      showToast(`Привязки обновлены: +${toAttach.length}, -${toDetach.length}`, 'success');
      haptic('notification', 'success');
    } catch (e) {
      showToast(`Ошибка сохранения: ${e.message}`, 'error');
    } finally {
      if (btnSave) btnSave.disabled = false;
    }
  };

  window.loadRuleRecordActivity = async function (presetId, presetName) {
    const overviewFeed = document.getElementById('recordOverviewActivityFeed');
    const fullFeed = document.getElementById('recordFullActivityFeed');
    const badge = document.getElementById('recordActivityCountBadge');

    try {
      const resp = await apiRequest(`/api/audit-events?rule_id=${presetId}&page_size=30`);
      let events = (resp && resp.items) || [];

      // Fallback search by rule_name if no rule_id events yet
      if (events.length === 0 && presetName) {
        const fallbackResp = await apiRequest(`/api/audit-events?search=${encodeURIComponent(presetName)}&page_size=20`);
        events = (fallbackResp && fallbackResp.items) || [];
      }

      state.currentRecordEvents = events;
      if (badge) badge.textContent = String(events.length);

      if (events.length === 0) {
        const emptyHtml = `
          <div style="padding: 16px 10px; color: var(--text-muted); font-size: 12.5px; text-align: center;">
            Событий и срабатываний по этому правилу пока не зафиксировано.
          </div>
        `;
        if (overviewFeed) overviewFeed.innerHTML = emptyHtml;
        if (fullFeed) fullFeed.innerHTML = emptyHtml;
        return;
      }

      const renderItem = (evt) => {
        const timeAgo = formatTimeAgo(new Date(evt.created_at || Date.now()));
        const statusDotClass = evt.status === 'SUCCESS' ? 'dot-emerald' : (evt.status === 'ERROR' ? 'dot-red' : 'dot-blue');
        return `
          <div class="record-activity-item">
            <div class="record-activity-avatar">B</div>
            <div class="record-activity-text">
              <b>${escapeHtml(evt.actor_id || 'Бот Buyerly')}</b>
              <span>${escapeHtml(evt.message || evt.event_type || 'Выполнил проверку')}</span>
              ${evt.account_name ? `<span style="color:var(--text-muted)"> &bull; ${escapeHtml(evt.account_name)}</span>` : ''}
              ${evt.adset_name ? `<span style="color:var(--text-muted)"> &bull; адсет: ${escapeHtml(evt.adset_name)}</span>` : ''}
            </div>
            <div class="record-activity-time" style="display:inline-flex;align-items:center;gap:5px;"><span class="rules-column-dot ${statusDotClass}" style="width:6px;height:6px;"></span> ${timeAgo}</div>
          </div>
        `;
      };

      if (overviewFeed) {
        overviewFeed.innerHTML = events.slice(0, 4).map(renderItem).join('');
      }
      if (fullFeed) {
        fullFeed.innerHTML = events.map(renderItem).join('');
      }
    } catch (e) {
      if (overviewFeed) overviewFeed.innerHTML = '<div style="color:var(--text-muted); font-size:12px;">Логи временно недоступны</div>';
    }
  };

  window.renderRecordHighlights = function (preset, linkedAccountsCount) {
    const grid = document.getElementById('recordHighlightsGrid');
    if (!grid) return;

    const events = state.currentRecordEvents || [];
    const todayCount = events.filter(e => {
      const d = new Date(e.created_at);
      const now = new Date();
      return d.toDateString() === now.toDateString();
    }).length;

    const lastEvent = events[0];
    const lastTriggeredText = lastEvent ? formatTimeAgo(new Date(lastEvent.created_at)) : 'Пока нет';

    grid.innerHTML = `
      <div class="record-highlight-card">
        <span class="record-highlight-label">Статус</span>
        <span class="record-highlight-value" style="color: var(--color-success, #02ad6e);">Активно</span>
      </div>
      <div class="record-highlight-card">
        <span class="record-highlight-label">Срабатываний сегодня</span>
        <span class="record-highlight-value">${todayCount} раз</span>
      </div>
      <div class="record-highlight-card">
        <span class="record-highlight-label">Последнее действие</span>
        <span class="record-highlight-value" style="font-size: 13.5px;">${lastTriggeredText}</span>
      </div>
      <div class="record-highlight-card">
        <span class="record-highlight-label">Кабинетов привязано</span>
        <span class="record-highlight-value">${linkedAccountsCount}</span>
      </div>
    `;
  };

  window.setRecordActiveTab = function (tabName) {
    state.recordActiveTab = tabName;
    document.querySelectorAll('.record-tab-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.tab === tabName);
    });
    document.getElementById('recordTabOverview')?.classList.toggle('hidden', tabName !== 'overview');
    document.getElementById('recordTabActivity')?.classList.toggle('hidden', tabName !== 'activity');
    document.getElementById('recordTabAccounts')?.classList.toggle('hidden', tabName !== 'accounts');
  };

  window.navigateRecordRule = function (delta) {
    if (!state.currentRecordPresetId) return;
    const preset = state.presets.find(p => p.id === state.currentRecordPresetId);
    if (!preset) return;

    const parentGroup = state.ruleGroups.find(g => (g.preset_ids || []).includes(preset.id));
    const groupPresets = parentGroup
      ? (parentGroup.preset_ids || []).map(id => state.presets.find(p => p.id === id)).filter(Boolean)
      : state.presets;

    const currentIndex = groupPresets.findIndex(p => p.id === preset.id);
    if (currentIndex < 0) return;

    const newIndex = currentIndex + delta;
    if (newIndex >= 0 && newIndex < groupPresets.length) {
      window.openRuleRecordPage(groupPresets[newIndex].id, 'push');
    }
  };

  // Inline Title Editing in Record View
  window.enableInlineTitleEdit = function () {
    const display = document.getElementById('recordTitleDisplay');
    const input = document.getElementById('recordTitleInput');
    if (!display || !input) return;
    display.classList.add('hidden');
    input.classList.remove('hidden');
    input.focus();
    input.select();
  };

  window.saveInlineTitleEdit = async function () {
    const display = document.getElementById('recordTitleDisplay');
    const input = document.getElementById('recordTitleInput');
    const titleText = document.getElementById('recordTitleText');
    if (!display || !input || !state.currentRecordPresetId) return;

    const newName = input.value.trim();
    display.classList.remove('hidden');
    input.classList.add('hidden');

    if (!newName) return;

    const preset = state.presets.find(p => p.id === state.currentRecordPresetId);
    if (!preset || preset.name === newName) return;

    try {
      const payload = {
        name: newName,
        action: preset.action,
        conditions: preset.conditions || [],
        condition_logic: preset.condition_logic || 'and',
        cooldown_minutes: preset.cooldown_minutes || 0,
        check_interval_minutes: preset.check_interval_minutes || 5,
        notify_tg: preset.notify_tg !== false,
        budget_change_percent: preset.budget_change_percent || 0.0,
        budget_max_daily: preset.budget_max_daily || 0.0
      };

      const updated = await apiRequest(`/api/presets/${preset.id}`, {
        method: 'PUT',
        body: JSON.stringify(payload)
      });

      preset.name = updated.name || newName;
      if (titleText) titleText.textContent = preset.name;
      showToast('Название правила обновлено', 'success');
      haptic('notification', 'success');

      if (state.activeTab === 'rules') renderRulesTab();
    } catch (e) {
      showToast(`Ошибка сохранения: ${e.message}`, 'error');
    }
  };

  window.onInlineTitleKeydown = function (event) {
    if (event.key === 'Enter') {
      event.preventDefault();
      window.saveInlineTitleEdit();
    } else if (event.key === 'Escape') {
      const display = document.getElementById('recordTitleDisplay');
      const input = document.getElementById('recordTitleInput');
      if (display && input) {
        input.classList.add('hidden');
        display.classList.remove('hidden');
      }
    }
  };

  window.runRuleCheckNow = async function () {
    haptic('impact', 'medium');
    showToast('Запущена ручная проверка правила...', 'info');
    try {
      await apiRequest('/api/worker/run-now', { method: 'POST' });
      showToast('Проверка успешно выполнена!', 'success');
      if (state.currentRecordPresetId) {
        const preset = state.presets.find(p => p.id === state.currentRecordPresetId);
        if (preset) await window.loadRuleRecordActivity(preset.id, preset.name);
      }
    } catch (e) {
      showToast(`Результат проверки: ${e.message}`, 'info');
    }
  };

  window.openMoveRuleGroupFromOverlay = function () {
    if (!state.currentRecordPresetId) return;
    window.openChooseGroupModal([state.currentRecordPresetId]);
  };

  window.deleteRuleFromOverlay = async function () {
    if (!state.currentRecordPresetId) return;
    const preset = state.presets.find(p => p.id === state.currentRecordPresetId);
    const name = preset ? preset.name : 'правило';
    if (!confirm(`Вы уверены, что хотите удалить «${name}»?`)) return;

    window.closeRuleRecordPage('push');
    await window.deletePresetDirectly(state.currentRecordPresetId);
  };

  // ==========================================================
  // ATTIO EDIT RULE MODAL (LAYER 2 / PHOTO 2)
  // ==========================================================
  window.openEditRuleFromOverlay = function () {
    if (state.currentRecordPresetId) {
      window.openEditRuleModal(state.currentRecordPresetId);
    }
  };

  window.openEditRuleModal = function (presetId) {
    haptic('selection');
    state.ruleBuilderMode = 'edit';
    const preset = state.presets.find(p => p.id === Number(presetId));
    if (!preset) {
      showToast('Правило не найдено', 'error');
      return;
    }

    const idInput = document.getElementById('editRulePresetId');
    if (idInput) idInput.value = String(preset.id);

    const sub = document.getElementById('editRuleSubtitle');
    if (sub) sub.textContent = `Настройка параметров и условий для «${preset.name}»`;

    const nameInput = document.getElementById('editRuleNameInput');
    if (nameInput) nameInput.value = preset.name || '';

    // Group Select
    const parentGroup = state.ruleGroups.find(g => (g.preset_ids || []).includes(preset.id));
    window.populateEditRuleGroupSelect(parentGroup ? parentGroup.id : null);

    // Action Select
    const actionSelect = document.getElementById('editRuleActionSelect');
    if (actionSelect) actionSelect.value = preset.action || 'turn_off';
    window.onEditRuleActionChange(preset.action || 'turn_off');

    // Budget config
    const budgetPercent = document.getElementById('editRuleBudgetPercentInput');
    if (budgetPercent) budgetPercent.value = preset.budget_change_percent || 20;
    const budgetCeiling = document.getElementById('editRuleBudgetCeilingInput');
    if (budgetCeiling) budgetCeiling.value = preset.budget_max_daily || 0;

    // Logic
    window.setEditRuleLogic(preset.condition_logic || 'and');

    // Cooldown & Telegram
    const cooldownSelect = document.getElementById('editRuleCooldownSelect');
    const customCooldown = document.getElementById('editRuleCustomCooldownInput');
    const standardCooldowns = ['0', '15', '30', '60', '120', '360', '720', '1440'];
    const cdVal = String(preset.cooldown_minutes || 0);
    if (standardCooldowns.includes(cdVal)) {
      if (cooldownSelect) cooldownSelect.value = cdVal;
      if (customCooldown) {
        customCooldown.classList.add('hidden');
        customCooldown.value = '';
      }
    } else {
      if (cooldownSelect) cooldownSelect.value = 'custom';
      if (customCooldown) {
        customCooldown.classList.remove('hidden');
        customCooldown.value = cdVal;
      }
    }

    const notifyToggle = document.getElementById('editRuleNotifyTgToggle');
    if (notifyToggle) notifyToggle.checked = preset.notify_tg !== false;

    // Conditions List
    const container = document.getElementById('editRuleConditionsList');
    if (container) container.innerHTML = '';
    const conditions = (preset.conditions && preset.conditions.length > 0)
      ? preset.conditions
      : [{ metric: 'spend', operator: 'gte', value: 2.0, time_window: 'today' }];

    conditions.forEach(c => {
      window.addEditRuleConditionRow(c.metric, c.operator, c.value, c.time_window);
    });

    window.renderEditRuleDraftSummary();
    window.openModal('modalEditRule');
    setTimeout(() => nameInput?.focus(), 50);
  };

  window.onEditRuleOverlayClick = function (event) {
    if (event.target.id === 'modalEditRule') {
      window.closeModal('modalEditRule');
    }
  };

  window.populateEditRuleGroupSelect = function (selectedGroupId = null) {
    const select = document.getElementById('editRuleGroupSelect');
    if (!select) return;

    let html = '<option value="">Без группы</option>';
    (state.ruleGroups || []).forEach(g => {
      const isSel = selectedGroupId !== null && Number(selectedGroupId) === g.id;
      html += `<option value="${g.id}" ${isSel ? 'selected' : ''}>${escapeHtml(g.name)}</option>`;
    });
    select.innerHTML = html;
  };

  window.onEditRuleActionChange = function (action) {
    const budgetSection = document.getElementById('editRuleBudgetSection');
    const ceilingField = document.getElementById('editRuleBudgetCeilingField');
    if (action === 'increase_budget' || action === 'decrease_budget') {
      budgetSection?.classList.remove('hidden');
      if (ceilingField) {
        ceilingField.style.display = action === 'increase_budget' ? 'flex' : 'none';
      }
    } else {
      budgetSection?.classList.add('hidden');
    }
    window.renderEditRuleDraftSummary();
  };

  window.setEditRuleLogic = function (logic = 'and') {
    document.querySelectorAll('#editRuleLogicGroup .attio-logic-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.logic === (logic || 'and'));
    });
    window.renderEditRuleDraftSummary();
  };

  window.getEditRuleLogic = function () {
    const activeBtn = document.querySelector('#editRuleLogicGroup .attio-logic-btn.active');
    return activeBtn?.dataset.logic || 'and';
  };

  window.toggleEditRuleMoreSettings = function () {
    const moreBody = document.getElementById('editRuleMoreBody');
    const moreArrow = document.getElementById('editRuleMoreArrow');
    if (!moreBody || !moreArrow) return;
    const isHidden = moreBody.classList.contains('hidden');
    moreBody.classList.toggle('hidden', !isHidden);
    moreArrow.classList.toggle('open', isHidden);
  };

  window.onEditRuleCooldownChange = function (val) {
    const customInput = document.getElementById('editRuleCustomCooldownInput');
    if (val === 'custom') {
      customInput?.classList.remove('hidden');
      customInput?.focus();
    } else {
      customInput?.classList.add('hidden');
      if (customInput) customInput.value = '';
    }
    window.renderEditRuleDraftSummary();
  };

  window.getEditRuleCooldownFromUI = function () {
    const select = document.getElementById('editRuleCooldownSelect');
    const customInput = document.getElementById('editRuleCustomCooldownInput');
    if (!select) return 0;
    if (select.value === 'custom') {
      return parseInt(customInput?.value) || 0;
    }
    return parseInt(select.value) || 0;
  };

  window.addEditRuleConditionRow = function (metric = 'spend', operator = 'gte', value = '2.0', timeWindow = 'today') {
    haptic('selection');
    const container = document.getElementById('editRuleConditionsList');
    if (!container) return;

    const row = document.createElement('div');
    row.className = 'attio-cond-row';
    row.innerHTML = `
      <select class="attio-cond-metric attio-field-select" onchange="window.renderEditRuleDraftSummary()">
        <option value="spend" ${metric === 'spend' ? 'selected' : ''}>Спенд (валюта кабинета)</option>
        <option value="cpl" ${metric === 'cpl' ? 'selected' : ''}>CPL (Цена лида)</option>
        <option value="cpreg" ${metric === 'cpreg' ? 'selected' : ''}>CPReg (Цена регистрации)</option>
        <option value="cpp" ${metric === 'cpp' ? 'selected' : ''}>CPP (Цена покупки)</option>
        <option value="leads" ${metric === 'leads' ? 'selected' : ''}>Лиды (шт)</option>
        <option value="registrations" ${metric === 'registrations' ? 'selected' : ''}>Регистрации (шт)</option>
        <option value="purchases" ${metric === 'purchases' ? 'selected' : ''}>Покупки (шт)</option>
        <option value="ctr" ${metric === 'ctr' ? 'selected' : ''}>CTR All (%)</option>
        <option value="cpc" ${metric === 'cpc' ? 'selected' : ''}>CPC All (валюта кабинета)</option>
      </select>
      <select class="attio-cond-op attio-field-select" onchange="window.renderEditRuleDraftSummary()">
        <option value="gt" ${operator === 'gt' ? 'selected' : ''}>&gt; (больше)</option>
        <option value="gte" ${operator === 'gte' ? 'selected' : ''}>&ge; (не меньше)</option>
        <option value="lt" ${operator === 'lt' ? 'selected' : ''}>&lt; (меньше)</option>
        <option value="lte" ${operator === 'lte' ? 'selected' : ''}>&le; (не больше)</option>
        <option value="eq" ${operator === 'eq' ? 'selected' : ''}>= (равно)</option>
      </select>
      <input type="number" class="attio-cond-val attio-field-input text-center" placeholder="0.0" step="0.5" min="0" inputmode="decimal" value="${value}" oninput="window.renderEditRuleDraftSummary()">
      <select class="attio-cond-win attio-field-select" onchange="window.renderEditRuleDraftSummary()">
        <option value="today" ${timeWindow === 'today' ? 'selected' : ''}>Сегодня</option>
        <option value="yesterday" ${timeWindow === 'yesterday' ? 'selected' : ''}>Вчера</option>
        <option value="last_3d" ${timeWindow === 'last_3d' ? 'selected' : ''}>3 дня</option>
        <option value="last_7d" ${timeWindow === 'last_7d' ? 'selected' : ''}>7 дней</option>
      </select>
      <button type="button" class="attio-cond-del-btn" onclick="window.removeEditRuleConditionRow(this)" title="Удалить условие">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
      </button>
    `;
    container.appendChild(row);
    window.renderEditRuleDraftSummary();
  };

  window.removeEditRuleConditionRow = function (button) {
    haptic('selection');
    const container = document.getElementById('editRuleConditionsList');
    button?.closest('.attio-cond-row')?.remove();
    if (container && container.children.length === 0) {
      window.addEditRuleConditionRow('spend', 'gte', 2.0, 'today');
    } else {
      window.renderEditRuleDraftSummary();
    }
  };

  window.getEditRuleConditionsFromUI = function () {
    const rows = document.querySelectorAll('#editRuleConditionsList .attio-cond-row');
    const conditions = [];
    rows.forEach(r => {
      const metric = r.querySelector('.attio-cond-metric')?.value || 'spend';
      const operator = r.querySelector('.attio-cond-op')?.value || 'gte';
      const valInput = r.querySelector('.attio-cond-val')?.value;
      const timeWindow = r.querySelector('.attio-cond-win')?.value || 'today';
      const value = parseFloat(valInput);
      if (!isNaN(value)) {
        conditions.push({ metric, operator, value, time_window: timeWindow });
      }
    });
    return conditions;
  };

  window.renderEditRuleDraftSummary = function () {
    const textElement = document.getElementById('editRulePlainText');
    const errorsElement = document.getElementById('editRuleValidationErrors');
    const saveButton = document.getElementById('btnSubmitEditRule');
    if (!textElement || !errorsElement) return [];

    const action = document.getElementById('editRuleActionSelect')?.value || 'turn_off';
    const logic = window.getEditRuleLogic();
    const conditions = window.getEditRuleConditionsFromUI();
    const budgetPercent = parseFloat(document.getElementById('editRuleBudgetPercentInput')?.value) || 0;
    const budgetCeiling = parseFloat(document.getElementById('editRuleBudgetCeilingInput')?.value) || 0;

    const plainText = buildPlainRuleTextFromValues(action, logic, conditions, budgetPercent, budgetCeiling);
    textElement.textContent = plainText;
    textElement.dataset.plainName = fitPlainRuleName(plainText);

    const errors = [];
    if (conditions.length === 0) {
      errors.push('Добавьте хотя бы одно корректное условие.');
    }
    if (conditions.some(c => ['leads', 'registrations', 'purchases'].includes(c.metric) && !Number.isInteger(c.value))) {
      errors.push('Лиды, регистрации и покупки указываются только целыми числами.');
    }
    if ((action === 'increase_budget' || action === 'decrease_budget') && (budgetPercent <= 0 || budgetPercent > 100)) {
      errors.push('Изменение бюджета должно быть от 1% до 100%.');
    }
    if (action === 'increase_budget' && (budgetCeiling <= 0 || budgetCeiling > 10000000)) {
      errors.push('Для увеличения бюджета укажите безопасный дневной потолок (> 0).');
    }

    errorsElement.innerHTML = errors.map(e => `
      <div class="attio-val-item error">
        <svg width="12" height="12" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
        <span>${escapeHtml(e)}</span>
      </div>
    `).join('');

    if (saveButton) saveButton.disabled = errors.length > 0;
    return errors;
  };

  window.useEditRulePlainName = function () {
    const input = document.getElementById('editRuleNameInput');
    const name = document.getElementById('editRulePlainText')?.dataset.plainName || '';
    if (input && name) {
      input.value = name;
      input.focus();
      showToast('Понятное название вставлено', 'success');
    }
  };

  window.submitEditRule = async function () {
    const presetIdStr = document.getElementById('editRulePresetId')?.value;
    const presetId = presetIdStr ? Number(presetIdStr) : null;
    if (!presetId) return;

    const nameInput = document.getElementById('editRuleNameInput');
    const actionSelect = document.getElementById('editRuleActionSelect');
    const groupSelect = document.getElementById('editRuleGroupSelect');
    const saveButton = document.getElementById('btnSubmitEditRule');

    const name = nameInput?.value.trim() || document.getElementById('editRulePlainText')?.dataset.plainName || 'Правило';
    const action = actionSelect?.value || 'turn_off';
    const newGroupId = groupSelect?.value ? Number(groupSelect.value) : null;
    const logic = window.getEditRuleLogic();
    const conditions = window.getEditRuleConditionsFromUI();
    const cooldownMins = window.getEditRuleCooldownFromUI();
    const notifyTg = document.getElementById('editRuleNotifyTgToggle')?.checked !== false;
    const budgetPercent = parseFloat(document.getElementById('editRuleBudgetPercentInput')?.value) || 0;
    const budgetCeiling = parseFloat(document.getElementById('editRuleBudgetCeilingInput')?.value) || 0;

    const errors = window.renderEditRuleDraftSummary();
    if (errors && errors.length > 0) {
      showToast(errors[0], 'error');
      return;
    }

    const payload = {
      name,
      action,
      conditions,
      condition_logic: logic,
      cooldown_minutes: cooldownMins,
      check_interval_minutes: 5,
      notify_tg: notifyTg,
      budget_change_percent: (action === 'increase_budget' || action === 'decrease_budget') ? budgetPercent : 0.0,
      budget_max_daily: action === 'increase_budget' ? budgetCeiling : 0.0
    };

    if (saveButton) saveButton.disabled = true;
    try {
      const updatedPreset = await apiRequest(`/api/presets/${presetId}`, {
        method: 'PUT',
        body: JSON.stringify(payload)
      });

      // Handle group assignment changes
      const prevGroup = state.ruleGroups.find(g => (g.preset_ids || []).includes(presetId));
      const prevGroupId = prevGroup ? prevGroup.id : null;

      if (prevGroupId !== newGroupId) {
        // Remove from old group
        if (prevGroup) {
          await apiRequest(`/api/rule-groups/${prevGroup.id}`, {
            method: 'PUT',
            body: JSON.stringify({
              name: prevGroup.name,
              description: prevGroup.description || '',
              preset_ids: (prevGroup.preset_ids || []).filter(id => id !== presetId)
            })
          });
        }
        // Add to new group
        if (newGroupId) {
          const targetGroup = state.ruleGroups.find(g => g.id === newGroupId);
          if (targetGroup) {
            const currentIds = targetGroup.preset_ids || [];
            if (!currentIds.includes(presetId)) {
              await apiRequest(`/api/rule-groups/${newGroupId}`, {
                method: 'PUT',
                body: JSON.stringify({
                  name: targetGroup.name,
                  description: targetGroup.description || '',
                  preset_ids: [...currentIds, presetId]
                })
              });
            }
          }
        }
      }

      haptic('notification', 'success');
      showToast(`Правило «${updatedPreset.name || name}» успешно обновлено!`, 'success');
      window.closeModal('modalEditRule');

      await Promise.all([loadPresets(), loadRuleGroups(), loadAccounts()]);

      // If Record Screen is currently viewing this rule, refresh it live
      if (state.currentRecordPresetId === presetId) {
        const recordView = document.getElementById('rulesRecordView');
        if (recordView && !recordView.classList.contains('hidden')) {
          await window.openRuleRecordPage(presetId, 'none');
        }
      }

      if (state.activeTab === 'rules') renderRulesTab();
    } catch (err) {
      showToast(`Ошибка сохранения: ${err.message}`, 'error');
    } finally {
      if (saveButton) saveButton.disabled = false;
    }
  };

  window.deletePresetDirectly = async function (presetId) {
    haptic('impact', 'medium');
    try {
      await apiRequest(`/api/presets/${presetId}`, { method: 'DELETE' });
      showToast('Правило удалено', 'success');
      await Promise.all([loadPresets(), loadRuleGroups(), loadAccounts()]);
      renderRulesTab();
    } catch (e) {
      showToast(`Ошибка удаления: ${e.message}`, 'error');
    }
  };

  async function loadRuleGroups() {
    try {
      state.ruleGroups = await apiRequest('/api/rule-groups') || [];
    } catch (error) {
      state.ruleGroups = [];
      console.error('Failed to load rule groups:', error);
    }
  }

  function renderRuleGroups() {
    renderRulesTab();
  }

  function renderRuleGroupChoices(selectedIds = []) {
    const container = document.getElementById('ruleGroupRulesList');
    const selected = new Set(selectedIds);
    if (!container) return;
    const actionLabels = {
      turn_off: 'Выключить', notify_only: 'Уведомить', turn_on: 'Включить',
      increase_budget: 'Увеличить бюджет', decrease_budget: 'Уменьшить бюджет'
    };
    container.innerHTML = state.presets.map(preset => `
      <label class="rule-group-choice">
        <input class="rule-group-preset-check" type="checkbox" value="${preset.id}" ${selected.has(preset.id) ? 'checked' : ''}>
        <span class="rule-group-choice-copy">
          <b>${escapeHtml(preset.name)}</b>
          <small>${(preset.conditions || []).length} условий · каждые ${preset.check_interval_minutes || 5} мин</small>
        </span>
        <span class="rule-group-choice-action">${escapeHtml(actionLabels[preset.action] || preset.action)}</span>
      </label>`).join('');
    updateRuleGroupSelectedCount();
    container.querySelectorAll('.rule-group-preset-check').forEach(input => {
      input.addEventListener('change', updateRuleGroupSelectedCount);
    });
  }

  function updateRuleGroupSelectedCount() {
    const count = document.querySelectorAll('.rule-group-preset-check:checked').length;
    const badge = document.getElementById('ruleGroupSelectedCount');
    if (badge) badge.textContent = `Выбрано: ${count}`;
  }

  window.openCreateRuleGroup = async function () {
    window.openAddColumnPopover();
  };

  window.editRuleGroup = async function (groupId) {
    window.openGroupMenuPopover(null, groupId);
  };

  window.deleteRuleGroup = async function (groupId) {
    const group = state.ruleGroups.find(item => item.id === groupId);
    if (!group || !window.confirm(`Удалить группу «${group.name}»? Уже назначенные правила останутся в кабинетах.`)) return;
    try {
      await apiRequest(`/api/rule-groups/${groupId}`, { method: 'DELETE' });
      showToast('Группа удалена. Правила сохранены.', 'success');
      await loadRuleGroups();
      renderRulesTab();
      window.closeModal('modalRuleGroup');
    } catch (error) {
      showToast(`Ошибка: ${error.message}`, 'error');
    }
  };

  document.getElementById('btnSaveRuleGroup')?.addEventListener('click', async () => {
    const groupId = document.getElementById('editingRuleGroupId').value;
    const name = document.getElementById('ruleGroupName').value.trim();
    const description = document.getElementById('ruleGroupDescription').value.trim();
    const presetIds = [...document.querySelectorAll('.rule-group-preset-check:checked')].map(input => Number(input.value));
    if (!name) {
      showToast('Введите название группы', 'error');
      return;
    }
    const button = document.getElementById('btnSaveRuleGroup');
    button.disabled = true;
    try {
      await apiRequest(groupId ? `/api/rule-groups/${groupId}` : '/api/rule-groups', {
        method: groupId ? 'PUT' : 'POST',
        body: JSON.stringify({ name, description, preset_ids: presetIds })
      });
      showToast(groupId ? 'Группа обновлена' : 'Группа создана', 'success');
      await loadRuleGroups();
      renderRulesTab();
      window.closeModal('modalRuleGroup');
    } catch (error) {
      showToast(`Ошибка: ${error.message}`, 'error');
    } finally {
      button.disabled = false;
    }
  });

  document.getElementById('btnDeleteRuleGroup')?.addEventListener('click', () => {
    const groupId = Number(document.getElementById('editingRuleGroupId').value);
    if (groupId) window.deleteRuleGroup(groupId);
  });

  window.openCreateRuleFromTab = function () {
    window.openChooseRuleModal(null);
  };

  // ==========================================================
  // QUICK ASSIGN MODAL (PHOTO 3)
  // ==========================================================
  let currentAssignAccountId = null;

  window.openAssignRuleModal = async function (accountId) {
    haptic('impact', 'medium');
    currentAssignAccountId = accountId;
    const acc = state.accounts.find(a => a.account_id === accountId);
    if (!acc) return;

    await Promise.all([loadPresets(), loadRuleGroups()]);

    document.getElementById('assignRuleAccountId').value = acc.account_id;
    document.getElementById('assignRuleModalTitle').textContent = `Правила для ${acc.name}`;

    const groupsSection = document.getElementById('assignGroupsSection');
    const groupsList = document.getElementById('assignGroupsList');
    groupsSection?.classList.toggle('hidden', state.ruleGroups.length === 0);
    if (groupsList) {
      const attachedIds = new Set((acc.active_rules || []).map(rule => rule.preset_id));
      groupsList.innerHTML = state.ruleGroups.map(group => {
        const groupIds = group.preset_ids || [];
        const attachedCount = groupIds.filter(id => attachedIds.has(id)).length;
        const complete = groupIds.length > 0 && attachedCount === groupIds.length;
        const missingCount = groupIds.length - attachedCount;
        const detail = complete
          ? `${groupIds.length} из ${groupIds.length} правил уже подключены`
          : `Подключено ${attachedCount} · будет добавлено ${missingCount}`;
        return `
          <div class="assign-group-item ${complete ? 'complete' : ''}">
            <div class="assign-group-copy">
              <b>${complete ? '<svg width="12" height="12" fill="none" viewBox="0 0 24 24" stroke="currentColor" style="display:inline-block;vertical-align:middle;margin-right:2px;"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7"/></svg>' : ''}${escapeHtml(group.name)}</b>
              <small>${escapeHtml(detail)}</small>
            </div>
            <button class="btn ${complete ? 'btn-secondary' : 'btn-primary'} btn-sm" ${complete ? 'disabled' : ''} onclick="window.pickRuleGroupForAccount(${group.id})">
              ${complete ? 'Подключена' : 'Назначить группу'}
            </button>
          </div>`;
      }).join('');
    }

    const listEl = document.getElementById('assignPresetsList');
    if (!state.presets || state.presets.length === 0) {
      listEl.innerHTML = `
        <div style="text-align:center; padding: 24px 12px; color:var(--tg-hint);">
          <p style="margin-bottom:8px; font-size:13px;">У вас пока нет сохраненных правил.</p>
        </div>
      `;
    } else {
      const actionBadgeMap = {
        'turn_off': 'Стоп',
        'notify_only': 'Пуш',
        'turn_on': 'Старт',
        'increase_budget': '+Бюджет',
        'decrease_budget': '-Бюджет'
      };

      listEl.innerHTML = state.presets.map(p => {
        const isCurrent = acc.active_rules && acc.active_rules.some(r => r.preset_id === p.id);
        const actLabel = actionBadgeMap[p.action] || p.action;
        const condCount = p.conditions ? p.conditions.length : 0;
        return `
          <div class="assign-preset-item ${isCurrent ? 'active' : ''}" onclick="window.${isCurrent ? 'detachRuleFromCurrentAccount' : 'pickRuleForAccount'}(${p.id})">
            <div class="assign-preset-info">
              <div class="assign-preset-title">${isCurrent ? '<svg width="12" height="12" fill="none" viewBox="0 0 24 24" stroke="currentColor" style="display:inline-block;vertical-align:middle;margin-right:2px;"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7"/></svg>' : ''}${escapeHtml(p.name)}</div>
              <div class="assign-preset-sub">${actLabel} · ${condCount} условий · каждые ${p.check_interval_minutes || 5}м</div>
            </div>
            <button class="btn btn-secondary btn-sm" style="pointer-events:none;">
              ${isCurrent ? 'Отвязать' : 'Привязать'}
            </button>
          </div>
        `;
      }).join('');
    }

    window.openModal('modalAssignRule');
  };

  window.pickRuleForAccount = async function (presetId) {
    if (!currentAssignAccountId) return;
    haptic('impact', 'medium');
    try {
      const res = await apiRequest(`/api/accounts/${currentAssignAccountId}/assign-rule`, {
        method: 'POST',
        body: JSON.stringify({ preset_id: presetId })
      });
      showToast(res.message || 'Правило успешно привязано!', 'success');
      window.closeModal('modalAssignRule');
      await loadAccounts();
      if (state.activeTab === 'rules') renderRulesTab();
    } catch (e) {
      showToast(`Ошибка: ${e.message}`, 'error');
    }
  };

  window.pickRuleGroupForAccount = async function (groupId) {
    if (!currentAssignAccountId) return;
    haptic('impact', 'medium');
    try {
      const result = await apiRequest(`/api/accounts/${currentAssignAccountId}/assign-rule-group/${groupId}`, {
        method: 'POST'
      });
      showToast(result.message || 'Группа правил назначена', 'success');
      window.closeModal('modalAssignRule');
      await loadAccounts();
      if (state.activeTab === 'rules') renderRulesTab();
    } catch (error) {
      showToast(`Ошибка: ${error.message}`, 'error');
    }
  };

  window.openCreateRuleForCurrentAccount = function () {
    window.closeModal('modalAssignRule');
    if (currentAssignAccountId) {
      window.openEditLimitsModal(currentAssignAccountId);
    }
  };

  window.detachRuleFromCurrentAccount = async function (presetId) {
    if (!currentAssignAccountId) return;
    const preset = state.presets.find(p => p.id === presetId);
    if (!window.confirm(`Отвязать правило «${preset?.name || `#${presetId}`}» от кабинета?`)) return;
    haptic('impact', 'medium');
    try {
      await apiRequest(`/api/accounts/${currentAssignAccountId}/detach-rule/${presetId}`, { method: 'POST' });
      showToast('Правило отвязано от кабинета', 'success');
      window.closeModal('modalAssignRule');
      await loadAccounts();
      if (state.activeTab === 'rules') renderRulesTab();
    } catch (e) {
      showToast(`Ошибка: ${e.message}`, 'error');
    }
  };

  async function loadPresets() {
    try {
      const data = await apiRequest('/api/presets');
      state.presets = data || [];
      renderPresetsList(state.activePresetId || state.templatePresetId);
    } catch (e) {
      console.error('Failed to load presets:', e);
    }
  }

  function renderPresetsList(selectedId = null) {
    const listEl = document.getElementById('userPresetsList');
    if (!listEl) return;

    if (!state.presets || state.presets.length === 0) {
      listEl.innerHTML = '<span style="font-size:12px; color:var(--tg-hint); padding: 4px 0;">Нет сохраненных пресетов. Соберите первое правило ниже.</span>';
      return;
    }

    listEl.innerHTML = state.presets.map(p => {
      const isSelected = selectedId === p.id;
      const actionBadge = p.action === 'turn_off' ? 'Стоп' : (p.action === 'notify_only' ? 'Пуш' : (p.action === 'turn_on' ? 'Старт' : (p.action === 'increase_budget' ? '+Бюджет' : '-Бюджет')));
      return `
        <div class="preset-chip-item ${isSelected ? 'active' : ''}" onclick="window.selectPreset(${p.id})" title="${state.ruleBuilderMode === 'edit' ? 'Перейти к редактированию правила' : 'Использовать параметры как шаблон нового правила'}">
          <span class="preset-chip-badge">${actionBadge}</span>
          <span>${escapeHtml(p.name)}</span>
        </div>
      `;
    }).join('');
  }

  // ==========================================================
  // RULE SETTINGS (COOLDOWN, INTERVAL, TELEGRAM TOGGLE)
  // ==========================================================
  function setCooldownUI(minutes = 0) {
    const group = document.getElementById('cooldownChipGroup');
    const customInput = document.getElementById('customCooldownInput');
    if (!group) return;

    let matched = false;
    group.querySelectorAll('.chip-btn').forEach(btn => {
      const v = btn.dataset.val;
      if (v !== 'custom' && parseInt(v) === minutes) {
        btn.classList.add('active');
        matched = true;
      } else {
        btn.classList.remove('active');
      }
    });

    if (!matched) {
      const customBtn = group.querySelector('.chip-btn[data-val="custom"]');
      if (customBtn) customBtn.classList.add('active');
      if (customInput) {
        customInput.classList.remove('hidden');
        customInput.value = minutes || '';
      }
    } else {
      if (customInput) {
        customInput.classList.add('hidden');
        customInput.value = '';
      }
    }
  }

  function getCooldownFromUI() {
    const group = document.getElementById('cooldownChipGroup');
    const customInput = document.getElementById('customCooldownInput');
    const activeBtn = group?.querySelector('.chip-btn.active');
    if (!activeBtn) return 0;
    if (activeBtn.dataset.val === 'custom') {
      return parseInt(customInput?.value) || 0;
    }
    return parseInt(activeBtn.dataset.val) || 0;
  }

  function setIntervalUI(minutes = 5) {
    const group = document.getElementById('intervalChipGroup');
    const customInput = document.getElementById('customIntervalInput');
    if (!group) return;

    let matched = false;
    group.querySelectorAll('.chip-btn').forEach(btn => {
      const v = btn.dataset.val;
      if (v !== 'custom' && parseInt(v) === minutes) {
        btn.classList.add('active');
        matched = true;
      } else {
        btn.classList.remove('active');
      }
    });

    if (!matched) {
      const customBtn = group.querySelector('.chip-btn[data-val="custom"]');
      if (customBtn) customBtn.classList.add('active');
      if (customInput) {
        customInput.classList.remove('hidden');
        customInput.value = minutes || 5;
      }
    } else {
      if (customInput) {
        customInput.classList.add('hidden');
        customInput.value = '';
      }
    }
  }

  function getIntervalFromUI() {
    const group = document.getElementById('intervalChipGroup');
    const customInput = document.getElementById('customIntervalInput');
    const activeBtn = group?.querySelector('.chip-btn.active');
    if (!activeBtn) return 5;
    if (activeBtn.dataset.val === 'custom') {
      return parseInt(customInput?.value) || 5;
    }
    return parseInt(activeBtn.dataset.val) || 5;
  }

  function getRuleDraftRows() {
    return Array.from(document.querySelectorAll('.rule-condition-row')).map(row => {
      const rawValue = row.querySelector('.cond-value')?.value ?? '';
      const value = Number(rawValue);
      return {
        metric: row.querySelector('.cond-metric')?.value || 'spend',
        operator: row.querySelector('.cond-operator')?.value || 'gte',
        value,
        time_window: row.querySelector('.cond-window')?.value || 'today',
        valid: rawValue !== '' && Number.isFinite(value) && value >= 0 && value <= 1000000000
      };
    });
  }

  function ruleConditionSignature(condition) {
    return [condition.metric, condition.operator, Number(condition.value), condition.time_window || 'today'].join('|');
  }

  function ruleTriggerSignature(logic, conditions) {
    return `${logic}|${conditions.map(ruleConditionSignature).sort().join('||')}`;
  }

  function updateDraftBound(current, candidate, direction) {
    if (!current) return candidate;
    if (direction === 'lower') {
      if (candidate.value > current.value) return candidate;
      if (candidate.value < current.value) return current;
    } else {
      if (candidate.value < current.value) return candidate;
      if (candidate.value > current.value) return current;
    }
    return { value: candidate.value, inclusive: current.inclusive && candidate.inclusive };
  }

  function draftAndRangeIsEmpty(conditions) {
    let lower = null;
    let upper = null;
    conditions.forEach(condition => {
      if (condition.operator === 'gt') lower = updateDraftBound(lower, { value: condition.value, inclusive: false }, 'lower');
      if (condition.operator === 'gte') lower = updateDraftBound(lower, { value: condition.value, inclusive: true }, 'lower');
      if (condition.operator === 'lt') upper = updateDraftBound(upper, { value: condition.value, inclusive: false }, 'upper');
      if (condition.operator === 'lte') upper = updateDraftBound(upper, { value: condition.value, inclusive: true }, 'upper');
      if (condition.operator === 'eq') {
        lower = updateDraftBound(lower, { value: condition.value, inclusive: true }, 'lower');
        upper = updateDraftBound(upper, { value: condition.value, inclusive: true }, 'upper');
      }
    });
    if (!lower || !upper) return false;
    return lower.value > upper.value || (lower.value === upper.value && !(lower.inclusive && upper.inclusive));
  }

  function draftOrRangeIsAlwaysTrue(conditions) {
    const signatures = new Set(conditions.map(condition => `${condition.operator}|${condition.value}`));
    return conditions.some(condition => (
      (signatures.has(`gte|${condition.value}`) && signatures.has(`lt|${condition.value}`))
      || (signatures.has(`gt|${condition.value}`) && signatures.has(`lte|${condition.value}`))
    ));
  }

  function stopConditionNeedsDeepConversion(condition) {
    if (condition.metric === 'cpreg' || condition.metric === 'cpp') return true;
    if (condition.metric !== 'registrations' && condition.metric !== 'purchases') return false;
    return condition.operator === 'gt'
      || ((condition.operator === 'gte' || condition.operator === 'eq') && condition.value > 0);
  }

  function validateRuleDraft() {
    const rows = getRuleDraftRows();
    const conditions = rows.filter(row => row.valid).map(({ valid, ...condition }) => condition);
    const logic = getLogicFromUI();
    const action = document.getElementById('ruleActionSelect')?.value || 'turn_off';
    const errors = [];
    const warnings = [];

    if (rows.length === 0) errors.push('Добавьте хотя бы одно условие.');
    if (rows.some(row => !row.valid)) errors.push('У каждого условия должно быть заполнено корректное неотрицательное значение.');

    const signatures = conditions.map(ruleConditionSignature);
    if (new Set(signatures).size !== signatures.length) {
      errors.push('Одно и то же условие добавлено несколько раз. Оставьте только одно.');
    }

    if (conditions.some(condition => ['leads', 'registrations', 'purchases'].includes(condition.metric) && !Number.isInteger(condition.value))) {
      errors.push('Лиды, регистрации и покупки указываются только целыми числами.');
    }

    if (action === 'turn_off' && conditions.some(stopConditionNeedsDeepConversion)) {
      errors.push('Такое правило не сработает: регистрация или покупка защищает группу объявлений от выключения. Для этого случая выберите уведомление или включение.');
    }

    const grouped = new Map();
    conditions.forEach(condition => {
      const key = `${condition.metric}|${condition.time_window}`;
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(condition);
    });
    grouped.forEach(metricConditions => {
      if (logic === 'and' && draftAndRangeIsEmpty(metricConditions)) {
        errors.push('В одном из показателей задан невозможный диапазон: нижняя граница выше верхней.');
      }
      if (logic === 'or' && draftOrRangeIsAlwaysTrue(metricConditions)) {
        errors.push('Два условия покрывают вообще все значения, поэтому правило будет срабатывать всегда.');
      }
    });

    const oppositeActions = [
      new Set(['turn_off', 'turn_on']),
      new Set(['increase_budget', 'decrease_budget'])
    ];
    const currentPresetId = Number(document.getElementById('editingPresetId')?.value || 0);
    const draftTrigger = ruleTriggerSignature(logic, conditions);
    state.presets.forEach(preset => {
      if (preset.id === currentPresetId) return;
      const actions = new Set([action, preset.action]);
      const isOpposite = oppositeActions.some(pair => pair.size === actions.size && [...pair].every(item => actions.has(item)));
      if (!isOpposite) return;
      const presetTrigger = ruleTriggerSignature(preset.condition_logic || 'and', preset.conditions || []);
      if (draftTrigger === presetTrigger) {
        warnings.push(`Конфликт с правилом «${preset.name}»: одинаковые условия запускают противоположные действия. Вместе к одному кабинету они не назначатся.`);
      }
    });

    return { rows, conditions, logic, action, errors: [...new Set(errors)], warnings: [...new Set(warnings)] };
  }

  function plainRuleValue(condition) {
    const value = Number(condition.value);
    const shown = Number.isInteger(value) ? String(value) : String(Number(value.toFixed(2)));
    if (condition.metric === 'ctr') return `${shown}%`;
    if (['spend', 'cpl', 'cpreg', 'cpp', 'cpc'].includes(condition.metric)) {
      return `${shown} в валюте кабинета`;
    }
    return shown;
  }

  function plainRuleCondition(condition) {
    const windows = {
      today: 'сегодня', yesterday: 'вчера', last_3d: 'за последние 3 дня', last_7d: 'за последние 7 дней'
    };
    const metrics = {
      spend: 'расход', cpl: 'цена лида', cpreg: 'цена регистрации', cpp: 'цена покупки',
      leads: 'лидов', registrations: 'регистраций', purchases: 'покупок',
      ctr: 'кликабельность', cpc: 'цена клика'
    };
    const operators = {
      gt: 'больше', gte: 'не меньше', lt: 'меньше', lte: 'не больше', eq: 'ровно'
    };
    return `${windows[condition.time_window] || 'сегодня'} ${metrics[condition.metric] || condition.metric} ${operators[condition.operator] || condition.operator} ${plainRuleValue(condition)}`;
  }

  function buildPlainRuleTextFromValues(action, logic, conditions, budgetPercent = 0, budgetCeiling = 0) {
    const actions = {
      turn_off: 'Выключить группу объявлений',
      notify_only: 'Прислать уведомление',
      turn_on: 'Включить группу объявлений',
      increase_budget: `Увеличить дневной бюджет на ${budgetPercent || 0}%${budgetCeiling > 0 ? `, но не выше ${budgetCeiling} в валюте кабинета` : ''}`,
      decrease_budget: `Уменьшить дневной бюджет на ${budgetPercent || 0}%`
    };
    const joiner = logic === 'or' ? ' или ' : ' и ';
    const conditionText = conditions.map(plainRuleCondition).join(joiner);
    return conditionText
      ? `${actions[action] || 'Выполнить действие'}, если ${conditionText}.`
      : 'Настройте действие и условия — здесь появится понятное описание.';
  }

  function buildPlainRuleText(validation) {
    return buildPlainRuleTextFromValues(
      validation.action,
      validation.logic,
      validation.conditions,
      Number(document.getElementById('budgetChangePercentInput')?.value || 0),
      Number(document.getElementById('budgetMaxDailyInput')?.value || 0)
    );
  }

  function fitPlainRuleName(text) {
    const clean = String(text || '').replace(/\.$/, '');
    if (clean.length <= 120) return clean;
    const shortened = clean.slice(0, 119);
    const lastSpace = shortened.lastIndexOf(' ');
    return `${shortened.slice(0, lastSpace > 80 ? lastSpace : 119)}…`;
  }

  function renderRuleDraftSummary() {
    const textElement = document.getElementById('rulePlainText');
    const detailsElement = document.getElementById('rulePlainDetails');
    const messagesElement = document.getElementById('ruleValidationMessages');
    if (!textElement || !detailsElement || !messagesElement) return { errors: [], warnings: [] };

    const validation = validateRuleDraft();
    const plainText = buildPlainRuleText(validation);
    textElement.textContent = plainText;
    textElement.dataset.ruleName = fitPlainRuleName(plainText);

    const interval = getIntervalFromUI();
    const cooldown = getCooldownFromUI();
    const notify = document.getElementById('ruleNotifyTgToggle')?.checked !== false;
    const protection = validation.action === 'turn_off'
      ? ' Если есть регистрация или покупка, выключения не будет.'
      : '';
    detailsElement.textContent = `Проверять каждые ${interval} мин. Пауза после срабатывания: ${cooldown ? `${cooldown} мин` : 'нет'}. Telegram: ${notify ? 'да' : 'нет'}.${protection}`;
    messagesElement.innerHTML = [
      ...validation.errors.map(message => `<div class="rule-validation-message error"><svg width="12" height="12" fill="none" viewBox="0 0 24 24" stroke="currentColor" style="display:inline-block; vertical-align:middle; margin-right:4px;"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>${escapeHtml(message)}</div>`),
      ...validation.warnings.map(message => `<div class="rule-validation-message warning"><svg width="12" height="12" fill="none" viewBox="0 0 24 24" stroke="currentColor" style="display:inline-block; vertical-align:middle; margin-right:4px;"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>${escapeHtml(message)}</div>`)
    ].join('');

    const saveButton = document.getElementById('btnSaveLimits');
    if (saveButton) saveButton.disabled = validation.errors.length > 0;
    return validation;
  }

  function setupRuleBuilderPreview() {
    const preview = document.getElementById('rulePlainPreview');
    if (!preview || preview.dataset.bound === 'true') return;
    preview.dataset.bound = 'true';

    const builder = document.getElementById('modalEditLimits');
    builder?.addEventListener('input', event => {
      if (event.target.matches('.cond-value, #budgetChangePercentInput, #budgetMaxDailyInput, #customCooldownInput, #customIntervalInput')) {
        renderRuleDraftSummary();
      }
    });
    builder?.addEventListener('change', event => {
      if (event.target.matches('.cond-metric, .cond-operator, .cond-window, #ruleNotifyTgToggle')) {
        renderRuleDraftSummary();
      }
    });

    document.getElementById('btnUseRulePlainName')?.addEventListener('click', () => {
      const input = document.getElementById('ruleNameInput');
      const name = document.getElementById('rulePlainText')?.dataset.ruleName || '';
      if (input && name) {
        input.value = name;
        input.focus();
        showToast('Понятное название вставлено', 'success');
      }
    });
    document.getElementById('btnCopyRulePlainText')?.addEventListener('click', async () => {
      const text = document.getElementById('rulePlainText')?.textContent || '';
      try {
        await navigator.clipboard.writeText(text);
        showToast('Описание скопировано', 'success');
      } catch (error) {
        showToast('Не удалось скопировать описание', 'error');
      }
    });
    renderRuleDraftSummary();
  }

  function setupSettingsChips() {
    document.querySelectorAll('#cooldownChipGroup .chip-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        haptic('selection');
        document.querySelectorAll('#cooldownChipGroup .chip-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        const customInput = document.getElementById('customCooldownInput');
        if (btn.dataset.val === 'custom') {
          customInput?.classList.remove('hidden');
          customInput?.focus();
        } else {
          customInput?.classList.add('hidden');
        }
        renderRuleDraftSummary();
      });
    });

    document.querySelectorAll('#intervalChipGroup .chip-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        haptic('selection');
        document.querySelectorAll('#intervalChipGroup .chip-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        const customInput = document.getElementById('customIntervalInput');
        if (btn.dataset.val === 'custom') {
          customInput?.classList.remove('hidden');
          customInput?.focus();
        } else {
          customInput?.classList.add('hidden');
        }
        renderRuleDraftSummary();
      });
    });
  }

  function getLogicFromUI() {
    const activeBtn = document.querySelector('#logicToggleGroup .chip-btn.active');
    return activeBtn?.dataset.logic || 'and';
  }

  function setLogicUI(logic = 'and') {
    document.querySelectorAll('#logicToggleGroup .chip-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.logic === (logic || 'and'));
    });
  }

  function setupLogicToggle() {
    document.querySelectorAll('#logicToggleGroup .chip-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        haptic('selection');
        document.querySelectorAll('#logicToggleGroup .chip-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        renderRuleDraftSummary();
      });
    });
  }

  function handleActionChange(action) {
    const budgetSection = document.getElementById('budgetConfigSection');
    const funnelProtectionNotice = document.getElementById('funnelProtectionNotice');
    if (action === 'increase_budget' || action === 'decrease_budget') {
      budgetSection?.classList.remove('hidden');
    } else {
      budgetSection?.classList.add('hidden');
    }
    funnelProtectionNotice?.classList.toggle('hidden', action !== 'turn_off');
  }

  document.getElementById('ruleActionSelect')?.addEventListener('change', (e) => {
    handleActionChange(e.target.value);
    renderRuleDraftSummary();
  });

  function updateRuleSaveButtonLabel() {
    const saveButton = document.getElementById('btnSaveLimits');
    if (!saveButton) return;
    const hasAccount = Boolean(document.getElementById('editLimitsAccountId')?.value);
    const isEditing = Boolean(document.getElementById('editingPresetId')?.value);
    saveButton.textContent = hasAccount
      ? 'Сохранить и применить'
      : (isEditing ? 'Сохранить изменения' : 'Создать правило');
  }

  window.selectPreset = function (presetId) {
    haptic('selection');
    const preset = state.presets.find(p => p.id === presetId);
    if (!preset) return;

    const isEditing = state.ruleBuilderMode === 'edit';
    state.activePresetId = isEditing ? preset.id : null;
    state.templatePresetId = isEditing ? null : preset.id;
    document.getElementById('editingPresetId').value = isEditing ? preset.id : '';
    document.getElementById('ruleNameInput').value = preset.name;
    const action = preset.action || 'turn_off';
    document.getElementById('ruleActionSelect').value = action;
    handleActionChange(action);

    document.getElementById('budgetChangePercentInput').value = preset.budget_change_percent || 20;
    document.getElementById('budgetMaxDailyInput').value = preset.budget_max_daily || 0;
    setLogicUI(preset.condition_logic || 'and');

    document.getElementById('builderModeTag').textContent = isEditing
      ? `Редактирование: ${preset.name}`
      : `Шаблон: ${preset.name} · будет создано новое правило`;
    document.getElementById('btnDeletePreset')?.classList.toggle('hidden', !isEditing);
    updateRuleSaveButtonLabel();

    setCooldownUI(preset.cooldown_minutes || 0);
    setIntervalUI(preset.check_interval_minutes || 5);
    const tgToggle = document.getElementById('ruleNotifyTgToggle');
    if (tgToggle) tgToggle.checked = preset.notify_tg !== false;

    const parentGroup = state.ruleGroups.find(g => (g.preset_ids || []).includes(preset.id));
    window.populateRuleGroupSelect(parentGroup ? parentGroup.id : null);

    renderConditions(preset.conditions || []);
    renderPresetsList(preset.id);
    renderRuleDraftSummary();
  };

  window.newPresetMode = function () {
    haptic('selection');
    state.ruleBuilderMode = 'create';
    state.activePresetId = null;
    state.templatePresetId = null;
    document.getElementById('editingPresetId').value = '';
    document.getElementById('ruleNameInput').value = '';
    document.getElementById('ruleActionSelect').value = 'turn_off';
    handleActionChange('turn_off');

    window.populateRuleGroupSelect(state.chooseRuleTargetGroupId);

    document.getElementById('budgetChangePercentInput').value = 20;
    document.getElementById('budgetMaxDailyInput').value = 0;
    setLogicUI('and');

    document.getElementById('builderModeTag').textContent = 'Новое правило';
    document.getElementById('btnDeletePreset')?.classList.add('hidden');
    updateRuleSaveButtonLabel();

    setCooldownUI(0);
    setIntervalUI(5);
    const tgToggle = document.getElementById('ruleNotifyTgToggle');
    if (tgToggle) tgToggle.checked = true;

    renderConditions([
      { metric: 'spend', operator: 'gte', value: 2.0, time_window: 'today' }
    ]);
    renderPresetsList(null);
    renderRuleDraftSummary();
    document.getElementById('ruleNameInput')?.focus();
  };

  window.addConditionRow = function (metric = 'spend', operator = 'gte', value = '', timeWindow = 'today') {
    haptic('selection');
    const container = document.getElementById('ruleConditionsContainer');
    if (!container) return;

    const normalizedMetric = metric === 'cpr' ? 'cpreg' : (metric === 'cpa' ? 'legacy_cpa' : metric);

    const row = document.createElement('div');
    row.className = 'rule-condition-row';
    row.innerHTML = `
      <select class="cond-metric form-select">
        <option value="spend" ${metric === 'spend' ? 'selected' : ''}>Спенд (валюта кабинета)</option>
        <option value="cpl" ${normalizedMetric === 'cpl' ? 'selected' : ''}>Цена лида · CPL (валюта кабинета)</option>
        <option value="cpreg" ${normalizedMetric === 'cpreg' ? 'selected' : ''}>Цена регистрации · CPReg (валюта кабинета)</option>
        <option value="cpp" ${normalizedMetric === 'cpp' ? 'selected' : ''}>Цена покупки · CPP (валюта кабинета)</option>
        <option value="leads" ${normalizedMetric === 'leads' ? 'selected' : ''}>Лиды (шт)</option>
        <option value="registrations" ${normalizedMetric === 'registrations' ? 'selected' : ''}>Регистрации (шт)</option>
        <option value="purchases" ${normalizedMetric === 'purchases' ? 'selected' : ''}>Покупки (шт)</option>
        <option value="ctr" ${normalizedMetric === 'ctr' ? 'selected' : ''}>CTR всех кликов (%)</option>
        <option value="cpc" ${normalizedMetric === 'cpc' ? 'selected' : ''}>CPC всех кликов (валюта кабинета)</option>
        ${normalizedMetric === 'legacy_cpa' ? '<option value="legacy_cpa" selected disabled>Старый общий CPA — замените</option>' : ''}
      </select>
      <select class="cond-operator form-select">
        <option value="gt" ${operator === 'gt' ? 'selected' : ''}>&gt; (больше)</option>
        <option value="gte" ${operator === 'gte' ? 'selected' : ''}>&ge; (больше или равно)</option>
        <option value="lt" ${operator === 'lt' ? 'selected' : ''}>&lt; (меньше)</option>
        <option value="lte" ${operator === 'lte' ? 'selected' : ''}>&le; (меньше или равно)</option>
        <option value="eq" ${operator === 'eq' ? 'selected' : ''}>= (равно)</option>
      </select>
      <input type="number" class="cond-value form-input text-center" placeholder="0.0" step="0.5" min="0" inputmode="decimal" value="${value}">
      <select class="cond-window form-select">
        <option value="today" ${timeWindow === 'today' ? 'selected' : ''}>Сегодня</option>
        <option value="yesterday" ${timeWindow === 'yesterday' ? 'selected' : ''}>Вчера</option>
        <option value="last_3d" ${timeWindow === 'last_3d' ? 'selected' : ''}>3 дня</option>
        <option value="last_7d" ${timeWindow === 'last_7d' ? 'selected' : ''}>7 дней</option>
      </select>
      <button type="button" class="btn-remove-cond" onclick="window.removeConditionRow(this)" title="Удалить условие">&times;</button>
    `;
    container.appendChild(row);
    renderRuleDraftSummary();
  };

  window.removeConditionRow = function (button) {
    button?.closest('.rule-condition-row')?.remove();
    renderRuleDraftSummary();
  };

  function renderConditions(conditionsList) {
    const container = document.getElementById('ruleConditionsContainer');
    if (!container) return;
    container.innerHTML = '';

    if (!conditionsList || conditionsList.length === 0) {
      window.addConditionRow('spend', 'gte', 2.0, 'today');
      return;
    }

    conditionsList.forEach(c => {
      window.addConditionRow(c.metric, c.operator || 'gte', c.value, c.time_window || 'today');
    });
    renderRuleDraftSummary();
  }

  function getConditionsFromUI() {
    const rows = document.querySelectorAll('.rule-condition-row');
    const conds = [];
    rows.forEach(r => {
      const metric = r.querySelector('.cond-metric')?.value || 'spend';
      const operator = r.querySelector('.cond-operator')?.value || 'gte';
      const valInput = r.querySelector('.cond-value')?.value;
      const timeWindow = r.querySelector('.cond-window')?.value || 'today';
      const value = parseFloat(valInput);
      if (!isNaN(value)) {
        conds.push({ metric, operator, value, time_window: timeWindow });
      }
    });
    return conds;
  }

  window.openEditLimitsModal = async function (accountId) {
    haptic('impact', 'medium');
    const acc = state.accounts.find(a => a.account_id === accountId);
    if (!acc) return;

    await loadPresets();

    document.getElementById('editLimitsAccountId').value = acc.account_id;
    document.getElementById('modalLimitsTitle').textContent = `Правило для ${accountDisplayName(acc)}`;
    window.newPresetMode();

    window.openModal('modalEditLimits');
  };

  window.deleteActivePreset = async function () {
    const presetId = state.activePresetId;
    if (!presetId) return;

    haptic('impact', 'medium');
    try {
      await apiRequest(`/api/presets/${presetId}`, { method: 'DELETE' });
      showToast('Пресет удален', 'success');
      state.activePresetId = null;
      await Promise.all([loadPresets(), loadRuleGroups(), loadAccounts()]);
      window.newPresetMode();
    } catch (e) {
      showToast(`Ошибка: ${e.message}`, 'error');
    }
  };

  document.getElementById('btnSaveLimits')?.addEventListener('click', async () => {
    const accountId = document.getElementById('editLimitsAccountId').value;
    const editingPresetId = document.getElementById('editingPresetId').value;
    const ruleName = document.getElementById('ruleNameInput').value.trim() || 'Правило стопа';
    const action = document.getElementById('ruleActionSelect').value;
    const conditions = getConditionsFromUI();
    const conditionLogic = getLogicFromUI();
    const cooldownMins = getCooldownFromUI();
    const checkIntervalMins = getIntervalFromUI();
    const notifyTg = document.getElementById('ruleNotifyTgToggle')?.checked !== false;
    const budgetChangePercent = parseFloat(document.getElementById('budgetChangePercentInput')?.value) || 0.0;
    const budgetMaxDaily = parseFloat(document.getElementById('budgetMaxDailyInput')?.value) || 0.0;

    const draftValidation = renderRuleDraftSummary();
    if (draftValidation.errors.length > 0) {
      showToast(draftValidation.errors[0], 'error');
      return;
    }

    if (!Number.isInteger(cooldownMins) || cooldownMins < 0 || cooldownMins > 10080) {
      showToast('Пауза должна быть от 0 до 10 080 минут', 'error');
      return;
    }
    if (!Number.isInteger(checkIntervalMins) || checkIntervalMins < 1 || checkIntervalMins > 1440) {
      showToast('Интервал проверки должен быть от 1 до 1 440 минут', 'error');
      return;
    }

    const isBudgetAction = action === 'increase_budget' || action === 'decrease_budget';
    if (isBudgetAction && (!Number.isFinite(budgetChangePercent) || budgetChangePercent <= 0 || budgetChangePercent > 100)) {
      showToast('Изменение бюджета должно быть больше 0% и не больше 100%', 'error');
      return;
    }
    if (action === 'increase_budget' && (!Number.isFinite(budgetMaxDaily) || budgetMaxDaily <= 0 || budgetMaxDaily > 10000000)) {
      showToast('Для увеличения бюджета укажите безопасный дневной потолок', 'error');
      return;
    }
    if (conditions.some(condition => !Number.isFinite(condition.value) || condition.value < 0 || condition.value > 1000000000)) {
      showToast('Значение условия должно быть числом от 0 до 1 000 000 000', 'error');
      return;
    }

    try {
      const payload = {
        name: ruleName,
        action: action,
        conditions: conditions,
        condition_logic: conditionLogic,
        cooldown_minutes: cooldownMins,
        check_interval_minutes: checkIntervalMins,
        notify_tg: notifyTg,
        budget_change_percent: isBudgetAction ? budgetChangePercent : 0.0,
        budget_max_daily: action === 'increase_budget' ? budgetMaxDaily : 0.0
      };

      if (accountId) {
        let savedPreset;
        if (editingPresetId) {
          savedPreset = await apiRequest(`/api/presets/${editingPresetId}`, {
            method: 'PUT',
            body: JSON.stringify(payload)
          });
        } else {
          savedPreset = await apiRequest('/api/presets', {
            method: 'POST',
            body: JSON.stringify(payload)
          });
        }

        const account = state.accounts.find(a => a.account_id === accountId);
        const isAlreadyAttached = account?.active_rules?.some(rule => rule.preset_id === savedPreset.id);
        if (!isAlreadyAttached) {
          await apiRequest(`/api/accounts/${accountId}/assign-rule`, {
            method: 'POST',
            body: JSON.stringify({ preset_id: savedPreset.id })
          });
        }
        haptic('notification', 'success');
        showToast('Правило сохранено и применено!', 'success');
      } else {
        let savedPreset;
        if (editingPresetId) {
          savedPreset = await apiRequest(`/api/presets/${editingPresetId}`, {
            method: 'PUT',
            body: JSON.stringify(payload)
          });
          showToast('Пресет успешно обновлен!', 'success');
        } else {
          savedPreset = await apiRequest('/api/presets', {
            method: 'POST',
            body: JSON.stringify(payload)
          });
          showToast('Пресет успешно создан!', 'success');
        }
        haptic('notification', 'success');

        const groupSelect = document.getElementById('ruleGroupSelect');
        if (groupSelect && savedPreset) {
          const targetGrpId = groupSelect.value ? Number(groupSelect.value) : null;
          const targetPresetId = savedPreset.id || Number(editingPresetId);
          
          for (const grp of state.ruleGroups) {
            const currentIds = grp.preset_ids || [];
            const hasPreset = currentIds.includes(targetPresetId);
            if (targetGrpId !== null && grp.id === targetGrpId && !hasPreset) {
              await apiRequest(`/api/rule-groups/${grp.id}`, {
                method: 'PUT',
                body: JSON.stringify({
                  name: grp.name,
                  description: grp.description || '',
                  preset_ids: [...currentIds, targetPresetId]
                })
              });
            } else if ((targetGrpId === null || grp.id !== targetGrpId) && hasPreset) {
              await apiRequest(`/api/rule-groups/${grp.id}`, {
                method: 'PUT',
                body: JSON.stringify({
                  name: grp.name,
                  description: grp.description || '',
                  preset_ids: currentIds.filter(id => id !== targetPresetId)
                })
              });
            }
          }
        }
      }

      window.closeModal('modalEditLimits');

      await Promise.all([loadPresets(), loadRuleGroups(), loadAccounts()]);
      if (state.activeTab === 'rules') renderRulesTab();
    } catch (err) {
      showToast(`Ошибка сохранения: ${err.message}`, 'error');
    }
  });

  document.getElementById('btnOpenDeleteFromModal')?.addEventListener('click', () => {
    const accountId = document.getElementById('editLimitsAccountId').value;
    const acc = state.accounts.find(a => a.account_id === accountId);
    if (acc) {
      window.closeModal('modalEditLimits');
      window.openDeleteConfirmModal(acc.account_id, accountDisplayName(acc));
    }
  });

  // ==========================================================
  // DELETE ACCOUNT MODAL
  // ==========================================================
  let pendingDeleteAccountId = null;

  window.openDeleteConfirmModal = function (accountId, name) {
    haptic('impact', 'medium');
    pendingDeleteAccountId = accountId;
    document.getElementById('deleteAccountName').textContent = name;
    document.getElementById('deleteAccountId').textContent = accountId;
    window.openModal('modalDeleteConfirm');
  };

  document.getElementById('btnConfirmDelete')?.addEventListener('click', async () => {
    if (!pendingDeleteAccountId) return;
    try {
      await apiRequest(`/api/accounts/${pendingDeleteAccountId}`, {
        method: 'DELETE'
      });
      haptic('notification', 'success');
      showToast('Кабинет удален из системы', 'success');
      window.closeModal('modalDeleteConfirm');
      state.accounts = state.accounts.filter(a => a.account_id !== pendingDeleteAccountId);
      renderAccounts();
    } catch (err) {
      showToast(`Ошибка удаления: ${err.message}`, 'error');
    }
  });

  const periodTextMap = {
    'today': 'за сегодня',
    'yesterday': 'за вчера',
    'last_3d': 'за 3 дня',
    'last_7d': 'за 7 дней'
  };

  function updateFetchButtonLabel(period) {
    const label = periodTextMap[period] || 'за период';
    const textEl = document.getElementById('btnFetchSummaryText');
    if (textEl) {
      textEl.textContent = `Обновить данные ${label}`;
    }
  }

  function renderLocalSummaryCache(data) {
    const generatedAt = new Date(data.generated_at || 0).getTime();
    const ageSeconds = generatedAt ? Math.max(0, (Date.now() - generatedAt) / 1000) : 0;
    renderSummaryData({
      ...data,
      cache: { ...(data.cache || {}), is_cached: true, age_seconds: ageSeconds, origin: 'browser' }
    });
  }

  function summaryAgeMs(data) {
    const generatedAt = new Date(data?.generated_at || 0).getTime();
    return generatedAt ? Math.max(0, Date.now() - generatedAt) : Number.POSITIVE_INFINITY;
  }

  function refreshSummaryIfStale(period, data) {
    if (
      state.activeTab !== 'summary' ||
      document.hidden ||
      state.summaryLoading ||
      summaryAgeMs(data) < SUMMARY_AUTO_REFRESH_MS
    ) return;

    window.setTimeout(() => {
      if (state.activeTab === 'summary' && state.currentPeriod === period && !document.hidden) {
        loadSummary(period, true, { silent: true, reason: 'auto' });
      }
    }, 0);
  }

  function startSummaryAutoRefresh() {
    if (summaryAutoRefreshTimer) window.clearInterval(summaryAutoRefreshTimer);
    summaryAutoRefreshTimer = window.setInterval(() => {
      if (state.activeTab !== 'summary' || document.hidden || state.summaryLoading) return;
      loadSummary(state.currentPeriod, true, { silent: true, reason: 'auto' });
    }, SUMMARY_AUTO_REFRESH_MS);
  }

  async function initializeSummaryTab() {
    await Promise.all([loadSummaryViewPreference(), loadAccounts()]);
    if (state.activeTab !== 'summary') return;

    state.currentPeriod = state.summaryView.period;
    updateFetchButtonLabel(state.currentPeriod);
    document.getElementById('kpiPeriodLabel').textContent = periodTextMap[state.currentPeriod] || '';
    loadStoppedAdsets();

    const savedData = state.summaryCache[state.currentPeriod];
    if (savedData) {
      renderLocalSummaryCache(savedData);
      refreshSummaryIfStale(state.currentPeriod, savedData);
    } else {
      loadSummary(state.currentPeriod, false, { silent: true, refreshIfStale: true });
    }
  }

  function normalizeSummaryView(preference = {}) {
    const canonicalOrder = SUMMARY_COLUMNS.map(column => column.key);
    const knownColumns = new Set(canonicalOrder);
    const savedOrder = Array.isArray(preference.column_order) ? preference.column_order : [];
    const isLegacyProfileColumnView = savedOrder.length > 0
      && !savedOrder.includes('custom_name')
      && !savedOrder.includes('note');
    const requested = Array.isArray(preference.visible_columns)
      ? preference.visible_columns.filter(key => knownColumns.has(key))
      : SUMMARY_VIEW_PRESETS.all;
    const visibleSet = new Set([...requested, 'account', 'data']);
    // One-time compatibility for views saved before the profile columns existed.
    // Once the new order is persisted, users may hide both columns normally.
    if (isLegacyProfileColumnView) {
      visibleSet.add('custom_name');
      visibleSet.add('note');
    }
    const requestedOrder = Array.isArray(preference.column_order)
      ? preference.column_order.filter(key => knownColumns.has(key))
      : canonicalOrder;
    const columnOrder = [];
    requestedOrder.forEach(key => {
      if (!columnOrder.includes(key)) columnOrder.push(key);
    });
    canonicalOrder.forEach(key => {
      if (!columnOrder.includes(key)) columnOrder.push(key);
    });
    const requestedWidths = preference.column_widths && typeof preference.column_widths === 'object'
      ? preference.column_widths
      : {};
    const columnWidths = Object.fromEntries(canonicalOrder.map(key => {
      const requestedWidth = Number(requestedWidths[key]);
      const width = Number.isFinite(requestedWidth)
        ? Math.round(requestedWidth)
        : SUMMARY_DEFAULT_COLUMN_WIDTHS[key];
      return [key, Math.max(SUMMARY_COLUMN_MIN_WIDTH, Math.min(SUMMARY_COLUMN_MAX_WIDTH, width))];
    }));
    const sortColumn = knownColumns.has(preference.sort_column) ? preference.sort_column : '';
    const sortDirection = preference.sort_direction === 'asc' ? 'asc' : 'desc';
    const rawFilters = preference.filters && typeof preference.filters === 'object'
      ? preference.filters
      : {};
    const statusFilter = ['all', 'synced', 'blocked', 'error'].includes(rawFilters.status)
      ? rawFilters.status
      : 'all';
    const queryFilter = String(rawFilters.query || '').trim().slice(0, 120);
    const rawGroupFilter = String(rawFilters.group_id || 'all');
    const groupFilter = rawGroupFilter === 'all' || /^\d+$/.test(rawGroupFilter)
      ? rawGroupFilter
      : 'all';
    const period = ['today', 'yesterday', 'last_3d', 'last_7d'].includes(preference.period)
      ? preference.period
      : 'today';
    const viewMode = ['all', 'overview', 'delivery', 'traffic', 'funnel', 'custom'].includes(preference.view_mode)
      ? preference.view_mode
      : 'all';
    return {
      view_mode: viewMode,
      visible_columns: canonicalOrder.filter(key => visibleSet.has(key)),
      column_order: columnOrder,
      column_widths: columnWidths,
      sort_column: sortColumn,
      sort_direction: sortDirection,
      filters: { query: queryFilter, status: statusFilter, group_id: groupFilter },
      period
    };
  }

  function summaryVisibleColumnCount() {
    return Math.max(2, state.summaryView.visible_columns.length);
  }

  function updateSummaryViewControls() {
    document.querySelectorAll('[data-summary-view]').forEach(button => {
      button.classList.toggle('active', button.dataset.summaryView === state.summaryView.view_mode);
    });
    const count = document.getElementById('summaryVisibleColumnsCount');
    if (count) count.textContent = summaryVisibleColumnCount();
    const searchInput = document.getElementById('summaryAccountSearch');
    if (searchInput && searchInput.value !== state.summaryView.filters.query) {
      searchInput.value = state.summaryView.filters.query;
    }
    document.getElementById('summaryAccountSearchClear')?.classList.toggle(
      'hidden',
      !state.summaryView.filters.query
    );
    document.querySelectorAll('[data-summary-status-filter]').forEach(button => {
      button.classList.toggle('active', button.dataset.summaryStatusFilter === state.summaryView.filters.status);
    });
    document.querySelectorAll('.period-btn').forEach(button => {
      button.classList.toggle('active', button.dataset.period === state.summaryView.period);
    });
    renderSummaryGroupSelector();
  }

  function renderSummaryGroupSelector() {
    const select = document.getElementById('summaryAccountGroupSelect');
    if (!select) return;
    const selectedId = state.summaryView.filters.group_id || 'all';
    const selectedExists = selectedId === 'all' || state.accountGroups.some(group => String(group.id) === selectedId);
    if (!selectedExists) state.summaryView.filters.group_id = 'all';
    const activeId = state.summaryView.filters.group_id || 'all';
    select.innerHTML = [
      `<option value="all">Все кабинеты (${state.accounts.length})</option>`,
      ...state.accountGroups.map(group => `<option value="${group.id}">${escapeHtml(group.name)} (${group.accounts_count || 0})</option>`)
    ].join('');
    select.value = activeId;
  }

  function renderSummaryTableHeader() {
    const head = document.getElementById('summaryTableHead');
    const colgroup = document.getElementById('summaryTableColumns');
    if (!head || !colgroup) return;
    const visible = new Set(state.summaryView.visible_columns);
    const definitions = new Map(SUMMARY_COLUMNS.map(column => [column.key, column]));
    const orderedColumns = state.summaryView.column_order
      .filter(key => visible.has(key))
      .map(key => definitions.get(key))
      .filter(Boolean);
    const hasGroupedColumns = orderedColumns.some(column => column.group !== 'base');
    const leafHeader = (column, rowspan = false) => {
      const alignment = column.key === 'account' || column.key === 'data' ? '' : ' text-right';
      const isActiveSort = state.summaryView.sort_column === column.key;
      const direction = state.summaryView.sort_direction;
      const indicator = isActiveSort ? (direction === 'asc' ? '↑' : '↓') : '↕';
      const ariaSort = isActiveSort ? (direction === 'asc' ? 'ascending' : 'descending') : 'none';
      const columnWidth = state.summaryView.column_widths[column.key];
      return `<th${rowspan ? ' rowspan="2"' : ''} class="summary-sortable-header${alignment}" data-summary-column="${column.key}" data-summary-sort="${column.key}" tabindex="0" role="button" aria-sort="${ariaSort}">${escapeHtml(column.label)} <span class="summary-sort-indicator">${indicator}</span><span class="summary-column-resizer" data-summary-column-resizer="${column.key}" role="separator" aria-orientation="vertical" aria-label="Изменить ширину колонки ${escapeHtml(column.label)}" aria-valuemin="${SUMMARY_COLUMN_MIN_WIDTH}" aria-valuemax="${SUMMARY_COLUMN_MAX_WIDTH}" aria-valuenow="${columnWidth}" tabindex="0" title="Потяните для изменения ширины · двойной клик — сброс"></span></th>`;
    };
    const runs = [];
    orderedColumns.forEach(column => {
      const previous = runs[runs.length - 1];
      if (column.group !== 'base' && previous?.group === column.group) {
        previous.columns.push(column);
      } else {
        runs.push({ group: column.group, columns: [column] });
      }
    });

    const firstRow = runs.map(run => {
      if (run.group === 'base') {
        const column = run.columns[0];
        return leafHeader(column, hasGroupedColumns);
      }
      const groupLabel = SUMMARY_COLUMN_GROUPS[run.group]?.label || run.group;
      return `<th colspan="${run.columns.length}" class="text-center" data-summary-group="${run.group}">${escapeHtml(groupLabel)}</th>`;
    }).join('');
    const secondRow = runs
      .filter(run => run.group !== 'base')
      .flatMap(run => run.columns)
      .map(column => leafHeader(column))
      .join('');
    head.innerHTML = `
      <tr class="table-group-header">${firstRow}</tr>
      ${hasGroupedColumns ? `<tr>${secondRow}</tr>` : ''}`;

    colgroup.innerHTML = orderedColumns.map(column => {
      const width = state.summaryView.column_widths[column.key];
      return `<col data-summary-column-width="${column.key}" style="width:${width}px;">`;
    }).join('');
  }

  function summaryTableWidth() {
    const visible = new Set(state.summaryView.visible_columns);
    return state.summaryView.column_order
      .filter(key => visible.has(key))
      .reduce((total, key) => total + state.summaryView.column_widths[key], 0);
  }

  function updateSummaryTableWidth() {
    const table = document.querySelector('.summary-metrics-table');
    if (!table) return;
    const width = Math.max(380, summaryTableWidth());
    table.style.width = `${width}px`;
    table.style.minWidth = `${width}px`;
  }

  function setSummaryColumnWidth(column, requestedWidth) {
    if (!Object.hasOwn(SUMMARY_DEFAULT_COLUMN_WIDTHS, column)) return false;
    const width = Math.max(
      SUMMARY_COLUMN_MIN_WIDTH,
      Math.min(SUMMARY_COLUMN_MAX_WIDTH, Math.round(Number(requestedWidth)))
    );
    if (!Number.isFinite(width) || state.summaryView.column_widths[column] === width) return false;
    state.summaryView = {
      ...state.summaryView,
      column_widths: { ...state.summaryView.column_widths, [column]: width }
    };
    const col = document.querySelector(`#summaryTableColumns [data-summary-column-width="${column}"]`);
    if (col) col.style.width = `${width}px`;
    const resizer = document.querySelector(`#summaryTableHead [data-summary-column-resizer="${column}"]`);
    if (resizer) resizer.setAttribute('aria-valuenow', String(width));
    updateSummaryTableWidth();
    return true;
  }

  function finishSummaryColumnResize() {
    if (!summaryColumnResizeState) return;
    const { resizer, changed } = summaryColumnResizeState;
    resizer.classList.remove('active');
    document.body.classList.remove('summary-column-resizing');
    window.removeEventListener('pointermove', moveSummaryColumnResize);
    window.removeEventListener('pointerup', finishSummaryColumnResize);
    window.removeEventListener('pointercancel', finishSummaryColumnResize);
    summaryColumnResizeState = null;
    if (changed) persistSummaryView(state.summaryView);
  }

  function moveSummaryColumnResize(event) {
    if (!summaryColumnResizeState) return;
    const nextWidth = summaryColumnResizeState.startWidth + event.clientX - summaryColumnResizeState.startX;
    if (setSummaryColumnWidth(summaryColumnResizeState.column, nextWidth)) {
      summaryColumnResizeState.changed = true;
    }
  }

  function startSummaryColumnResize(event) {
    const resizer = event.target.closest('[data-summary-column-resizer]');
    if (!resizer || event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    finishSummaryColumnResize();
    const column = resizer.dataset.summaryColumnResizer;
    summaryColumnResizeState = {
      column,
      resizer,
      startX: event.clientX,
      startWidth: state.summaryView.column_widths[column],
      changed: false
    };
    resizer.classList.add('active');
    document.body.classList.add('summary-column-resizing');
    window.addEventListener('pointermove', moveSummaryColumnResize);
    window.addEventListener('pointerup', finishSummaryColumnResize);
    window.addEventListener('pointercancel', finishSummaryColumnResize);
  }

  function resetSummaryColumnWidth(event) {
    const resizer = event.target.closest('[data-summary-column-resizer]');
    if (!resizer) return;
    event.preventDefault();
    event.stopPropagation();
    const column = resizer.dataset.summaryColumnResizer;
    if (setSummaryColumnWidth(column, SUMMARY_DEFAULT_COLUMN_WIDTHS[column])) {
      persistSummaryView(state.summaryView);
    }
  }

  function resizeSummaryColumnWithKeyboard(event) {
    const resizer = event.target.closest('[data-summary-column-resizer]');
    if (!resizer || !['ArrowLeft', 'ArrowRight', 'Home'].includes(event.key)) return false;
    event.preventDefault();
    event.stopPropagation();
    const column = resizer.dataset.summaryColumnResizer;
    const currentWidth = state.summaryView.column_widths[column];
    const nextWidth = event.key === 'Home'
      ? SUMMARY_DEFAULT_COLUMN_WIDTHS[column]
      : currentWidth + (event.key === 'ArrowRight' ? 8 : -8);
    if (setSummaryColumnWidth(column, nextWidth)) persistSummaryView(state.summaryView);
    return true;
  }

  function applySummaryColumnVisibility() {
    const visible = new Set(state.summaryView.visible_columns);
    renderSummaryTableHeader();
    document.querySelectorAll('#summaryTableBody tr').forEach(row => {
      const cells = new Map(
        Array.from(row.querySelectorAll('td[data-summary-column]'))
          .map(cell => [cell.dataset.summaryColumn, cell])
      );
      state.summaryView.column_order.forEach(key => {
        const cell = cells.get(key);
        if (cell) row.appendChild(cell);
      });
      cells.forEach((cell, key) => {
        cell.classList.toggle('summary-column-hidden', !visible.has(key));
      });
    });

    updateSummaryTableWidth();
    document.querySelectorAll('#summaryTableBody td[data-summary-empty]').forEach(cell => {
      cell.colSpan = summaryVisibleColumnCount();
    });
    updateSummaryViewControls();
  }

  function renderSummaryColumnOptions() {
    const container = document.getElementById('summaryColumnOptions');
    if (!container) return;
    const visible = new Set(state.summaryView.visible_columns);
    const definitions = new Map(SUMMARY_COLUMNS.map(column => [column.key, column]));
    container.innerHTML = state.summaryView.column_order.map((key, index) => {
      const column = definitions.get(key);
      const isRequired = Boolean(column?.required);
      const groupLabel = SUMMARY_COLUMN_GROUPS[column?.group]?.label || '';
      const width = state.summaryView.column_widths[key];
      return `
        <div class="summary-column-choice${isRequired ? ' required' : ''}" data-summary-column-option="${key}">
          <span class="summary-column-drag" draggable="true" title="Перетащить" aria-hidden="true">⋮⋮</span>
          <input class="summary-column-visible" type="checkbox" value="${key}" ${visible.has(key) ? 'checked' : ''} ${isRequired ? 'disabled' : ''} aria-label="Показывать ${escapeHtml(column?.label || key)}">
          <div class="summary-column-copy">
            <b>${escapeHtml(column?.label || key)}</b>
            <small>${escapeHtml(groupLabel)}${isRequired ? ' · обязательно' : ''}</small>
          </div>
          <label class="summary-column-width-control">
            <span>Ширина</span>
            <input type="range" min="${SUMMARY_COLUMN_MIN_WIDTH}" max="${SUMMARY_COLUMN_MAX_WIDTH}" step="4" value="${width}" data-summary-column-width-input="${key}">
            <output>${width}px</output>
          </label>
          <div class="summary-column-move-buttons">
            <button type="button" data-summary-move="up" aria-label="Поднять колонку" ${index === 0 ? 'disabled' : ''}>↑</button>
            <button type="button" data-summary-move="down" aria-label="Опустить колонку" ${index === state.summaryView.column_order.length - 1 ? 'disabled' : ''}>↓</button>
          </div>
        </div>`;
    }).join('');
    setupSummaryColumnEditor(container);
  }

  function setupSummaryColumnEditor(container) {
    let draggedItem = null;
    container.ondragstart = event => {
      const item = event.target.closest('[data-summary-column-option]');
      if (!item) return;
      draggedItem = item;
      item.classList.add('dragging');
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', item.dataset.summaryColumnOption);
    };
    container.ondragover = event => {
      event.preventDefault();
      const target = event.target.closest('[data-summary-column-option]');
      if (!draggedItem || !target || target === draggedItem) return;
      const rect = target.getBoundingClientRect();
      const insertAfter = event.clientY > rect.top + rect.height / 2;
      container.insertBefore(draggedItem, insertAfter ? target.nextSibling : target);
    };
    container.ondragend = () => {
      draggedItem?.classList.remove('dragging');
      draggedItem = null;
      refreshSummaryColumnMoveButtons(container);
    };
    container.oninput = event => {
      const range = event.target.closest('[data-summary-column-width-input]');
      if (!range) return;
      const output = range.parentElement.querySelector('output');
      if (output) output.textContent = `${range.value}px`;
    };
    container.onclick = event => {
      const button = event.target.closest('[data-summary-move]');
      if (!button) return;
      const item = button.closest('[data-summary-column-option]');
      if (button.dataset.summaryMove === 'up' && item.previousElementSibling) {
        container.insertBefore(item, item.previousElementSibling);
      } else if (button.dataset.summaryMove === 'down' && item.nextElementSibling) {
        container.insertBefore(item.nextElementSibling, item);
      }
      refreshSummaryColumnMoveButtons(container);
    };
  }

  function refreshSummaryColumnMoveButtons(container) {
    const items = Array.from(container.querySelectorAll('[data-summary-column-option]'));
    items.forEach((item, index) => {
      const up = item.querySelector('[data-summary-move="up"]');
      const down = item.querySelector('[data-summary-move="down"]');
      if (up) up.disabled = index === 0;
      if (down) down.disabled = index === items.length - 1;
    });
  }

  async function persistSummaryView(preference, options = {}) {
    const normalized = normalizeSummaryView(preference);
    const changeVersion = ++summaryViewChangeVersion;
    state.summaryView = normalized;
    state.summaryViewLoaded = true;
    applySummaryColumnVisibility();
    try {
      summaryViewSaveQueue = summaryViewSaveQueue
        .catch(() => null)
        .then(() => apiRequest('/api/analytics-view', {
          method: 'PUT',
          body: JSON.stringify(normalized)
        }));
      const saved = await summaryViewSaveQueue;
      if (changeVersion === summaryViewChangeVersion) {
        state.summaryView = normalizeSummaryView(saved);
        applySummaryColumnVisibility();
        if (options.toast) showToast(options.toast, 'success');
      }
      return saved;
    } catch (err) {
      if (changeVersion === summaryViewChangeVersion) {
        showToast(`Вид применён, но не сохранён: ${err.message}`, 'error');
      }
      return null;
    }
  }

  async function loadSummaryViewPreference() {
    if (state.summaryViewLoaded) {
      applySummaryColumnVisibility();
      return state.summaryView;
    }
    const changeVersion = summaryViewChangeVersion;
    try {
      const preference = await apiRequest('/api/analytics-view');
      if (changeVersion === summaryViewChangeVersion) {
        state.summaryView = normalizeSummaryView(preference);
      }
    } catch (err) {
      console.warn('Не удалось загрузить сохранённый вид аналитики:', err);
      if (changeVersion === summaryViewChangeVersion) {
        state.summaryView = normalizeSummaryView({ view_mode: 'all' });
      }
    } finally {
      state.summaryViewLoaded = true;
      applySummaryColumnVisibility();
    }
    return state.summaryView;
  }

  window.openSummaryColumns = function () {
    renderSummaryColumnOptions();
    window.openModal('modalSummaryColumns');
  };

  // ==========================================================
  // TAB 2: SUMMARY (СВОДКА И АНАЛИТИКА)
  // ==========================================================
  async function loadSummary(period = 'today', force = false, options = {}) {
    state.currentPeriod = period;
    if (state.summaryLoading) {
      state.summaryQueuedRequest = { period, force, options };
      return state.summaryCache[period] || null;
    }

    const silent = options.silent === true;
    const existingData = state.summaryCache[period] || null;
    let loadedData = null;
    const tableBody = document.getElementById('summaryTableBody');
    const mobileCards = document.getElementById('summaryMobileCards');
    const fetchBtn = document.getElementById('btnFetchSummary');
    const statusLabel = document.getElementById('summaryStatusLabel');
    
    // Update period switchers
    document.querySelectorAll('.period-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.period === period);
    });

    updateFetchButtonLabel(period);
    document.getElementById('kpiPeriodLabel').textContent = periodTextMap[period] || '';

    state.summaryLoading = true;
    if (fetchBtn) {
      fetchBtn.classList.add('loading');
      fetchBtn.disabled = true;
    }
    if (statusLabel) {
      statusLabel.textContent = existingData
        ? `Обновляем данные · пока показываем снимок от ${formatSummaryTime(existingData.generated_at)}`
        : 'Загружаем последние сохранённые данные...';
    }

    try {
      const data = await apiRequest(`/api/summary?period=${period}${force ? '&force=true' : ''}`);
      loadedData = data;
      state.summary = data;
      state.summaryCache[period] = data;

      if (state.currentPeriod === period) {
        renderSummaryData(data);
        loadStoppedAdsets();
      }

      if (!silent) showToast('Сводка обновлена и сохранена', 'success');

    } catch (err) {
      if (existingData) {
        if (state.currentPeriod === period) {
          renderSummaryProvenance(existingData, { refreshError: err.message });
        }
      } else if (state.currentPeriod === period) {
        tableBody.innerHTML = `<tr><td colspan="22" class="text-danger text-center">${escapeHtml(err.message)}</td></tr>`;
        mobileCards.innerHTML = `<div class="empty-state"><p class="text-danger">${escapeHtml(err.message)}</p></div>`;
        if (statusLabel) statusLabel.textContent = `Не удалось загрузить данные: ${err.message}`;
      }
      if (!silent) showToast(`Ошибка обновления: ${err.message}`, 'error');
    } finally {
      state.summaryLoading = false;
      if (fetchBtn) {
        fetchBtn.classList.remove('loading');
        fetchBtn.disabled = false;
      }
      if (!force && options.refreshIfStale !== false && loadedData) {
        refreshSummaryIfStale(period, loadedData);
      }
      const queuedRequest = state.summaryQueuedRequest;
      state.summaryQueuedRequest = null;
      if (queuedRequest && queuedRequest.period !== period) {
        window.setTimeout(() => loadSummary(
          queuedRequest.period,
          queuedRequest.force,
          queuedRequest.options
        ), 0);
      }
    }
  }

  function normalizeCurrencyCode(value) {
    const code = String(value || '').trim().toUpperCase();
    return /^[A-Z]{3}$/.test(code) ? code : '';
  }

  function formatMoneyOrDash(value, currency) {
    if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
    const code = normalizeCurrencyCode(currency);
    if (!code) return `${value.toFixed(2)} · валюта неизвестна`;
    try {
      return new Intl.NumberFormat('ru-RU', {
        style: 'currency',
        currency: code,
        currencyDisplay: 'code',
        maximumFractionDigits: ['CLP', 'ISK', 'JPY', 'KRW', 'PYG', 'VND'].includes(code) ? 0 : 2
      }).format(value);
    } catch (_) {
      return `${value.toFixed(2)} ${code}`;
    }
  }

  function formatNumber(value) {
    return Number(value || 0).toLocaleString('ru-RU');
  }

  function formatOptionalNumber(value) {
    return value === null || value === undefined ? '—' : formatNumber(value);
  }

  function formatDecimalOrDash(value, digits = 2, suffix = '') {
    return typeof value === 'number' && Number.isFinite(value)
      ? `${value.toFixed(digits)}${suffix}`
      : '—';
  }

  function formatSummaryTime(value) {
    if (!value) return 'время неизвестно';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return 'время неизвестно';
    return new Intl.DateTimeFormat('ru-RU', {
      day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'
    }).format(date);
  }

  function formatSummaryAge(ageSeconds) {
    const seconds = Math.max(0, Number(ageSeconds || 0));
    if (seconds < 60) return `${Math.round(seconds)} сек назад`;
    if (seconds < 3600) return `${Math.round(seconds / 60)} мин назад`;
    return `${Math.round(seconds / 3600)} ч назад`;
  }

  function renderSummaryProvenance(data, options = {}) {
    const status = document.getElementById('summaryStatusLabel');
    const freshness = document.getElementById('summaryFreshnessBadge');
    const generatedLabel = formatSummaryTime(data.generated_at);
    const ageSeconds = summaryAgeMs(data) / 1000;
    const origin = data.cache?.origin || (data.cache?.is_cached ? 'memory' : 'live');
    const isStale = ageSeconds >= (SUMMARY_AUTO_REFRESH_MS / 1000);
    if (status) {
      const scopeLabel = data.scope?.group_id && data.scope.group_id !== 'all'
        ? ` · группа «${data.scope.name}»`
        : '';
      status.textContent = options.refreshError
        ? `Обновление не удалось · показываем данные от ${generatedLabel}${scopeLabel}`
        : `${data.source || 'Meta Marketing API'} · последнее обновление ${generatedLabel}${scopeLabel}`;
    }
    if (freshness) {
      if (options.refreshError) {
        freshness.className = 'summary-freshness-badge stale';
        freshness.textContent = 'Сохранённые данные';
      } else if (origin === 'live' && !isStale) {
        freshness.className = 'summary-freshness-badge fresh';
        freshness.textContent = 'Свежие данные';
      } else {
        freshness.className = `summary-freshness-badge ${isStale ? 'stale' : 'cached'}`;
        freshness.textContent = `${origin === 'database' ? 'Сохранено' : 'Последние данные'} · ${formatSummaryAge(ageSeconds)}`;
      }
    }
    const lastSync = document.getElementById('lastSyncLabel');
    if (lastSync) lastSync.textContent = `Последнее обновление · ${generatedLabel}`;
  }

  function renderSpendComparison(data) {
    const comparison = document.getElementById('kpiSpendPrevious');
    if (!comparison) return;
    if (data.scope?.group_id && data.scope.group_id !== 'all') {
      comparison.className = 'kpi-comparison';
      comparison.textContent = `Срез по группе «${data.scope.name}» · ${data.accounts_count || 0} кабинетов`;
      return;
    }
    const previous = data.snapshot?.previous;
    if (!previous) {
      comparison.className = 'kpi-comparison';
      comparison.textContent = 'Предыдущий снимок появится после следующего обновления';
      return;
    }

    const currency = normalizeCurrencyCode(data.display_currency);
    const previousCurrency = normalizeCurrencyCode(previous.display_currency);
    if (!currency || currency !== previousCurrency || data.mixed_currencies || previous.mixed_currencies) {
      comparison.className = 'kpi-comparison';
      comparison.textContent = 'Сравнение общего Spend недоступно: валюты разделены';
      return;
    }

    const currentSpend = Number(data.total_spend);
    const previousSpend = Number(previous.total_spend);
    const delta = currentSpend - previousSpend;
    const deltaLabel = `${delta > 0 ? '+' : delta < 0 ? '−' : '±'}${formatMoneyOrDash(Math.abs(delta), currency)}`;
    comparison.className = 'kpi-comparison has-previous';
    comparison.textContent = `До обновления ${formatSummaryTime(previous.generated_at)} · ${formatMoneyOrDash(previousSpend, currency)} · изменение ${deltaLabel}`;
  }

  function renderCurrencyBreakdown(data) {
    const container = document.getElementById('summaryCurrencyBreakdown');
    if (!container) return;
    const totals = Array.isArray(data.currency_totals) ? data.currency_totals : [];
    if (!totals.length) {
      container.className = 'summary-currency-breakdown hidden';
      container.innerHTML = '';
      return;
    }
    const needsExplanation = data.mixed_currencies || !normalizeCurrencyCode(data.display_currency);
    container.className = 'summary-currency-breakdown';
    container.innerHTML = `
      <div class="summary-currency-head">
        <div><b>Денежные итоги по валютам</b><span>${needsExplanation ? 'Разные валюты не складываются в общий Spend и CPA' : 'Валюта подтверждена Meta для кабинетов'}</span></div>
      </div>
      <div class="summary-currency-grid">
        ${totals.map(item => `
          <div class="summary-currency-item">
            <div><b>${escapeHtml(item.currency || 'UNKNOWN')}</b><span>${formatNumber(item.accounts_count)} каб.</span></div>
            <strong>${formatMoneyOrDash(Number(item.spend || 0), item.currency)}</strong>
            <small>CPL ${formatMoneyOrDash(item.cost_per_lead, item.currency)} · CPReg ${formatMoneyOrDash(item.cost_per_registration, item.currency)} · CPP ${formatMoneyOrDash(item.cost_per_purchase, item.currency)}</small>
          </div>`).join('')}
      </div>`;
  }

  function renderMetricDefinitions(definitions = {}) {
    const container = document.getElementById('summaryDefinitionsList');
    if (!container) return;
    const labels = {
      spend: 'Spend', impressions: 'Показы', reach: 'Охват',
      frequency: 'Частота', cpm: 'CPM', leads: 'Лиды', cost_per_lead: 'CPL',
      registrations: 'Регистрации', cost_per_registration: 'CPReg',
      purchases: 'Покупки', cost_per_purchase: 'CPP', clicks: 'Все клики',
      unique_clicks: 'Unique Clicks', link_clicks: 'Link Clicks',
      outbound_clicks: 'Outbound Clicks', landing_page_views: 'LP Views',
      ctr: 'CTR All', link_ctr: 'CTR Link', outbound_ctr: 'CTR Outbound',
      cpc: 'CPC All', cpc_link: 'CPC Link',
      cost_per_landing_page_view: 'Цена LP View'
    };
    container.innerHTML = Object.entries(definitions).map(([key, definition]) => `
      <div class="metric-definition-item">
        <b>${escapeHtml(labels[key] || key)}</b>
        <p>${escapeHtml(definition)}</p>
      </div>`).join('');
  }

  function renderSummaryQuality(quality = {}) {
    const status = quality.status || 'unavailable';
    const coverageCard = document.getElementById('kpiCoverageCard');
    const banner = document.getElementById('summaryQualityBanner');
    coverageCard.classList.remove('complete', 'partial', 'unavailable');
    coverageCard.classList.add(status);
    document.getElementById('kpiCoverage').textContent = `${Number(quality.metrics_coverage_percent || 0).toFixed(1)}%`;
    document.getElementById('kpiSyncedAccounts').textContent = quality.accounts_synced || 0;
    document.getElementById('kpiTotalAccounts').textContent = quality.accounts_total || 0;
    document.getElementById('kpiFailedAccounts').textContent = quality.accounts_failed || 0;

    if (status === 'complete') {
      banner.className = 'summary-quality-banner hidden';
      banner.textContent = '';
      return;
    }
    const failed = quality.accounts_failed || 0;
    const blocked = quality.accounts_blocked || 0;
    banner.className = `summary-quality-banner${status === 'unavailable' ? ' error' : ''}`;
    banner.innerHTML = `<b>Неполная синхронизация:</b> данные получены от ${quality.accounts_synced || 0} из ${quality.accounts_total || 0} кабинетов. Ошибок Meta: ${failed}, недоступных кабинетов: ${blocked}. Итоговые суммы рассчитаны только по успешно синхронизированным данным.`;
  }

  function summaryDataStatus(account) {
    const status = summaryAccountStatusKey(account);
    const labels = { synced: 'Получены', blocked: 'Недоступны', error: 'Ошибка Meta' };
    return `<span class="summary-data-status ${status}" title="${escapeHtml(account.data_status_label || '')}">${labels[status] || 'Нет данных'}</span>`;
  }

  function summaryAccountStatusKey(account) {
    return account.data_status || (account.has_error ? 'error' : (account.is_banned ? 'blocked' : 'synced'));
  }

  function summaryAccountHasMetrics(account) {
    return summaryAccountStatusKey(account) === 'synced';
  }

  function summaryAccountsWithLiveMetadata(accounts = []) {
    const liveById = new Map(state.accounts.map(account => [account.account_id, account]));
    return accounts.map(account => {
      const live = liveById.get(account.account_id);
      if (!live) return account;
      return {
        ...account,
        name: live.name || account.name,
        custom_name: live.custom_name || '',
        note: live.note || '',
        group_ids: Array.isArray(live.group_ids) ? live.group_ids : (account.group_ids || [])
      };
    });
  }

  function summaryCost(spend, count) {
    return count > 0 ? Math.round((spend / count) * 100) / 100 : null;
  }

  function buildScopedSummaryData(rawData) {
    const allAccounts = summaryAccountsWithLiveMetadata(Array.isArray(rawData?.accounts) ? rawData.accounts : []);
    const groupId = state.summaryView.filters.group_id || 'all';
    if (groupId === 'all') return { ...rawData, accounts: allAccounts, scope: { group_id: 'all', name: 'Все кабинеты' } };

    const group = state.accountGroups.find(item => String(item.id) === groupId);
    if (!group) {
      state.summaryView.filters.group_id = 'all';
      return { ...rawData, accounts: allAccounts, scope: { group_id: 'all', name: 'Все кабинеты' } };
    }

    const accountIdSet = new Set((group.account_ids || []).map(String));
    const accounts = allAccounts.filter(account => accountIdSet.has(String(account.account_id)));
    const synced = accounts.filter(summaryAccountHasMetrics);
    const sum = key => synced.reduce((total, account) => total + Number(account[key] || 0), 0);
    const totalSpendAcrossCurrencies = sum('spend');
    const totalClicks = sum('clicks');
    const totalImpressions = sum('impressions');
    const totalReach = sum('reach');
    const totalUniqueClicks = sum('unique_clicks');
    const totalLinkClicks = sum('link_clicks');
    const totalOutboundClicks = sum('outbound_clicks');
    const totalLandingPageViews = sum('landing_page_views');
    const totalLeads = sum('leads');
    const totalRegs = sum('registrations');
    const totalPurchases = sum('purchases');
    const buckets = new Map();
    synced.forEach(account => {
      const currency = normalizeCurrencyCode(account.currency) || 'UNKNOWN';
      const bucket = buckets.get(currency) || {
        currency, accounts_count: 0, spend: 0, impressions: 0, clicks: 0,
        link_clicks: 0, landing_page_views: 0, leads: 0, registrations: 0, purchases: 0
      };
      bucket.accounts_count += 1;
      ['spend', 'impressions', 'clicks', 'link_clicks', 'landing_page_views', 'leads', 'registrations', 'purchases']
        .forEach(key => { bucket[key] += Number(account[key] || 0); });
      buckets.set(currency, bucket);
    });
    const currencyTotals = Array.from(buckets.values())
      .sort((left, right) => left.currency.localeCompare(right.currency))
      .map(bucket => ({
        ...bucket,
        spend: Math.round(bucket.spend * 100) / 100,
        cpm: summaryCost(bucket.spend * 1000, bucket.impressions),
        cpc: summaryCost(bucket.spend, bucket.clicks),
        cpc_link: summaryCost(bucket.spend, bucket.link_clicks),
        cost_per_landing_page_view: summaryCost(bucket.spend, bucket.landing_page_views),
        cost_per_lead: summaryCost(bucket.spend, bucket.leads),
        cost_per_registration: summaryCost(bucket.spend, bucket.registrations),
        cost_per_purchase: summaryCost(bucket.spend, bucket.purchases)
      }));
    const displayCurrency = currencyTotals.length === 1 && currencyTotals[0].currency !== 'UNKNOWN'
      ? currencyTotals[0].currency
      : '';
    const monetaryTotalsAvailable = Boolean(displayCurrency);
    const blocked = accounts.filter(account => summaryAccountStatusKey(account) === 'blocked').length;
    const failed = accounts.filter(account => summaryAccountStatusKey(account) === 'error').length;
    const coverage = accounts.length ? Math.round((synced.length / accounts.length) * 1000) / 10 : 0;
    const qualityStatus = synced.length === accounts.length && accounts.length
      ? 'complete'
      : (synced.length ? 'partial' : 'unavailable');

    return {
      ...rawData,
      accounts,
      accounts_count: accounts.length,
      total_spend: monetaryTotalsAvailable ? Math.round(totalSpendAcrossCurrencies * 100) / 100 : null,
      display_currency: displayCurrency,
      mixed_currencies: currencyTotals.length > 1,
      currency_totals: currencyTotals,
      total_clicks: totalClicks,
      total_impressions: totalImpressions,
      total_reach: totalReach,
      total_unique_clicks: totalUniqueClicks,
      total_link_clicks: totalLinkClicks,
      total_outbound_clicks: totalOutboundClicks,
      total_landing_page_views: totalLandingPageViews,
      total_leads: totalLeads,
      total_regs: totalRegs,
      total_purchases: totalPurchases,
      avg_cpc: monetaryTotalsAvailable ? summaryCost(totalSpendAcrossCurrencies, totalClicks) : null,
      avg_ctr: totalImpressions > 0 ? (totalClicks / totalImpressions) * 100 : 0,
      avg_frequency: totalReach > 0 ? totalImpressions / totalReach : null,
      avg_cpm: monetaryTotalsAvailable ? summaryCost(totalSpendAcrossCurrencies * 1000, totalImpressions) : null,
      avg_cpc_link: monetaryTotalsAvailable ? summaryCost(totalSpendAcrossCurrencies, totalLinkClicks) : null,
      avg_ctr_link: totalImpressions > 0 ? (totalLinkClicks / totalImpressions) * 100 : null,
      avg_ctr_outbound: totalImpressions > 0 ? (totalOutboundClicks / totalImpressions) * 100 : null,
      cost_per_landing_page_view: monetaryTotalsAvailable ? summaryCost(totalSpendAcrossCurrencies, totalLandingPageViews) : null,
      cost_per_lead: monetaryTotalsAvailable ? summaryCost(totalSpendAcrossCurrencies, totalLeads) : null,
      cost_per_registration: monetaryTotalsAvailable ? summaryCost(totalSpendAcrossCurrencies, totalRegs) : null,
      cost_per_purchase: monetaryTotalsAvailable ? summaryCost(totalSpendAcrossCurrencies, totalPurchases) : null,
      data_quality: {
        status: qualityStatus,
        accounts_total: accounts.length,
        accounts_synced: synced.length,
        accounts_failed: failed,
        accounts_blocked: blocked,
        metrics_coverage_percent: coverage
      },
      snapshot: rawData.snapshot ? { ...rawData.snapshot, previous: null } : rawData.snapshot,
      scope: { group_id: groupId, name: group.name, description: group.description || '' }
    };
  }

  function summaryAccountSortValue(account, key) {
    if (key === 'account') return String(account.short_name || account.name || account.account_id || '').toLocaleLowerCase('ru');
    if (key === 'custom_name') return String(account.custom_name || '').toLocaleLowerCase('ru');
    if (key === 'note') return String(account.note || '').toLocaleLowerCase('ru');
    if (key === 'data') return summaryAccountStatusKey(account);
    if (!summaryAccountHasMetrics(account)) return null;
    const metricMap = {
      spend: 'spend', impressions: 'impressions', reach: 'reach', frequency: 'frequency', cpm: 'cpm',
      clicks: 'clicks', link_clicks: 'link_clicks', unique_clicks: 'unique_clicks',
      outbound_clicks: 'outbound_clicks', landing_page_views: 'landing_page_views',
      ctr: 'ctr', ctr_link: 'ctr_link', cpc: 'cpc', cpc_link: 'cpc_link',
      leads: 'leads', registrations: 'registrations', purchases: 'purchases',
      cpl: 'cost_per_lead', cpreg: 'cost_per_registration', cpp: 'cost_per_purchase'
    };
    const value = account[metricMap[key]];
    if (value === null || value === undefined || value === '') return null;
    const numericValue = Number(value);
    return Number.isFinite(numericValue) ? numericValue : null;
  }

  function filteredSortedSummaryAccounts(accounts = []) {
    const query = state.summaryView.filters.query.toLocaleLowerCase('ru');
    const status = state.summaryView.filters.status;
    const filtered = accounts.filter(account => {
      if (status !== 'all' && summaryAccountStatusKey(account) !== status) return false;
      if (!query) return true;
      const groupNames = accountGroupsFor(account).map(group => group.name);
      return [account.short_name, account.name, account.custom_name, account.note, account.account_id, ...groupNames]
        .some(value => String(value || '').toLocaleLowerCase('ru').includes(query));
    });

    const sortColumn = state.summaryView.sort_column;
    if (!sortColumn) return filtered;
    const direction = state.summaryView.sort_direction === 'asc' ? 1 : -1;
    return filtered
      .map((account, index) => ({ account, index }))
      .sort((left, right) => {
        const leftValue = summaryAccountSortValue(left.account, sortColumn);
        const rightValue = summaryAccountSortValue(right.account, sortColumn);
        const leftMissing = leftValue === null || leftValue === undefined || Number.isNaN(leftValue);
        const rightMissing = rightValue === null || rightValue === undefined || Number.isNaN(rightValue);
        if (leftMissing && rightMissing) return left.index - right.index;
        if (leftMissing) return 1;
        if (rightMissing) return -1;
        const comparison = typeof leftValue === 'string'
          ? leftValue.localeCompare(String(rightValue), 'ru', { numeric: true, sensitivity: 'base' })
          : leftValue - rightValue;
        return comparison === 0 ? left.index - right.index : comparison * direction;
      })
      .map(item => item.account);
  }

  function renderSummaryAccountRows(data) {
    const allAccounts = Array.isArray(data?.accounts) ? data.accounts : [];
    const accounts = filteredSortedSummaryAccounts(allAccounts);
    const rowsCount = document.getElementById('summaryRowsCount');
    const hasActiveFilters = Boolean(state.summaryView.filters.query) || state.summaryView.filters.status !== 'all';
    if (rowsCount) {
      rowsCount.textContent = hasActiveFilters
        ? `${accounts.length} из ${allAccounts.length} кабинетов`
        : `${accounts.length} кабинетов`;
    }

    const tableBody = document.getElementById('summaryTableBody');
    if (accounts.length === 0) {
      const emptyMessage = allAccounts.length === 0
        ? 'Нет подключенных кабинетов'
        : 'По выбранным фильтрам кабинеты не найдены';
      tableBody.innerHTML = `<tr><td data-summary-empty colspan="${summaryVisibleColumnCount()}" class="text-center" style="color: var(--tg-hint);">${emptyMessage}</td></tr>`;
    } else {
      tableBody.innerHTML = accounts.map(acc => {
        const hasMetrics = summaryAccountHasMetrics(acc);
        const spendStr = hasMetrics ? formatMoneyOrDash(Number(acc.spend || 0), acc.currency) : '—';
        const displayName = acc.short_name || acc.name;

        return `
          <tr>
            <td data-summary-column="account"><b>${escapeHtml(displayName)}</b> <span class="mono text-hint" style="font-size:11px;">(${acc.account_id})</span></td>
            <td class="summary-custom-name-cell" data-summary-column="custom_name">${acc.custom_name ? `<b>${escapeHtml(acc.custom_name)}</b>` : '<span class="text-hint">—</span>'}</td>
            <td class="summary-note-cell" data-summary-column="note" title="${escapeHtml(acc.note || '')}">${acc.note ? escapeHtml(acc.note) : '<span class="text-hint">—</span>'}</td>
            <td data-summary-column="data">${summaryDataStatus(acc)}</td>
            <td class="text-right mono" data-summary-column="spend"><b>${spendStr}</b></td>
            <td class="text-right mono" data-summary-column="impressions">${hasMetrics ? formatNumber(acc.impressions) : '—'}</td>
            <td class="text-right mono" data-summary-column="reach">${hasMetrics ? formatOptionalNumber(acc.reach) : '—'}</td>
            <td class="text-right mono" data-summary-column="frequency">${hasMetrics ? formatDecimalOrDash(acc.frequency) : '—'}</td>
            <td class="text-right mono" data-summary-column="cpm">${hasMetrics ? formatMoneyOrDash(acc.cpm, acc.currency) : '—'}</td>
            <td class="text-right mono" data-summary-column="clicks">${hasMetrics ? formatNumber(acc.clicks) : '—'}</td>
            <td class="text-right mono" data-summary-column="link_clicks">${hasMetrics ? formatOptionalNumber(acc.link_clicks) : '—'}</td>
            <td class="text-right mono" data-summary-column="unique_clicks">${hasMetrics ? formatOptionalNumber(acc.unique_clicks) : '—'}</td>
            <td class="text-right mono" data-summary-column="outbound_clicks">${hasMetrics ? formatOptionalNumber(acc.outbound_clicks) : '—'}</td>
            <td class="text-right mono" data-summary-column="landing_page_views">${hasMetrics ? formatOptionalNumber(acc.landing_page_views) : '—'}</td>
            <td class="text-right mono" data-summary-column="ctr">${hasMetrics ? Number(acc.ctr || 0).toFixed(2) + '%' : '—'}</td>
            <td class="text-right mono" data-summary-column="ctr_link">${hasMetrics ? formatDecimalOrDash(acc.ctr_link, 2, '%') : '—'}</td>
            <td class="text-right mono" data-summary-column="cpc">${hasMetrics ? formatMoneyOrDash(acc.cpc, acc.currency) : '—'}</td>
            <td class="text-right mono" data-summary-column="cpc_link">${hasMetrics ? formatMoneyOrDash(acc.cpc_link, acc.currency) : '—'}</td>
            <td class="text-right mono" data-summary-column="leads" style="color:var(--tg-link);">${hasMetrics ? formatNumber(acc.leads) : '—'}</td>
            <td class="text-right mono" data-summary-column="registrations" style="color:var(--color-success);">${hasMetrics ? formatNumber(acc.registrations) : '—'}</td>
            <td class="text-right mono" data-summary-column="purchases">${hasMetrics ? formatNumber(acc.purchases) : '—'}</td>
            <td class="text-right mono" data-summary-column="cpl">${hasMetrics ? formatMoneyOrDash(acc.cost_per_lead, acc.currency) : '—'}</td>
            <td class="text-right mono" data-summary-column="cpreg">${hasMetrics ? formatMoneyOrDash(acc.cost_per_registration, acc.currency) : '—'}</td>
            <td class="text-right mono" data-summary-column="cpp"><b>${hasMetrics ? formatMoneyOrDash(acc.cost_per_purchase, acc.currency) : '—'}</b></td>
          </tr>`;
      }).join('');
    }
    applySummaryColumnVisibility();
    return accounts;
  }

  function renderSummaryData(rawData) {
    const data = buildScopedSummaryData(rawData);
    // KPI Cards
    const displayCurrency = normalizeCurrencyCode(data.display_currency);
    document.getElementById('kpiSpend').textContent = displayCurrency
      ? formatMoneyOrDash(Number(data.total_spend), displayCurrency)
      : (data.mixed_currencies ? 'По валютам' : '—');
    document.getElementById('kpiLeads').textContent = formatNumber(data.total_leads);
    document.getElementById('kpiRegs').textContent = formatNumber(data.total_regs);
    document.getElementById('kpiCpl').textContent = formatMoneyOrDash(data.cost_per_lead, displayCurrency);
    document.getElementById('kpiCpreg').textContent = formatMoneyOrDash(data.cost_per_registration, displayCurrency);
    document.getElementById('kpiPurchases').textContent = formatNumber(data.total_purchases);
    document.getElementById('kpiCpp').textContent = formatMoneyOrDash(data.cost_per_purchase, displayCurrency);
    document.getElementById('kpiImpressions').textContent = formatOptionalNumber(data.total_impressions);
    document.getElementById('kpiReach').textContent = formatOptionalNumber(data.total_reach);
    document.getElementById('kpiFrequency').textContent = formatDecimalOrDash(data.avg_frequency);
    document.getElementById('kpiCpm').textContent = formatMoneyOrDash(data.avg_cpm, displayCurrency);
    document.getElementById('kpiClicks').textContent = formatNumber(data.total_clicks);
    document.getElementById('kpiCtr').textContent = data.total_impressions > 0 ? `${Number(data.avg_ctr || 0).toFixed(2)}%` : '—';
    document.getElementById('kpiCpc').textContent = data.total_clicks > 0 ? formatMoneyOrDash(data.avg_cpc, displayCurrency) : '—';
    document.getElementById('kpiLinkClicks').textContent = formatOptionalNumber(data.total_link_clicks);
    document.getElementById('kpiLinkCtr').textContent = formatDecimalOrDash(data.avg_ctr_link, 2, '%');
    document.getElementById('kpiCpcLink').textContent = formatMoneyOrDash(data.avg_cpc_link, displayCurrency);
    document.getElementById('kpiOutboundClicks').textContent = formatOptionalNumber(data.total_outbound_clicks);
    document.getElementById('kpiOutboundCtr').textContent = formatDecimalOrDash(data.avg_ctr_outbound, 2, '%');
    document.getElementById('kpiLandingPageViews').textContent = formatOptionalNumber(data.total_landing_page_views);
    document.getElementById('kpiCostPerLandingPageView').textContent = formatMoneyOrDash(data.cost_per_landing_page_view, displayCurrency);
    document.getElementById('kpiUniqueClicks').textContent = formatOptionalNumber(data.total_unique_clicks);
    renderSpendComparison(data);
    renderSummaryProvenance(data);
    renderSummaryQuality(data.data_quality || {});
    renderCurrencyBreakdown(data);
    renderMetricDefinitions(data.metric_definitions || {});

    const visibleAccounts = renderSummaryAccountRows(data);

    // Mobile Cards
    const mobileCards = document.getElementById('summaryMobileCards');
    if (visibleAccounts.length === 0) {
      const emptyMessage = Array.isArray(data.accounts) && data.accounts.length > 0
        ? 'По выбранным фильтрам кабинеты не найдены'
        : 'Нет данных для отображения';
      mobileCards.innerHTML = `<div class="empty-state"><p>${emptyMessage}</p></div>`;
    } else {
      mobileCards.innerHTML = visibleAccounts.map(acc => {
        const metaName = acc.short_name || acc.name;
        const displayName = acc.custom_name || metaName;
        const subLabel = acc.custom_name
          ? `${escapeHtml(metaName)} · ${acc.account_id}`
          : (acc.name !== metaName ? `${escapeHtml(acc.name)} · ${acc.account_id}` : acc.account_id);
        const hasMetrics = summaryAccountHasMetrics(acc);
        const statusPillHtml = summaryDataStatus(acc);
        const noteHtml = acc.note
          ? `<p class="mob-summary-note">${escapeHtml(acc.note)}</p>`
          : '';
        const groupsHtml = renderAccountGroupTags(acc, { compact: true });

        return `
          <div class="mob-summary-card">
            <div class="mob-card-head">
              <div style="display:flex; flex-direction:column; overflow:hidden; padding-right:8px; flex:1;">
                <b class="mob-card-name" style="font-size:14px; font-weight:600; color:var(--tg-text); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${escapeHtml(displayName)}</b>
                <span class="mono text-hint" style="font-size:11px; color:var(--tg-hint); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${subLabel}</span>
                ${statusPillHtml}
              </div>
              <span class="mono" style="font-size:16px;font-weight:700;color:var(--tg-link);white-space:nowrap;flex-shrink:0;">${hasMetrics ? formatMoneyOrDash(Number(acc.spend || 0), acc.currency) : '—'}</span>
            </div>
            ${noteHtml}
            ${groupsHtml ? `<div class="mob-summary-groups">${groupsHtml}</div>` : ''}
            <span class="mob-card-section-label">Доставка</span>
            <div class="mob-card-stats">
              <div class="stat-box">
                <span class="stat-box-label">Показы</span>
                <span class="stat-box-val">${hasMetrics ? formatNumber(acc.impressions) : '—'}</span>
              </div>
              <div class="stat-box">
                <span class="stat-box-label">Охват</span>
                <span class="stat-box-val">${hasMetrics ? formatOptionalNumber(acc.reach) : '—'}</span>
              </div>
              <div class="stat-box">
                <span class="stat-box-label">Частота</span>
                <span class="stat-box-val">${hasMetrics ? formatDecimalOrDash(acc.frequency) : '—'}</span>
              </div>
              <div class="stat-box">
                <span class="stat-box-label">CPM</span>
                <span class="stat-box-val">${hasMetrics ? formatMoneyOrDash(acc.cpm, acc.currency) : '—'}</span>
              </div>
            </div>
            <span class="mob-card-section-label">Трафик</span>
            <div class="mob-card-stats">
              <div class="stat-box">
                <span class="stat-box-label">Все клики</span>
                <span class="stat-box-val">${hasMetrics ? formatNumber(acc.clicks) : '—'}</span>
              </div>
              <div class="stat-box">
                <span class="stat-box-label">Link</span>
                <span class="stat-box-val">${hasMetrics ? formatOptionalNumber(acc.link_clicks) : '—'}</span>
              </div>
              <div class="stat-box">
                <span class="stat-box-label">Outbound</span>
                <span class="stat-box-val">${hasMetrics ? formatOptionalNumber(acc.outbound_clicks) : '—'}</span>
              </div>
              <div class="stat-box">
                <span class="stat-box-label">LP Views</span>
                <span class="stat-box-val">${hasMetrics ? formatOptionalNumber(acc.landing_page_views) : '—'}</span>
              </div>
            </div>
            <span class="mob-card-section-label">Воронка</span>
            <div class="mob-card-stats">
              <div class="stat-box">
                <span class="stat-box-label">Лиды</span>
                <span class="stat-box-val" style="color:var(--tg-link);">${hasMetrics ? formatNumber(acc.leads) : '—'}</span>
              </div>
              <div class="stat-box">
                <span class="stat-box-label">CPL</span>
                <span class="stat-box-val">${hasMetrics ? formatMoneyOrDash(acc.cost_per_lead, acc.currency) : '—'}</span>
              </div>
              <div class="stat-box">
                <span class="stat-box-label">Регистрации</span>
                <span class="stat-box-val" style="color:var(--color-success);">${hasMetrics ? formatNumber(acc.registrations) : '—'}</span>
              </div>
              <div class="stat-box">
                <span class="stat-box-label">CPReg</span>
                <span class="stat-box-val">${hasMetrics ? formatMoneyOrDash(acc.cost_per_registration, acc.currency) : '—'}</span>
              </div>
              <div class="stat-box">
                <span class="stat-box-label">Покупки</span>
                <span class="stat-box-val">${hasMetrics ? formatNumber(acc.purchases) : '—'}</span>
              </div>
              <div class="stat-box">
                <span class="stat-box-label">CPP</span>
                <span class="stat-box-val">${hasMetrics ? formatMoneyOrDash(acc.cost_per_purchase, acc.currency) : '—'}</span>
              </div>
            </div>
          </div>
        `;
      }).join('');
    }
  }


  const auditEventLabels = {
    STOP: 'Ad set остановлен',
    NOTIFY_ONLY: 'Отправлено уведомление',
    AUTO_REACTIVATE: 'Ad set включён автоматически',
    MANUAL_REACTIVATE: 'Ad set включён вручную',
    PROPOSE_REACTIVATE: 'Предложено включение',
    INCREASE_BUDGET: 'Бюджет увеличен',
    DECREASE_BUDGET: 'Бюджет уменьшен',
    RULE_ACTION_COOLDOWN: 'Пропуск по cooldown',
    ACCOUNT_ISSUE: 'Проблема кабинета',
    TOKEN_EXPIRED: 'Проблема токена',
    ACCOUNT_DAY_STARTED: 'Новые сутки кабинета',
    DAY_START: 'Старое обнаружение Spend',
    HIDE_STOPPED_NOTIFICATION: 'Карточка остановки скрыта',
    MANUAL_PAUSE: 'Ad set остановлен вручную',
    UNDO_ACTION: 'Действие отменено',
    UNDO_ACTION_FAILED: 'Ошибка отмены',
    RULE_ACTION_PENDING: 'Действие зафиксировано',
    RULE_ACTION_RECONCILED: 'Действие сверено с Meta',
    RULE_ACTION: 'Действие правила'
  };

  const auditStatusLabels = {
    SUCCESS: 'Выполнено',
    ERROR: 'Ошибка',
    SKIPPED: 'Пропущено',
    REVERTED: 'Отменено',
    WARNING: 'Внимание',
    INFO: 'Информация'
  };

  function auditStatusBadge(status) {
    const normalized = (status || 'INFO').toUpperCase();
    const modifier = ['SUCCESS', 'ERROR', 'WARNING'].includes(normalized)
      ? normalized.toLowerCase()
      : normalized === 'SKIPPED' ? 'warning' : 'info';
    return `<span class="log-status log-status-${modifier}"><span class="status-dot dot-${modifier === 'error' ? 'danger' : modifier}"></span>${auditStatusLabels[normalized] || escapeHtml(normalized)}</span>`;
  }

  function formatAuditTime(value, compact = false) {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return escapeHtml(value);
    return new Intl.DateTimeFormat('ru-RU', {
      day: '2-digit', month: '2-digit',
      hour: '2-digit', minute: '2-digit', second: compact ? undefined : '2-digit'
    }).format(date);
  }

  function auditTarget(event) {
    return event.account_name || event.account_id || event.adset_name || event.adset_id || 'Система';
  }

  function auditEventLabel(event) {
    return auditEventLabels[(event.event_type || '').toUpperCase()] || (event.event_type || event.category || 'Событие');
  }

  function populateLogsAccountFilter() {
    const select = document.getElementById('logsAccountFilter');
    if (!select) return;
    const current = select.value;
    select.innerHTML = '<option value="">Все кабинеты</option>' + state.accounts.map(account =>
      `<option value="${escapeHtml(account.account_id)}">${escapeHtml(accountDisplayName(account))}</option>`
    ).join('');
    select.value = state.pendingLogsAccountId || current;
    state.pendingLogsAccountId = '';
  }

  async function loadLogsTab(page = 1) {
    const tableBody = document.getElementById('logsTableBody');
    const refreshBtn = document.getElementById('btnRefreshLogs');
    if (!tableBody) return;
    state.auditPage = Math.max(1, page);
    refreshBtn?.classList.add('loading');

    if (state.accounts.length === 0) {
      await loadAccounts();
    }
    populateLogsAccountFilter();

    const params = new URLSearchParams({ page: state.auditPage.toString(), page_size: '25' });
    const category = document.getElementById('logsCategoryFilter')?.value;
    const status = document.getElementById('logsStatusFilter')?.value;
    const accountId = document.getElementById('logsAccountFilter')?.value;
    const search = document.getElementById('logsSearchInput')?.value.trim();
    if (category) params.set('category', category);
    if (status) params.set('status', status);
    if (accountId) params.set('account_id', accountId);
    if (search) params.set('search', search);

    try {
      const [data] = await Promise.all([
        apiRequest(`/api/audit-events?${params.toString()}`),
        loadStoppedAdsets()
      ]);
      state.auditEvents = data.items || [];
      state.auditPage = data.page || 1;
      state.auditTotalPages = data.total_pages || 1;
      renderAuditEvents(data);
    } catch (err) {
      tableBody.innerHTML = `<tr><td colspan="7" class="text-danger text-center">${escapeHtml(err.message)}</td></tr>`;
      document.getElementById('logsMobileList').innerHTML = `<div class="empty-state"><p class="text-danger">${escapeHtml(err.message)}</p></div>`;
    } finally {
      refreshBtn?.classList.remove('loading');
    }
  }

  function renderAuditEvents(data) {
    const events = state.auditEvents;
    const tableBody = document.getElementById('logsTableBody');
    const mobileList = document.getElementById('logsMobileList');
    const emptyState = document.getElementById('logsEmptyState');
    const pagination = document.querySelector('.logs-pagination');
    const counts = data.status_counts || {};

    document.getElementById('logsTotalCount').textContent = data.total || 0;
    document.getElementById('logsSuccessCount').textContent = counts.SUCCESS || 0;
    document.getElementById('logsErrorCount').textContent = counts.ERROR || 0;
    document.getElementById('logsSkippedCount').textContent = counts.SKIPPED || 0;
    document.getElementById('logsPageLabel').textContent = `Страница ${state.auditPage} из ${state.auditTotalPages}`;
    document.getElementById('btnLogsPrev').disabled = state.auditPage <= 1;
    document.getElementById('btnLogsNext').disabled = state.auditPage >= state.auditTotalPages;

    emptyState.classList.toggle('hidden', events.length > 0);
    pagination.classList.toggle('hidden', events.length === 0);
    if (events.length === 0) {
      tableBody.innerHTML = '';
      mobileList.innerHTML = '';
      return;
    }

    tableBody.innerHTML = events.map(event => `
      <tr>
        <td class="mono text-hint">${formatAuditTime(event.created_at)}</td>
        <td>${auditStatusBadge(event.display_status || event.status)}</td>
        <td class="log-event-cell">${escapeHtml(auditEventLabel(event))}</td>
        <td class="log-target">${escapeHtml(auditTarget(event))}<small>${escapeHtml(event.adset_name || event.adset_id || event.account_id || '')}</small></td>
        <td>${escapeHtml(event.rule_name || '—')}</td>
        <td class="log-message">${escapeHtml(event.message || 'Без дополнительного сообщения')}<small>${event.action ? `Действие: ${escapeHtml(event.action)}` : ''}</small></td>
        <td><button class="log-row-action" type="button" onclick="window.openLogDetails(${event.id})">Детали</button></td>
      </tr>`).join('');

    mobileList.innerHTML = events.map(event => `
      <article class="log-mobile-card" onclick="window.openLogDetails(${event.id})" role="button" tabindex="0">
        <div class="log-mobile-head">${auditStatusBadge(event.display_status || event.status)}<span class="log-mobile-time">${formatAuditTime(event.created_at, true)}</span></div>
        <h4>${escapeHtml(auditEventLabel(event))}</h4>
        <p>${escapeHtml(event.message || 'Без дополнительного сообщения')}</p>
        <div class="log-mobile-target"><span>${escapeHtml(auditTarget(event))}</span><span>${escapeHtml(event.rule_name || '')}</span></div>
      </article>`).join('');
  }

  window.openLogDetails = function (eventId) {
    const event = state.auditEvents.find(item => item.id === eventId);
    const content = document.getElementById('logDetailsContent');
    if (!event || !content) return;
    const detailsJson = Object.keys(event.details || {}).length ? JSON.stringify(event.details, null, 2) : 'Нет дополнительных данных';
    const stateJson = Object.keys(event.before_state || {}).length || Object.keys(event.after_state || {}).length
      ? JSON.stringify({ before: event.before_state || {}, after: event.after_state || {} }, null, 2)
      : 'Изменения состояния не зафиксированы';
    content.innerHTML = `
      <div class="log-details-grid">
        <div class="log-detail-block"><span>Статус</span>${auditStatusBadge(event.display_status || event.status)}</div>
        <div class="log-detail-block"><span>Время</span><b>${formatAuditTime(event.created_at)}</b></div>
        <div class="log-detail-block"><span>Событие</span><b>${escapeHtml(auditEventLabel(event))}</b></div>
        <div class="log-detail-block"><span>Инициатор</span><b>${escapeHtml(event.actor_type || 'system')}</b></div>
        <div class="log-detail-block"><span>Кабинет</span><b>${escapeHtml(event.account_name || event.account_id || '—')}</b></div>
        <div class="log-detail-block"><span>Ad set</span><b>${escapeHtml(event.adset_name || event.adset_id || '—')}</b></div>
        <div class="log-detail-block"><span>Правило</span><b>${escapeHtml(event.rule_name || '—')}</b></div>
        <div class="log-detail-block"><span>Действие</span><b>${escapeHtml(event.action || '—')}</b></div>
        <div class="log-detail-block wide"><span>Описание</span><p>${escapeHtml(event.message || '—')}</p></div>
        <div class="log-detail-block wide"><span>Метрики и условия</span><pre class="log-json">${escapeHtml(detailsJson)}</pre></div>
        <div class="log-detail-block wide"><span>До / после</span><pre class="log-json">${escapeHtml(stateJson)}</pre></div>
        <div class="log-detail-block wide"><span>ID операции</span><p class="mono">${escapeHtml(event.correlation_id || '—')}</p></div>
        ${event.reverts_event_id ? `<div class="log-detail-block wide"><span>Отменяет событие</span><p class="mono">#${event.reverts_event_id}</p></div>` : ''}
        ${event.reverted_by_event_id ? `<div class="log-detail-block wide"><span>Событие отмены</span><p class="mono">#${event.reverted_by_event_id}</p></div>` : ''}
        ${event.can_undo ? `<div class="log-detail-block wide"><button id="btnUndoAuditEvent" class="btn btn-secondary btn-block" type="button" onclick="window.undoAuditEvent(${event.id})">Отменить действие</button></div>` : ''}
        ${!event.can_undo && event.undo_reason ? `<div class="log-detail-block wide"><span>Отмена недоступна</span><p>${escapeHtml(event.undo_reason)}</p></div>` : ''}
      </div>`;
    window.openModal('modalLogDetails');
  };

  window.undoAuditEvent = async function (eventId) {
    const button = document.getElementById('btnUndoAuditEvent');
    if (button) {
      button.disabled = true;
      button.textContent = 'Сверяем с Meta…';
    }
    haptic('impact', 'medium');
    try {
      const result = await apiRequest(`/api/audit-events/${eventId}/undo`, { method: 'POST' });
      showToast(result.message || 'Действие отменено', 'success');
      window.closeModal('modalLogDetails');
      await loadLogsTab(state.auditPage);
    } catch (error) {
      showToast(error.message || 'Не удалось отменить действие', 'error');
      if (button) {
        button.disabled = false;
        button.textContent = 'Отменить действие';
      }
    }
  };

  function renderStoppedAdsets() {
    const records = state.stoppedAdsets;
    const section = document.getElementById('logsStoppedSection');
    const listEl = document.getElementById('stoppedAdsetsList');
    const countBadge = document.getElementById('stoppedCountBadge');
    const banner = document.getElementById('logsAttentionBanner');
    const hasRecords = records.length > 0;

    section?.classList.toggle('hidden', !hasRecords);
    banner?.classList.toggle('hidden', !hasRecords);
    if (countBadge) countBadge.textContent = records.length.toString();
    if (document.getElementById('logsAttentionCount')) document.getElementById('logsAttentionCount').textContent = records.length;
    if (!listEl) return;

    listEl.innerHTML = records.map(record => `
      <div class="stopped-card-item" id="stopped-item-${escapeHtml(record.adset_id)}">
        <div>
          <b>${escapeHtml(record.adset_name)}</b> <span class="mono text-hint">${escapeHtml(record.account_id)}</span>
          <div style="font-size:11px;color:var(--tg-hint);margin-top:2px;">
            Остановлен ${escapeHtml(record.stopped_at || '—')} · Спенд <b>${formatMoneyOrDash(Number(record.stop_spend || 0), record.currency)}</b> · Лиды ${record.stop_leads || 0} · Реги ${record.stop_registrations || 0}
          </div>
        </div>
        <div style="display:flex;gap:6px;flex-shrink:0;">
          <button class="btn btn-primary btn-sm" onclick="window.reactivateAdset('${escapeHtml(record.adset_id)}')">Включить обратно</button>
          <button class="btn btn-secondary btn-sm" title="Скрыть карточку из списка" onclick="window.dismissAdset('${escapeHtml(record.adset_id)}')">Скрыть</button>
        </div>
      </div>`).join('');
  }

  // Load stopped adsets independently from the audit history.
  async function loadStoppedAdsets() {
    try {
      const records = await apiRequest('/api/adsets/stopped');
      state.stoppedAdsets = records || [];
      renderStoppedAdsets();
    } catch (e) {
      state.stoppedAdsets = [];
      renderStoppedAdsets();
    }
  }

  window.reactivateAdset = async function (adsetId) {
    haptic('impact', 'medium');
    try {
      const res = await apiRequest(`/api/adsets/${adsetId}/reactivate`, { method: 'POST' });
      showToast(res.message, 'success');
      state.stoppedAdsets = state.stoppedAdsets.filter(item => item.adset_id !== adsetId);
      renderStoppedAdsets();
      if (state.activeTab === 'logs') loadLogsTab(state.auditPage);
    } catch (err) {
      showToast(`Ошибка: ${err.message}`, 'error');
    }
  };

  window.dismissAdset = async function (adsetId) {
    try {
      await apiRequest(`/api/adsets/${adsetId}/dismiss`, { method: 'POST' });
      state.stoppedAdsets = state.stoppedAdsets.filter(item => item.adset_id !== adsetId);
      renderStoppedAdsets();
      showToast('Карточка скрыта. Ad set остался выключенным.', 'success');
      if (state.activeTab === 'logs') loadLogsTab(state.auditPage);
    } catch (err) {
      showToast(`Ошибка: ${err.message}`, 'error');
    }
  };

  // ==========================================================
  // TAB 4: META OAUTH + BATCH ADD
  // ==========================================================
  function consumeMetaOAuthCallback() {
    if (state.metaOAuth.callbackHandled) return {};
    state.metaOAuth.callbackHandled = true;
    const params = new URLSearchParams(window.location.search);
    const status = params.get('meta_status') || '';
    const connectionId = Number(params.get('meta_connection') || 0) || null;
    if (status) {
      const messages = {
        connected: ['Facebook-профиль подключён. Загружаем кабинеты…', 'success'],
        cancelled: ['Подключение Facebook отменено.', 'info'],
        expired_state: ['Ссылка входа истекла. Запустите подключение ещё раз.', 'error'],
        invalid_callback: ['Meta вернула неполный ответ. Запустите подключение ещё раз.', 'error'],
        not_configured: ['OAuth Meta ещё не настроен на сервере.', 'error'],
        connection_failed: ['Не удалось подтвердить подключение Meta. Попробуйте войти ещё раз.', 'error']
      };
      const item = messages[status] || ['Не удалось завершить подключение Meta.', 'error'];
      showToast(item[0], item[1]);
      try { window.history.replaceState({}, '', window.location.pathname); } catch (e) {}
    }
    return { status, connectionId };
  }

  function renderMetaConnections() {
    const container = document.getElementById('metaConnectionsList');
    if (!container) return;
    if (!state.metaOAuth.connections.length) {
      container.innerHTML = '';
      return;
    }
    container.innerHTML = state.metaOAuth.connections.map(connection => {
      const healthy = connection.status === 'active';
      return `
        <div class="meta-connection-row ${healthy ? '' : 'needs-reconnect'}">
          <div class="meta-connection-avatar">f</div>
          <div class="meta-connection-info">
            <b>${escapeHtml(connection.provider_user_name || 'Facebook-профиль')}</b>
            <span>${healthy ? 'Подключение активно' : 'Требуется повторный вход'} · ID ${escapeHtml(connection.provider_user_id)}</span>
          </div>
          <button class="btn btn-secondary btn-sm" type="button" onclick="window.openMetaConnection(${connection.id})">
            ${healthy ? 'Показать кабинеты' : 'Переподключить'}
          </button>
        </div>`;
    }).join('');
  }

  async function loadMetaConnections() {
    const statusEl = document.getElementById('metaOAuthStatus');
    const setupEl = document.getElementById('metaOAuthSetupNotice');
    const startButton = document.getElementById('btnStartMetaOAuth');
    const callback = consumeMetaOAuthCallback();
    try {
      const config = await apiRequest('/api/meta/oauth/config');
      state.metaOAuth.configured = Boolean(config.configured);
      if (!config.configured) {
        const missing = Object.entries(config.checks || {}).filter(([, ready]) => !ready).map(([key]) => key);
        statusEl.innerHTML = '<span class="status-dot dot-warning"></span><span>Серверная авторизация Meta требует настройки</span>';
        setupEl.textContent = `Не заполнено на сервере: ${missing.join(', ')}. Данные текущих кабинетов продолжают работать.`;
        setupEl.classList.remove('hidden');
        startButton.disabled = true;
      } else {
        statusEl.innerHTML = '<span class="status-dot dot-success"></span><span>OAuth готов · защищённое подключение через Meta</span>';
        setupEl.classList.add('hidden');
        startButton.disabled = false;
      }
      state.metaOAuth.connections = await apiRequest('/api/meta/connections');
      renderMetaConnections();
      if (callback.status === 'connected' && callback.connectionId) {
        await discoverMetaAssets(callback.connectionId);
      }
    } catch (err) {
      statusEl.innerHTML = `<span class="status-dot dot-danger"></span><span>${escapeHtml(err.message)}</span>`;
      startButton.disabled = true;
    }
  }

  document.getElementById('btnStartMetaOAuth')?.addEventListener('click', async () => {
    const button = document.getElementById('btnStartMetaOAuth');
    if (!state.metaOAuth.configured) return;
    button.disabled = true;
    button.querySelector('span').textContent = 'Открываем Facebook…';
    try {
      const result = await apiRequest('/api/meta/oauth/start?return_path=/add-accounts', { method: 'POST' });
      window.location.assign(result.authorization_url);
    } catch (err) {
      showToast(err.message, 'error');
      button.disabled = false;
      button.querySelector('span').textContent = 'Войти через Facebook';
    }
  });

  window.openMetaConnection = async function (connectionId) {
    const connection = state.metaOAuth.connections.find(item => item.id === connectionId);
    if (connection && connection.status !== 'active') {
      document.getElementById('btnStartMetaOAuth')?.click();
      return;
    }
    await discoverMetaAssets(connectionId);
  };

  async function discoverMetaAssets(connectionId) {
    const panel = document.getElementById('metaAssetsPanel');
    const groups = document.getElementById('metaAssetGroups');
    state.metaOAuth.activeConnectionId = connectionId;
    state.metaOAuth.selectedAccountIds.clear();
    panel.classList.remove('hidden');
    groups.innerHTML = '<div class="empty-state"><div class="spinner"></div><p>Получаем доступные кабинеты из Meta…</p></div>';
    document.getElementById('metaAssetsEmpty').classList.add('hidden');
    try {
      const result = await apiRequest(`/api/meta/connections/${connectionId}/discover`, { method: 'POST' });
      state.metaOAuth.assets = result.accounts || [];
      document.getElementById('metaAssetsTitle').textContent = result.connection?.provider_user_name || 'Доступные кабинеты';
      document.getElementById('metaAssetsSubtitle').textContent = `Найдено ${result.count || 0} · уже подключено ${result.imported_count || 0}`;
      renderMetaAssets();
      panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (err) {
      groups.innerHTML = '';
      document.getElementById('metaAssetsEmpty').classList.remove('hidden');
      document.querySelector('#metaAssetsEmpty h3').textContent = 'Не удалось получить кабинеты';
      document.querySelector('#metaAssetsEmpty p').textContent = err.message;
      showToast(err.message, 'error');
      await loadMetaConnections();
    }
  }

  function renderMetaAssets() {
    const groupsEl = document.getElementById('metaAssetGroups');
    const emptyEl = document.getElementById('metaAssetsEmpty');
    const grouped = new Map();
    state.metaOAuth.assets.forEach(asset => {
      const key = asset.business_name || 'Без Business Manager';
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(asset);
    });
    if (!state.metaOAuth.assets.length) {
      groupsEl.innerHTML = '';
      emptyEl.classList.remove('hidden');
      updateMetaSelection();
      return;
    }
    emptyEl.classList.add('hidden');
    groupsEl.innerHTML = Array.from(grouped.entries()).map(([businessName, accounts]) => `
      <section class="meta-business-group">
        <div class="meta-business-title">
          <span>${escapeHtml(businessName)}</span>
          <small>${accounts.length} кабинетов</small>
        </div>
        <div class="meta-asset-list">
          ${accounts.map(asset => `
            <label class="meta-asset-row ${asset.imported ? 'is-imported' : ''}">
              <input class="meta-asset-checkbox" type="checkbox" value="${escapeHtml(asset.account_id)}" ${asset.imported ? 'checked disabled' : ''}>
              <span class="meta-asset-main"><b>${escapeHtml(asset.name)}</b><code>${escapeHtml(asset.account_id)}</code></span>
              <span class="meta-asset-meta"><b>${escapeHtml(asset.currency)}</b><small>${escapeHtml(asset.timezone_name)}</small></span>
              <span class="badge ${asset.imported ? 'badge-success' : 'badge-neutral'}">${asset.imported ? 'Подключён' : 'Доступен'}</span>
            </label>`).join('')}
        </div>
      </section>`).join('');
    document.querySelectorAll('.meta-asset-checkbox:not(:disabled)').forEach(input => {
      input.addEventListener('change', () => {
        if (input.checked) state.metaOAuth.selectedAccountIds.add(input.value);
        else state.metaOAuth.selectedAccountIds.delete(input.value);
        updateMetaSelection();
      });
    });
    updateMetaSelection();
  }

  function updateMetaSelection() {
    const available = state.metaOAuth.assets.filter(asset => !asset.imported);
    const selected = state.metaOAuth.selectedAccountIds.size;
    const selectAll = document.getElementById('metaSelectAll');
    document.getElementById('metaSelectionCount').textContent = `Выбрано: ${selected}`;
    const importButton = document.getElementById('btnImportMetaAssets');
    importButton.disabled = selected === 0;
    importButton.querySelector('span').textContent = selected ? `Добавить выбранные (${selected})` : 'Добавить выбранные кабинеты';
    selectAll.checked = available.length > 0 && selected === available.length;
    selectAll.indeterminate = selected > 0 && selected < available.length;
  }

  document.getElementById('metaSelectAll')?.addEventListener('change', event => {
    state.metaOAuth.selectedAccountIds.clear();
    if (event.target.checked) {
      state.metaOAuth.assets.filter(asset => !asset.imported).forEach(asset => state.metaOAuth.selectedAccountIds.add(asset.account_id));
    }
    document.querySelectorAll('.meta-asset-checkbox:not(:disabled)').forEach(input => {
      input.checked = state.metaOAuth.selectedAccountIds.has(input.value);
    });
    updateMetaSelection();
  });

  document.getElementById('btnRefreshMetaAssets')?.addEventListener('click', () => {
    if (state.metaOAuth.activeConnectionId) discoverMetaAssets(state.metaOAuth.activeConnectionId);
  });

  document.getElementById('btnImportMetaAssets')?.addEventListener('click', async () => {
    const accountIds = Array.from(state.metaOAuth.selectedAccountIds);
    if (!accountIds.length || !state.metaOAuth.activeConnectionId) return;
    window.openModal('modalBatchProgress');
    document.getElementById('batchProgressBar').style.width = '35%';
    document.getElementById('batchProgressText').textContent = `Проверяем и подключаем ${accountIds.length} кабинетов…`;
    document.getElementById('batchResultsList').innerHTML = '';
    document.getElementById('btnBatchDone').classList.add('hidden');
    document.getElementById('btnBatchOpenRules').classList.add('hidden');
    try {
      const result = await apiRequest(`/api/meta/connections/${state.metaOAuth.activeConnectionId}/import`, {
        method: 'POST',
        body: JSON.stringify({ account_ids: accountIds })
      });
      document.getElementById('batchProgressBar').style.width = '100%';
      document.getElementById('batchProgressText').textContent = `Подключено: ${result.success_count} из ${accountIds.length}`;
      const rows = [];
      (result.added || []).forEach(item => rows.push(`<div class="batch-res-item"><span><span class="status-dot dot-success"></span><b>${escapeHtml(item.name)}</b> (${escapeHtml(item.account_id)})</span><span class="badge badge-success">OK</span></div>`));
      (result.errors || []).forEach(item => rows.push(`<div class="batch-res-item"><span><span class="status-dot dot-danger"></span><b>${escapeHtml(item.account_id)}</b>: ${escapeHtml(item.error)}</span><span class="badge badge-danger">Ошибка</span></div>`));
      document.getElementById('batchResultsList').innerHTML = rows.join('');
      document.getElementById('btnBatchDone').classList.remove('hidden');
      if (result.success_count > 0) document.getElementById('btnBatchOpenRules').classList.remove('hidden');
      state.metaOAuth.selectedAccountIds.clear();
      await discoverMetaAssets(state.metaOAuth.activeConnectionId);
    } catch (err) {
      document.getElementById('batchProgressText').textContent = `Ошибка: ${err.message}`;
      document.getElementById('btnBatchDone').classList.remove('hidden');
    }
  });

  const rawInput = document.getElementById('rawAccountsInput');
  const parsedBadge = document.getElementById('parsedCountBadge');
  const previewBox = document.getElementById('parsedPreviewBox');
  const chipsList = document.getElementById('parsedChipsList');
  const btnSubmit = document.getElementById('btnSubmitBatchAdd');

  // Real-time Parser Regex
  function parseAccountsLocally(text) {
    if (!text) return [];
    const lines = text.split('\n').map(l => l.trim()).filter(Boolean);
    const list = [];
    const seen = new Set();

    for (let i = 0; i < lines.length; i++) {
      const match = lines[i].match(/(?:Ad account ID|Account ID|ID|act_)[:\s]*(\d{8,25})/i);
      if (match) {
        const accId = `act_${match[1]}`;
        let name = '';
        if (i > 0 && !lines[i-1].match(/(?:Ad account ID|Owned by|info for|scope|permission)/i)) {
          name = lines[i-1];
        }
        if (!seen.has(accId)) {
          seen.add(accId);
          list.push({ account_id: accId, name });
        }
      }
    }

    if (list.length === 0) {
      const genericMatches = text.match(/(?:act_)?(\d{8,25})/g) || [];
      genericMatches.forEach(rawId => {
        const cleanNum = rawId.replace('act_', '');
        const accId = `act_${cleanNum}`;
        if (!seen.has(accId)) {
          seen.add(accId);
          list.push({ account_id: accId, name: '' });
        }
      });
    }

    return list;
  }

  function renderParsedChips() {
    parsedBadge.textContent = `Найдено: ${state.parsedAccounts.length}`;

    if (state.parsedAccounts.length > 0) {
      previewBox.classList.remove('hidden');
      chipsList.innerHTML = state.parsedAccounts.map((p, idx) => {
        const namePart = p.name ? ` (${escapeHtml(p.name)})` : '';
        return `
          <span class="parsed-item-chip">
            <code>${p.account_id}</code>${namePart}
            <button type="button" class="chip-del-btn" title="Исключить кабинет" onclick="window.removeParsedChip(${idx})">&times;</button>
          </span>
        `;
      }).join('');
      btnSubmit.disabled = false;
      btnSubmit.querySelector('span').textContent = `Подключить ${state.parsedAccounts.length} кабинетов`;
    } else {
      previewBox.classList.add('hidden');
      chipsList.innerHTML = '';
      btnSubmit.disabled = true;
      btnSubmit.querySelector('span').textContent = 'Подключить кабинеты';
    }
  }

  window.removeParsedChip = function (idx) {
    haptic('impact', 'light');
    if (idx >= 0 && idx < state.parsedAccounts.length) {
      state.parsedAccounts.splice(idx, 1);
      renderParsedChips();
    }
  };

  rawInput?.addEventListener('input', () => {
    state.parsedAccounts = parseAccountsLocally(rawInput.value);
    renderParsedChips();
  });

  // Toggle Password Visibility
  document.getElementById('btnToggleTokenVisibility')?.addEventListener('click', () => {
    const input = document.getElementById('accessTokenInput');
    input.type = input.type === 'password' ? 'text' : 'password';
  });

  // Submit Batch Add
  btnSubmit?.addEventListener('click', async () => {
    const token = document.getElementById('accessTokenInput').value.trim();
    const batchName = document.getElementById('batchNameInput').value.trim() || '-';

    if (!token) {
      showToast('Введите Meta Access Token', 'error');
      document.getElementById('accessTokenInput').focus();
      return;
    }

    if (state.parsedAccounts.length === 0) {
      showToast('Не найдено ни одного ID кабинета', 'error');
      return;
    }

    haptic('impact', 'heavy');
    window.openModal('modalBatchProgress');
    document.getElementById('batchProgressBar').style.width = '30%';
    document.getElementById('batchProgressText').textContent = `Проверка ${state.parsedAccounts.length} кабинетов через Meta API...`;
    document.getElementById('batchResultsList').innerHTML = '';
    document.getElementById('btnBatchDone').classList.add('hidden');
    document.getElementById('btnBatchDone').classList.add('btn-block');
    document.getElementById('btnBatchOpenRules').classList.add('hidden');

    try {
      const res = await apiRequest('/api/accounts/batch-add', {
        method: 'POST',
        body: JSON.stringify({
          accounts: state.parsedAccounts,
          batch_name: batchName,
          access_token: token
        })
      });

      document.getElementById('batchProgressBar').style.width = '100%';
      document.getElementById('batchProgressText').textContent = `Успешно подключено: ${res.success_count} из ${state.parsedAccounts.length}`;
      
      const resultsHtml = [];
      if (res.added) {
        res.added.forEach(item => {
          resultsHtml.push(`
            <div class="batch-res-item">
              <span><span class="status-dot dot-success"></span><b>${escapeHtml(item.name)}</b> (${item.account_id})</span>
              <span class="badge badge-success">OK</span>
            </div>
          `);
        });
      }
      if (res.errors) {
        res.errors.forEach(item => {
          resultsHtml.push(`
            <div class="batch-res-item">
              <span><span class="status-dot dot-danger"></span><b>${item.account_id}</b>: ${escapeHtml(item.error)}</span>
              <span class="badge badge-danger">Ошибка</span>
            </div>
          `);
        });
      }

      document.getElementById('batchResultsList').innerHTML = resultsHtml.join('');
      document.getElementById('btnBatchDone').classList.remove('hidden');
      document.getElementById('btnBatchDone').classList.remove('btn-block');
      if (res.success_count > 0) {
        document.getElementById('btnBatchOpenRules').classList.remove('hidden');
      }
      haptic('notification', 'success');

      // Clear input fields
      rawInput.value = '';
      rawInput.dispatchEvent(new Event('input'));
      document.getElementById('batchNameInput').value = '';
    } catch (err) {
      document.getElementById('batchProgressText').textContent = `Ошибка: ${err.message}`;
      document.getElementById('btnBatchDone').classList.remove('hidden');
    }

  });

  window.closeBatchProgress = function (targetTab = 'accounts') {
    window.closeModal('modalBatchProgress');
    window.switchTab(targetTab);
  };

  // ==========================================================
  // TAB 4: SETTINGS (НАСТРОЙКИ)
  // ==========================================================
  window.switchSettingsPage = function (page) {
    const target = page === 'automation' ? 'automation' : 'account';
    document.querySelectorAll('[data-settings-page]').forEach(element => {
      element.classList.toggle('hidden', element.dataset.settingsPage !== target);
    });
    document.querySelectorAll('[data-settings-nav]').forEach(button => {
      button.classList.toggle('active', button.dataset.settingsNav === target);
    });
  };

  async function loadSettings() {
    try {
      const data = await apiRequest('/api/settings');
      state.settings = data;

      const canManageInterval = data.user_role === 'admin';
      const fieldValues = {
        settingBackgroundInterval: data.poll_interval_minutes,
        settingCriticalRuleInterval: data.critical_rule_interval_minutes,
        settingStopConfirmation: data.stop_confirmation_minutes,
        settingInventoryCache: data.inventory_cache_minutes,
        settingAccountHealthInterval: data.account_health_interval_minutes,
        settingConcurrentAccounts: data.max_concurrent_accounts,
        settingConcurrentActions: data.max_concurrent_actions,
        settingUsageSoftLimit: data.usage_soft_limit_percent,
        settingUsageHardLimit: data.usage_hard_limit_percent
      };
      Object.entries(fieldValues).forEach(([id, value]) => {
        const input = document.getElementById(id);
        if (input) {
          input.value = String(value ?? '');
          input.disabled = !canManageInterval;
        }
      });
      const adaptiveInput = document.getElementById('settingAdaptivePolling');
      if (adaptiveInput) {
        adaptiveInput.checked = data.adaptive_polling_enabled !== false;
        adaptiveInput.disabled = !canManageInterval;
      }
      const passwordInput = document.getElementById('automationSettingsPassword');
      const saveButton = document.getElementById('btnSaveAutomationSettings');
      if (passwordInput) passwordInput.disabled = !canManageInterval;
      if (saveButton) {
        saveButton.disabled = !canManageInterval;
        saveButton.title = canManageInterval ? '' : 'Изменение доступно только администратору';
      }
      renderAutomationRuntime(data.runtime || {});

      if (state.user) {
        const dName = document.getElementById('settingsDisplayName');
        const uDesc = document.getElementById('settingsUserDesc');
        const uRole = document.getElementById('settingsUserRole');
        const tgInput = document.getElementById('settingsTelegramIdInput');
        const aLarge = document.getElementById('settingsAvatarLarge');

        const name = state.user.full_name || state.user.username || 'Пользователь';
        if (dName) dName.textContent = name;
        if (uDesc) uDesc.textContent = `@${state.user.username || 'user'}`;
        if (uRole) uRole.textContent = state.user.role || 'buyer';
        if (tgInput && state.user.telegram_id) tgInput.value = state.user.telegram_id;
        if (aLarge) aLarge.textContent = name.charAt(0).toUpperCase();
      }
    } catch (err) {}
  }

  function renderAutomationRuntime(runtime) {
    const lastCycle = document.getElementById('automationLastCycle');
    const duration = document.getElementById('automationCycleDuration');
    const usage = document.getElementById('automationUsagePercent');
    const errors = document.getElementById('automationCycleErrors');
    const badge = document.getElementById('automationRuntimeBadge');
    const finishedAt = runtime.finished_at || runtime.updated_at || '';
    const usagePercent = Number(runtime.usage?.max_percent ?? runtime.usage_percent ?? 0);
    const errorCount = Number(runtime.errors_count ?? runtime.errors?.length ?? 0);

    if (lastCycle) lastCycle.textContent = finishedAt ? formatSummaryTime(finishedAt) : 'Ожидает первый цикл';
    if (duration) duration.textContent = Number.isFinite(Number(runtime.duration_ms))
      ? `${(Number(runtime.duration_ms) / 1000).toFixed(1)} сек`
      : '—';
    if (usage) usage.textContent = usagePercent > 0 ? `${usagePercent.toFixed(0)}%` : 'Нет данных';
    if (errors) errors.textContent = String(errorCount);
    if (badge) {
      const waiting = !finishedAt;
      const unhealthy = waiting || errorCount > 0 || usagePercent >= Number(state.settings.usage_hard_limit_percent || 80);
      badge.className = `badge ${unhealthy ? 'badge-warning' : 'badge-success'}`;
      const label = waiting ? 'Ожидает первый цикл' : unhealthy ? 'Нужна проверка' : 'Воркер онлайн';
      badge.innerHTML = `<span class="status-dot ${unhealthy ? 'dot-warning' : 'dot-success'}"></span>${label}`;
    }
  }

  window.saveAutomationSettings = async function () {
    const readNumber = id => Number(document.getElementById(id)?.value);
    const passwordInput = document.getElementById('automationSettingsPassword');
    const password = passwordInput?.value || '';
    const payload = {
      current_password: password,
      poll_interval_minutes: readNumber('settingBackgroundInterval'),
      critical_rule_interval_minutes: readNumber('settingCriticalRuleInterval'),
      stop_confirmation_minutes: readNumber('settingStopConfirmation'),
      inventory_cache_minutes: readNumber('settingInventoryCache'),
      account_health_interval_minutes: readNumber('settingAccountHealthInterval'),
      max_concurrent_accounts: readNumber('settingConcurrentAccounts'),
      max_concurrent_actions: readNumber('settingConcurrentActions'),
      usage_soft_limit_percent: readNumber('settingUsageSoftLimit'),
      usage_hard_limit_percent: readNumber('settingUsageHardLimit'),
      adaptive_polling_enabled: document.getElementById('settingAdaptivePolling')?.checked !== false
    };
    if (!password) {
      showToast('Введите текущий пароль учётной записи', 'error');
      passwordInput?.focus();
      return;
    }
    if (payload.usage_soft_limit_percent >= payload.usage_hard_limit_percent) {
      showToast('Мягкий порог квоты должен быть ниже жёсткого', 'error');
      return;
    }

    const button = document.getElementById('btnSaveAutomationSettings');
    if (button) button.disabled = true;
    haptic('impact', 'medium');
    try {
      const res = await apiRequest('/api/settings/automation', {
        method: 'POST',
        body: JSON.stringify(payload)
      });
      if (passwordInput) passwordInput.value = '';
      showToast(res.message || 'Настройки автоматики сохранены', 'success');
      await loadSettings();
    } catch (error) {
      showToast(`Ошибка: ${error.message}`, 'error');
    } finally {
      if (button) button.disabled = state.user?.role !== 'admin';
    }
  };

  window.saveTelegramId = async function () {
    const input = document.getElementById('settingsTelegramIdInput');
    const tgId = input?.value.trim();
    if (!tgId) {
      showToast('Введите Telegram ID', 'error');
      return;
    }
    haptic('impact', 'medium');
    try {
      const res = await apiRequest('/api/auth/update-profile', {
        method: 'POST',
        body: JSON.stringify({ telegram_id: tgId })
      });
      if (state.user) state.user.telegram_id = tgId;
      showToast(res.message || 'Telegram ID успешно сохранен', 'success');
    } catch (e) {
      showToast(`Ошибка: ${e.message}`, 'error');
    }
  };

  window.changeUserPassword = async function () {
    const oldInput = document.getElementById('settingsOldPasswordInput');
    const newInput = document.getElementById('settingsNewPasswordInput');
    const oldPw = oldInput?.value || '';
    const newPw = newInput?.value || '';
    if (!oldPw) {
      showToast('Введите текущий пароль', 'error');
      oldInput?.focus();
      return;
    }
    if (newPw.length < 8) {
      showToast('Пароль должен быть не менее 8 символов', 'error');
      newInput?.focus();
      return;
    }
    haptic('impact', 'medium');
    try {
      const res = await apiRequest('/api/auth/change-password', {
        method: 'POST',
        body: JSON.stringify({ old_password: oldPw, new_password: newPw })
      });
      showToast(res.message || 'Пароль успешно обновлен', 'success');
      if (oldInput) oldInput.value = '';
      if (newInput) newInput.value = '';
    } catch (e) {
      showToast(`Ошибка: ${e.message}`, 'error');
    }
  };

  // ==========================================================
  // GLOBAL MODALS & UTILS
  // ==========================================================
  const activeModalStack = [];

  window.openModal = function (modalId) {
    const el = document.getElementById(modalId);
    if (!el) return;
    el.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
    if (!activeModalStack.includes(modalId)) {
      activeModalStack.push(modalId);
    }
  };

  window.closeModal = function (modalId) {
    const el = document.getElementById(modalId);
    if (el) el.classList.add('hidden');
    const idx = activeModalStack.indexOf(modalId);
    if (idx !== -1) {
      activeModalStack.splice(idx, 1);
    }
    if (activeModalStack.length === 0) {
      document.body.style.overflow = '';
    }
  };

  document.getElementById('btnOpenTokenGuide')?.addEventListener('click', () => {
    window.openModal('modalTokenGuide');
  });

  document.getElementById('btnOpenSummaryColumns')?.addEventListener('click', () => {
    haptic('selection');
    window.openSummaryColumns();
  });

  document.querySelectorAll('[data-summary-view]').forEach(button => {
    button.addEventListener('click', () => {
      const viewMode = button.dataset.summaryView;
      const columns = SUMMARY_VIEW_PRESETS[viewMode];
      if (!columns) return;
      haptic('selection');
      persistSummaryView({ ...state.summaryView, view_mode: viewMode, visible_columns: columns });
    });
  });

  document.getElementById('btnSaveSummaryColumns')?.addEventListener('click', async () => {
    const button = document.getElementById('btnSaveSummaryColumns');
    const selected = Array.from(document.querySelectorAll('#summaryColumnOptions .summary-column-visible:checked'))
      .map(input => input.value);
    const columnOrder = Array.from(document.querySelectorAll('#summaryColumnOptions [data-summary-column-option]'))
      .map(item => item.dataset.summaryColumnOption);
    const columnWidths = { ...state.summaryView.column_widths };
    document.querySelectorAll('#summaryColumnOptions [data-summary-column-width-input]').forEach(input => {
      columnWidths[input.dataset.summaryColumnWidthInput] = Number(input.value);
    });
    if (button) button.disabled = true;
    const saved = await persistSummaryView(
      {
        ...state.summaryView,
        view_mode: 'custom',
        visible_columns: selected,
        column_order: columnOrder,
        column_widths: columnWidths
      },
      { toast: 'Представление таблицы сохранено' }
    );
    if (button) button.disabled = false;
    if (saved) window.closeModal('modalSummaryColumns');
  });

  document.getElementById('btnResetSummaryColumns')?.addEventListener('click', async () => {
    const saved = await persistSummaryView(
      {
        ...state.summaryView,
        view_mode: 'all',
        visible_columns: SUMMARY_VIEW_PRESETS.all,
        column_order: SUMMARY_VIEW_PRESETS.all,
        column_widths: SUMMARY_DEFAULT_COLUMN_WIDTHS
      },
      { toast: 'Восстановлен полный вид таблицы' }
    );
    if (saved) window.closeModal('modalSummaryColumns');
  });

  // Search & Filter & Sort event listeners
  document.getElementById('accountSearchInput')?.addEventListener('input', (e) => {
    state.searchQuery = e.target.value;
    document.getElementById('searchClearBtn')?.classList.toggle('hidden', !state.searchQuery);
    renderAccounts();
  });

  document.getElementById('searchClearBtn')?.addEventListener('click', () => {
    const input = document.getElementById('accountSearchInput');
    input.value = '';
    state.searchQuery = '';
    document.getElementById('searchClearBtn')?.classList.add('hidden');
    renderAccounts();
  });

  document.querySelectorAll('.chip[data-filter]').forEach(chip => {
    chip.addEventListener('click', () => {
      haptic('selection');
      document.querySelectorAll('.chip[data-filter]').forEach(c => c.classList.remove('active'));
      chip.classList.add('active');
      state.filter = chip.dataset.filter;
      if (chip.dataset.filter === 'all') {
        state.accountGroupFilter = 'all';
        renderAccountGroups();
      }
      renderAccounts();
    });
  });

  document.getElementById('accountGroupsBar')?.addEventListener('click', event => {
    const button = event.target.closest('[data-account-group-filter]');
    if (!button) return;
    const targetFilter = button.dataset.accountGroupFilter;
    const nextFilter = state.accountGroupFilter === targetFilter ? 'all' : (targetFilter || 'all');
    window.switchAccountGroup(nextFilter);
  });

  function rerenderSummaryForTableControls() {
    const data = state.summaryCache[state.currentPeriod] || state.summary;
    if (data) renderSummaryData(data);
  }

  function updateSummaryFilters(patch, options = {}) {
    state.summaryView = normalizeSummaryView({
      ...state.summaryView,
      filters: { ...state.summaryView.filters, ...patch }
    });
    rerenderSummaryForTableControls();
    updateSummaryViewControls();

    if (summaryFilterSaveTimer) window.clearTimeout(summaryFilterSaveTimer);
    if (options.immediate) {
      persistSummaryView(state.summaryView);
    } else {
      summaryFilterSaveTimer = window.setTimeout(() => {
        summaryFilterSaveTimer = null;
        persistSummaryView(state.summaryView);
      }, 500);
    }
  }

  document.getElementById('summaryAccountSearch')?.addEventListener('input', event => {
    updateSummaryFilters({ query: event.target.value });
  });

  document.getElementById('summaryAccountGroupSelect')?.addEventListener('change', event => {
    haptic('selection');
    updateSummaryFilters({ group_id: event.target.value || 'all' }, { immediate: true });
  });

  document.getElementById('summaryAccountSearchClear')?.addEventListener('click', () => {
    updateSummaryFilters({ query: '' }, { immediate: true });
    document.getElementById('summaryAccountSearch')?.focus();
  });

  document.querySelectorAll('[data-summary-status-filter]').forEach(button => {
    button.addEventListener('click', () => {
      haptic('selection');
      updateSummaryFilters({ status: button.dataset.summaryStatusFilter }, { immediate: true });
    });
  });

  function changeSummarySort(column) {
    const isSameColumn = state.summaryView.sort_column === column;
    const defaultDirection = ['account', 'data'].includes(column) ? 'asc' : 'desc';
    state.summaryView = normalizeSummaryView({
      ...state.summaryView,
      sort_column: column,
      sort_direction: isSameColumn
        ? (state.summaryView.sort_direction === 'asc' ? 'desc' : 'asc')
        : defaultDirection
    });
    haptic('selection');
    rerenderSummaryForTableControls();
    persistSummaryView(state.summaryView);
  }

  document.getElementById('summaryTableHead')?.addEventListener('click', event => {
    if (event.target.closest('[data-summary-column-resizer]')) return;
    const header = event.target.closest('[data-summary-sort]');
    if (header) changeSummarySort(header.dataset.summarySort);
  });

  document.getElementById('summaryTableHead')?.addEventListener('keydown', event => {
    if (resizeSummaryColumnWithKeyboard(event)) return;
    if (event.key !== 'Enter' && event.key !== ' ') return;
    const header = event.target.closest('[data-summary-sort]');
    if (!header) return;
    event.preventDefault();
    changeSummarySort(header.dataset.summarySort);
  });

  document.getElementById('summaryTableHead')?.addEventListener('pointerdown', startSummaryColumnResize);
  document.getElementById('summaryTableHead')?.addEventListener('dblclick', resetSummaryColumnWidth);

  // Period Switcher Listeners in Summary
  document.querySelectorAll('.period-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const period = btn.dataset.period;
      state.currentPeriod = period;
      state.summaryView = normalizeSummaryView({ ...state.summaryView, period });
      haptic('selection');

      document.querySelectorAll('.period-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');

      updateFetchButtonLabel(period);
      document.getElementById('kpiPeriodLabel').textContent = periodTextMap[period] || '';
      persistSummaryView(state.summaryView);

      if (state.summaryCache[period]) {
        renderLocalSummaryCache(state.summaryCache[period]);
        refreshSummaryIfStale(period, state.summaryCache[period]);
      } else {
        loadSummary(period, false, { silent: true, refreshIfStale: true });
      }
    });
  });

  // Fetch summary button listener
  document.getElementById('btnFetchSummary')?.addEventListener('click', () => {
    haptic('impact', 'medium');
    loadSummary(state.currentPeriod, true);
  });

  // Sync Button with 30-sec Cooldown & In-Flight Protection
  let isSyncing = false;
  let syncCooldownUntil = 0;

  document.getElementById('btnSync')?.addEventListener('click', async () => {
    const now = Date.now();
    if (isSyncing) return;
    if (now < syncCooldownUntil) {
      const remainingSec = Math.ceil((syncCooldownUntil - now) / 1000);
      showToast(`Пожалуйста, подождите ${remainingSec} сек перед повторным запросом к Meta`, 'info');
      return;
    }

    const btn = document.getElementById('btnSync');
    btn.classList.add('syncing');
    haptic('impact', 'medium');
    isSyncing = true;

    try {
      if (state.activeTab === 'accounts') {
        await loadAccounts();
      } else if (state.activeTab === 'summary') {
        await loadSummary(state.currentPeriod, true); // force refresh
      } else if (state.activeTab === 'logs') {
        await loadLogsTab(state.auditPage);
      }
      showToast('Данные успешно обновлены', 'success');
      syncCooldownUntil = Date.now() + 30000; // 30s cooldown
    } catch (e) {
      showToast('Ошибка синхронизации', 'error');
    } finally {
      isSyncing = false;
      setTimeout(() => btn.classList.remove('syncing'), 600);
    }
  });


  // Navigation Click Handlers
  document.querySelectorAll('.nav-tab, .mobile-nav-item').forEach(btn => {
    btn.addEventListener('click', () => {
      window.switchTab(btn.dataset.tab);
    });
  });

  const userBadge = document.getElementById('userBadge');
  userBadge?.addEventListener('click', () => window.switchTab('settings'));
  userBadge?.addEventListener('keydown', event => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      window.switchTab('settings');
    }
  });

  document.getElementById('btnRefreshLogs')?.addEventListener('click', () => loadLogsTab(state.auditPage));
  document.getElementById('btnLogsPrev')?.addEventListener('click', () => loadLogsTab(state.auditPage - 1));
  document.getElementById('btnLogsNext')?.addEventListener('click', () => loadLogsTab(state.auditPage + 1));
  ['logsCategoryFilter', 'logsStatusFilter', 'logsAccountFilter'].forEach(id => {
    document.getElementById(id)?.addEventListener('change', () => loadLogsTab(1));
  });

  // ==========================================================
  // NOTIFICATIONS POPOVER MODULE (ATTIO STYLE)
  // ==========================================================
  window.toggleNotificationsPopover = function (e) {
    if (e && e.stopPropagation) e.stopPropagation();
    const popover = document.getElementById('notificationsPopover');
    const btn = document.getElementById('sidebarNotificationsBtn');
    if (!popover) return;
    const isHidden = popover.classList.contains('hidden');
    if (isHidden) {
      window.closeQuickSearchModal();
      popover.classList.remove('hidden');
      btn?.classList.add('active');
    } else {
      popover.classList.add('hidden');
      btn?.classList.remove('active');
    }
  };

  window.switchNotificationsTab = function (tabName) {
    const tabBtns = document.querySelectorAll('.notifications-tab-btn');
    tabBtns.forEach(btn => {
      btn.classList.toggle('active', btn.dataset.tab === tabName);
    });
    const paneNotifications = document.getElementById('notificationsTabPaneNotifications');
    const paneRequests = document.getElementById('notificationsTabPaneRequests');
    if (paneNotifications) paneNotifications.classList.toggle('hidden', tabName !== 'notifications');
    if (paneRequests) paneRequests.classList.toggle('hidden', tabName !== 'requests');
  };

  // ==========================================================
  // GLOBAL QUICK SEARCH / COMMAND PALETTE MODULE (ATTIO STYLE)
  // ==========================================================
  let quickSearchSelectedIndex = 0;
  let quickSearchResultsList = [];

  function highlightMatches(text, query) {
    if (!query || !text) return escapeHtml(text || '');
    const cleanText = String(text);
    const escapedQuery = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const regex = new RegExp(`(${escapedQuery})`, 'gi');
    const parts = cleanText.split(regex);
    return parts.map((part) => {
      if (part.toLowerCase() === query.toLowerCase()) {
        return `<mark>${escapeHtml(part)}</mark>`;
      }
      return escapeHtml(part);
    }).join('');
  }

  function getQuickSearchEntities(query) {
    const q = (query || '').trim().toLowerCase();
    const sections = [];

    // 1. Navigation Sections
    const navItems = [
      {
        id: 'nav_home',
        title: 'Главная',
        subtitle: 'Главная панель и сводка показателей',
        badge: 'Раздел',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6"/></svg>',
        keywords: ['главная', 'home', 'дашборд', 'dashboard', 'сводка', 'метрики'],
        action: () => window.switchTab('home')
      },
      {
        id: 'nav_accounts',
        title: 'Все рекламные кабинеты',
        subtitle: 'Управление рекламными аккаунтами Meta Ads',
        badge: 'Раздел',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>',
        keywords: ['кабинеты', 'аккаунты', 'accounts', 'кампании', 'рекламные'],
        action: () => window.switchTab('accounts')
      },
      {
        id: 'nav_fb_accounts',
        title: 'Facebook Аккаунты',
        subtitle: 'Подключенные профили Facebook и Business Managers',
        badge: 'Раздел',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"/></svg>',
        keywords: ['facebook', 'fb', 'профили', 'бм', 'bm', 'соцсети'],
        action: () => window.switchTab('fb_accounts')
      },
      {
        id: 'nav_rules',
        title: 'Правила автостопа и лимитов',
        subtitle: 'Настройка автоматических правил и контроль рисков',
        badge: 'Правила',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/></svg>',
        keywords: ['правила', 'rules', 'автостоп', 'лимиты', 'автоматизация'],
        action: () => window.switchTab('rules')
      },
      {
        id: 'nav_summary',
        title: 'Сводная таблица аналитики',
        subtitle: 'Показатели расхода, лидов, кликов и конверсий',
        badge: 'Раздел',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/></svg>',
        keywords: ['сводка', 'summary', 'статистика', 'аналитика', 'таблица', 'расход'],
        action: () => window.switchTab('summary')
      },
      {
        id: 'nav_logs',
        title: 'Логи и журнал событий',
        subtitle: 'История аудита, остановки адсетов и системные события',
        badge: 'Раздел',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"/></svg>',
        keywords: ['логи', 'logs', 'история', 'аудит', 'события', 'журнал'],
        action: () => window.switchTab('logs')
      },
      {
        id: 'nav_add',
        title: 'Подключить Facebook аккаунты',
        subtitle: 'Meta OAuth подключение и импорт кабинетов',
        badge: 'Раздел',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="16"/><line x1="8" y1="12" x2="16" y2="12"/></svg>',
        keywords: ['добавить', 'подключить', 'oauth', 'meta', 'импорт', 'add'],
        action: () => window.switchTab('add')
      },
      {
        id: 'nav_workspace_settings',
        title: 'Настройки воркспейса',
        subtitle: 'Интервалы опроса, лимиты и управление воркспейсом',
        badge: 'Настройки',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"/><path d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/></svg>',
        keywords: ['настройки', 'воркспейс', 'workspace', 'settings', 'интервалы'],
        action: () => window.openWorkspaceSettings()
      },
      {
        id: 'nav_account_settings',
        title: 'Настройки аккаунта',
        subtitle: 'Профиль пользователя и параметры безопасности',
        badge: 'Настройки',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"/></svg>',
        keywords: ['профиль', 'пароль', 'пользователь', 'user', 'account'],
        action: () => window.openAccountSettings()
      },
      {
        id: 'nav_new_workspace',
        title: 'Создать новый воркспейс',
        subtitle: 'Добавление отдельного рабочего пространства',
        badge: 'Действие',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 4v16m8-8H4"/></svg>',
        keywords: ['новый воркспейс', 'создать', 'create workspace'],
        action: () => window.openCreateWorkspacePage()
      }
    ];

    const matchedNav = navItems.filter(item => {
      if (!q) return true;
      return item.title.toLowerCase().includes(q) ||
        item.subtitle.toLowerCase().includes(q) ||
        item.keywords.some(k => k.includes(q));
    });

    if (matchedNav.length > 0) {
      sections.push({
        title: 'Разделы',
        items: matchedNav.slice(0, q ? 6 : 8)
      });
    }

    // 2. Ad Accounts (state.accounts)
    if (state.accounts && state.accounts.length > 0) {
      const matchedAccounts = state.accounts.filter(acc => {
        if (!q) return false;
        const name = (acc.name || '').toLowerCase();
        const customName = (acc.custom_name || '').toLowerCase();
        const accId = String(acc.account_id || '').toLowerCase();
        const note = (acc.note || '').toLowerCase();
        return name.includes(q) || customName.includes(q) || accId.includes(q) || note.includes(q);
      }).map(acc => {
        const title = acc.custom_name ? `${acc.custom_name} (${acc.name})` : (acc.name || `act_${acc.account_id}`);
        const subtitle = `ID: ${acc.account_id}` + (acc.note ? ` • ${acc.note}` : '') + (acc.currency ? ` • ${acc.currency}` : '');
        const isActive = String(acc.account_status) === '1';
        const badge = isActive ? 'Активен' : 'Отключен';
        return {
          id: `acc_${acc.account_id}`,
          title: title,
          subtitle: subtitle,
          badge: badge,
          icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>',
          action: () => {
            window.openAccountDetails(acc.account_id);
          }
        };
      });

      if (matchedAccounts.length > 0) {
        sections.push({
          title: 'Рекламные аккаунты',
          items: matchedAccounts.slice(0, 8)
        });
      }
    }

    // 3. Facebook Connections (state.fbConnections / state.fbAccounts / state.metaOAuth.connections)
    const fbList = (state.fbConnections && state.fbConnections.length > 0)
      ? state.fbConnections
      : (state.fbAccounts && state.fbAccounts.length > 0)
        ? state.fbAccounts
        : ((state.metaOAuth && state.metaOAuth.connections) || []);

    if (fbList && fbList.length > 0) {
      const matchedFb = fbList.filter(fb => {
        if (!q) return false;
        const name = (fb.name || fb.fb_user_name || '').toLowerCase();
        const id = String(fb.id || fb.fb_user_id || '').toLowerCase();
        return name.includes(q) || id.includes(q);
      }).map(fb => {
        const name = fb.name || fb.fb_user_name || `FB User ${fb.id || fb.fb_user_id}`;
        const subtitle = `ID: ${fb.id || fb.fb_user_id}` + (fb.accounts_count ? ` • ${fb.accounts_count} кабинетов` : '');
        return {
          id: `fb_${fb.id || fb.fb_user_id}`,
          title: name,
          subtitle: subtitle,
          badge: 'Facebook',
          icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"/></svg>',
          action: () => window.switchTab('fb_accounts')
        };
      });

      if (matchedFb.length > 0) {
        sections.push({
          title: 'Facebook Профили',
          items: matchedFb.slice(0, 5)
        });
      }
    }

    // 4. Presets & Rules (state.presets)
    if (state.presets && state.presets.length > 0) {
      const matchedPresets = state.presets.filter(p => {
        if (!q) return false;
        const title = (p.title || p.name || '').toLowerCase();
        const conditions = Array.isArray(p.conditions) ? p.conditions.map(c => JSON.stringify(c)).join(' ').toLowerCase() : '';
        return title.includes(q) || conditions.includes(q);
      }).map(p => {
        return {
          id: `rule_${p.id}`,
          title: p.title || p.name || 'Правило без названия',
          subtitle: `Действие: ${p.action || 'автостоп'} • Условий: ${(p.conditions || []).length}`,
          badge: 'Правило',
          icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/></svg>',
          action: () => {
            window.switchTab('rules');
          }
        };
      });

      if (matchedPresets.length > 0) {
        sections.push({
          title: 'Правила',
          items: matchedPresets.slice(0, 5)
        });
      }
    }

    // 5. Account Groups / Lists (state.accountGroups)
    if (state.accountGroups && state.accountGroups.length > 0) {
      const matchedGroups = state.accountGroups.filter(g => {
        if (!q) return false;
        const name = (g.name || '').toLowerCase();
        const desc = (g.description || '').toLowerCase();
        return name.includes(q) || desc.includes(q);
      }).map(g => {
        const count = Array.isArray(g.account_ids) ? g.account_ids.length : 0;
        return {
          id: `group_${g.id}`,
          title: g.name || 'Список',
          subtitle: `${count} кабинетов` + (g.description ? ` • ${g.description}` : ''),
          badge: 'Список',
          icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 6h16M4 10h16M4 14h16M4 18h16"/></svg>',
          action: () => {
            window.switchAccountGroup(g.id);
          }
        };
      });

      if (matchedGroups.length > 0) {
        sections.push({
          title: 'Списки',
          items: matchedGroups.slice(0, 5)
        });
      }
    }

    return sections;
  }

  function renderQuickSearchResults(query) {
    const container = document.getElementById('quickSearchResults');
    if (!container) return;
    const q = (query || '').trim();
    const sections = getQuickSearchEntities(q);

    quickSearchResultsList = [];
    let html = '';

    if (sections.length === 0) {
      html = `
        <div class="quick-search-empty">
          <svg class="quick-search-empty-icon" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
          </svg>
          <div class="quick-search-empty-title">Ничего не найдено</div>
          <div class="quick-search-empty-desc">По запросу «${escapeHtml(q)}» ничего не найдено. Проверьте правильность написания.</div>
        </div>
      `;
      container.innerHTML = html;
      return;
    }

    sections.forEach((section) => {
      html += `
        <div class="quick-search-section">
          <div class="quick-search-section-header">${escapeHtml(section.title)}</div>
      `;
      section.items.forEach((item) => {
        const globalIndex = quickSearchResultsList.length;
        quickSearchResultsList.push(item);
        const isSelected = globalIndex === quickSearchSelectedIndex;

        html += `
          <div class="quick-search-item ${isSelected ? 'is-selected' : ''}" data-index="${globalIndex}" onclick="window.selectQuickSearchResult(${globalIndex});">
            <div class="quick-search-item-left">
              <div class="quick-search-item-icon">${item.icon}</div>
              <div class="quick-search-item-info">
                <div class="quick-search-item-title">${highlightMatches(item.title, q)}</div>
                <div class="quick-search-item-subtitle">${highlightMatches(item.subtitle, q)}</div>
              </div>
            </div>
            <div class="quick-search-item-right">
              ${item.badge ? `<span class="quick-search-item-badge">${escapeHtml(item.badge)}</span>` : ''}
              <span class="quick-search-item-enter"><kbd class="quick-search-kbd">↵</kbd></span>
            </div>
          </div>
        `;
      });
      html += `</div>`;
    });

    container.innerHTML = html;
    updateQuickSearchSelectionHighlight();
  }

  function updateQuickSearchSelectionHighlight() {
    const items = document.querySelectorAll('.quick-search-item');
    items.forEach((el) => {
      const idx = parseInt(el.dataset.index, 10);
      el.classList.toggle('is-selected', idx === quickSearchSelectedIndex);
    });
    const selectedEl = document.querySelector(`.quick-search-item[data-index="${quickSearchSelectedIndex}"]`);
    if (selectedEl) {
      selectedEl.scrollIntoView({ block: 'nearest' });
    }
  }

  window.selectQuickSearchResult = function (index) {
    const item = quickSearchResultsList[index];
    if (item && typeof item.action === 'function') {
      window.closeQuickSearchModal();
      item.action();
    }
  };

  window.openQuickSearchModal = function () {
    const modal = document.getElementById('quickSearchModal');
    const input = document.getElementById('quickSearchInput');
    const clearBtn = document.getElementById('quickSearchClearBtn');
    const popover = document.getElementById('notificationsPopover');
    const notifBtn = document.getElementById('sidebarNotificationsBtn');
    if (popover) popover.classList.add('hidden');
    if (notifBtn) notifBtn.classList.remove('active');

    if (!modal || !input) return;
    modal.classList.remove('hidden');
    input.value = '';
    if (clearBtn) clearBtn.classList.add('hidden');
    quickSearchSelectedIndex = 0;
    renderQuickSearchResults('');
    setTimeout(() => input.focus(), 30);
  };

  window.closeQuickSearchModal = function () {
    const modal = document.getElementById('quickSearchModal');
    if (modal) modal.classList.add('hidden');
  };

  window.clearQuickSearchInput = function () {
    const input = document.getElementById('quickSearchInput');
    const clearBtn = document.getElementById('quickSearchClearBtn');
    if (input) {
      input.value = '';
      input.focus();
    }
    if (clearBtn) clearBtn.classList.add('hidden');
    quickSearchSelectedIndex = 0;
    renderQuickSearchResults('');
  };

  function setupQuickSearchListeners() {
    const input = document.getElementById('quickSearchInput');
    const clearBtn = document.getElementById('quickSearchClearBtn');

    input?.addEventListener('input', (e) => {
      const val = e.target.value;
      if (clearBtn) clearBtn.classList.toggle('hidden', !val);
      quickSearchSelectedIndex = 0;
      renderQuickSearchResults(val);
    });

    input?.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (quickSearchResultsList.length > 0) {
          quickSearchSelectedIndex = (quickSearchSelectedIndex + 1) % quickSearchResultsList.length;
          updateQuickSearchSelectionHighlight();
        }
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (quickSearchResultsList.length > 0) {
          quickSearchSelectedIndex = (quickSearchSelectedIndex - 1 + quickSearchResultsList.length) % quickSearchResultsList.length;
          updateQuickSearchSelectionHighlight();
        }
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (quickSearchResultsList.length > 0 && quickSearchSelectedIndex >= 0) {
          window.selectQuickSearchResult(quickSearchSelectedIndex);
        }
      } else if (e.key === 'Escape') {
        e.preventDefault();
        window.closeQuickSearchModal();
      }
    });

    // Close notifications on outside click
    document.addEventListener('click', (e) => {
      const popover = document.getElementById('notificationsPopover');
      const btn = document.getElementById('sidebarNotificationsBtn');
      if (popover && !popover.classList.contains('hidden')) {
        if (!popover.contains(e.target) && !btn?.contains(e.target)) {
          popover.classList.add('hidden');
          btn?.classList.remove('active');
        }
      }
    });

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        const popover = document.getElementById('notificationsPopover');
        const notifBtn = document.getElementById('sidebarNotificationsBtn');
        if (popover && !popover.classList.contains('hidden')) {
          popover.classList.add('hidden');
          notifBtn?.classList.remove('active');
        }
      }
    });
  }

  function escapeHtml(text) {
    if (!text) return '';
    return text.toString()
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // ==========================================================
  // APP INITIALIZATION
  // ==========================================================
  async function initApp() {
    setupSettingsChips();
    setupLogicToggle();
    setupRuleBuilderPreview();
    setupModalListeners();
    setupSidebarListeners();
    setupQuickSearchListeners();

    try {
      window.Telegram?.WebApp?.ready();
      window.Telegram?.WebApp?.expand();
    } catch (e) {}

    // Direct browser login uses a token; Telegram Mini App uses signed initData.
    const authToken = getWebAuthToken();
    const telegramInitData = getTelegramInitData();

    if (!authToken && !telegramInitData) {
      rememberReturnRoute();
      // Immediate clean display of login screen
      const loginScreen = document.getElementById('loginScreen');
      const appEl = document.getElementById('app');
      if (appEl) {
        appEl.style.display = 'none';
        appEl.classList.add('hidden');
      }
      if (loginScreen) {
        loginScreen.style.display = 'flex';
        loginScreen.classList.remove('hidden');
      }
      return;
    }

    // Authenticate and load initial profile
    try {
      const user = await apiRequest('/api/me');
      state.user = user;
      
      // Hide login screen & reveal app UI
      const loginScreen = document.getElementById('loginScreen');
      const appEl = document.getElementById('app');
      if (loginScreen) {
        loginScreen.style.display = 'none';
        loginScreen.classList.add('hidden');
      }
      if (appEl) {
        // Let responsive CSS choose flex on mobile and grid on desktop.
        appEl.style.display = '';
        appEl.classList.remove('hidden');
      }

      if (user) {
        state.workspaces = user.workspaces || [];
        state.activeWorkspace = user.active_workspace || (state.workspaces.length ? state.workspaces[0] : null);
        renderWorkspacesDropdown();

        const uName = document.getElementById('userName');
        const uAvatar = document.getElementById('userAvatar');
        if (uName) uName.textContent = user.full_name || user.username || 'Медиабайер';
        if (uAvatar) uAvatar.textContent = (user.full_name || user.username || 'B').charAt(0).toUpperCase();

        const currentPath = normalizeAppPath(window.location.pathname);
        const isLoginPath = currentPath === '/sign-in' || currentPath === '/login';
        const initialPath = isLoginPath ? consumeReturnRoute() : (currentPath + window.location.search);
        const parsed = parsePathLocation(initialPath);
        const targetSlug = parsed.workspaceSlug;

        if (targetSlug && state.workspaces && state.activeWorkspace && state.activeWorkspace.slug !== targetSlug) {
          const matchWs = state.workspaces.find(w => w.slug === targetSlug);
          if (matchWs) {
            try {
              const swRes = await apiRequest('/api/workspaces/switch', {
                method: 'POST',
                body: JSON.stringify({ workspace_id: matchWs.id })
              });
              state.activeWorkspace = swRes.active_workspace;
              state.workspaces = swRes.workspaces || state.workspaces;
              renderWorkspacesDropdown();
            } catch (e) {}
          }
        }

        const initialTab = parsed.tab || 'home';
        if (parsed.groupFilter && parsed.groupFilter !== 'all') {
          state.accountGroupFilter = String(parsed.groupFilter);
        }

        // Restore the requested page only after authentication succeeds.
        startSummaryAutoRefresh();

        // Always load global workspace navigation data so sidebar groups, counters and quick search are immediately ready
        const initDataPromise = Promise.allSettled([
          loadAccounts(),
          loadFacebookAccounts(),
          loadPresets(),
          loadRuleGroups()
        ]);

        if (parsed.groupFilter && parsed.groupFilter !== 'all') {
          await initDataPromise;
          window.switchAccountGroup(parsed.groupFilter, {
            historyMode: 'replace',
            haptic: false,
            scrollBehavior: 'auto'
          });
        } else {
          window.switchTab(initialTab, {
            historyMode: 'replace',
            haptic: false,
            scrollBehavior: 'auto'
          });
          if (initialTab === 'rules' && parsed.ruleId) {
            await initDataPromise;
            window.openRuleRecordPage(parsed.ruleId, 'replace');
          }
        }
      }
    } catch (e) {
      console.warn("Unauthorized / access locked:", e);
      rememberReturnRoute();
      setWebAuthToken('');
      const loginScreen = document.getElementById('loginScreen');
      const loginError = document.getElementById('loginError');
      const appEl = document.getElementById('app');
      if (appEl) {
        appEl.style.display = 'none';
        appEl.classList.add('hidden');
      }
      if (loginScreen) {
        loginScreen.style.display = 'flex';
        loginScreen.classList.remove('hidden');
        if (loginError) {
          loginError.textContent = e.message || 'Доступ к Buyerly не подтверждён';
          loginError.classList.remove('hidden');
        }
        try { window.history.replaceState({}, '', '/sign-in'); } catch (e) {}
      }
    }
  }

  window.toggleLoginPassword = function () {
    const pwInput = document.getElementById('loginPassword');
    if (pwInput) {
      pwInput.type = pwInput.type === 'password' ? 'text' : 'password';
    }
  };

  window.submitLogin = async function () {
    const usernameInput = document.getElementById('loginUsername');
    const passwordInput = document.getElementById('loginPassword');
    const submitBtn = document.getElementById('btnLoginSubmit');
    const errorEl = document.getElementById('loginError');

    const username = usernameInput?.value ? usernameInput.value.trim() : '';
    const password = passwordInput?.value || '';

    if (!username) {
      if (errorEl) {
        errorEl.textContent = 'Введите логин';
        errorEl.classList.remove('hidden');
      }
      usernameInput?.focus();
      return;
    }

    if (!password) {
      if (errorEl) {
        errorEl.textContent = 'Введите пароль';
        errorEl.classList.remove('hidden');
      }
      passwordInput?.focus();
      return;
    }

    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.innerHTML = '<div class="spinner" style="width:18px;height:18px;margin:0 auto;"></div>';
    }
    if (errorEl) errorEl.classList.add('hidden');

    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password })
      });
      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.detail || 'Неверный логин или пароль');
      }

      setWebAuthToken(data.token);
      showToast(`Добро пожаловать, ${data.full_name || data.username}!`, 'success');
      await initApp();
    } catch (err) {
      if (errorEl) {
        errorEl.textContent = err.message || 'Ошибка входа';
        errorEl.classList.remove('hidden');
      }
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.innerHTML = '<span>Войти в систему</span>';
      }
    }
  };

  window.quickFillLogin = function (username, password) {
    const uInput = document.getElementById('loginUsername');
    const pInput = document.getElementById('loginPassword');
    if (uInput) uInput.value = username;
    if (pInput) pInput.value = password;
    window.submitLogin();
  };

  function setupModalListeners() {
    // Close modals on Escape key or return from Rule Record page
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        if (activeModalStack.length > 0) {
          const topModal = activeModalStack[activeModalStack.length - 1];
          window.closeModal(topModal);
        } else if (state.activeTab === 'rules' && state.currentRecordPresetId) {
          window.closeRuleRecordPage('push');
        }
      }
    });

    // Close modals on backdrop click
    document.querySelectorAll('.modal-overlay').forEach(overlay => {
      overlay.addEventListener('click', (e) => {
        if (e.target === overlay) {
          window.closeModal(overlay.id);
        }
      });
    });
  }

  function setupSidebarListeners() {
    const mainSidebar = document.getElementById('mainSidebar');
    const collapseSidebarBtn = document.getElementById('collapseSidebarBtn');
    const expandSidebarBtn = document.getElementById('expandSidebarBtn');
    const workspaceBtn = document.getElementById('workspaceBtn');
    const workspaceDropdown = document.getElementById('workspaceDropdown');
    const sidebarResizer = document.getElementById('sidebarResizer');

    const MIN_SIDEBAR_WIDTH = 200;
    const MAX_SIDEBAR_WIDTH = 360;
    const DEFAULT_SIDEBAR_WIDTH = 240;
    const STORAGE_KEY = 'buyerly_sidebar_width';

    function setSidebarWidth(width, saveToStorage = true) {
      const clampedWidth = Math.min(MAX_SIDEBAR_WIDTH, Math.max(MIN_SIDEBAR_WIDTH, Math.round(width)));
      if (mainSidebar) {
        mainSidebar.style.width = clampedWidth + 'px';
      }
      document.documentElement.style.setProperty('--sidebar-width', clampedWidth + 'px');
      if (saveToStorage) {
        try {
          localStorage.setItem(STORAGE_KEY, String(clampedWidth));
        } catch (e) {}
      }
      return clampedWidth;
    }

    function getSavedSidebarWidth() {
      try {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (raw) {
          const parsed = parseInt(raw, 10);
          if (!isNaN(parsed) && parsed >= MIN_SIDEBAR_WIDTH && parsed <= MAX_SIDEBAR_WIDTH) {
            return parsed;
          }
        }
      } catch (e) {}
      return DEFAULT_SIDEBAR_WIDTH;
    }

    // Initialize saved width
    setSidebarWidth(getSavedSidebarWidth(), false);

    // Resizer drag logic
    if (sidebarResizer && mainSidebar) {
      let isResizing = false;
      let startX = 0;
      let startWidth = DEFAULT_SIDEBAR_WIDTH;

      function onPointerDown(e) {
        if (e.button !== 0) return;
        if (mainSidebar.classList.contains('collapsed')) return;

        isResizing = true;
        startX = e.clientX;
        startWidth = mainSidebar.getBoundingClientRect().width || getSavedSidebarWidth();

        document.body.classList.add('is-sidebar-resizing');
        mainSidebar.classList.add('resizing');

        window.addEventListener('pointermove', onPointerMove);
        window.addEventListener('pointerup', onPointerUp);
        window.addEventListener('pointercancel', onPointerUp);
        e.preventDefault();
      }

      function onPointerMove(e) {
        if (!isResizing) return;
        const deltaX = e.clientX - startX;
        setSidebarWidth(startWidth + deltaX, false);
      }

      function onPointerUp(e) {
        if (!isResizing) return;
        isResizing = false;

        document.body.classList.remove('is-sidebar-resizing');
        mainSidebar.classList.remove('resizing');

        window.removeEventListener('pointermove', onPointerMove);
        window.removeEventListener('pointerup', onPointerUp);
        window.removeEventListener('pointercancel', onPointerUp);

        const finalWidth = mainSidebar.getBoundingClientRect().width || getSavedSidebarWidth();
        setSidebarWidth(finalWidth, true);
      }

      sidebarResizer.addEventListener('pointerdown', onPointerDown);
    }

    function toggleSidebar() {
      if (!mainSidebar) return;
      const willCollapse = !mainSidebar.classList.contains('collapsed');
      mainSidebar.classList.toggle('collapsed', willCollapse);
      if (expandSidebarBtn) {
        expandSidebarBtn.classList.toggle('show', willCollapse);
      }
      if (!willCollapse) {
        setSidebarWidth(getSavedSidebarWidth(), false);
      }
    }

    if (collapseSidebarBtn) collapseSidebarBtn.addEventListener('click', toggleSidebar);
    if (expandSidebarBtn) expandSidebarBtn.addEventListener('click', toggleSidebar);

    document.addEventListener('keydown', (e) => {
      if (e.ctrlKey && e.key === '.') {
        e.preventDefault();
        toggleSidebar();
      }
      if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        window.openQuickSearchModal();
      }
    });

    if (workspaceBtn && workspaceDropdown) {
      workspaceBtn.addEventListener('click', (e) => {
        window.toggleWorkspaceDropdown(e);
      });
      document.addEventListener('click', (e) => {
        if (!workspaceDropdown.contains(e.target) && !workspaceBtn.contains(e.target)) {
          workspaceDropdown.classList.remove('show');
        }
      });
    }

    document.addEventListener('click', (e) => {
      const sortDropdown = document.getElementById('listsSortDropdown');
      const btnSort = document.getElementById('btnListsSortMenu');
      if (sortDropdown && btnSort && !sortDropdown.contains(e.target) && !btnSort.contains(e.target)) {
        sortDropdown.classList.add('hidden');
        btnSort.classList.remove('active');
        document.getElementById('headerAccountsSection')?.classList.remove('has-open-menu');
      }
    });

    applySidebarSectionsCollapsedState();
  }

  window.logoutUser = async function () {
    if (getTelegramInitData()) {
      try {
        window.Telegram.WebApp.close();
        return;
      } catch (e) {}
    }
    try {
      await apiRequest('/api/auth/logout', { method: 'POST' }).catch(() => {});
    } catch (e) {}
    setWebAuthToken('');
    showToast('Вы вышли из системы', 'info');
    setTimeout(() => {
      try { window.history.replaceState({}, '', '/sign-in'); } catch (e) {}
      window.location.reload();
    }, 300);
  };

  window.addEventListener('popstate', (event) => {
    if (!state.user) return;
    const stateObj = event?.state || {};
    const parsed = parsePathLocation();
    const groupFilter = stateObj.groupFilter !== undefined ? stateObj.groupFilter : parsed.groupFilter;
    const ruleId = stateObj.ruleId !== undefined ? stateObj.ruleId : parsed.ruleId;

    if (groupFilter && groupFilter !== 'all') {
      window.switchAccountGroup(groupFilter, {
        historyMode: 'none',
        haptic: false,
        scrollBehavior: 'auto'
      });
    } else {
      const tab = stateObj.tab || parsed.tab;
      window.switchTab(tab, {
        historyMode: 'none',
        haptic: false,
        scrollBehavior: 'auto'
      });
      if (tab === 'rules') {
        if (ruleId) {
          window.openRuleRecordPage(ruleId, 'none');
        } else {
          window.closeRuleRecordPage('none');
        }
      }
    }
  });

  document.addEventListener('visibilitychange', () => {
    if (
      !document.hidden &&
      state.user &&
      state.activeTab === 'summary' &&
      !state.summaryLoading
    ) {
      const data = state.summaryCache[state.currentPeriod];
      if (!data) {
        loadSummary(state.currentPeriod, false, { silent: true, refreshIfStale: true });
      } else {
        refreshSummaryIfStale(state.currentPeriod, data);
      }
    }
  });

  // Global Outside Click listener for Attio popovers
  document.addEventListener('click', (e) => {
    // Close color palettes if clicking outside them
    const groupPalette = document.getElementById('ruleGroupColorPalette');
    if (groupPalette && !groupPalette.classList.contains('hidden')) {
      if (!groupPalette.contains(e.target) && !e.target.closest('#ruleGroupPopoverDotBtn')) {
        groupPalette.classList.add('hidden');
      }
    }
    const addColumnPalette = document.getElementById('newColumnColorPalette');
    if (addColumnPalette && !addColumnPalette.classList.contains('hidden')) {
      if (!addColumnPalette.contains(e.target) && !e.target.closest('#newColumnPopoverDotBtn')) {
        addColumnPalette.classList.add('hidden');
      }
    }

    const groupPopover = document.getElementById('ruleGroupMenuPopover');
    if (groupPopover && !groupPopover.classList.contains('hidden')) {
      if (!groupPopover.contains(e.target) && !e.target.closest('.rules-column-title-wrap') && !e.target.closest('.rules-column-btn')) {
        window.saveGroupNameFromPopover();
        groupPopover.classList.add('hidden');
      }
    }
    const addColumnPopover = document.getElementById('ruleAddColumnPopover');
    if (addColumnPopover && !addColumnPopover.classList.contains('hidden')) {
      if (!addColumnPopover.contains(e.target) && !e.target.closest('.rules-add-column-card')) {
        addColumnPopover.classList.add('hidden');
      }
    }
  });

  // Global Keyboard shortcuts (ESC to close, Ctrl+Enter to save, Arrow Left/Right to navigate)
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      const editRuleModal = document.getElementById('modalEditRule');
      if (editRuleModal && !editRuleModal.classList.contains('hidden')) {
        e.preventDefault();
        window.submitEditRule();
        return;
      }
      const createModal = document.getElementById('modalCreateRule');
      if (createModal && !createModal.classList.contains('hidden')) {
        e.preventDefault();
        window.submitCreateRule();
        return;
      }
      const editModal = document.getElementById('modalEditLimits');
      if (editModal && !editModal.classList.contains('hidden')) {
        e.preventDefault();
        document.getElementById('btnSaveLimits')?.click();
        return;
      }
    }
    if (e.key === 'Escape') {
      const editRuleModal = document.getElementById('modalEditRule');
      if (editRuleModal && !editRuleModal.classList.contains('hidden')) {
        window.closeModal('modalEditRule');
        return;
      }
      const createModal = document.getElementById('modalCreateRule');
      if (createModal && !createModal.classList.contains('hidden')) {
        window.closeModal('modalCreateRule');
        return;
      }
      const editModal = document.getElementById('modalEditRule');
      if (editModal && !editModal.classList.contains('hidden')) {
        window.closeModal('modalEditRule');
        return;
      }
      const recordView = document.getElementById('rulesRecordView');
      if (recordView && !recordView.classList.contains('hidden')) {
        window.closeRuleRecordPage('push');
        return;
      }
      const chooseModal = document.getElementById('modalChooseRule');
      if (chooseModal && !chooseModal.classList.contains('hidden')) {
        window.closeModal('modalChooseRule');
      }
      document.getElementById('ruleGroupColorPalette')?.classList.add('hidden');
      document.getElementById('newColumnColorPalette')?.classList.add('hidden');
      const groupPopover = document.getElementById('ruleGroupMenuPopover');
      if (groupPopover && !groupPopover.classList.contains('hidden')) {
        groupPopover.classList.add('hidden');
      }
      const addColumnPopover = document.getElementById('ruleAddColumnPopover');
      if (addColumnPopover && !addColumnPopover.classList.contains('hidden')) {
        addColumnPopover.classList.add('hidden');
      }
    }
    // Arrow Left / Right navigation inside Rule Record Screen
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      const recordView = document.getElementById('rulesRecordView');
      if (recordView && !recordView.classList.contains('hidden')) {
        const activeTag = document.activeElement ? document.activeElement.tagName.toLowerCase() : '';
        if (activeTag !== 'input' && activeTag !== 'textarea' && activeTag !== 'select') {
          e.preventDefault();
          window.navigateRecordRule(e.key === 'ArrowLeft' ? -1 : 1);
        }
      }
    }
  });

  // Run on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initApp);
  } else {
    initApp();
  }

})();
