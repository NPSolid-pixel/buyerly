/**
 * Buyerly Telegram Web App — Core Frontend Application Logic
 */

(function () {
  'use strict';

  // Telegram WebApp SDK
  const tg = window.Telegram?.WebApp;

  // Application State
  const state = {
    user: null,
    accounts: [],
    summary: null,
    currentPeriod: 'today',
    activeTab: 'accounts',
    filter: 'all',
    searchQuery: '',
    parsedAccounts: [],
    selectedPreset: { l0: 2.0, l1: 6.0, lcpa: 6.0 },
    settings: { poll_interval_minutes: 10 }
  };

  // API Client with initData Authentication
  async function apiRequest(endpoint, options = {}) {
    const headers = {
      'Content-Type': 'application/json',
      ...(options.headers || {})
    };

    if (tg?.initData) {
      headers['Authorization'] = `tma ${tg.initData}`;
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
    
    let icon = 'ℹ️';
    if (type === 'success') icon = '✅';
    if (type === 'error') icon = '❌';

    toast.innerHTML = `<span>${icon}</span> <span>${message}</span>`;
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
      loadSummary(state.currentPeriod);
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
      listEl.innerHTML = `<div class="empty-state"><p class="text-danger">❌ ${err.message}</p></div>`;
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

    // Update summary counts
    const totalCount = state.accounts.length;
    const activeCount = state.accounts.filter(a => a.account_status === 1 && a.is_active).length;
    const rulesCount = state.accounts.filter(a => a.rules_enabled).length;
    const issueCount = state.accounts.filter(a => a.account_status !== 1 || !a.is_active).length;

    document.getElementById('countAll').textContent = totalCount;
    document.getElementById('countActive').textContent = activeCount;
    document.getElementById('countRules').textContent = rulesCount;
    document.getElementById('countIssue').textContent = issueCount;

    document.getElementById('stripTotal').textContent = totalCount;
    document.getElementById('stripRulesActive').textContent = rulesCount;
    document.getElementById('stripMetaActive').textContent = activeCount;

    if (filtered.length === 0) {
      listEl.innerHTML = '';
      emptyEl.classList.remove('hidden');
      return;
    }

    emptyEl.classList.add('hidden');
    listEl.innerHTML = filtered.map(acc => {
      let statusClass = 'status-active';
      let statusText = '🟢 Активен';
      
      if (acc.account_status === 2 || !acc.is_active) {
        statusClass = 'status-disabled';
        statusText = '🔴 Заблокирован';
      } else if (acc.account_status === 3) {
        statusClass = 'status-unsettled';
        statusText = '💳 Hold на карте';
      }

      const cardClass = [
        'account-card',
        acc.rules_enabled ? 'rules-active' : '',
        acc.account_status !== 1 ? 'account-disabled' : ''
      ].filter(Boolean).join(' ');

      return `
        <div class="${cardClass}" id="card-${acc.account_id}">
          <div class="card-header-row">
            <div class="card-title-area">
              <span class="card-title">${escapeHtml(acc.name)}</span>
              <span class="card-id-copy mono" onclick="window.copyToClipboard('${acc.account_id}', this)">
                ${acc.account_id}
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
              </span>
            </div>
            <span class="status-badge ${statusClass}">${statusText}</span>
          </div>

          <!-- Master Auto-Rules Switch -->
          <div class="card-rules-section">
            <div class="rules-label-wrap">
              <span class="rules-main-label">🛡 Авто-правила стопов</span>
              <span class="rules-sub-label">${acc.rules_enabled ? '🟢 Включены (контроль $2/$6/$6)' : '⚪ Выключены (только статистика)'}</span>
            </div>
            <label class="switch">
              <input type="checkbox" ${acc.rules_enabled ? 'checked' : ''} onchange="window.toggleRules('${acc.account_id}', this.checked)">
              <span class="slider round"></span>
            </label>
          </div>

          <!-- Stepped Limits Pill -->
          <div class="card-limits-row">
            <span style="color: var(--tg-hint);">Лимиты правил:</span>
            <div class="limits-pill mono">
              <span>$${acc.max_spend_0_leads.toFixed(1)} / $${acc.max_spend_1_lead.toFixed(1)} / $${acc.max_cpa_multiple_leads.toFixed(1)}</span>
            </div>
          </div>

          <!-- Actions Footer -->
          <div class="card-actions-row">
            <div class="card-tz-info">
              🕒 <code>${acc.timezone_name}</code>
            </div>
            <div class="card-btns">
              <button class="btn btn-secondary btn-sm" onclick="window.openEditLimitsModal('${acc.account_id}')">
                ⚙️ Лимиты
              </button>
              <button class="btn btn-secondary btn-sm" style="color: var(--color-danger);" onclick="window.openDeleteConfirmModal('${acc.account_id}', '${escapeHtml(acc.name)}')">
                🗑
              </button>
            </div>
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
  // LIMITS EDIT MODAL
  // ==========================================================
  window.openEditLimitsModal = function (accountId) {
    haptic('impact', 'medium');
    const acc = state.accounts.find(a => a.account_id === accountId);
    if (!acc) return;

    document.getElementById('editLimitsAccountId').value = acc.account_id;
    document.getElementById('modalLimitsTitle').textContent = `Лимиты для ${acc.name}`;
    document.getElementById('lim0Input').value = acc.max_spend_0_leads;
    document.getElementById('lim1Input').value = acc.max_spend_1_lead;
    document.getElementById('limCpaInput').value = acc.max_cpa_multiple_leads;
    document.getElementById('convEventSelect').value = acc.conversion_event || 'all';
    document.getElementById('autoReactivateSwitch').checked = !!acc.auto_reactivate;

    window.openModal('modalEditLimits');
  };

  window.fillLimitsPreset = function (l0, l1, lcpa) {
    haptic('selection');
    document.getElementById('lim0Input').value = l0;
    document.getElementById('lim1Input').value = l1;
    document.getElementById('limCpaInput').value = lcpa;
  };

  document.getElementById('btnSaveLimits')?.addEventListener('click', async () => {
    const accountId = document.getElementById('editLimitsAccountId').value;
    const l0 = parseFloat(document.getElementById('lim0Input').value);
    const l1 = parseFloat(document.getElementById('lim1Input').value);
    const lcpa = parseFloat(document.getElementById('limCpaInput').value);
    const convEvent = document.getElementById('convEventSelect').value;
    const autoReactivate = document.getElementById('autoReactivateSwitch').checked;

    if (isNaN(l0) || isNaN(l1) || isNaN(lcpa)) {
      showToast('Заполните все поля лимитов числами', 'error');
      return;
    }

    try {
      const res = await apiRequest(`/api/accounts/${accountId}/limits`, {
        method: 'POST',
        body: JSON.stringify({
          max_spend_0_leads: l0,
          max_spend_1_lead: l1,
          max_cpa_multiple_leads: lcpa,
          conversion_event: convEvent,
          auto_reactivate: autoReactivate
        })
      });

      haptic('notification', 'success');
      showToast('Лимиты успешно сохранены!', 'success');
      window.closeModal('modalEditLimits');

      const acc = state.accounts.find(a => a.account_id === accountId);
      if (acc) {
        acc.max_spend_0_leads = l0;
        acc.max_spend_1_lead = l1;
        acc.max_cpa_multiple_leads = lcpa;
        acc.conversion_event = convEvent;
        acc.auto_reactivate = autoReactivate;
        renderAccounts();
      }
    } catch (err) {
      showToast(`Ошибка сохранения: ${err.message}`, 'error');
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

  // ==========================================================
  // TAB 2: SUMMARY (СВОДКА И АНАЛИТИКА)
  // ==========================================================
  async function loadSummary(period = 'today') {
    state.currentPeriod = period;
    const tableBody = document.getElementById('summaryTableBody');
    const mobileCards = document.getElementById('summaryMobileCards');
    
    // Update period switchers
    document.querySelectorAll('.period-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.period === period);
    });

    const periodLabels = {
      'today': 'за сегодня',
      'yesterday': 'за вчера',
      'last_3d': 'за 3 дня',
      'last_7d': 'за 7 дней'
    };
    document.getElementById('kpiPeriodLabel').textContent = periodLabels[period] || '';

    try {
      const data = await apiRequest(`/api/summary?period=${period}`);
      state.summary = data;
      renderSummaryData(data);
      loadStoppedAdsets();
    } catch (err) {
      tableBody.innerHTML = `<tr><td colspan="8" class="text-danger text-center">❌ ${err.message}</td></tr>`;
      mobileCards.innerHTML = `<div class="empty-state"><p class="text-danger">❌ ${err.message}</p></div>`;
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
        const statusLabel = isBanned ? '🔴 Блок' : '🟢 Ок';
        const spendStr = `$${acc.spend.toFixed(2)}`;
        const cpaStr = acc.total_conversions > 0 ? `$${acc.cpa.toFixed(2)}` : '—';
        
        return `
          <tr>
            <td><b>${escapeHtml(acc.name)}</b> <span class="mono text-hint" style="font-size:11px;">(${acc.account_id})</span></td>
            <td>${statusLabel}</td>
            <td class="text-right mono"><b>${spendStr}</b></td>
            <td class="text-right mono">${acc.clicks}</td>
            <td class="text-right mono" style="color: #38bdf8;">${acc.leads}</td>
            <td class="text-right mono" style="color: #22c55e;">${acc.registrations}</td>
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
        return `
          <div class="mob-summary-card">
            <div class="mob-card-head">
              <b>${escapeHtml(acc.name)}</b>
              <span class="mono" style="font-size: 15px; font-weight:700; color: #38bdf8;">$${acc.spend.toFixed(2)}</span>
            </div>
            <div class="mob-card-stats">
              <div class="stat-box">
                <span class="stat-box-label">Клики</span>
                <span class="stat-box-val">${acc.clicks}</span>
              </div>
              <div class="stat-box">
                <span class="stat-box-label">Лиды</span>
                <span class="stat-box-val" style="color:#38bdf8;">${acc.leads}</span>
              </div>
              <div class="stat-box">
                <span class="stat-box-label">Реги</span>
                <span class="stat-box-val" style="color:#22c55e;">${acc.registrations}</span>
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
              <button class="btn btn-secondary btn-sm" onclick="window.dismissAdset('${r.adset_id}')">✕</button>
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

  rawInput?.addEventListener('input', () => {
    const parsed = parseAccountsLocally(rawInput.value);
    state.parsedAccounts = parsed;

    parsedBadge.textContent = `Найдено: ${parsed.length}`;

    if (parsed.length > 0) {
      previewBox.classList.remove('hidden');
      chipsList.innerHTML = parsed.map(p => {
        const namePart = p.name ? ` (${escapeHtml(p.name)})` : '';
        return `<span class="parsed-item-chip"><code>${p.account_id}</code>${namePart}</span>`;
      }).join('');
      btnSubmit.disabled = false;
      btnSubmit.querySelector('span').textContent = `Подключить ${parsed.length} кабинетов`;
    } else {
      previewBox.classList.add('hidden');
      btnSubmit.disabled = true;
      btnSubmit.querySelector('span').textContent = 'Подключить кабинеты';
    }
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
              <span>🟢 <b>${escapeHtml(item.name)}</b> (${item.account_id})</span>
              <span class="badge badge-success">OK</span>
            </div>
          `);
        });
      }
      if (res.errors) {
        res.errors.forEach(item => {
          resultsHtml.push(`
            <div class="batch-res-item">
              <span>🔴 <b>${item.account_id}</b>: ${escapeHtml(item.error)}</span>
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
      document.getElementById('batchProgressText').textContent = `❌ Ошибка: ${err.message}`;
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
  window.openModal = function (modalId) {
    document.getElementById(modalId)?.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
  };

  window.closeModal = function (modalId) {
    document.getElementById(modalId)?.classList.add('hidden');
    document.body.style.overflow = '';
  };

  document.getElementById('btnOpenTokenGuide')?.addEventListener('click', () => {
    window.openModal('modalTokenGuide');
  });

  // Search & Filter event listeners
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
      haptic('selection');
      loadSummary(btn.dataset.period);
    });
  });

  // Sync Button
  document.getElementById('btnSync')?.addEventListener('click', async () => {
    const btn = document.getElementById('btnSync');
    btn.classList.add('syncing');
    haptic('impact', 'medium');

    try {
      if (state.activeTab === 'accounts') {
        await loadAccounts();
      } else if (state.activeTab === 'summary') {
        await loadSummary(state.currentPeriod);
      }
      showToast('Данные успешно обновлены', 'success');
    } catch (e) {
      showToast('Ошибка синхронизации', 'error');
    } finally {
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
    if (tg) {
      tg.ready();
      tg.expand();
      try {
        tg.enableClosingConfirmation();
      } catch (e) {}
    }

    // Authenticate and load initial profile
    try {
      const user = await apiRequest('/api/me');
      state.user = user;
      document.getElementById('userName').textContent = user.full_name || user.username || 'Медиабайер';
      document.getElementById('userAvatar').textContent = (user.full_name || user.username || 'B').charAt(0).toUpperCase();
    } catch (e) {
      console.warn("Could not load user profile:", e);
    }

    // Load initial tab (Accounts)
    window.switchTab('accounts');
  }

  // Run on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initApp);
  } else {
    initApp();
  }

})();
