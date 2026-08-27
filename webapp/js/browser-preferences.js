/** Browser preference persistence with validation and per-key recovery. */
(function (global) {
  'use strict';

  function isPlainObject(value) {
    return value !== null && typeof value === 'object' && !Array.isArray(value);
  }

  function resetBrowserPreference(key) {
    try {
      localStorage.removeItem(key);
    } catch (_) {
      // Storage can be unavailable in hardened/private browser contexts.
    }
  }

  function writeBrowserPreference(key, value, options = {}) {
    try {
      localStorage.setItem(key, options.json ? JSON.stringify(value) : String(value));
    } catch (_) {
      // UI state remains usable even when persistence is unavailable.
    }
  }

  function readBrowserPreference(key, fallback, options = {}) {
    try {
      const raw = localStorage.getItem(key);
      if (raw === null) return fallback;
      const value = options.json ? JSON.parse(raw) : raw;
      if (options.validate && !options.validate(value)) {
        throw new TypeError(`Invalid browser preference: ${key}`);
      }
      return value;
    } catch (_) {
      resetBrowserPreference(key);
      return fallback;
    }
  }

  const isStringArray = value => Array.isArray(value)
    && value.every(item => typeof item === 'string' && item.trim().length > 0)
    && new Set(value).size === value.length;

  const isIdArray = value => {
    if (!Array.isArray(value)) return false;
    const ids = value.map(Number);
    return ids.every(id => Number.isSafeInteger(id) && id > 0)
      && new Set(ids).size === ids.length;
  };

  const isWidthRecord = value => isPlainObject(value) && Object.values(value)
    .every(width => Number.isFinite(width) && width > 0 && width <= 2000);

  const isStringRecord = value => isPlainObject(value) && Object.values(value)
    .every(item => typeof item === 'string');

  global.BuyerlyBrowserPreferences = Object.freeze({
    isPlainObject,
    resetBrowserPreference,
    writeBrowserPreference,
    readBrowserPreference,
    isStringArray,
    isIdArray,
    isWidthRecord,
    isStringRecord
  });
})(window);
