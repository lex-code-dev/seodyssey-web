"""
Разбор robots.txt по правилам Google (RFC 9309), с объяснением вердикта.

Зачем свой разбор вместо urllib.robotparser: стандартный парсер не понимает
ни `*` внутри пути, ни якорь `$` — правила вида `Disallow: /*?sort=` он просто
не замечает. Плюс он не умеет сказать, какое именно правило закрыло адрес,
а нам это нужно, чтобы отвечать на вопрос «почему страница закрыта».

Отличия от старого стандарта 1994 года, которых придерживаемся:
* пустая строка не завершает блок — блок заканчивается следующим User-agent;
* при конфликте правил выигрывает более длинное (специфичное), при равной
  длине — разрешающее.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Rule:
    allow: bool
    pattern: str

    def matches(self, path: str) -> bool:
        return bool(re.match(self._regex(), path))

    def _regex(self) -> str:
        anchored = self.pattern.endswith("$")
        body = self.pattern[:-1] if anchored else self.pattern
        parts = [re.escape(p) for p in body.split("*")]
        return ".*".join(parts) + ("$" if anchored else "")

    @property
    def weight(self) -> int:
        """Длина правила — по ней Google выбирает более специфичное."""
        return len(self.pattern)


@dataclass
class Group:
    agents: list[str] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)


@dataclass
class Robots:
    groups: list[Group] = field(default_factory=list)
    sitemaps: list[str] = field(default_factory=list)
    #: Полный запрет: robots.txt отдал 401/403 (RFC 9309)
    disallow_all: bool = False

    def group_for(self, agent: str) -> Group | None:
        """
        Выбираем группу с самым длинным подходящим токеном User-agent:
        для YandexBot группа `User-agent: Yandex` важнее общей `*`.
        """
        agent = agent.lower()
        best: tuple[int, Group] | None = None
        fallback: Group | None = None
        for group in self.groups:
            for token in group.agents:
                if token == "*":
                    fallback = fallback or group
                elif agent.startswith(token) or token.startswith(agent):
                    if best is None or len(token) > best[0]:
                        best = (len(token), group)
        return best[1] if best else fallback

    def check(self, agent: str, path: str) -> tuple[bool, Rule | None]:
        """
        Возвращает (разрешено, правило-победитель).
        Правило возвращаем и для запрета, и для явного Allow — чтобы показать,
        почему адрес открыт вопреки более общему запрету.
        """
        if self.disallow_all:
            return False, None

        group = self.group_for(agent)
        if group is None:
            return True, None

        winner: Rule | None = None
        for rule in group.rules:
            if not rule.matches(path):
                continue
            if winner is None or rule.weight > winner.weight:
                winner = rule
            elif rule.weight == winner.weight and rule.allow:
                winner = rule  # при равной длине выигрывает разрешающее

        if winner is None:
            return True, None
        return winner.allow, winner


def parse_robots(content: str, http_status: int | None = 200) -> Robots:
    """
    Разбирает текст robots.txt. http_status учитывается по RFC 9309:
    401/403 — сайт закрыт целиком, прочие 4xx — robots.txt нет, всё открыто.
    """
    robots = Robots()

    if http_status in (401, 403):
        robots.disallow_all = True
        return robots
    if http_status is not None and http_status >= 400:
        return robots

    current: Group | None = None
    agents_open = False  # идут ли подряд строки User-agent (общая группа)

    for raw_line in content.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue

        field_name, _, value = line.partition(":")
        field_name = field_name.strip().lower()
        value = value.strip()

        if field_name == "user-agent":
            if current is None or not agents_open:
                current = Group()
                robots.groups.append(current)
                agents_open = True
            current.agents.append(value.lower())
        elif field_name in ("disallow", "allow"):
            agents_open = False
            if current is None:
                continue  # правило без User-agent — игнорируем
            if field_name == "disallow" and value == "":
                continue  # пустой Disallow = разрешено всё, правилом не считаем
            current.rules.append(Rule(allow=field_name == "allow", pattern=value))
        elif field_name == "sitemap" and value:
            robots.sitemaps.append(value)

    return robots
