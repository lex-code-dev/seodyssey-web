from django.conf import settings
from django.db import models


class Module(models.Model):
    title = models.CharField("Название", max_length=200)
    slug = models.SlugField("Код", unique=True)
    order = models.PositiveIntegerField("Порядок", default=0)
    description = models.TextField("Описание", blank=True)
    is_published = models.BooleanField("Опубликован", default=False)
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        verbose_name = "Модуль"
        verbose_name_plural = "Модули"
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.order}. {self.title}"


class Lesson(models.Model):
    module = models.ForeignKey(
        Module,
        on_delete=models.PROTECT,
        related_name="lessons",
        verbose_name="Модуль",
        null=True, blank=True,
        help_text="Пусто = урок вне модуля",
    )
    title = models.CharField("Название", max_length=200)
    slug = models.SlugField("Код", unique=True)
    order = models.PositiveIntegerField("Порядок", default=0)
    video_url = models.URLField(
        "Ссылка на видео", blank=True,
        help_text="VK Видео (обычная ссылка или код плеера video_ext.php) либо YouTube",
    )
    body = models.TextField("Текст под видео", blank=True)
    is_free = models.BooleanField(
        "Бесплатный", default=False,
        help_text="Вводный урок, открыт всем без оплаты",
    )
    is_published = models.BooleanField("Опубликован", default=False)
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        verbose_name = "Урок"
        verbose_name_plural = "Уроки"
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.order}. {self.title}"


class Quiz(models.Model):
    lesson = models.OneToOneField(
        Lesson,
        on_delete=models.CASCADE,
        related_name="quiz",
        verbose_name="Урок",
    )
    title = models.CharField(
        "Название", max_length=200, blank=True,
        help_text="Пусто = используется название урока",
    )
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        verbose_name = "Тест"
        verbose_name_plural = "Тесты"
        ordering = ["lesson__order", "id"]

    def __str__(self):
        return self.title or f"Тест: {self.lesson.title}"


class Question(models.Model):
    quiz = models.ForeignKey(
        Quiz,
        on_delete=models.CASCADE,
        related_name="questions",
        verbose_name="Тест",
    )
    text = models.TextField("Текст вопроса")
    order = models.PositiveIntegerField("Порядок", default=0)

    class Meta:
        verbose_name = "Вопрос"
        verbose_name_plural = "Вопросы"
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.quiz} — вопрос {self.order}"


class Choice(models.Model):
    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE,
        related_name="choices",
        verbose_name="Вопрос",
    )
    text = models.CharField("Текст варианта", max_length=255)
    is_correct = models.BooleanField("Правильный", default=False)

    class Meta:
        verbose_name = "Вариант ответа"
        verbose_name_plural = "Варианты ответа"
        ordering = ["id"]

    def __str__(self):
        mark = "✓" if self.is_correct else "✗"
        return f"{mark} {self.text}"


class Homework(models.Model):
    lesson = models.OneToOneField(
        Lesson,
        on_delete=models.CASCADE,
        related_name="homework",
        verbose_name="Урок",
    )
    text = models.TextField("Условие задания")
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Домашнее задание"
        verbose_name_plural = "Домашние задания"
        ordering = ["lesson__order", "id"]

    def __str__(self):
        return f"Домашка: {self.lesson.title}"


class Submission(models.Model):
    STATUS_SUBMITTED = "submitted"
    STATUS_ACCEPTED = "accepted"
    STATUS_NEEDS_REVISION = "needs_revision"
    STATUS_CHOICES = [
        (STATUS_SUBMITTED, "На проверке"),
        (STATUS_ACCEPTED, "Принято"),
        (STATUS_NEEDS_REVISION, "На доработку"),
    ]

    homework = models.ForeignKey(
        Homework,
        on_delete=models.CASCADE,
        related_name="submissions",
        verbose_name="Задание",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="course_submissions",
        verbose_name="Ученик",
    )
    body = models.TextField("Ответ ученика")
    status = models.CharField(
        "Статус", max_length=15,
        choices=STATUS_CHOICES, default=STATUS_SUBMITTED,
    )
    reviewer_comment = models.TextField("Комментарий проверяющего", blank=True)
    submitted_at = models.DateTimeField("Сдано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Сдача задания"
        verbose_name_plural = "Сдачи заданий"
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"{self.user} — {self.homework.lesson.title} ({self.get_status_display()})"


class CourseAccess(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="course_access",
        verbose_name="Пользователь",
    )
    payment = models.ForeignKey(
        "billing.Payment",
        on_delete=models.SET_NULL,
        related_name="course_accesses",
        verbose_name="Платёж",
        null=True, blank=True,
        help_text="Платёж, по которому выдан доступ",
    )
    paid_at = models.DateTimeField("Оплачено")
    is_active = models.BooleanField("Активен", default=True)
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        verbose_name = "Доступ к курсу"
        verbose_name_plural = "Доступы к курсу"
        ordering = ["-paid_at"]

    def __str__(self):
        state = "активен" if self.is_active else "отключён"
        return f"{self.user} — доступ {state} с {self.paid_at:%d.%m.%Y}"

    @staticmethod
    def user_has_access(user):
        """
        Есть ли у пользователя доступ к платному контенту курса.
        Уроки с is_free=True открыты всем и здесь не проверяются.
        """
        if not user.is_authenticated:
            return False
        return CourseAccess.objects.filter(user=user, is_active=True).exists()
