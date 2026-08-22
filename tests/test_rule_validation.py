import unittest

from core.metrics import validate_rule_set_compatibility, validate_runtime_rule


def rule(*, action="turn_off", logic="and", conditions=None, name="Правило"):
    return {
        "name": name,
        "action": action,
        "logic": logic,
        "conditions": conditions or [
            {"metric": "spend", "operator": "gte", "value": 5, "time_window": "today"}
        ],
        "cooldown_minutes": 0,
        "check_interval": 5,
        "notify_tg": True,
        "budget_change_percent": 0,
        "budget_max_daily": 0,
    }


class TestRuleValidation(unittest.TestCase):
    def test_rejects_duplicate_conditions(self):
        condition = {"metric": "spend", "operator": "gte", "value": 5, "time_window": "today"}
        with self.assertRaisesRegex(ValueError, "несколько раз"):
            validate_runtime_rule(rule(conditions=[condition, condition.copy()]))

    def test_rejects_impossible_and_range(self):
        with self.assertRaisesRegex(ValueError, "противоречат"):
            validate_runtime_rule(
                rule(
                    conditions=[
                        {"metric": "spend", "operator": "gte", "value": 10, "time_window": "today"},
                        {"metric": "spend", "operator": "lt", "value": 5, "time_window": "today"},
                    ]
                )
            )

    def test_accepts_reachable_and_range(self):
        validate_runtime_rule(
            rule(
                conditions=[
                    {"metric": "spend", "operator": "gte", "value": 5, "time_window": "today"},
                    {"metric": "spend", "operator": "lt", "value": 10, "time_window": "today"},
                ]
            )
        )

    def test_rejects_or_that_is_always_true(self):
        with self.assertRaisesRegex(ValueError, "срабатывать всегда"):
            validate_runtime_rule(
                rule(
                    action="notify_only",
                    logic="or",
                    conditions=[
                        {"metric": "leads", "operator": "gte", "value": 1, "time_window": "today"},
                        {"metric": "leads", "operator": "lt", "value": 1, "time_window": "today"},
                    ],
                )
            )

    def test_count_metrics_must_be_whole_numbers(self):
        with self.assertRaisesRegex(ValueError, "целыми числами"):
            validate_runtime_rule(
                rule(
                    action="notify_only",
                    conditions=[
                        {"metric": "registrations", "operator": "gte", "value": 1.5, "time_window": "today"}
                    ],
                )
            )

    def test_stop_can_use_registration_or_purchase_conditions(self):
        validate_runtime_rule(
            rule(
                conditions=[
                    {"metric": "registrations", "operator": "gte", "value": 1, "time_window": "today"}
                ]
            )
        )
        validate_runtime_rule(
            rule(
                conditions=[
                    {"metric": "registrations", "operator": "eq", "value": 0, "time_window": "today"}
                ]
            )
        )

    def test_rejects_opposite_actions_with_the_same_trigger(self):
        stop = rule(action="turn_off", name="Выключить")
        start = rule(action="turn_on", name="Включить")
        with self.assertRaisesRegex(ValueError, "противоречат"):
            validate_rule_set_compatibility([stop, start])

    def test_accepts_opposite_actions_with_different_triggers(self):
        stop = rule(action="turn_off", name="Выключить")
        start = rule(
            action="turn_on",
            name="Включить",
            conditions=[
                {"metric": "leads", "operator": "gte", "value": 1, "time_window": "today"}
            ],
        )
        validate_rule_set_compatibility([stop, start])


if __name__ == "__main__":
    unittest.main()
