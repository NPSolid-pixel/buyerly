from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


# Meta can still return backward-compatible IANA names that are absent from
# slim Linux tzdata builds. Persist canonical names so every service agrees.
META_TIMEZONE_ALIASES = {
    "US/Hawaii": "Pacific/Honolulu",
    "HST": "Pacific/Honolulu",
    "US/Alaska": "America/Anchorage",
    "US/Aleutian": "America/Adak",
    "US/Arizona": "America/Phoenix",
    "US/Central": "America/Chicago",
    "US/East-Indiana": "America/Indiana/Indianapolis",
    "US/Eastern": "America/New_York",
    "US/Indiana-Starke": "America/Indiana/Knox",
    "US/Michigan": "America/Detroit",
    "US/Mountain": "America/Denver",
    "US/Pacific": "America/Los_Angeles",
    "US/Samoa": "Pacific/Pago_Pago",
    "Canada/Atlantic": "America/Halifax",
    "Canada/Central": "America/Winnipeg",
    "Canada/Eastern": "America/Toronto",
    "Canada/Mountain": "America/Edmonton",
    "Canada/Newfoundland": "America/St_Johns",
    "Canada/Pacific": "America/Vancouver",
    "Canada/Saskatchewan": "America/Regina",
    "Canada/Yukon": "America/Whitehorse",
    "Brazil/Acre": "America/Rio_Branco",
    "Brazil/East": "America/Sao_Paulo",
    "Brazil/West": "America/Manaus",
    "Chile/Continental": "America/Santiago",
    "Mexico/General": "America/Mexico_City",
    "Asia/Calcutta": "Asia/Kolkata",
    "Asia/Katmandu": "Asia/Kathmandu",
    "Europe/Kiev": "Europe/Kyiv",
}


@dataclass(frozen=True)
class AccountLocalClock:
    configured_name: str
    canonical_name: str
    zone: ZoneInfo


@dataclass(frozen=True)
class DayBoundaryDecision:
    current_date: str
    should_update: bool
    should_notify: bool
    reason: str


def canonical_timezone_name(value: object) -> str:
    name = str(value or "").strip()
    return META_TIMEZONE_ALIASES.get(name, name)


def resolve_account_clock(value: object) -> Optional[AccountLocalClock]:
    configured_name = str(value or "").strip()
    if not configured_name:
        return None
    canonical_name = canonical_timezone_name(configured_name)
    try:
        zone = ZoneInfo(canonical_name)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return AccountLocalClock(
        configured_name=configured_name,
        canonical_name=canonical_name,
        zone=zone,
    )


def evaluate_day_boundary(
    previous_date: object,
    local_now: datetime,
    *,
    notification_window_minutes: int = 5,
) -> DayBoundaryDecision:
    current_date = local_now.date().isoformat()
    previous = str(previous_date or "").strip()
    if previous == current_date:
        return DayBoundaryDecision(current_date, False, False, "same_date")
    if not previous:
        return DayBoundaryDecision(current_date, True, False, "initialized")

    minutes_after_midnight = local_now.hour * 60 + local_now.minute
    should_notify = 0 <= minutes_after_midnight < max(1, notification_window_minutes)
    return DayBoundaryDecision(
        current_date,
        True,
        should_notify,
        "new_day" if should_notify else "missed_window",
    )


def utc_offset_label(local_now: datetime) -> str:
    offset = local_now.utcoffset()
    if offset is None:
        return "UTC"
    total_minutes = int(offset / timedelta(minutes=1))
    sign = "+" if total_minutes >= 0 else "−"
    absolute = abs(total_minutes)
    hours, minutes = divmod(absolute, 60)
    return f"UTC{sign}{hours:02d}:{minutes:02d}"
