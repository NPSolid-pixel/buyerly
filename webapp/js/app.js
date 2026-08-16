/**
 * Buyerly Web App — Core Frontend Application Logic (Standalone SaaS)
 */

(function () {
  'use strict';

  // Application State
  const state = {
    user: null,
    accounts: [],
    summary: null,
    summaryCache: {},
    summaryLastFetchedAt: {},
    presets: [],
    activePresetId: null,
    currentPeriod: 'today',
    activeTab: 'accounts',
    filter: 'all',
    searchQuery: '',
    parsedAccounts: [],
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
      updateFetchButtonLabel(state.currentPeriod);
      if (state.summaryCache[state.currentPeriod]) {
        renderSummaryData(state.summaryCache[state.currentPeriod]);
        const statusEl = document.getElementById('summaryStatusLabel');
        if (statusEl && state.summaryLastFetchedAt[state.currentPeriod]) {
          statusEl.textContent = `Сводка сформирована в ${state.summaryLastFetchedAt[state.currentPeriod]}`;
        }
      }
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

    if (filtered.length === 0) {
      listEl.innerHTML = '';
      emptyEl.classList.remove('hidden');
      return;
    }

    emptyEl.classList.add('hidden');
    listEl.innerHTML = filtered.map(acc => {
      let statusClass = 'status-active';
      let statusText = '<span class="status-dot dot-success"></span>Активен';
      
      if (acc.account_status === 2 || !acc.is_active) {
        statusClass = 'status-disabled';
        statusText = '<span class="status-dot dot-danger"></span>Заблокирован';
      } else if (acc.account_status === 3) {
        statusClass = 'status-unsettled';
        statusText = '<span class="status-dot dot-warning"></span>Hold на карте';
      }

      const cardClass = [
        'account-card',
        acc.rules_enabled ? 'rules-active' : '',
        acc.account_status !== 1 ? 'account-disabled' : ''
      ].filter(Boolean).join(' ');

      const activeRules = Array.isArray(acc.active_rules) ? acc.active_rules : [];
      let rulePillHtml = '';

      if (activeRules.length > 0) {
        const actionIcons = {
          'turn_off': '🔴', 'notify_only': '🔔', 'turn_on': '🟢',
          'increase_budget': '📈', 'decrease_budget': '📉'
        };
        rulePillHtml = `
          <div class="card-rules-list">
            ${activeRules.map(rule => `
              <button type="button" class="card-limits-btn ${acc.rules_enabled ? 'active' : ''}" onclick="window.openAssignRuleModal('${acc.account_id}')" title="Управлять правилами кабинета">
                <span style="font-size:13px; line-height:1;">${actionIcons[rule.action] || '⚙️'}</span>
                <span class="limits-value">${escapeHtml(rule.name || `Правило #${rule.preset_id}`)}</span>
              </button>
            `).join('')}
            <button type="button" class="card-limits-btn empty" onclick="window.openAssignRuleModal('${acc.account_id}')" title="Добавить ещё одно правило">
              <span class="plus-icon" style="font-size:14px; font-weight:700; color:#7aa2f7;">+</span>
              <span class="limits-value" style="color:#7aa2f7; font-weight:600;">Добавить</span>
            </button>
          </div>
        `;
      } else {
        rulePillHtml = `
          <button type="button" class="card-limits-btn empty" onclick="window.openAssignRuleModal('${acc.account_id}')" title="Привязать правило к кабинету">
            <span class="plus-icon" style="font-size:14px; font-weight:700; color:#7aa2f7;">+</span>
            <span class="limits-value" style="color:#7aa2f7; font-weight:600;">Добавить правило</span>
          </button>
        `;
      }

      return `
        <div class="${cardClass}" id="card-${acc.account_id}">
          <div class="card-header-row">
            <div class="card-title-area">
              <span class="card-title">${escapeHtml(acc.name)}</span>
              <div class="card-subtitle-row">
                <span class="card-id-copy mono" onclick="window.copyToClipboard('${acc.account_id}', this)" title="Скопировать ID">
                  ${acc.account_id}
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                </span>
                <span class="card-sub-dot">·</span>
                <span class="card-tz mono" title="Часовой пояс сброса статистики 00:00">
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:middle; margin-right:2px;"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                  ${acc.timezone_name}
                </span>
              </div>
            </div>
            <span class="status-badge ${statusClass}">${statusText}</span>
          </div>

          <!-- Bottom Row: Unified Interactive Rule Pill + Master Rules Toggle -->
          <div class="card-control-row">
            ${rulePillHtml}

            <label class="switch" title="Включить/выключить авто-правила">
              <input type="checkbox" ${acc.rules_enabled ? 'checked' : ''} onchange="window.toggleRules('${acc.account_id}', this.checked)">
              <span class="slider round"></span>
            </label>
          </div>
        </div>
      `;
    }).join('');
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

  // ==========================================================
  // TAB: RULES & PRESETS MANAGEMENT (PHOTO 2 & TAB LOGIC)
  // ==========================================================
  async function loadRulesTab() {
    await loadPresets();
    await loadAccounts();
    renderRulesTab();
  }

  function renderRulesTab() {
    const container = document.getElementById('rulesCardsContainer');
    const emptyEl = document.getElementById('rulesEmptyState');
    const activeCountEl = document.getElementById('rulesActiveCount');
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
    if (linkedCountEl) linkedCountEl.textContent = linkedAccountsCount;

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
          'spend': 'Спенд', 'cpl': 'CPL', 'cpr': 'CPR', 'cpa': 'CPA',
          'leads': 'Лиды', 'registrations': 'Реги', 'purchases': 'Покупки',
          'ctr': 'CTR', 'cpc': 'CPC'
        };
        const mLabel = metricLabels[c.metric] || c.metric;
        const op = (c.operator === 'gte' || c.operator === 'gt') ? '≥' : ((c.operator === 'lte' || c.operator === 'lt') ? '≤' : '=');
        const unit = (c.metric === 'leads' || c.metric === 'registrations' || c.metric === 'purchases') ? ' шт' : (c.metric === 'ctr' ? '%' : '$');
        const valStr = unit === '$' ? `$${c.value.toFixed(1)}` : `${c.value}${unit}`;
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

      let budgetInfoHtml = '';
      if (p.action === 'increase_budget' || p.action === 'decrease_budget') {
        const sign = p.action === 'increase_budget' ? '+' : '-';
        const cap = p.budget_max_daily > 0 ? ` · Макс: $${p.budget_max_daily}/день` : '';
        budgetInfoHtml = `<div style="font-size:11.5px; color:#7aa2f7; font-weight:600;">💰 Шаг: ${sign}${p.budget_change_percent || 20}%${cap}</div>`;
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
      await loadPresets();
      await loadAccounts();
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

    await loadPresets();

    document.getElementById('assignRuleAccountId').value = acc.account_id;
    document.getElementById('assignRuleModalTitle').textContent = `Правило для ${acc.name}`;

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

    const isGte = operator === 'gte' || operator === 'gt';
    const isLte = operator === 'lte' || operator === 'lt';
    const isEq = operator === 'eq';

    const row = document.createElement('div');
    row.className = 'rule-condition-row';
    row.innerHTML = `
      <select class="cond-metric form-select">
        <option value="spend" ${metric === 'spend' ? 'selected' : ''}>Спенд ($)</option>
        <option value="cpl" ${metric === 'cpl' ? 'selected' : ''}>Цена за лид ($)</option>
        <option value="cpr" ${metric === 'cpr' ? 'selected' : ''}>Цена за регу ($)</option>
        <option value="cpa" ${metric === 'cpa' ? 'selected' : ''}>CPA общий ($)</option>
        <option value="leads" ${metric === 'leads' ? 'selected' : ''}>Лиды (шт)</option>
        <option value="registrations" ${metric === 'registrations' ? 'selected' : ''}>Реги (шт)</option>
        <option value="purchases" ${metric === 'purchases' ? 'selected' : ''}>Покупки (шт)</option>
        <option value="ctr" ${metric === 'ctr' ? 'selected' : ''}>CTR (%)</option>
        <option value="cpc" ${metric === 'cpc' ? 'selected' : ''}>CPC ($)</option>
      </select>
      <select class="cond-operator form-select">
        <option value="gte" ${isGte ? 'selected' : ''}>&ge; (больше или равно)</option>
        <option value="lte" ${isLte ? 'selected' : ''}>&le; (меньше или равно)</option>
        <option value="eq" ${isEq ? 'selected' : ''}>= (равно)</option>
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
      await loadPresets();
      await loadAccounts();
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

      await loadPresets();
      await loadAccounts();
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
      textEl.textContent = `Сформировать сводку ${label}`;
    }
  }

  // ==========================================================
  // TAB 2: SUMMARY (СВОДКА И АНАЛИТИКА)
  // ==========================================================
  async function loadSummary(period = 'today', force = false) {
    state.currentPeriod = period;
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

    if (fetchBtn) {
      fetchBtn.classList.add('loading');
      fetchBtn.disabled = true;
    }
    if (statusLabel) {
      statusLabel.textContent = 'Запрос данных из Meta API...';
    }

    try {
      const data = await apiRequest(`/api/summary?period=${period}${force ? '&force=true' : ''}`);
      state.summary = data;
      state.summaryCache[period] = data;

      const now = new Date();
      const timeStr = `${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}`;
      state.summaryLastFetchedAt[period] = timeStr;

      renderSummaryData(data);
      loadStoppedAdsets();

      if (statusLabel) {
        statusLabel.textContent = `Сводка успешно сформирована в ${timeStr}`;
      }
      showToast('Сводка успешно сформирована', 'success');
    } catch (err) {
      tableBody.innerHTML = `<tr><td colspan="8" class="text-danger text-center">${err.message}</td></tr>`;
      mobileCards.innerHTML = `<div class="empty-state"><p class="text-danger">${err.message}</p></div>`;
      if (statusLabel) {
        statusLabel.textContent = `Ошибка: ${err.message}`;
      }
      showToast(`Ошибка: ${err.message}`, 'error');
    } finally {
      if (fetchBtn) {
        fetchBtn.classList.remove('loading');
        fetchBtn.disabled = false;
      }
    }
  }

  function renderSummaryData(data) {
    // KPI Cards
    document.getElementById('kpiSpend').textContent = `$${data.total_spend.toFixed(2)}`;
    document.getElementById('kpiConversions').textContent = data.total_conversions;
    document.getElementById('kpiLeads').textContent = data.total_leads;
    document.getElementById('kpiRegs').textContent = data.total_regs;
    document.getElementById('kpiCpa').textContent = `$${data.avg_cpa.toFixed(2)}`;
    document.getElementById('kpiClicks').textContent = data.total_clicks;
    document.getElementById('kpiCtr').textContent = `${data.avg_ctr.toFixed(2)}%`;
    document.getElementById('kpiCpc').textContent = `$${data.avg_cpc.toFixed(2)}`;

    const now = new Date();
    document.getElementById('lastSyncLabel').textContent = `Синхронизировано в ${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}`;

    // Desktop Table
    const tableBody = document.getElementById('summaryTableBody');
    if (!data.accounts || data.accounts.length === 0) {
      tableBody.innerHTML = `<tr><td colspan="8" class="text-center" style="color: var(--tg-hint);">Нет подключенных кабинетов</td></tr>`;
    } else {
      tableBody.innerHTML = data.accounts.map(acc => {
        const isBanned = acc.is_banned;
        const statusLabel = isBanned ? '<span class="status-dot dot-danger"></span>Блок' : '<span class="status-dot dot-success"></span>Ок';
        const spendStr = `$${acc.spend.toFixed(2)}`;
        const cpaStr = acc.total_conversions > 0 ? `$${acc.cpa.toFixed(2)}` : '—';
        const displayName = acc.short_name || acc.name;
        
        return `
          <tr>
            <td><b>${escapeHtml(displayName)}</b> <span class="mono text-hint" style="font-size:11px;">(${acc.account_id})</span></td>
            <td>${statusLabel}</td>
            <td class="text-right mono"><b>${spendStr}</b></td>
            <td class="text-right mono">${acc.clicks}</td>
            <td class="text-right mono" style="color: var(--tg-link);">${acc.leads}</td>
            <td class="text-right mono" style="color: var(--color-success);">${acc.registrations}</td>
            <td class="text-right mono">${acc.purchases}</td>
            <td class="text-right mono"><b>${cpaStr}</b></td>
          </tr>
        `;
      }).join('');
    }

    // Mobile Cards
    const mobileCards = document.getElementById('summaryMobileCards');
    if (!data.accounts || data.accounts.length === 0) {
      mobileCards.innerHTML = `<div class="empty-state"><p>Нет данных для отображения</p></div>`;
    } else {
      mobileCards.innerHTML = data.accounts.map(acc => {
        const displayName = acc.short_name || acc.name;
        const subLabel = acc.name !== displayName ? `${escapeHtml(acc.name)} · ${acc.account_id}` : acc.account_id;
        const isBanned = acc.is_banned;
        const statusPillHtml = isBanned
          ? `<span class="mob-status-pill status-disabled"><span class="status-dot dot-danger"></span>Заблокирован</span>`
          : `<span class="mob-status-pill status-active"><span class="status-dot dot-success"></span>Активен</span>`;

        return `
          <div class="mob-summary-card">
            <div class="mob-card-head">
              <div style="display:flex; flex-direction:column; overflow:hidden; padding-right:8px; flex:1;">
                <b class="mob-card-name" style="font-size:14px; font-weight:600; color:var(--tg-text); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${escapeHtml(displayName)}</b>
                <span class="mono text-hint" style="font-size:11px; color:var(--tg-hint); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${subLabel}</span>
                ${statusPillHtml}
              </div>
              <span class="mono" style="font-size: 16px; font-weight:700; color: var(--tg-link); white-space:nowrap; flex-shrink:0;">$${acc.spend.toFixed(2)}</span>
            </div>
            <div class="mob-card-stats">
              <div class="stat-box">
                <span class="stat-box-label">Клики</span>
                <span class="stat-box-val">${acc.clicks}</span>
              </div>
              <div class="stat-box">
                <span class="stat-box-label">Лиды</span>
                <span class="stat-box-val" style="color:var(--tg-link);">${acc.leads}</span>
              </div>
              <div class="stat-box">
                <span class="stat-box-label">Реги</span>
                <span class="stat-box-val" style="color:var(--color-success);">${acc.registrations}</span>
              </div>
              <div class="stat-box">
                <span class="stat-box-label">CPA</span>
                <span class="stat-box-val">${acc.total_conversions > 0 ? '$' + acc.cpa.toFixed(2) : '—'}</span>
              </div>
            </div>
          </div>
        `;
      }).join('');
    }
  }


  // Load Stopped Adsets for Reactivation
  async function loadStoppedAdsets() {
    const section = document.getElementById('stoppedAdsetsSection');
    const listEl = document.getElementById('stoppedAdsetsList');
    const countBadge = document.getElementById('stoppedCountBadge');

    try {
      const records = await apiRequest('/api/adsets/stopped');
      if (records && records.length > 0) {
        section.classList.remove('hidden');
        countBadge.textContent = `${records.length} шт`;
        listEl.innerHTML = records.map(r => `
          <div class="stopped-card-item" id="stopped-item-${r.adset_id}">
            <div>
              <b>${escapeHtml(r.adset_name)}</b> <span class="mono text-hint">(${r.account_id})</span>
              <div style="font-size: 11px; color: var(--tg-hint); margin-top:2px;">
                Спенд: <b>$${r.stop_spend.toFixed(2)}</b> · Лиды: ${r.stop_leads} · Реги: ${r.stop_registrations}
              </div>
            </div>
            <div style="display:flex; gap:6px;">
              <button class="btn btn-primary btn-sm" onclick="window.reactivateAdset('${r.adset_id}')">Включить</button>
              <button class="btn btn-secondary btn-sm" onclick="window.dismissAdset('${r.adset_id}')">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
              </button>
            </div>
          </div>
        `).join('');
      } else {
        section.classList.add('hidden');
      }

    } catch (e) {
      section.classList.add('hidden');
    }
  }

  window.reactivateAdset = async function (adsetId) {
    haptic('impact', 'medium');
    try {
      const res = await apiRequest(`/api/adsets/${adsetId}/reactivate`, { method: 'POST' });
      showToast(res.message, 'success');
      document.getElementById(`stopped-item-${adsetId}`)?.remove();
    } catch (err) {
      showToast(`Ошибка: ${err.message}`, 'error');
    }
  };

  window.dismissAdset = async function (adsetId) {
    try {
      await apiRequest(`/api/adsets/${adsetId}/dismiss`, { method: 'POST' });
      document.getElementById(`stopped-item-${adsetId}`)?.remove();
    } catch (err) {}
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

  // Period Switcher Listeners in Summary
  document.querySelectorAll('.period-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const period = btn.dataset.period;
      state.currentPeriod = period;
      haptic('selection');

      document.querySelectorAll('.period-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');

      updateFetchButtonLabel(period);
      document.getElementById('kpiPeriodLabel').textContent = periodTextMap[period] || '';

      if (state.summaryCache[period]) {
        renderSummaryData(state.summaryCache[period]);
        const statusEl = document.getElementById('summaryStatusLabel');
        if (statusEl && state.summaryLastFetchedAt[period]) {
          statusEl.textContent = `Сводка сформирована в ${state.summaryLastFetchedAt[period]}`;
        }
      } else {
        const statusEl = document.getElementById('summaryStatusLabel');
        if (statusEl) {
          statusEl.textContent = 'Нажмите кнопку для расчета статистики из Meta';
        }
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
        appEl.style.display = 'block';
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

  // Run on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initApp);
  } else {
    initApp();
  }

})();
