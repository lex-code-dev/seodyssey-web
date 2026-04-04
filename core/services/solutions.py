from typing import Any, Dict, Optional

from core.models import IssueSolution


def _rules_match(match_rules: dict, context: dict) -> bool:
    """
    Простая проверка: все ключи из match_rules должны совпасть
    со значениями в context.
    """
    if not match_rules:
        return True

    if not context:
        context = {}

    for key, expected_value in match_rules.items():
        actual_value = context.get(key)
        if actual_value != expected_value:
            return False

    return True


def get_solution_for_issue(
    *,
    check_key: str,
    severity: str,
    issue_code: str = "",
    context: Optional[Dict[str, Any]] = None,
) -> Optional[IssueSolution]:
    """
    Возвращает наиболее подходящее решение:
    1. сначала ищем точные решения по issue_code
    2. затем более общие по check_key + severity
    3. внутри — учитываем priority и match_rules
    """
    context = context or {}

    # 1. Кандидаты с issue_code
    if issue_code:
        exact_candidates = IssueSolution.objects.filter(
            is_active=True,
            severity=severity,
            issue_code=issue_code,
        ).order_by("priority", "id")

        for solution in exact_candidates:
            if _rules_match(solution.match_rules, context):
                return solution

    # 2. Кандидаты по check_key
    generic_candidates = IssueSolution.objects.filter(
        is_active=True,
        severity=severity,
        check_key=check_key,
    ).order_by("priority", "id")

    for solution in generic_candidates:
        if _rules_match(solution.match_rules, context):
            return solution

    return None