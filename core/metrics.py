"""Canonical Buyerly metric definitions and calculations.

The API, rule engine, audit trail and user interfaces must use this module
instead of inventing local meanings for the same metric.
"""

from dataclasses import dataclass
import math
from typing import Any, Iterable, Mapping, Optional


LEGACY_METRIC_ALIASES = {
    "cpr": "cpreg",
    "cpa": "legacy_cpa",
}

PUBLIC_RULE_METRICS = frozenset(
    {
        "spend",
        "cpl",
        "cpreg",
        "cpp",
        "leads",
        "registrations",
        "purchases",
        "ctr",
        "cpc",
    }
)

RULE_OPERATORS = frozenset({"gt", "gte", "lt", "lte", "eq"})
RULE_ACTIONS = frozenset(
    {"turn_off", "notify_only", "turn_on", "increase_budget", "decrease_budget"}
)
RULE_LOGICS = frozenset({"and", "or"})
RULE_TIME_WINDOWS = frozenset({"today", "yesterday", "last_3d", "last_7d"})
RULE_MAX_CONDITIONS = 20
RULE_MAX_VALUE = 1_000_000_000.0
RULE_MAX_BUDGET = 10_000_000.0
RULE_MAX_COOLDOWN_MINUTES = 10_080
RULE_MAX_CHECK_INTERVAL_MINUTES = 1_440
RULE_MAX_BUDGET_CHANGE_PERCENT = 100.0

METRIC_LABELS = {
    "spend": "Спенд",
    "cpl": "Цена за лид (CPL)",
    "cpreg": "Цена регистрации (CPReg)",
    "cpp": "Цена покупки (CPP)",
    "leads": "Лиды",
    "registrations": "Регистрации",
    "purchases": "Покупки",
    "ctr": "CTR",
    "cpc": "CPC",
    "legacy_cpa": "Старый общий CPA",
}

METRIC_UNITS = {
    "spend": "$",
    "cpl": "$",
    "cpreg": "$",
    "cpp": "$",
    "leads": "",
    "registrations": "",
    "purchases": "",
    "ctr": "%",
    "cpc": "$",
    "legacy_cpa": "$",
}

SUMMARY_METRIC_DEFINITIONS = {
    "spend": "Account-level Spend из Meta за выбранный период. Включает расходы всех объявлений и ad set, которые откручивались в периоде, независимо от их текущего статуса.",
    "impressions": "Показы: сколько раз объявления были показаны на экране. Один человек может создать несколько показов.",
    "reach": "Охват: сумма уникального охвата внутри каждого кабинета. При нескольких кабинетах аудитории могут пересекаться, поэтому это не глобально уникальные люди.",
    "frequency": "Показы / суммарный охват кабинетов. Показывает среднее число показов на одного охваченного пользователя с учётом возможного пересечения между кабинетами.",
    "cpm": "Spend / показы × 1 000. Стоимость тысячи показов.",
    "leads": "События lead из Meta. Не складываются с регистрациями или покупками.",
    "cost_per_lead": "Spend / лиды. Если лидов нет, значение отсутствует.",
    "registrations": "События complete_registration из Meta. Считаются отдельно от лидов.",
    "cost_per_registration": "Spend / регистрации. Если регистраций нет, значение отсутствует.",
    "purchases": "События purchase из Meta. Считаются отдельно от лидов и регистраций.",
    "cost_per_purchase": "Spend / покупки. Если покупок нет, значение отсутствует.",
    "clicks": "Все клики из поля Meta clicks: ссылки, реакции и другие кликабельные элементы объявления.",
    "unique_clicks": "Сумма людей с хотя бы одним кликом внутри каждого кабинета. Повторные клики дедуплицируются в кабинете, но один человек в разных кабинетах может учитываться повторно.",
    "link_clicks": "Inline Link Clicks: клики по ссылкам и отдельным направлениям внутри объявления. Не равны всем кликам и не гарантируют загрузку сайта.",
    "outbound_clicks": "Клики, которые ведут за пределы приложений и сервисов Meta.",
    "landing_page_views": "Landing Page Views: случаи, когда после клика целевая страница действительно загрузилась и событие было доступно Meta.",
    "ctr": "CTR All = все клики / показы × 100.",
    "link_ctr": "Link CTR = Inline Link Clicks / показы × 100.",
    "outbound_ctr": "Outbound CTR = исходящие клики / показы × 100.",
    "cpc": "CPC All = Spend / все клики.",
    "cpc_link": "CPC Link = Spend / Inline Link Clicks.",
    "cost_per_landing_page_view": "Spend / Landing Page Views. Если загрузок страницы нет, значение отсутствует.",
}


@dataclass(frozen=True)
class MetricReading:
    key: str
    label: str
    unit: str
    value: Optional[float]
    unavailable_reason: str = ""

    @property
    def available(self) -> bool:
        return self.value is not None


def canonical_rule_metric(metric: Any) -> str:
    key = str(metric or "").strip().lower()
    return LEGACY_METRIC_ALIASES.get(key, key)


def cost_per_event(spend: Any, event_count: Any, *, digits: Optional[int] = None) -> Optional[float]:
    spend_value = float(spend or 0.0)
    count_value = int(event_count or 0)
    if count_value <= 0:
        return None
    value = spend_value / count_value
    return round(value, digits) if digits is not None else value


def _number(data: Mapping[str, Any], key: str) -> float:
    try:
        return float(data.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def rule_metric_reading(metric: Any, data: Mapping[str, Any]) -> MetricReading:
    key = canonical_rule_metric(metric)
    label = METRIC_LABELS.get(key, key or "Неизвестная метрика")
    unit = METRIC_UNITS.get(key, "")
    spend = _number(data, "spend")
    leads = int(_number(data, "leads"))
    registrations = int(_number(data, "registrations"))
    purchases = int(_number(data, "purchases"))
    clicks = int(_number(data, "clicks"))
    impressions = int(_number(data, "impressions"))

    if key == "spend":
        value = spend
    elif key == "leads":
        value = float(leads)
    elif key == "registrations":
        value = float(registrations)
    elif key == "purchases":
        value = float(purchases)
    elif key == "cpl":
        value = cost_per_event(spend, leads)
    elif key == "cpreg":
        value = cost_per_event(spend, registrations)
    elif key == "cpp":
        value = cost_per_event(spend, purchases)
    elif key == "cpc":
        value = cost_per_event(spend, clicks)
    elif key == "ctr":
        value = (clicks / impressions * 100) if impressions > 0 else None
    elif key == "legacy_cpa":
        return MetricReading(
            key=key,
            label=label,
            unit=unit,
            value=None,
            unavailable_reason="Общий CPA объединял лиды и регистрации. Выберите CPL, CPReg или CPP.",
        )
    else:
        return MetricReading(
            key=key,
            label=label,
            unit=unit,
            value=None,
            unavailable_reason="Метрика не поддерживается текущей версией Buyerly.",
        )

    if value is None:
        denominator_labels = {
            "cpl": "лидов",
            "cpreg": "регистраций",
            "cpp": "покупок",
            "cpc": "кликов",
            "ctr": "показов",
        }
        return MetricReading(
            key=key,
            label=label,
            unit=unit,
            value=None,
            unavailable_reason=f"Нет {denominator_labels.get(key, 'данных')} для расчёта.",
        )

    return MetricReading(key=key, label=label, unit=unit, value=float(value))


def compare_metric(reading: MetricReading, operator: str, target: Any) -> bool:
    """Compare raw values; rounding is only for display and never affects a rule."""

    if not reading.available or operator not in RULE_OPERATORS:
        return False
    value = float(reading.value)
    target_value = float(target)
    if operator == "gt":
        return value > target_value
    if operator == "gte":
        return value >= target_value
    if operator == "lt":
        return value < target_value
    if operator == "lte":
        return value <= target_value
    return math.isclose(value, target_value, rel_tol=1e-9, abs_tol=1e-9)


def normalize_rule_conditions(conditions: Any) -> tuple[list[dict[str, Any]], bool, bool]:
    """Return normalized conditions, whether data changed and legacy CPA presence."""

    if not isinstance(conditions, list):
        return [], conditions not in (None, []), False
    normalized: list[dict[str, Any]] = []
    changed = False
    has_legacy_cpa = False
    for raw_condition in conditions:
        if not isinstance(raw_condition, dict):
            changed = True
            continue
        condition = dict(raw_condition)
        original_metric = str(condition.get("metric", ""))
        metric = canonical_rule_metric(original_metric)
        if metric != original_metric:
            condition["metric"] = metric
            changed = True
        if metric == "legacy_cpa":
            has_legacy_cpa = True
            if condition.get("migration_note") != "replace_with_cpl_cpreg_or_cpp":
                condition["migration_note"] = "replace_with_cpl_cpreg_or_cpp"
                changed = True
        normalized.append(condition)
    return normalized, changed, has_legacy_cpa


def normalize_runtime_rule(rule: Mapping[str, Any]) -> tuple[dict[str, Any], bool, bool]:
    normalized_rule = dict(rule)
    conditions, conditions_changed, has_legacy_cpa = normalize_rule_conditions(
        normalized_rule.get("conditions", [])
    )
    changed = conditions_changed
    normalized_rule["conditions"] = conditions
    if has_legacy_cpa:
        if normalized_rule.get("enabled") is not False:
            normalized_rule["enabled"] = False
            changed = True
        if normalized_rule.get("needs_review") is not True:
            normalized_rule["needs_review"] = True
            changed = True
        review_reason = "Замените старый общий CPA на CPL, CPReg или CPP."
        if normalized_rule.get("review_reason") != review_reason:
            normalized_rule["review_reason"] = review_reason
            changed = True
    return normalized_rule, changed, has_legacy_cpa


def validate_public_rule_conditions(conditions: Iterable[Mapping[str, Any]]) -> None:
    for condition in conditions:
        metric = canonical_rule_metric(condition.get("metric"))
        if metric not in PUBLIC_RULE_METRICS:
            raise ValueError(f"Unsupported rule metric: {condition.get('metric')}")
        operator = str(condition.get("operator", ""))
        if operator not in RULE_OPERATORS:
            raise ValueError(f"Unsupported rule operator: {operator}")
        time_window = str(condition.get("time_window", "today"))
        if time_window not in RULE_TIME_WINDOWS:
            raise ValueError(f"Unsupported rule time window: {time_window}")
        try:
            value = float(condition.get("value"))
        except (TypeError, ValueError) as error:
            raise ValueError("Rule value must be numeric") from error
        if not math.isfinite(value) or value < 0 or value > RULE_MAX_VALUE:
            raise ValueError("Rule value is outside the safe range")


def validate_runtime_rule(rule: Mapping[str, Any]) -> None:
    """Fail closed for stored snapshots before RuleEngine can choose an action."""

    if not isinstance(rule, Mapping):
        raise ValueError("Rule must be an object")
    action = str(rule.get("action", ""))
    if action not in RULE_ACTIONS:
        raise ValueError(f"Unsupported rule action: {action}")
    logic = str(rule.get("logic", rule.get("condition_logic", "")))
    if logic not in RULE_LOGICS:
        raise ValueError(f"Unsupported rule logic: {logic}")
    conditions = rule.get("conditions")
    if not isinstance(conditions, list) or not 1 <= len(conditions) <= RULE_MAX_CONDITIONS:
        raise ValueError("Rule must contain between 1 and 20 conditions")
    validate_public_rule_conditions(conditions)

    def safe_number(key: str, default: float = 0.0) -> float:
        try:
            value = float(rule.get(key, default))
        except (TypeError, ValueError) as error:
            raise ValueError(f"Invalid numeric rule field: {key}") from error
        if not math.isfinite(value):
            raise ValueError(f"Non-finite numeric rule field: {key}")
        return value

    cooldown = safe_number("cooldown_minutes")
    check_interval = safe_number(
        "check_interval",
        safe_number("check_interval_minutes", 5.0),
    )
    budget_percent = safe_number("budget_change_percent")
    budget_max = safe_number("budget_max_daily")
    if cooldown < 0 or cooldown > RULE_MAX_COOLDOWN_MINUTES or not cooldown.is_integer():
        raise ValueError("Cooldown is outside the safe range")
    if (
        check_interval < 1
        or check_interval > RULE_MAX_CHECK_INTERVAL_MINUTES
        or not check_interval.is_integer()
    ):
        raise ValueError("Check interval is outside the safe range")
    if budget_max < 0 or budget_max > RULE_MAX_BUDGET:
        raise ValueError("Budget limit is outside the safe range")
    if action in {"increase_budget", "decrease_budget"}:
        if budget_percent <= 0 or budget_percent > RULE_MAX_BUDGET_CHANGE_PERCENT:
            raise ValueError("Budget change percentage is outside the safe range")
        if action == "increase_budget" and budget_max <= 0:
            raise ValueError("Budget increase requires a positive daily ceiling")
    elif budget_percent != 0 or budget_max != 0:
        raise ValueError("Non-budget actions cannot contain budget parameters")


def format_optional_cost(value: Optional[float]) -> str:
    return "—" if value is None else f"${value:.2f}"
