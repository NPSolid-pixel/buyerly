/**
 * Buyerly Telegram Web App — Core Frontend Application Logic
 */

(function () {
  'use strict';

  // Telegram WebApp SDK
  const tg = window.Telegram?.WebApp;

  // Helper to extract Telegram initData from multiple possible sources
  function getTelegramInitData() {
    // 1. Telegram WebApp SDK initData
    if (window.Telegram?.WebApp?.initData) {
      return window.Telegram.WebApp.initData;
    }
    // 2. Hash parameters (e.g. Telegram Desktop webview)
    if (window.location.hash) {
      const hash = window.location.hash.startsWith('#') ? window.location.hash.substring(1) : window.location.hash;
      const hashParams = new URLSearchParams(hash);
      const tgWebAppData = hashParams.get('tgWebAppData');
      if (tgWebAppData) return tgWebAppData;
    }
    // 3. Query string parameters
    if (window.location.search) {
      const searchParams = new URLSearchParams(window.location.search);
      const tgWebAppData = searchParams.get('tgWebAppData') || searchParams.get('initData');
      if (tgWebAppData) return tgWebAppData;
    }
    return '';
  }

  // Application State
  const state = {
    user: null,
    accounts: [],
    summary: null,
    summaryCache: {},
    summaryLastFetchedAt: {},
    presets: [],
    activePresetId: null,
    selectedPreset: { l0: 2.0, l1: 6.0, lcpa: 6.0 },
    currentPeriod: 'today',
    activeTab: 'accounts',
    filter: 'all',
    searchQuery: '',
    parsedAccounts: [],
    settings: { poll_interval_minutes: 10 }
  };

  // API Client with initData Authentication
  async function apiRequest(endpoint, options = {}) {
    const headers = {
      'Content-Type': 'application/json',
      ...(options.headers || {})
    };

    const initData = getTelegramInitData();
    if (initData) {
      headers['Authorization'] = `tma ${initData}`;
      headers['X-Init-Data'] = initData;
    }

    try {
      const response = await fetch(endpoint, {
        ...options,
        headers
      });


      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `Ошибка сервера (${response.status})`);
      }

      return await response.json();
    } catch (err) {
      console.error(`API Error on ${endpoint}:`, err);
      throw err;
    }
  }

  // Haptic Feedback Helper
  function haptic(type = 'impact', style = 'medium') {
    try {
      if (tg?.HapticFeedback) {
        if (type === 'impact') {
          tg.HapticFeedback.impactOccurred(style);
        } else if (type === 'notification') {
          tg.HapticFeedback.notificationOccurred(style);
        } else if (type === 'selection') {
          tg.HapticFeedback.selectionChanged();
        }
      }
    } catch (e) {}
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

      // Format rule summary pill
      let ruleBadgeText = 'Настроить правило';
      const hasConditions = acc.rule_conditions && acc.rule_conditions.length > 0;
      
      if (acc.preset_name && hasConditions) {
        ruleBadgeText = acc.preset_name;
      } else if (hasConditions) {
        const first = acc.rule_conditions[0];
        const metricLabels = {
          'spend': 'Спенд',
          'cpl': 'CPL',
          'cpr': 'CPR',
          'cpa': 'CPA',
          'leads': 'Лиды',
          'registrations': 'Реги',
          'purchases': 'Покупки',
          'ctr': 'CTR',
          'cpc': 'CPC'
        };
        const mLabel = metricLabels[first.metric] || first.metric;
        const op = (first.operator === 'gte' || first.operator === 'gt') ? '≥' : ((first.operator === 'lte' || first.operator === 'lt') ? '≤' : '=');
        const unit = (first.metric === 'leads' || first.metric === 'registrations' || first.metric === 'purchases') ? ' шт' : (first.metric === 'ctr' ? '%' : '$');
        const valStr = unit === '$' ? `$${first.value.toFixed(1)}` : `${first.value}${unit}`;
        ruleBadgeText = `${mLabel} ${op} ${valStr}`;
      } else {
        ruleBadgeText = 'Настроить правило';
      }

      const actionIcons = {
        'turn_off': '🛑',
        'notify_only': '🔔',
        'turn_on': '🟢',
        'increase_budget': '📈',
        'decrease_budget': '📉'
      };
      const actionIcon = actionIcons[acc.rule_action] || '⚙️';

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
            <div class="card-limits-btn ${acc.rules_enabled ? 'active' : ''}" onclick="window.openEditLimitsModal('${acc.account_id}')" title="Нажмите, чтобы настроить правило">
              <span style="font-size:13px; line-height:1;">${actionIcon}</span>
              <span class="limits-label">Правило:</span>
              <span class="limits-value">${escapeHtml(ruleBadgeText)}</span>
              <svg class="limits-edit-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
            </div>

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
  // RULE BUILDER & PRESETS LOGIC
  // ==========================================================
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

    const accId = document.getElementById('editLimitsAccountId')?.value;
    const currentAcc = state.accounts.find(a => a.account_id === accId);
    if (currentAcc && (currentAcc.preset_id || (currentAcc.rule_conditions && currentAcc.rule_conditions.length > 0))) {
      document.getElementById('btnDetachRule')?.classList.remove('hidden');
    } else {
      document.getElementById('btnDetachRule')?.classList.add('hidden');
    }

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

    const accId = document.getElementById('editLimitsAccountId')?.value;
    const currentAcc = state.accounts.find(a => a.account_id === accId);
    if (currentAcc && (currentAcc.preset_id || (currentAcc.rule_conditions && currentAcc.rule_conditions.length > 0))) {
      document.getElementById('btnDetachRule')?.classList.remove('hidden');
    } else {
      document.getElementById('btnDetachRule')?.classList.add('hidden');
    }

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

    setCooldownUI(acc.rule_cooldown_minutes || 0);
    setIntervalUI(acc.rule_check_interval || 5);
    const tgToggle = document.getElementById('ruleNotifyTgToggle');
    if (tgToggle) tgToggle.checked = acc.rule_notify_tg !== false;

    if (acc.preset_id && state.presets.some(p => p.id === acc.preset_id)) {
      window.selectPreset(acc.preset_id);
    } else if (acc.rule_conditions && acc.rule_conditions.length > 0) {
      state.activePresetId = null;
      document.getElementById('editingPresetId').value = '';
      document.getElementById('ruleNameInput').value = acc.preset_name || 'Кастомное правило';
      const action = acc.rule_action || 'turn_off';
      document.getElementById('ruleActionSelect').value = action;
      handleActionChange(action);

      document.getElementById('budgetChangePercentInput').value = acc.rule_budget_change_percent || 20;
      document.getElementById('budgetMaxDailyInput').value = acc.rule_budget_max_daily || 0;
      setLogicUI(acc.rule_condition_logic || 'and');

      document.getElementById('builderModeTag').textContent = 'Кастомное правило кабинета';
      document.getElementById('btnDeletePreset')?.classList.add('hidden');
      document.getElementById('btnDetachRule')?.classList.remove('hidden');
      renderConditions(acc.rule_conditions);
      renderPresetsList(null);
    } else if (state.presets.length > 0) {
      window.selectPreset(state.presets[0].id);
    } else {
      window.newPresetMode();
    }

    const hasActiveRule = (acc.preset_id || (acc.rule_conditions && acc.rule_conditions.length > 0));
    if (!hasActiveRule) {
      document.getElementById('btnDetachRule')?.classList.add('hidden');
    }

    window.openModal('modalEditLimits');
  };

  window.detachRuleFromAccount = async function () {
    const accountId = document.getElementById('editLimitsAccountId').value;
    if (!accountId) return;

    haptic('impact', 'medium');
    try {
      await apiRequest(`/api/accounts/${accountId}/detach-rule`, { method: 'POST' });
      showToast('Правило сброшено с кабинета', 'success');
      window.closeModal('modalEditLimits');
      await loadAccounts();
    } catch (e) {
      showToast(`Ошибка сброса: ${e.message}`, 'error');
    }
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
        preset_id: editingPresetId ? parseInt(editingPresetId) : null,
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

      const res = await apiRequest(`/api/accounts/${accountId}/apply-preset`, {
        method: 'POST',
        body: JSON.stringify(payload)
      });

      haptic('notification', 'success');
      showToast(res.message || 'Правило успешно сохранено и применено!', 'success');
      window.closeModal('modalEditLimits');

      await loadPresets();
      await loadAccounts();
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

  // Preset Selection in Add Form
  document.querySelectorAll('.btn-preset').forEach(btn => {
    btn.addEventListener('click', () => {
      haptic('selection');
      document.querySelectorAll('.btn-preset').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      state.selectedPreset = {
        l0: parseFloat(btn.dataset.l0),
        l1: parseFloat(btn.dataset.l1),
        lcpa: parseFloat(btn.dataset.lcpa)
      };
    });
  });

  // Submit Batch Add
  btnSubmit?.addEventListener('click', async () => {
    const token = document.getElementById('accessTokenInput').value.trim();
    const batchName = document.getElementById('batchNameInput').value.trim() || '-';
    const enableRules = document.getElementById('addEnableRulesSwitch').checked;

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

    try {
      const res = await apiRequest('/api/accounts/batch-add', {
        method: 'POST',
        body: JSON.stringify({
          accounts: state.parsedAccounts,
          batch_name: batchName,
          access_token: token,
          rules_enabled: enableRules,
          max_spend_0_leads: state.selectedPreset.l0,
          max_spend_1_lead: state.selectedPreset.l1,
          max_cpa_multiple_leads: state.selectedPreset.lcpa
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

  window.closeBatchProgress = function () {
    window.closeModal('modalBatchProgress');
    window.switchTab('accounts');
  };

  // ==========================================================
  // TAB 4: SETTINGS (НАСТРОЙКИ)
  // ==========================================================
  async function loadSettings() {
    try {
      const data = await apiRequest('/api/settings');
      state.settings = data;

      document.querySelectorAll('.btn-interval').forEach(btn => {
        btn.classList.toggle('active', parseInt(btn.dataset.interval) === data.poll_interval_minutes);
      });

      if (state.user) {
        document.getElementById('settingsUserDesc').textContent = `@${state.user.username || 'user'} (ID: ${state.user.telegram_id})`;
        document.getElementById('settingsUserRole').textContent = state.user.role;
      }
    } catch (err) {}
  }

  document.querySelectorAll('.btn-interval').forEach(btn => {
    btn.addEventListener('click', async () => {
      const interval = parseInt(btn.dataset.interval);
      haptic('impact', 'medium');

      try {
        await apiRequest('/api/settings/interval', {
          method: 'POST',
          body: JSON.stringify({ minutes: interval })
        });
        showToast(`Интервал опроса изменен на ${interval} мин!`, 'success');
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
    if (tg?.BackButton) {
      tg.BackButton.show();
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
      if (tg?.BackButton) {
        tg.BackButton.hide();
      }
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
    // Configure Telegram WebApp
    if (window.Telegram?.WebApp) {
      const tgApp = window.Telegram.WebApp;
      tgApp.ready();
      tgApp.expand();
      try {
        if (typeof tgApp.setHeaderColor === 'function') tgApp.setHeaderColor('#1a1b26');
        if (typeof tgApp.setBackgroundColor === 'function') tgApp.setBackgroundColor('#1a1b26');
        if (typeof tgApp.disableVerticalSwipes === 'function') tgApp.disableVerticalSwipes();
        if (typeof tgApp.enableClosingConfirmation === 'function') tgApp.enableClosingConfirmation();
      } catch (e) {}

      // Register BackButton handler
      if (tgApp.BackButton) {
        tgApp.BackButton.onClick(() => {
          if (activeModalStack.length > 0) {
            const topModal = activeModalStack[activeModalStack.length - 1];
            window.closeModal(topModal);
          }
        });
      }
    }

    // Give Telegram SDK a short moment to parse hash/params if needed
    let initData = getTelegramInitData();
    if (!initData && window.Telegram) {
      await new Promise(resolve => setTimeout(resolve, 100));
      initData = getTelegramInitData();
    }

    setupSettingsChips();
    setupLogicToggle();

    // Authenticate and load initial profile
    try {
      const user = await apiRequest('/api/me');
      state.user = user;
      // Hide unauthorized screen & reveal app UI
      const unauthEl = document.getElementById('unauthorizedScreen');
      const appEl = document.getElementById('app');
      if (unauthEl) {
        unauthEl.style.display = 'none';
        unauthEl.classList.add('hidden');
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

      // Load initial tab (Accounts)
      window.switchTab('accounts');
    } catch (e) {
      console.warn("Unauthorized / access locked:", e);
      const unauthEl = document.getElementById('unauthorizedScreen');
      const appEl = document.getElementById('app');
      if (appEl) {
        appEl.style.display = 'none';
        appEl.classList.add('hidden');
      }
      if (unauthEl) {
        unauthEl.style.display = 'flex';
        unauthEl.classList.remove('hidden');
        if (e.message && e.message.includes('одобрения')) {
          unauthEl.innerHTML = '<span>Ваш аккаунт ожидает одобрения администратора (@buyerly_bot)</span>';
        } else {
          unauthEl.innerHTML = '<span>Требуется авторизация через Telegram Web App (@buyerly_bot)</span>';
        }
      }
    }
  }







  // Run on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initApp);
  } else {
    initApp();
  }

})();
