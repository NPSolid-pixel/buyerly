import re

file_path = "webapp/js/app.js"
with open(file_path, "r") as f:
    content = f.read()

# 1. Update rules_enabled count check
content = content.replace(
    "if (a.rules_enabled && (a.preset_id || (a.rule_conditions && a.rule_conditions.length > 0))) {",
    "if (a.rules_enabled && a.active_rules && a.active_rules.length > 0) {"
)

# 2. Update renderAccountsTab Rule Pill rendering
old_pill = """      const hasConditions = acc.rule_conditions && acc.rule_conditions.length > 0;
      let rulePillHtml = '';

      if (hasConditions) {
        let ruleBadgeText = acc.preset_name || '';
        if (!ruleBadgeText && acc.rule_conditions.length > 0) {
          const first = acc.rule_conditions[0];
          const metricLabels = {
            'spend': 'Спенд', 'cpl': 'CPL', 'cpr': 'CPR', 'cpa': 'CPA',
            'leads': 'Лиды', 'registrations': 'Реги', 'purchases': 'Покупки',
            'ctr': 'CTR', 'cpc': 'CPC'
          };
          const mLabel = metricLabels[first.metric] || first.metric;
          const op = (first.operator === 'gte' || first.operator === 'gt') ? '≥' : ((first.operator === 'lte' || first.operator === 'lt') ? '≤' : '=');
          const unit = (first.metric === 'leads' || first.metric === 'registrations' || first.metric === 'purchases') ? ' шт' : (first.metric === 'ctr' ? '%' : '$');
          const valStr = unit === '$' ? `$${first.value.toFixed(1)}` : `${first.value}${unit}`;
          ruleBadgeText = `${mLabel} ${op} ${valStr}`;
        }

        const actionIcons = {
          'turn_off': '🔴', 'notify_only': '🔔', 'turn_on': '🟢',
          'increase_budget': '📈', 'decrease_budget': '📉'
        };
        const actionIcon = actionIcons[acc.rule_action] || '⚙️';

        rulePillHtml = `
          <div class="card-limits-btn ${acc.rules_enabled ? 'active' : ''}" onclick="window.openAssignRuleModal('${acc.account_id}')" title="Нажмите, чтобы изменить правило">
            <span style="font-size:13px; line-height:1;">${actionIcon}</span>
            <span class="limits-label">Правило:</span>
            <span class="limits-value">${escapeHtml(ruleBadgeText)}</span>
            <svg style="margin-left:4px; opacity:0.6;" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
          </div>
        `;
      }"""

new_pill = """      const hasRules = acc.active_rules && acc.active_rules.length > 0;
      let rulePillHtml = '';

      if (hasRules) {
        rulePillHtml = '<div style="display:flex; flex-wrap:wrap; gap:6px; margin-top:12px;">';
        acc.active_rules.forEach(rule => {
            let ruleBadgeText = rule.name || '';
            const actionIcons = {
              'turn_off': '🔴', 'notify_only': '🔔', 'turn_on': '🟢',
              'increase_budget': '📈', 'decrease_budget': '📉'
            };
            const actionIcon = actionIcons[rule.action] || '⚙️';
            
            rulePillHtml += `
              <div class="card-limits-btn ${acc.rules_enabled ? 'active' : ''}" style="margin-top:0;" onclick="window.detachRule('${acc.account_id}', ${rule.preset_id}, '${escapeHtml(rule.name)}')" title="Нажмите, чтобы отвязать правило">
                <span style="font-size:13px; line-height:1;">${actionIcon}</span>
                <span class="limits-label">Правило:</span>
                <span class="limits-value">${escapeHtml(ruleBadgeText)}</span>
                <svg style="margin-left:4px; opacity:0.6;" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
              </div>
            `;
        });
        
        // Add "Add Rule" button
        rulePillHtml += `
            <div class="card-limits-btn" style="margin-top:0; border: 1px dashed var(--border);" onclick="window.openAssignRuleModal('${acc.account_id}')" title="Добавить правило">
                <span style="font-size:13px; line-height:1;">➕</span>
                <span class="limits-label">Добавить правило</span>
            </div>
        `;
        rulePillHtml += '</div>';
      } else {
        rulePillHtml = `
          <div class="card-limits-btn" onclick="window.openAssignRuleModal('${acc.account_id}')" title="Нажмите, чтобы добавить правило">
            <span style="font-size:13px; line-height:1;">➕</span>
            <span class="limits-label">Нет правил.</span>
            <span class="limits-value">Добавить</span>
          </div>
        `;
      }"""

content = content.replace(old_pill, new_pill)

# 3. Update hasActiveRule check inside openAssignRuleModal (around 496)
content = content.replace(
    "const hasActiveRule = (acc.preset_id || (acc.rule_conditions && acc.rule_conditions.length > 0));",
    "const hasActiveRule = (acc.active_rules && acc.active_rules.length > 0);"
)

# 4. Remove btnDetachRule from modalAssignRule because we now detach by clicking the rule directly on the card
# The detachment logic from modal is removed.
content = content.replace(
    """    if (detachBtn) {
      detachBtn.classList.toggle('hidden', !hasActiveRule);
    }""",
    ""
)

# 5. Update renderPresetsList (around 517)
content = content.replace(
    "const isCurrent = acc.preset_id === p.id;",
    "const isCurrent = acc.active_rules && acc.active_rules.some(r => r.preset_id === p.id);"
)

# 6. Update apiRequest in window.pickRuleForAccount (around 541)
content = content.replace(
    "const res = await apiRequest(`/api/accounts/${currentAssignAccountId}/apply-preset`, {",
    "const res = await apiRequest(`/api/accounts/${currentAssignAccountId}/assign-rule`, {"
)

# 7. Add detachRule global function
detach_func = """
  window.detachRule = async function(accountId, presetId, ruleName) {
    if (!confirm(`Вы действительно хотите отвязать правило "${ruleName}" от этого кабинета?`)) return;
    try {
      const res = await apiRequest(`/api/accounts/${accountId}/detach-rule/${presetId}`, {
        method: 'POST'
      });
      showToast('Правило отвязано', 'success');
      await loadAccounts();
    } catch (e) {
      showToast(e.message, 'error');
    }
  };
"""
content = content.replace("window.openAssignRuleModal = function(accountId) {", detach_func + "\n  window.openAssignRuleModal = function(accountId) {")

# 8. Remove the old window.detachRuleFromModal (around 561)
old_detach = """  window.detachRuleFromModal = async function() {
    if (!currentAssignAccountId) return;
    if (!confirm('Вы действительно хотите отвязать правило от этого кабинета?')) return;
    try {
      const res = await apiRequest(`/api/accounts/${currentAssignAccountId}/detach-rule`, {
        method: 'POST'
      });
      showToast('Правило отвязано', 'success');
      window.closeModal('modalAssignRule');
      await loadAccounts();
    } catch (e) {
      showToast(e.message, 'error');
    }
  };"""
content = content.replace(old_detach, "")

# 9. Clean up currentAcc references around 780 and 812 (btnDetachRule which we deleted from HTML)
content = re.sub(r"if \(currentAcc && \(currentAcc\.preset_id \|\| \(currentAcc\.rule_conditions && currentAcc\.rule_conditions\.length > 0\)\)\) \{\s*document\.getElementById\('btnDetachRule'\)\?\.classList\.remove\('hidden'\);\s*\} else \{\s*document\.getElementById\('btnDetachRule'\)\?\.classList\.add\('hidden'\);\s*\}", "", content)

content = content.replace("const hasActiveRule = (acc.preset_id || (acc.rule_conditions && acc.rule_conditions.length > 0));\n    if (!hasActiveRule) {\n      document.getElementById('btnDetachRule')?.classList.add('hidden');\n    }", "")

with open(file_path, "w") as f:
    f.write(content)

print("Patch applied.")
