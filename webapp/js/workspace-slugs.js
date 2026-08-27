/** Deterministic workspace slug normalization shared by creation and editing UI. */
(function (global) {
  'use strict';

  const RESERVED_WORKSPACE_SLUGS = new Set([
    'api', 'admin', 'app', 'auth', 'static', 'uploads', 'health', 'docs', 'redoc',
    'openapi', 'openapi-json', 'settings', 'terms', 'privacy', 'data-deletion',
    'onboarding', 'login', 'sign-in', 'dashboard', 'home', 'accounts',
    'facebook-accounts', 'facebook-groups', 'groups', 'lists', 'collection',
    'rule-groups', 'add-accounts', 'rules', 'chats', 'summary', 'logs', 'invite',
    'invites', 'null', 'undefined'
  ]);

  const WORKSPACE_SLUG_CYRILLIC = Object.freeze({
    а: 'a', б: 'b', в: 'v', г: 'g', д: 'd', е: 'e', ё: 'e', ж: 'zh',
    з: 'z', и: 'i', й: 'y', к: 'k', л: 'l', м: 'm', н: 'n', о: 'o',
    п: 'p', р: 'r', с: 's', т: 't', у: 'u', ф: 'f', х: 'kh', ц: 'ts',
    ч: 'ch', ш: 'sh', щ: 'shch', ъ: '', ы: 'y', ь: '', э: 'e', ю: 'yu', я: 'ya'
  });

  function stableWorkspaceSlugHash(value) {
    let hash = 0x811c9dc5;
    new TextEncoder().encode(value).forEach(byte => {
      hash ^= byte;
      hash = Math.imul(hash, 0x01000193) >>> 0;
    });
    return hash.toString(16).padStart(8, '0');
  }

  function slugifyText(value) {
    const normalized = String(value || '').normalize('NFKC').trim().toLowerCase();
    const transliterated = Array.from(normalized)
      .map(char => Object.prototype.hasOwnProperty.call(WORKSPACE_SLUG_CYRILLIC, char)
        ? WORKSPACE_SLUG_CYRILLIC[char]
        : char)
      .join('');
    let slug = transliterated
      .normalize('NFKD')
      .replace(/[\u0300-\u036f]/g, '')
      .replace(/[^\x00-\x7F]/g, '')
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 60)
      .replace(/-+$/g, '');
    if (!slug) {
      slug = normalized ? `workspace-${stableWorkspaceSlugHash(normalized)}` : 'workspace';
    }
    if (RESERVED_WORKSPACE_SLUGS.has(slug)) {
      slug = `${slug.slice(0, 50).replace(/-+$/g, '')}-workspace`;
    }
    return slug;
  }

  global.BuyerlyWorkspaceSlugs = Object.freeze({ slugifyText });
})(window);
