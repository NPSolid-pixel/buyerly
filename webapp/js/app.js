/**
 * Buyerly Web App — Core Frontend Application Logic (Standalone SaaS)
 */

(function () {
  'use strict';

  const SUMMARY_AUTO_REFRESH_MS = 3 * 60 * 1000;
  const SUMMARY_COLUMNS = [
    { key: 'account', label: 'Кабинет', group: 'base', required: true },
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
    overview: ['account', 'data', 'spend', 'impressions', 'clicks', 'link_clicks', 'leads', 'registrations', 'purchases', 'cpl', 'cpreg', 'cpp'],
    delivery: ['account', 'data', 'spend', 'impressions', 'reach', 'frequency', 'cpm'],
    traffic: ['account', 'data', 'spend', 'impressions', 'clicks', 'link_clicks', 'unique_clicks', 'outbound_clicks', 'landing_page_views', 'ctr', 'ctr_link', 'cpc', 'cpc_link'],
    funnel: ['account', 'data', 'spend', 'leads', 'registrations', 'purchases', 'cpl', 'cpreg', 'cpp'],
    all: SUMMARY_COLUMNS.map(column => column.key)
  };
  const SUMMARY_COLUMN_GROUPS = {
    base: { label: 'Основное', columns: ['account', 'data', 'spend'] },
    delivery: { label: 'Доставка', columns: ['impressions', 'reach', 'frequency', 'cpm'] },
    traffic: { label: 'Трафик', columns: ['clicks', 'link_clicks', 'unique_clicks', 'outbound_clicks', 'landing_page_views', 'ctr', 'ctr_link', 'cpc', 'cpc_link'] },
    funnel: { label: 'Воронка', columns: ['leads', 'registrations', 'purchases', 'cpl', 'cpreg', 'cpp'] }
  };
  const SUMMARY_DEFAULT_COLUMN_WIDTHS = {
    account: 260, data: 120, spend: 112,
    impressions: 104, reach: 104, frequency: 96, cpm: 96,
    clicks: 104, link_clicks: 104, unique_clicks: 104, outbound_clicks: 112,
    landing_page_views: 120, ctr: 96, ctr_link: 96, cpc: 96, cpc_link: 96,
    leads: 88, registrations: 96, purchases: 96, cpl: 96, cpreg: 96, cpp: 96
  };
  const SUMMARY_COLUMN_MIN_WIDTH = 72;
  const SUMMARY_COLUMN_MAX_WIDTH = 420;
  let summaryAutoRefreshTimer = null;
  let summaryViewSaveQueue = Promise.resolve();
  let summaryViewChangeVersion = 0;
  let summaryFilterSaveTimer = null;

  // Application State
  const state = {
    user: null,
    accounts: [],
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
      filters: { query: '', status: 'all' },
      period: 'today'
    },
    summaryViewLoaded: false,
    presets: [],
    ruleGroups: [],
    activePresetId: null,
    currentPeriod: 'today',
    activeTab: 'accounts',
    filter: 'all',
    searchQuery: '',
    parsedAccounts: [],
    auditEvents: [],
    auditPage: 1,
    auditTotalPages: 1,
    pendingLogsAccountId: '',
    stoppedAdsets: [],
    settings: { poll_interval_minutes: 10 }
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
  // TAB NAVIGATION
  // ==========================================================
  window.switchTab = function (tabName) {
    state.activeTab = tabName;
    haptic('selection');

    // Update active tab buttons (Desktop & Mobile)
    document.querySelectorAll('.nav-tab, .mobile-nav-item').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.tab === tabName);
    });

    // Show active tab section
    document.querySelectorAll('.tab-content').forEach(section => {
      section.classList.toggle('active', section.id === `tab-${tabName}`);
    });

    // Auto-fetch data on tab switch
    if (tabName === 'accounts') {
      loadAccounts();
    } else if (tabName === 'rules') {
      loadRulesTab();
    } else if (tabName === 'summary') {
      initializeSummaryTab();
    } else if (tabName === 'logs') {
      loadLogsTab(1);
    } else if (tabName === 'settings') {
      loadSettings();
    }

    // Scroll to top
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  // ==========================================================
  // TAB 1: ACCOUNTS (МОИ КАБИНЕТЫ)
  // ==========================================================
  async function loadAccounts() {
    const listEl = document.getElementById('accountsList');
    const emptyEl = document.getElementById('accountsEmptyState');

    try {
      const data = await apiRequest('/api/accounts');
      state.accounts = data;
      renderAccounts();
    } catch (err) {
      listEl.innerHTML = `<div class="empty-state"><p class="text-danger">${err.message}</p></div>`;
    }
  }

  function renderAccounts() {
    const listEl = document.getElementById('accountsList');
    const emptyEl = document.getElementById('accountsEmptyState');
    const query = state.searchQuery.toLowerCase().trim();

    // Filter by search and chips
    const filtered = state.accounts.filter(acc => {
      const matchSearch = !query || 
        acc.name.toLowerCase().includes(query) || 
        acc.account_id.toLowerCase().includes(query);

      if (!matchSearch) return false;

      if (state.filter === 'active') return acc.account_status === 1 && acc.is_active;
      if (state.filter === 'rules') return acc.rules_enabled;
      if (state.filter === 'issue') return acc.account_status !== 1 || !acc.is_active;
      return true;
    });

    // Update chip counters
    const totalCount = state.accounts.length;
    const rulesCount = state.accounts.filter(a => a.rules_enabled).length;
    const activeCount = state.accounts.filter(a => a.account_status === 1 && a.is_active).length;
    const issueCount = state.accounts.filter(a => a.account_status !== 1 || !a.is_active).length;

    document.getElementById('countAll').textContent = totalCount;
    document.getElementById('countActive').textContent = activeCount;
    document.getElementById('countRules').textContent = rulesCount;
    document.getElementById('countIssue').textContent = issueCount;
    document.getElementById('accountsHealthTotal').textContent = totalCount;
    document.getElementById('accountsHealthActive').textContent = activeCount;
    document.getElementById('accountsHealthAutomation').textContent = rulesCount;
    document.getElementById('accountsHealthIssues').textContent = issueCount;

    if (filtered.length === 0) {
      listEl.innerHTML = '';
      emptyEl.classList.remove('hidden');
      return;
    }

    emptyEl.classList.add('hidden');
    listEl.innerHTML = filtered.map(acc => {
      const metaState = getAccountMetaState(acc);
      const activeRules = Array.isArray(acc.active_rules) ? acc.active_rules : [];
      const automationState = activeRules.length === 0
        ? { key: 'empty', label: 'Не настроена' }
        : (acc.rules_enabled ? { key: 'active', label: 'Включена' } : { key: 'paused', label: 'На паузе' });
      const actionLabels = {
        turn_off: 'Стоп', notify_only: 'Пуш', turn_on: 'Старт',
        increase_budget: '+Бюджет', decrease_budget: '-Бюджет'
      };
      const visibleRules = activeRules.slice(0, 2);
      const moreCount = Math.max(0, activeRules.length - visibleRules.length);

      return `
        <article class="account-card ${metaState.key !== 'active' ? 'account-disabled' : ''}" id="card-${escapeHtml(acc.account_id)}">
          <div class="card-header-row">
            <div class="card-title-area">
              <span class="card-title">${escapeHtml(acc.name)}</span>
              <div class="card-subtitle-row">
                <button class="card-id-copy mono" type="button" onclick="window.copyToClipboard('${escapeHtml(acc.account_id)}', this)" title="Скопировать ID">
                  ${escapeHtml(acc.account_id)}
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                </button>
                <span class="card-tz mono">${escapeHtml(acc.timezone_name || 'UTC')}</span>
              </div>
            </div>
            <button class="account-more-button" type="button" onclick="window.openAccountDetails('${escapeHtml(acc.account_id)}')">Подробнее</button>
          </div>

          <div class="account-state-grid">
            <div class="account-state-item">
              <span>Статус Meta</span>
              <b class="account-meta-state ${metaState.key}"><span class="status-dot dot-${metaState.dot}"></span>${metaState.label}</b>
            </div>
            <div class="account-state-item">
              <span>Автоматика</span>
              <b class="automation-state ${automationState.key}">${automationState.label}</b>
            </div>
            <div class="account-state-item">
              <span>Правила</span>
              <b>${activeRules.length}</b>
            </div>
          </div>

          <div class="account-rules-preview ${activeRules.length ? '' : 'empty'}">
            ${activeRules.length ? visibleRules.map(rule => `
              <span><b>${escapeHtml(actionLabels[rule.action] || 'Правило')}</b> · ${escapeHtml(rule.name || `#${rule.preset_id}`)}</span>
            `).join('') + (moreCount ? `<span class="account-rules-more">+ ещё ${moreCount}</span>` : '') : '<span>Правила ещё не назначены</span>'}
          </div>

          <div class="account-card-footer">
            <button class="btn btn-secondary btn-sm" type="button" onclick="window.openAssignRuleModal('${escapeHtml(acc.account_id)}')">Управлять правилами</button>
            <div class="automation-master-control">
              <span>${acc.rules_enabled ? 'Автоматика работает' : 'Автоматика выключена'}</span>
              <label class="switch" title="Включить или выключить автоматику">
                <input type="checkbox" ${acc.rules_enabled ? 'checked' : ''} onchange="window.toggleRules('${escapeHtml(acc.account_id)}', this.checked)">
                <span class="slider round"></span>
              </label>
            </div>
          </div>
        </article>`;
    }).join('');
  }

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

    content.innerHTML = `
      <div class="account-detail-hero">
        <div>
          <span class="eyebrow">Рекламный кабинет</span>
          <h2>${escapeHtml(account.name)}</h2>
          <button type="button" class="account-detail-copy mono" onclick="window.copyToClipboard('${escapeHtml(account.account_id)}', this)">${escapeHtml(account.account_id)} · копировать</button>
        </div>
        <span class="account-meta-state ${metaState.key}"><span class="status-dot dot-${metaState.dot}"></span>${metaState.label}</span>
      </div>

      <div class="account-detail-grid">
        <div class="account-detail-field"><span>Статус Meta</span><b>${escapeHtml(account.status_label || metaState.label)}</b></div>
        <div class="account-detail-field"><span>Часовой пояс</span><b class="mono">${escapeHtml(account.timezone_name || 'UTC')}</b></div>
        <div class="account-detail-field"><span>Автоматика</span><b>${account.rules_enabled ? 'Включена' : 'Выключена'}</b></div>
        <div class="account-detail-field"><span>Назначено правил</span><b>${activeRules.length}</b></div>
        ${ownerHtml}
        <div class="account-detail-field"><span>Добавлен</span><b>${escapeHtml(account.created_at || '—')}</b></div>
      </div>

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
    window.openDeleteConfirmModal(account.account_id, account.name);
  };

  // ==========================================================
  // TAB: RULES & PRESETS MANAGEMENT (PHOTO 2 & TAB LOGIC)
  // ==========================================================
  async function loadRulesTab() {
    await Promise.all([loadPresets(), loadRuleGroups(), loadAccounts()]);
    renderRulesTab();
  }

  function renderRulesTab() {
    const container = document.getElementById('rulesCardsContainer');
    const emptyEl = document.getElementById('rulesEmptyState');
    const activeCountEl = document.getElementById('rulesActiveCount');
    const groupsCountEl = document.getElementById('rulesGroupsCount');
    const linkedCountEl = document.getElementById('rulesLinkedAccsCount');
    if (!container || !emptyEl) return;

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
    renderRuleGroups();

    if (totalPresets === 0) {
      container.innerHTML = '';
      emptyEl.classList.remove('hidden');
      return;
    }

    emptyEl.classList.add('hidden');

    const actionBadgeMap = {
      'turn_off': { label: '🔴 Выключить адсеты', class: 'rule-action-turn_off' },
      'notify_only': { label: '🔔 Только уведомление', class: 'rule-action-notify_only' },
      'turn_on': { label: '🟢 Включить адсеты', class: 'rule-action-turn_on' },
      'increase_budget': { label: '📈 Увеличить бюджет', class: 'rule-action-increase_budget' },
      'decrease_budget': { label: '📉 Уменьшить бюджет', class: 'rule-action-decrease_budget' }
    };

    container.innerHTML = state.presets.map(p => {
      const act = actionBadgeMap[p.action] || { label: p.action, class: '' };
      const condList = p.conditions || [];
      const logicText = p.condition_logic === 'or' ? 'Логика: OR (Любое)' : 'Логика: AND (Все)';
      
      const condsHtml = condList.map(c => {
        const metricLabels = {
          'spend': 'Спенд', 'cpl': 'CPL', 'cpreg': 'CPReg', 'cpp': 'CPP',
          'legacy_cpa': 'Старый общий CPA',
          'leads': 'Лиды', 'registrations': 'Реги', 'purchases': 'Покупки',
          'ctr': 'CTR', 'cpc': 'CPC'
        };
        const mLabel = metricLabels[c.metric] || c.metric;
        const op = { gt: '>', gte: '≥', lt: '<', lte: '≤', eq: '=' }[c.operator] || c.operator;
        const unit = (c.metric === 'leads' || c.metric === 'registrations' || c.metric === 'purchases') ? ' шт' : (c.metric === 'ctr' ? '%' : '$');
        const numericValue = Number(c.value || 0);
        const valStr = unit === '$' ? `$${numericValue.toFixed(1)}` : `${numericValue}${unit}`;
        const windowLabels = { 'today': 'Сегодня', 'yesterday': 'Вчера', 'last_3d': '3 дня', 'last_7d': '7 дней' };
        const winStr = windowLabels[c.time_window || 'today'] || 'Сегодня';

        return `
          <div class="rule-condition-chip">
            <span class="rule-cond-bullet">•</span>
            <span><b>${mLabel}</b> ${op} <b>${valStr}</b></span>
            <span style="font-size:11px; color:var(--tg-hint);">(${winStr})</span>
          </div>
        `;
      }).join('');
      const legacyWarningHtml = condList.some(c => c.metric === 'legacy_cpa' || c.metric === 'cpa')
        ? '<div class="rule-migration-warning">Правило выключено: замените старый общий CPA на CPL, CPReg или CPP.</div>'
        : '';

      let budgetInfoHtml = '';
      if (p.action === 'increase_budget' || p.action === 'decrease_budget') {
        const sign = p.action === 'increase_budget' ? '+' : '-';
        const cap = p.budget_max_daily > 0 ? ` · Макс: $${p.budget_max_daily}/день` : '';
        budgetInfoHtml = `<div style="font-size:11.5px; color:var(--tg-link); font-weight:600;">💰 Шаг: ${sign}${p.budget_change_percent || 20}%${cap}</div>`;
      }

      return `
        <div class="rule-item-card">
          <div class="rule-card-top">
            <div>
              <div class="rule-card-title">${escapeHtml(p.name)}</div>
              <div class="rule-card-meta">
                <span>⏱ Проверка: ${p.check_interval_minutes || 5} мин</span>
                <span>·</span>
                <span>⏸ Пауза: ${p.cooldown_minutes ? p.cooldown_minutes + ' мин' : 'нет'}</span>
              </div>
            </div>
            <span class="rule-action-badge ${act.class}">${act.label}</span>
          </div>

          ${budgetInfoHtml}
          ${legacyWarningHtml}

          <div class="rule-conditions-list">
            <div style="font-size:11px; font-weight:700; color:var(--tg-hint); text-transform:uppercase; margin-bottom:2px;">
              ${logicText}
            </div>
            ${condsHtml || '<span style="font-size:12px; color:var(--tg-hint);">Без условий</span>'}
          </div>

          <div class="rule-card-footer">
            <span style="font-size:11px; color:var(--tg-hint);">🔔 ${p.notify_tg !== false ? 'Уведомления ВКЛ' : 'Без пушей'}</span>
            <div class="rule-card-actions">
              <button class="btn-rule-action" onclick="window.editPresetFromTab(${p.id})">Редактировать</button>
              <button class="btn-rule-action danger" onclick="window.deletePresetDirectly(${p.id})">Удалить</button>
            </div>
          </div>
        </div>
      `;
    }).join('');
  }

  async function loadRuleGroups() {
    try {
      state.ruleGroups = await apiRequest('/api/rule-groups') || [];
    } catch (error) {
      state.ruleGroups = [];
      console.error('Failed to load rule groups:', error);
    }
  }

  function renderRuleGroups() {
    const section = document.getElementById('ruleGroupsSection');
    const container = document.getElementById('ruleGroupsContainer');
    const singleHeading = document.getElementById('singleRulesHeading');
    if (!section || !container) return;

    const hasGroups = state.ruleGroups.length > 0;
    section.classList.toggle('hidden', !hasGroups);
    singleHeading?.classList.toggle('hidden', !hasGroups || state.presets.length === 0);
    if (!hasGroups) {
      container.innerHTML = '';
      return;
    }

    const actionLabels = {
      turn_off: 'Стоп', notify_only: 'Пуш', turn_on: 'Старт',
      increase_budget: '+Бюджет', decrease_budget: '-Бюджет'
    };
    container.innerHTML = state.ruleGroups.map(group => {
      const presetIds = new Set(group.preset_ids || []);
      const linkedAccounts = state.accounts.filter(account => {
        if (!account.rules_enabled || presetIds.size === 0) return false;
        const attached = new Set((account.active_rules || []).map(rule => rule.preset_id));
        return [...presetIds].every(id => attached.has(id));
      }).length;
      const chips = (group.rules || []).map(rule => `
        <span class="rule-group-rule-chip">
          <span>${escapeHtml(actionLabels[rule.action] || rule.action)}</span>
          <span>${escapeHtml(rule.name)}</span>
        </span>`).join('');
      return `
        <article class="rule-group-card">
          <div class="rule-group-card-head">
            <div>
              <div class="rule-group-card-title">${escapeHtml(group.name)}</div>
              <div class="rule-group-card-description">${escapeHtml(group.description || 'Готовый набор автоматических действий')}</div>
            </div>
            <span class="rule-group-count">${(group.rules || []).length} правил</span>
          </div>
          <div class="rule-group-rule-stack">${chips || '<span class="text-hint">В группе пока нет доступных правил</span>'}</div>
          <div class="rule-group-card-footer">
            <span>Подключена к ${linkedAccounts} кабинетам</span>
            <div class="rule-card-actions">
              <button class="btn-rule-action" onclick="window.editRuleGroup(${group.id})">Изменить</button>
              <button class="btn-rule-action danger" onclick="window.deleteRuleGroup(${group.id})">Удалить</button>
            </div>
          </div>
        </article>`;
    }).join('');
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
    if (state.presets.length === 0) await loadPresets();
    if (state.presets.length === 0) {
      showToast('Сначала создайте хотя бы одно правило', 'info');
      window.openCreateRuleFromTab();
      return;
    }
    document.getElementById('editingRuleGroupId').value = '';
    document.getElementById('ruleGroupName').value = '';
    document.getElementById('ruleGroupDescription').value = '';
    document.getElementById('ruleGroupModalTitle').textContent = 'Новая группа правил';
    document.getElementById('btnDeleteRuleGroup').classList.add('hidden');
    renderRuleGroupChoices([]);
    window.openModal('modalRuleGroup');
    document.getElementById('ruleGroupName')?.focus();
  };

  window.editRuleGroup = async function (groupId) {
    const group = state.ruleGroups.find(item => item.id === groupId);
    if (!group) return;
    if (state.presets.length === 0) await loadPresets();
    document.getElementById('editingRuleGroupId').value = group.id;
    document.getElementById('ruleGroupName').value = group.name;
    document.getElementById('ruleGroupDescription').value = group.description || '';
    document.getElementById('ruleGroupModalTitle').textContent = `Группа: ${group.name}`;
    document.getElementById('btnDeleteRuleGroup').classList.remove('hidden');
    renderRuleGroupChoices(group.preset_ids || []);
    window.openModal('modalRuleGroup');
  };

  window.deleteRuleGroup = async function (groupId) {
    const group = state.ruleGroups.find(item => item.id === groupId);
    if (!group || !window.confirm(`Удалить группу «${group.name}»? Уже назначенные правила останутся в кабинетах.`)) return;
    try {
      await apiRequest(`/api/rule-groups/${groupId}`, { method: 'DELETE' });
      showToast('Группа удалена. Активные правила сохранены.', 'success');
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
    if (presetIds.length === 0) {
      showToast('Выберите хотя бы одно правило', 'error');
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
    haptic('selection');
    document.getElementById('editLimitsAccountId').value = '';
    document.getElementById('modalLimitsTitle').textContent = 'Создание правила';
    window.newPresetMode();
    window.openModal('modalEditLimits');
  };

  window.editPresetFromTab = function (presetId) {
    haptic('selection');
    document.getElementById('editLimitsAccountId').value = '';
    document.getElementById('modalLimitsTitle').textContent = 'Редактирование правила';
    window.selectPreset(presetId);
    window.openModal('modalEditLimits');
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
              <b>${complete ? '✓ ' : ''}${escapeHtml(group.name)}</b>
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
        'turn_off': '🔴 Стоп',
        'notify_only': '🔔 Пуш',
        'turn_on': '🟢 Старт',
        'increase_budget': '📈 +Бюджет',
        'decrease_budget': '📉 -Бюджет'
      };

      listEl.innerHTML = state.presets.map(p => {
        const isCurrent = acc.active_rules && acc.active_rules.some(r => r.preset_id === p.id);
        const actLabel = actionBadgeMap[p.action] || p.action;
        const condCount = p.conditions ? p.conditions.length : 0;
        return `
          <div class="assign-preset-item ${isCurrent ? 'active' : ''}" onclick="window.${isCurrent ? 'detachRuleFromCurrentAccount' : 'pickRuleForAccount'}(${p.id})">
            <div class="assign-preset-info">
              <div class="assign-preset-title">${isCurrent ? '✓ ' : ''}${escapeHtml(p.name)}</div>
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
      renderPresetsList(state.activePresetId);
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
      const actionBadge = p.action === 'turn_off' ? '🔴 Стоп' : (p.action === 'notify_only' ? '🔔 Пуш' : '🟢 Старт');
      return `
        <div class="preset-chip-item ${isSelected ? 'active' : ''}" onclick="window.selectPreset(${p.id})">
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
      });
    });
  }

  function handleActionChange(action) {
    const budgetSection = document.getElementById('budgetConfigSection');
    if (action === 'increase_budget' || action === 'decrease_budget') {
      budgetSection?.classList.remove('hidden');
    } else {
      budgetSection?.classList.add('hidden');
    }
  }

  document.getElementById('ruleActionSelect')?.addEventListener('change', (e) => {
    handleActionChange(e.target.value);
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

    state.activePresetId = preset.id;
    document.getElementById('editingPresetId').value = preset.id;
    document.getElementById('ruleNameInput').value = preset.name;
    const action = preset.action || 'turn_off';
    document.getElementById('ruleActionSelect').value = action;
    handleActionChange(action);

    document.getElementById('budgetChangePercentInput').value = preset.budget_change_percent || 20;
    document.getElementById('budgetMaxDailyInput').value = preset.budget_max_daily || 0;
    setLogicUI(preset.condition_logic || 'and');

    document.getElementById('builderModeTag').textContent = `Пресет: ${preset.name}`;
    document.getElementById('btnDeletePreset')?.classList.remove('hidden');
    updateRuleSaveButtonLabel();

    setCooldownUI(preset.cooldown_minutes || 0);
    setIntervalUI(preset.check_interval_minutes || 5);
    const tgToggle = document.getElementById('ruleNotifyTgToggle');
    if (tgToggle) tgToggle.checked = preset.notify_tg !== false;

    renderConditions(preset.conditions || []);
    renderPresetsList(preset.id);
  };

  window.newPresetMode = function () {
    haptic('selection');
    state.activePresetId = null;
    document.getElementById('editingPresetId').value = '';
    document.getElementById('ruleNameInput').value = '';
    document.getElementById('ruleActionSelect').value = 'turn_off';
    handleActionChange('turn_off');

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
        <option value="spend" ${metric === 'spend' ? 'selected' : ''}>Спенд ($)</option>
        <option value="cpl" ${normalizedMetric === 'cpl' ? 'selected' : ''}>Цена лида · CPL ($)</option>
        <option value="cpreg" ${normalizedMetric === 'cpreg' ? 'selected' : ''}>Цена регистрации · CPReg ($)</option>
        <option value="cpp" ${normalizedMetric === 'cpp' ? 'selected' : ''}>Цена покупки · CPP ($)</option>
        <option value="leads" ${normalizedMetric === 'leads' ? 'selected' : ''}>Лиды (шт)</option>
        <option value="registrations" ${normalizedMetric === 'registrations' ? 'selected' : ''}>Регистрации (шт)</option>
        <option value="purchases" ${normalizedMetric === 'purchases' ? 'selected' : ''}>Покупки (шт)</option>
        <option value="ctr" ${normalizedMetric === 'ctr' ? 'selected' : ''}>CTR всех кликов (%)</option>
        <option value="cpc" ${normalizedMetric === 'cpc' ? 'selected' : ''}>CPC всех кликов ($)</option>
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
      <button type="button" class="btn-remove-cond" onclick="this.closest('.rule-condition-row').remove()" title="Удалить условие">&times;</button>
    `;
    container.appendChild(row);
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
    document.getElementById('modalLimitsTitle').textContent = `Правило для ${acc.name}`;
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

    if (conditions.length === 0) {
      showToast('Добавьте хотя бы одно условие правила', 'error');
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
        budget_change_percent: budgetChangePercent,
        budget_max_daily: budgetMaxDaily
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
        if (editingPresetId) {
          await apiRequest(`/api/presets/${editingPresetId}`, {
            method: 'PUT',
            body: JSON.stringify(payload)
          });
          showToast('Пресет успешно обновлен!', 'success');
        } else {
          await apiRequest('/api/presets', {
            method: 'POST',
            body: JSON.stringify(payload)
          });
          showToast('Пресет успешно создан!', 'success');
        }
        haptic('notification', 'success');
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
      window.openDeleteConfirmModal(acc.account_id, acc.name);
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
    await loadSummaryViewPreference();
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
    const requested = Array.isArray(preference.visible_columns)
      ? preference.visible_columns.filter(key => knownColumns.has(key))
      : SUMMARY_VIEW_PRESETS.all;
    const visibleSet = new Set([...requested, 'account', 'data']);
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
      filters: { query: queryFilter, status: statusFilter },
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
      return `<th${rowspan ? ' rowspan="2"' : ''} class="summary-sortable-header${alignment}" data-summary-column="${column.key}" data-summary-sort="${column.key}" tabindex="0" role="button" aria-sort="${ariaSort}">${escapeHtml(column.label)} <span class="summary-sort-indicator">${indicator}</span></th>`;
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

    const table = document.querySelector('.summary-metrics-table');
    if (table) {
      const width = state.summaryView.column_order
        .filter(key => visible.has(key))
        .reduce((total, key) => total + state.summaryView.column_widths[key], 0);
      table.style.width = `${Math.max(380, width)}px`;
      table.style.minWidth = `${Math.max(380, width)}px`;
    }
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

  function formatMoneyOrDash(value) {
    return typeof value === 'number' && Number.isFinite(value) ? `$${value.toFixed(2)}` : '—';
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
      status.textContent = options.refreshError
        ? `Обновление не удалось · показываем данные от ${generatedLabel}`
        : `${data.source || 'Meta Marketing API'} · последнее обновление ${generatedLabel}`;
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
    const previous = data.snapshot?.previous;
    if (!previous) {
      comparison.className = 'kpi-comparison';
      comparison.textContent = 'Предыдущий снимок появится после следующего обновления';
      return;
    }

    const currentSpend = Number(data.total_spend || 0);
    const previousSpend = Number(previous.total_spend || 0);
    const delta = currentSpend - previousSpend;
    const deltaLabel = `${delta > 0 ? '+' : delta < 0 ? '−' : '±'}$${Math.abs(delta).toFixed(2)}`;
    comparison.className = 'kpi-comparison has-previous';
    comparison.textContent = `До обновления ${formatSummaryTime(previous.generated_at)} · ${formatMoneyOrDash(previousSpend)} · изменение ${deltaLabel}`;
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

  function summaryAccountSortValue(account, key) {
    if (key === 'account') return String(account.short_name || account.name || account.account_id || '').toLocaleLowerCase('ru');
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
      return [account.short_name, account.name, account.account_id]
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
        const spendStr = hasMetrics ? formatMoneyOrDash(Number(acc.spend || 0)) : '—';
        const displayName = acc.short_name || acc.name;

        return `
          <tr>
            <td data-summary-column="account"><b>${escapeHtml(displayName)}</b> <span class="mono text-hint" style="font-size:11px;">(${acc.account_id})</span></td>
            <td data-summary-column="data">${summaryDataStatus(acc)}</td>
            <td class="text-right mono" data-summary-column="spend"><b>${spendStr}</b></td>
            <td class="text-right mono" data-summary-column="impressions">${hasMetrics ? formatNumber(acc.impressions) : '—'}</td>
            <td class="text-right mono" data-summary-column="reach">${hasMetrics ? formatOptionalNumber(acc.reach) : '—'}</td>
            <td class="text-right mono" data-summary-column="frequency">${hasMetrics ? formatDecimalOrDash(acc.frequency) : '—'}</td>
            <td class="text-right mono" data-summary-column="cpm">${hasMetrics ? formatMoneyOrDash(acc.cpm) : '—'}</td>
            <td class="text-right mono" data-summary-column="clicks">${hasMetrics ? formatNumber(acc.clicks) : '—'}</td>
            <td class="text-right mono" data-summary-column="link_clicks">${hasMetrics ? formatOptionalNumber(acc.link_clicks) : '—'}</td>
            <td class="text-right mono" data-summary-column="unique_clicks">${hasMetrics ? formatOptionalNumber(acc.unique_clicks) : '—'}</td>
            <td class="text-right mono" data-summary-column="outbound_clicks">${hasMetrics ? formatOptionalNumber(acc.outbound_clicks) : '—'}</td>
            <td class="text-right mono" data-summary-column="landing_page_views">${hasMetrics ? formatOptionalNumber(acc.landing_page_views) : '—'}</td>
            <td class="text-right mono" data-summary-column="ctr">${hasMetrics ? Number(acc.ctr || 0).toFixed(2) + '%' : '—'}</td>
            <td class="text-right mono" data-summary-column="ctr_link">${hasMetrics ? formatDecimalOrDash(acc.ctr_link, 2, '%') : '—'}</td>
            <td class="text-right mono" data-summary-column="cpc">${hasMetrics ? formatMoneyOrDash(acc.cpc) : '—'}</td>
            <td class="text-right mono" data-summary-column="cpc_link">${hasMetrics ? formatMoneyOrDash(acc.cpc_link) : '—'}</td>
            <td class="text-right mono" data-summary-column="leads" style="color:var(--tg-link);">${hasMetrics ? formatNumber(acc.leads) : '—'}</td>
            <td class="text-right mono" data-summary-column="registrations" style="color:var(--color-success);">${hasMetrics ? formatNumber(acc.registrations) : '—'}</td>
            <td class="text-right mono" data-summary-column="purchases">${hasMetrics ? formatNumber(acc.purchases) : '—'}</td>
            <td class="text-right mono" data-summary-column="cpl">${hasMetrics ? formatMoneyOrDash(acc.cost_per_lead) : '—'}</td>
            <td class="text-right mono" data-summary-column="cpreg">${hasMetrics ? formatMoneyOrDash(acc.cost_per_registration) : '—'}</td>
            <td class="text-right mono" data-summary-column="cpp"><b>${hasMetrics ? formatMoneyOrDash(acc.cost_per_purchase) : '—'}</b></td>
          </tr>`;
      }).join('');
    }
    applySummaryColumnVisibility();
    return accounts;
  }

  function renderSummaryData(data) {
    // KPI Cards
    document.getElementById('kpiSpend').textContent = formatMoneyOrDash(Number(data.total_spend || 0));
    document.getElementById('kpiLeads').textContent = formatNumber(data.total_leads);
    document.getElementById('kpiRegs').textContent = formatNumber(data.total_regs);
    document.getElementById('kpiCpl').textContent = formatMoneyOrDash(data.cost_per_lead);
    document.getElementById('kpiCpreg').textContent = formatMoneyOrDash(data.cost_per_registration);
    document.getElementById('kpiPurchases').textContent = formatNumber(data.total_purchases);
    document.getElementById('kpiCpp').textContent = formatMoneyOrDash(data.cost_per_purchase);
    document.getElementById('kpiImpressions').textContent = formatOptionalNumber(data.total_impressions);
    document.getElementById('kpiReach').textContent = formatOptionalNumber(data.total_reach);
    document.getElementById('kpiFrequency').textContent = formatDecimalOrDash(data.avg_frequency);
    document.getElementById('kpiCpm').textContent = formatMoneyOrDash(data.avg_cpm);
    document.getElementById('kpiClicks').textContent = formatNumber(data.total_clicks);
    document.getElementById('kpiCtr').textContent = data.total_impressions > 0 ? `${Number(data.avg_ctr || 0).toFixed(2)}%` : '—';
    document.getElementById('kpiCpc').textContent = data.total_clicks > 0 ? formatMoneyOrDash(Number(data.avg_cpc || 0)) : '—';
    document.getElementById('kpiLinkClicks').textContent = formatOptionalNumber(data.total_link_clicks);
    document.getElementById('kpiLinkCtr').textContent = formatDecimalOrDash(data.avg_ctr_link, 2, '%');
    document.getElementById('kpiCpcLink').textContent = formatMoneyOrDash(data.avg_cpc_link);
    document.getElementById('kpiOutboundClicks').textContent = formatOptionalNumber(data.total_outbound_clicks);
    document.getElementById('kpiOutboundCtr').textContent = formatDecimalOrDash(data.avg_ctr_outbound, 2, '%');
    document.getElementById('kpiLandingPageViews').textContent = formatOptionalNumber(data.total_landing_page_views);
    document.getElementById('kpiCostPerLandingPageView').textContent = formatMoneyOrDash(data.cost_per_landing_page_view);
    document.getElementById('kpiUniqueClicks').textContent = formatOptionalNumber(data.total_unique_clicks);
    renderSpendComparison(data);
    renderSummaryProvenance(data);
    renderSummaryQuality(data.data_quality || {});
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
        const displayName = acc.short_name || acc.name;
        const subLabel = acc.name !== displayName ? `${escapeHtml(acc.name)} · ${acc.account_id}` : acc.account_id;
        const hasMetrics = summaryAccountHasMetrics(acc);
        const statusPillHtml = summaryDataStatus(acc);

        return `
          <div class="mob-summary-card">
            <div class="mob-card-head">
              <div style="display:flex; flex-direction:column; overflow:hidden; padding-right:8px; flex:1;">
                <b class="mob-card-name" style="font-size:14px; font-weight:600; color:var(--tg-text); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${escapeHtml(displayName)}</b>
                <span class="mono text-hint" style="font-size:11px; color:var(--tg-hint); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${subLabel}</span>
                ${statusPillHtml}
              </div>
              <span class="mono" style="font-size:16px;font-weight:700;color:var(--tg-link);white-space:nowrap;flex-shrink:0;">${hasMetrics ? formatMoneyOrDash(Number(acc.spend || 0)) : '—'}</span>
            </div>
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
                <span class="stat-box-val">${hasMetrics ? formatMoneyOrDash(acc.cpm) : '—'}</span>
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
                <span class="stat-box-val">${hasMetrics ? formatMoneyOrDash(acc.cost_per_lead) : '—'}</span>
              </div>
              <div class="stat-box">
                <span class="stat-box-label">Регистрации</span>
                <span class="stat-box-val" style="color:var(--color-success);">${hasMetrics ? formatNumber(acc.registrations) : '—'}</span>
              </div>
              <div class="stat-box">
                <span class="stat-box-label">CPReg</span>
                <span class="stat-box-val">${hasMetrics ? formatMoneyOrDash(acc.cost_per_registration) : '—'}</span>
              </div>
              <div class="stat-box">
                <span class="stat-box-label">Покупки</span>
                <span class="stat-box-val">${hasMetrics ? formatNumber(acc.purchases) : '—'}</span>
              </div>
              <div class="stat-box">
                <span class="stat-box-label">CPP</span>
                <span class="stat-box-val">${hasMetrics ? formatMoneyOrDash(acc.cost_per_purchase) : '—'}</span>
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
    DAY_START: 'Начало дня',
    DISMISS_STOPPED: 'Решение подтверждено',
    RULE_ACTION: 'Действие правила'
  };

  const auditStatusLabels = {
    SUCCESS: 'Успешно',
    ERROR: 'Ошибка',
    WARNING: 'Внимание',
    INFO: 'Информация'
  };

  function auditStatusBadge(status) {
    const normalized = (status || 'INFO').toUpperCase();
    const modifier = ['SUCCESS', 'ERROR', 'WARNING'].includes(normalized) ? normalized.toLowerCase() : 'info';
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
      `<option value="${escapeHtml(account.account_id)}">${escapeHtml(account.name || account.account_id)}</option>`
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
        <td>${auditStatusBadge(event.status)}</td>
        <td class="log-event-cell">${escapeHtml(auditEventLabel(event))}</td>
        <td class="log-target">${escapeHtml(auditTarget(event))}<small>${escapeHtml(event.adset_name || event.adset_id || event.account_id || '')}</small></td>
        <td>${escapeHtml(event.rule_name || '—')}</td>
        <td class="log-message">${escapeHtml(event.message || 'Без дополнительного сообщения')}<small>${event.action ? `Действие: ${escapeHtml(event.action)}` : ''}</small></td>
        <td><button class="log-row-action" type="button" onclick="window.openLogDetails(${event.id})">Детали</button></td>
      </tr>`).join('');

    mobileList.innerHTML = events.map(event => `
      <article class="log-mobile-card" onclick="window.openLogDetails(${event.id})" role="button" tabindex="0">
        <div class="log-mobile-head">${auditStatusBadge(event.status)}<span class="log-mobile-time">${formatAuditTime(event.created_at, true)}</span></div>
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
        <div class="log-detail-block"><span>Статус</span>${auditStatusBadge(event.status)}</div>
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
      </div>`;
    window.openModal('modalLogDetails');
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
    if (document.getElementById('logsStoppedCount')) document.getElementById('logsStoppedCount').textContent = records.length;
    if (!listEl) return;

    listEl.innerHTML = records.map(record => `
      <div class="stopped-card-item" id="stopped-item-${escapeHtml(record.adset_id)}">
        <div>
          <b>${escapeHtml(record.adset_name)}</b> <span class="mono text-hint">${escapeHtml(record.account_id)}</span>
          <div style="font-size:11px;color:var(--tg-hint);margin-top:2px;">
            Остановлен ${escapeHtml(record.stopped_at || '—')} · Спенд <b>$${Number(record.stop_spend || 0).toFixed(2)}</b> · Лиды ${record.stop_leads || 0} · Реги ${record.stop_registrations || 0}
          </div>
        </div>
        <div style="display:flex;gap:6px;flex-shrink:0;">
          <button class="btn btn-primary btn-sm" onclick="window.reactivateAdset('${escapeHtml(record.adset_id)}')">Включить</button>
          <button class="btn btn-secondary btn-sm" title="Подтвердить остановку" onclick="window.dismissAdset('${escapeHtml(record.adset_id)}')">Подтвердить</button>
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
      showToast('Остановка подтверждена', 'success');
      if (state.activeTab === 'logs') loadLogsTab(state.auditPage);
    } catch (err) {
      showToast(`Ошибка: ${err.message}`, 'error');
    }
  };

  // ==========================================================
  // TAB 3: BATCH ADD (УМНЫЙ ИМПОРТ)
  // ==========================================================
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
  async function loadSettings() {
    try {
      const data = await apiRequest('/api/settings');
      state.settings = data;

      const canManageInterval = data.user_role === 'admin';
      document.querySelectorAll('.btn-interval').forEach(btn => {
        btn.classList.toggle('active', parseInt(btn.dataset.interval) === data.poll_interval_minutes);
        btn.disabled = !canManageInterval;
        btn.title = canManageInterval ? '' : 'Изменение доступно администратору';
      });

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

  document.querySelectorAll('.btn-interval').forEach(btn => {
    btn.addEventListener('click', async () => {
      const interval = parseInt(btn.dataset.interval);
      haptic('impact', 'medium');

      try {
        await apiRequest('/api/settings/interval', {
          method: 'POST',
          body: JSON.stringify({ minutes: interval })
        });
        showToast(`Базовый интервал мониторинга: ${interval} мин`, 'success');
        document.querySelectorAll('.btn-interval').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
      } catch (err) {
        showToast(`Ошибка: ${err.message}`, 'error');
      }
    });
  });

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
      renderAccounts();
    });
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
    const header = event.target.closest('[data-summary-sort]');
    if (header) changeSummarySort(header.dataset.summarySort);
  });

  document.getElementById('summaryTableHead')?.addEventListener('keydown', event => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    const header = event.target.closest('[data-summary-sort]');
    if (!header) return;
    event.preventDefault();
    changeSummarySort(header.dataset.summarySort);
  });

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

  let logsSearchTimer;
  document.getElementById('logsSearchInput')?.addEventListener('input', () => {
    window.clearTimeout(logsSearchTimer);
    logsSearchTimer = window.setTimeout(() => loadLogsTab(1), 350);
  });

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
    setupModalListeners();

    try {
      window.Telegram?.WebApp?.ready();
      window.Telegram?.WebApp?.expand();
    } catch (e) {}

    // Direct browser login uses a token; Telegram Mini App uses signed initData.
    const authToken = getWebAuthToken();
    const telegramInitData = getTelegramInitData();

    if (!authToken && !telegramInitData) {
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
        const uName = document.getElementById('userName');
        const uAvatar = document.getElementById('userAvatar');
        if (uName) uName.textContent = user.full_name || user.username || 'Медиабайер';
        if (uAvatar) uAvatar.textContent = (user.full_name || user.username || 'B').charAt(0).toUpperCase();
      }

      if (window.location.pathname === '/sign-in' || window.location.pathname === '/login') {
        try { window.history.replaceState({}, '', '/'); } catch (e) {}
      }

      // Load initial tab (Accounts)
      startSummaryAutoRefresh();
      window.switchTab('accounts');
    } catch (e) {
      console.warn("Unauthorized / access locked:", e);
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
    // Close modals on Escape key
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && activeModalStack.length > 0) {
        const topModal = activeModalStack[activeModalStack.length - 1];
        window.closeModal(topModal);
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
      window.location.reload();
    }, 300);
  };

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

  // Run on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initApp);
  } else {
    initApp();
  }

})();
