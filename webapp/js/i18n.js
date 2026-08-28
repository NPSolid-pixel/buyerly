(function () {
  'use strict';

  const DEFAULT_LOCALE = 'ru';
  const dictionaries = Object.freeze({
    ru: Object.freeze({
      'nav.today': 'Сегодня',
      'nav.efficiency': 'Эффективность',
      'nav.automations': 'Автоматизации',
      'nav.actionHistory': 'История действий',
      'nav.connections': 'Подключения',
      'nav.settings': 'Настройки',
      'entity.facebookProfile': 'Facebook-профиль',
      'entity.businessManager': 'Business Manager',
      'entity.adAccount': 'рекламный кабинет',
      'entity.accountGroup': 'группа кабинетов',
      'entity.campaign': 'кампания',
      'entity.adSet': 'группа объявлений',
      'entity.ad': 'объявление'
    })
  });

  let locale = DEFAULT_LOCALE;

  function t(key, fallback) {
    const dictionary = dictionaries[locale] || dictionaries[DEFAULT_LOCALE];
    return dictionary[key] || fallback || key;
  }

  function setLocale(nextLocale) {
    locale = Object.hasOwn(dictionaries, nextLocale) ? nextLocale : DEFAULT_LOCALE;
    document.documentElement.lang = locale;
    return locale;
  }

  window.BuyerlyI18n = Object.freeze({
    DEFAULT_LOCALE,
    dictionaries,
    getLocale: () => locale,
    setLocale,
    t
  });
})();
