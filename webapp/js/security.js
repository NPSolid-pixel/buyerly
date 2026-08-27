/** Output encoding and URL allow-list helpers for dynamic UI content. */
(function (global) {
  'use strict';

  function escapeHtml(text) {
    if (!text) return '';
    return text.toString()
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function sanitizeUrl(url) {
    if (!url || typeof url !== 'string') return '';
    const cleaned = url.replace(/[\u0000-\u001F\u007F-\u009F]/g, '').trim();
    if (!cleaned || cleaned.startsWith('//')) return '';
    if (cleaned.startsWith('/') && !cleaned.startsWith('/\\')) return cleaned;
    try {
      const parsed = new URL(cleaned, global.location ? global.location.origin : 'https://buyerly.app');
      return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? cleaned : '';
    } catch (_) {
      return '';
    }
  }

  function escapeJsArg(value) {
    if (value === undefined || value === null) return "''";
    const serialized = JSON.stringify(String(value))
      .replace(/\u2028/g, '\\u2028')
      .replace(/\u2029/g, '\\u2029');
    return escapeHtml(serialized);
  }

  global.BuyerlySecurity = Object.freeze({ escapeHtml, sanitizeUrl, escapeJsArg });
  window.sanitizeUrl = sanitizeUrl;
})(window);
