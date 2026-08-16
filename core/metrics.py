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
    "spend": "Сумма spend из Meta по всем успешно синхронизированным ad set за выбранный период.",
    "leads": "События lead из Meta. Не складываются с регистрациями или покупками.",
    "cost_per_lead": "Spend / лиды. Если лидов нет, значение отсутствует.",
    "registrations": "События complete_registration из Meta. Считаются отдельно от лидов.",
    "cost_per_registration": "Spend / регистрации. Если регистраций нет, значение отсутствует.",
    "purchases": "События purchase из Meta. Считаются отдельно от лидов и регистраций.",
    "cost_per_purchase": "Spend / покупки. Если покупок нет, значение отсутствует.",
    "clicks": "Все клики, которые Meta вернула в поле clicks. Это не только переходы по ссылке.",
    "ctr": "Все клики / показы × 100. До добавления Link CTR не трактуется как доля переходов на сайт.",
    "cpc": "Spend / все клики. До добавления CPC Link не трактуется как цена перехода на сайт.",
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


def format_optional_cost(value: Optional[float]) -> str:
    return "—" if value is None else f"${value:.2f}"
