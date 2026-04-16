import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.models import IssueSolution


class Command(BaseCommand):
    help = "Импортирует каталог решений в IssueSolution из JSON-файла"

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            type=str,
            required=True,
            help="Путь до JSON-файла, например data/issue_solutions_webmaster.json",
        )
        parser.add_argument(
            "--update",
            action="store_true",
            help="Обновлять существующие записи по issue_code. По умолчанию создаются только новые.",
        )

    def handle(self, *args, **options):
        file_path = Path(options["file"])
        should_update = options["update"]

        if not file_path.exists():
            raise CommandError(f"Файл не найден: {file_path}")

        try:
            with file_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as exc:
            raise CommandError(f"Некорректный JSON: {exc}") from exc

        if not isinstance(data, list):
            raise CommandError("JSON должен содержать список объектов")

        created_count = 0
        updated_count = 0
        skipped_count = 0

        required_fields = {
            "check_key",
            "issue_code",
            "severity",
            "title",
            "short_summary",
            "steps",
        }

        for idx, item in enumerate(data, start=1):
            if not isinstance(item, dict):
                self.stdout.write(self.style.WARNING(f"[{idx}] Пропущено: элемент не является объектом"))
                skipped_count += 1
                continue

            missing = required_fields - item.keys()
            if missing:
                self.stdout.write(
                    self.style.WARNING(
                        f"[{idx}] Пропущено: отсутствуют поля {', '.join(sorted(missing))}"
                    )
                )
                skipped_count += 1
                continue

            issue_code = item["issue_code"]

            defaults = {
                "check_key": item["check_key"],
                "severity": item["severity"],
                "title": item["title"],
                "short_summary": item.get("short_summary", ""),
                "steps": item.get("steps", []),
                "links": item.get("links", []),
                "match_rules": item.get("match_rules", {}),
                "priority": item.get("priority", 100),
                "is_active": item.get("is_active", True),
            }

            existing = IssueSolution.objects.filter(issue_code=issue_code).first()

            if existing:
                if should_update:
                    for field, value in defaults.items():
                        setattr(existing, field, value)
                    existing.save()
                    updated_count += 1
                    self.stdout.write(self.style.SUCCESS(f"[UPDATE] {issue_code}"))
                else:
                    skipped_count += 1
                    self.stdout.write(self.style.WARNING(f"[SKIP] Уже существует: {issue_code}"))
                continue

            IssueSolution.objects.create(**defaults)
            created_count += 1
            self.stdout.write(self.style.SUCCESS(f"[CREATE] {issue_code}"))

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Импорт завершён"))
        self.stdout.write(f"Создано: {created_count}")
        self.stdout.write(f"Обновлено: {updated_count}")
        self.stdout.write(f"Пропущено: {skipped_count}")