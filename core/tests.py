import ipaddress
import json
import socket
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from core.forms import AddSiteForm

WEBHOOK_SECRET = "test-webhook-secret"


def _fake_getaddrinfo(host, *args, **kwargs):
    """Литеральный IP отдаём как есть, остальное — публичный адрес."""
    try:
        ip = str(ipaddress.ip_address(host))
    except ValueError:
        ip = "93.184.216.34"
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]


class AddSiteFormSsrfTests(TestCase):
    """
    Аудит ходит по добавленному домену с нашего сервера, поэтому форма
    не должна пропускать адреса, ведущие внутрь инфраструктуры.
    """

    def setUp(self):
        patcher = mock.patch(
            "audits.net_guard.socket.getaddrinfo", side_effect=_fake_getaddrinfo
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _form(self, domain):
        return AddSiteForm(data={"name": "Тест", "domain": domain})

    def test_public_domain_accepted(self):
        form = self._form("https://example.com/page")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["domain"], "example.com")

    def test_loopback_domain_rejected(self):
        # Формальную проверку (точка + TLD) такой адрес проходит,
        # отсечь его может только резолв.
        form = self._form("127.0.0.1")
        self.assertFalse(form.is_valid())
        self.assertIn("domain", form.errors)

    def test_metadata_service_rejected(self):
        form = self._form("169.254.169.254")
        self.assertFalse(form.is_valid())

    def test_internal_tld_rejected(self):
        """metadata.google.internal формально выглядит как обычный домен."""
        with mock.patch(
            "audits.net_guard.socket.getaddrinfo",
            side_effect=lambda *a, **kw: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0))
            ],
        ):
            form = self._form("metadata.google.internal")
            self.assertFalse(form.is_valid())


@override_settings(TELEGRAM_WEBHOOK_SECRET=WEBHOOK_SECRET)
class TelegramWebhookAuthTests(TestCase):
    """
    Вебхук без CSRF и без авторизации — отличить настоящий апдейт
    от подделки может только секрет из setWebhook.
    """

    def setUp(self):
        self.url = reverse("telegram_webhook")
        self.payload = json.dumps(
            {"message": {"chat": {"id": 12345}, "text": "/start"}}
        )

    def _post(self, secret=None):
        headers = {}
        if secret is not None:
            headers["HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN"] = secret
        return self.client.post(
            self.url, self.payload, content_type="application/json", **headers
        )

    @mock.patch("notifications.telegram.send_telegram_message")
    def test_missing_secret_rejected(self, send):
        self.assertEqual(self._post().status_code, 403)
        send.assert_not_called()

    @mock.patch("notifications.telegram.send_telegram_message")
    def test_wrong_secret_rejected(self, send):
        self.assertEqual(self._post("не тот секрет").status_code, 403)
        send.assert_not_called()

    @mock.patch("notifications.telegram.send_telegram_message")
    def test_valid_secret_accepted(self, send):
        response = self._post(WEBHOOK_SECRET)
        self.assertEqual(response.status_code, 200)
        send.assert_called_once()

    @override_settings(TELEGRAM_WEBHOOK_SECRET="")
    @mock.patch("notifications.telegram.send_telegram_message")
    def test_unconfigured_secret_rejects_everything(self, send):
        """Пустой секрет в настройках не должен открывать эндпоинт всем."""
        self.assertEqual(self._post().status_code, 403)
        self.assertEqual(self._post("").status_code, 403)
        send.assert_not_called()


class AuditListXssTests(TestCase):
    """
    В results лежат title/description с проверяемого сайта — текст, который
    пишет его владелец. Раньше он уходил в <script> через |safe.
    """

    PAYLOAD = "</script><script>alert(1)</script>"

    def setUp(self):
        from django.contrib.auth.models import User

        from audits.models import AuditResult
        from core.models import Site, SiteMember

        self.user = User.objects.create_user("tester", password="pw-for-tests-1")
        site = Site.objects.create(name="Тест", domain="example.com")
        SiteMember.objects.create(
            user=self.user, site=site, role=SiteMember.ROLE_OWNER
        )
        self.audit = AuditResult.objects.create(
            site=site,
            status=AuditResult.STATUS_DONE,
            results={
                "seo": [
                    {"check": "title", "status": "ok", "message": self.PAYLOAD},
                    {"check": "desc", "status": "warning", "message": "ок"},
                    {"check": "h1", "status": "fail", "message": "нет H1"},
                ]
            },
        )
        self.client.force_login(self.user)

    def test_payload_not_rendered_raw(self):
        html = self.client.get(reverse("audit_list")).content.decode()
        self.assertNotIn(self.PAYLOAD, html)

    def test_counts_rendered_server_side(self):
        html = self.client.get(reverse("audit_list")).content.decode()
        # 1 ok / 1 warning / 1 fail посчитаны в Python, а не в браузере
        self.assertIn('<span class="numeric">1</span>', html)


LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class LoginThrottleTests(TestCase):
    """Подбор пароля должен упираться в лимит и на /login/, и в админке."""

    def setUp(self):
        from django.contrib.auth.models import User
        from django.core.cache import cache

        cache.clear()
        self.password = "correct-horse-battery-1"
        self.user = User.objects.create_user("victim", password=self.password)

    def _login(self, password, ip="203.0.113.9", username="victim"):
        return self.client.post(
            "/login/",
            {"username": username, "password": password},
            HTTP_X_REAL_IP=ip,
        )

    def test_correct_password_works(self):
        from core.login_throttle import USERNAME_LIMIT

        for _ in range(USERNAME_LIMIT - 1):
            self._login("wrong")
        # ещё не исчерпан — верный пароль пускает
        self._login(self.password)
        self.assertTrue(self.client.session.get("_auth_user_id"))

    def test_blocked_after_limit(self):
        from core.login_throttle import USERNAME_LIMIT

        for _ in range(USERNAME_LIMIT):
            self._login("wrong")
        # верный пароль больше не помогает — счётчик исчерпан
        self._login(self.password)
        self.assertIsNone(self.client.session.get("_auth_user_id"))

    def test_success_resets_counter(self):
        from core.login_throttle import USERNAME_LIMIT
        from django.core.cache import cache

        for _ in range(USERNAME_LIMIT - 1):
            self._login("wrong")
        self._login(self.password)
        self.client.logout()
        cache_key = f"login-fail:user:victim"
        self.assertIsNone(cache.get(cache_key))

    def test_ip_limit_covers_many_usernames(self):
        """Перебор разных логинов с одного адреса тоже упирается в лимит."""
        from django.contrib.auth.models import User

        from core.login_throttle import IP_LIMIT, is_blocked

        for i in range(IP_LIMIT):
            self._login("wrong", username=f"user{i}")

        request = type("R", (), {"META": {"HTTP_X_REAL_IP": "203.0.113.9"}})()
        self.assertTrue(is_blocked(request, None))

    def test_other_ip_unaffected(self):
        from core.login_throttle import USERNAME_LIMIT
        from core.login_throttle import is_blocked

        for i in range(USERNAME_LIMIT):
            self._login("wrong", username=f"other{i}")

        clean = type("R", (), {"META": {"HTTP_X_REAL_IP": "198.51.100.4"}})()
        self.assertFalse(is_blocked(clean, "victim"))

    def test_cache_failure_does_not_lock_out(self):
        """Падение Redis не должно запирать вход."""
        from core.login_throttle import is_blocked

        with mock.patch("core.login_throttle.cache.get", side_effect=RuntimeError):
            request = type("R", (), {"META": {}})()
            self.assertFalse(is_blocked(request, "victim"))


class EncryptedTokenTests(TestCase):
    """Токены Яндекса не должны читаться из файла базы напрямую."""

    TOKEN = "y0_AgAAAAB-secret-access-token"
    REFRESH = "1:refresh-secret-value"

    def setUp(self):
        from django.contrib.auth.models import User

        from core.models import YandexOAuth

        self.user = User.objects.create_user("owner", password="pw-for-tests-2")
        self.oauth = YandexOAuth.objects.create(
            user=self.user, access_token=self.TOKEN, refresh_token=self.REFRESH
        )

    def test_roundtrip(self):
        from core.models import YandexOAuth

        fresh = YandexOAuth.objects.get(pk=self.oauth.pk)
        self.assertEqual(fresh.access_token, self.TOKEN)
        self.assertEqual(fresh.refresh_token, self.REFRESH)

    def test_stored_value_is_not_plaintext(self):
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT access_token, refresh_token FROM core_yandexoauth WHERE id = %s",
                [self.oauth.pk],
            )
            access, refresh = cursor.fetchone()

        self.assertNotIn(self.TOKEN, access)
        self.assertNotIn(self.REFRESH, refresh)
        # Fernet-токен всегда начинается с версии 0x80 → "gAAAAA"
        self.assertTrue(access.startswith("gAAAAA"), access[:20])

    def test_same_value_encrypts_differently(self):
        """Случайный вектор: одинаковые токены не совпадают в базе."""
        from django.contrib.auth.models import User
        from django.db import connection

        from core.models import YandexOAuth

        other = User.objects.create_user("owner2", password="pw-for-tests-3")
        second = YandexOAuth.objects.create(user=other, access_token=self.TOKEN)

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT access_token FROM core_yandexoauth WHERE id IN (%s, %s)",
                [self.oauth.pk, second.pk],
            )
            values = [row[0] for row in cursor.fetchall()]

        self.assertNotEqual(values[0], values[1])

    def test_legacy_plaintext_still_readable(self):
        """До прогона миграции старые открытые строки не должны ломать интеграции."""
        from django.db import connection

        from core.models import YandexOAuth

        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE core_yandexoauth SET access_token = %s WHERE id = %s",
                ["legacy-plaintext-token", self.oauth.pk],
            )

        fresh = YandexOAuth.objects.get(pk=self.oauth.pk)
        self.assertEqual(fresh.access_token, "legacy-plaintext-token")
