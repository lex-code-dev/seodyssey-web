from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.models import Plan, Payment
from billing.services import apply_successful_payment
from core.models import Site, SiteMember
from notifications.email import send_expiry_warnings
from notifications.models import EmailLog

User = get_user_model()


@override_settings(EMAIL_ASYNC=False)
class RegistrationEmailTests(TestCase):
    def test_register_sends_welcome_and_owner_emails(self):
        resp = self.client.post(reverse("register"), {
            "username": "newuser",
            "email": "newuser@example.com",
            "password1": "Str0ng-Passw0rd!",
            "password2": "Str0ng-Passw0rd!",
        })
        self.assertEqual(resp.status_code, 302)

        self.assertEqual(len(mail.outbox), 2)
        recipients = {m.to[0] for m in mail.outbox}
        self.assertIn("newuser@example.com", recipients)
        self.assertIn("info@seodyssey.ru", recipients)

        welcome = next(m for m in mail.outbox if m.to == ["newuser@example.com"])
        self.assertIn("Добро пожаловать", welcome.subject)
        # есть HTML-альтернатива
        self.assertTrue(any(mt == "text/html" for _, mt in welcome.alternatives))

        self.assertEqual(
            EmailLog.objects.filter(status=EmailLog.STATUS_SENT).count(), 2
        )

    def test_honeypot_spam_sends_nothing(self):
        resp = self.client.post(reverse("register"), {
            "username": "bot",
            "email": "bot@example.com",
            "password1": "Str0ng-Passw0rd!",
            "password2": "Str0ng-Passw0rd!",
            "website": "spam.example",
        })
        self.assertEqual(resp.status_code, 200)  # форма с ошибкой
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(EmailLog.objects.count(), 0)


class PasswordResetTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="resetme", email="resetme@example.com", password="old-pass-123"
        )

    def test_reset_page_renders(self):
        resp = self.client.get(reverse("password_reset"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Восстановление пароля")

    def test_reset_email_sent_with_html_and_valid_link(self):
        resp = self.client.post(reverse("password_reset"), {"email": "resetme@example.com"})
        self.assertEqual(resp.status_code, 302)

        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ["resetme@example.com"])
        self.assertIn("Смена пароля", msg.subject)
        self.assertIn("/reset/", msg.body)
        html = next(body for body, mt in msg.alternatives if mt == "text/html")
        self.assertIn("/reset/", html)

        # ссылка из письма реально открывается
        link = next(line for line in msg.body.splitlines() if "/reset/" in line).strip()
        path = link.split("://", 1)[1].split("/", 1)[1]
        resp = self.client.get("/" + path, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Новый пароль")


@override_settings(EMAIL_ASYNC=False)
class PaymentNotificationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="payer", email="payer@example.com")
        self.plan = Plan.objects.create(name="Pro", slug="pro", price_monthly=990)
        self.payment = Payment.objects.create(
            user=self.user, plan=self.plan,
            yookassa_id="test-payment-1", amount=Decimal("990.00"),
        )

    def test_successful_payment_notifies_owner(self):
        apply_successful_payment(self.payment)

        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ["info@seodyssey.ru"])
        self.assertIn("990", msg.subject)
        self.assertIn("payer", msg.subject)

    def test_repeated_processing_sends_once(self):
        apply_successful_payment(self.payment)
        self.payment.refresh_from_db()
        apply_successful_payment(self.payment)  # идемпотентный повтор (ретрай вебхука)
        self.assertEqual(len(mail.outbox), 1)


@override_settings(EMAIL_ASYNC=False)
class ExpiryWarningTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner", email="owner@example.com")
        self.viewer = User.objects.create_user(username="viewer", email="viewer@example.com")
        self.no_email = User.objects.create_user(username="noemail", email="")
        self.site = Site.objects.create(name="Мой сайт", domain="example.com")
        for u in (self.owner, self.viewer, self.no_email):
            SiteMember.objects.create(user=u, site=self.site)

    @staticmethod
    def _checks(ssl_days=None, domain_days=None):
        checks = {}
        if ssl_days is not None:
            checks["ssl"] = {"status": "ok", "expires_in_days": ssl_days}
        if domain_days is not None:
            checks["domain"] = {"status": "ok", "expires_in_days": domain_days}
        return checks

    def test_no_warning_when_far_from_expiry(self):
        sent = send_expiry_warnings(self.site, self._checks(ssl_days=60, domain_days=200))
        self.assertEqual(sent, 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_threshold_30_sends_to_members_with_email(self):
        sent = send_expiry_warnings(self.site, self._checks(ssl_days=25))
        self.assertEqual(sent, 2)  # owner + viewer, noemail пропущен
        recipients = {m.to[0] for m in mail.outbox}
        self.assertEqual(recipients, {"owner@example.com", "viewer@example.com"})
        self.assertIn("SSL", mail.outbox[0].subject)

        log = EmailLog.objects.filter(event=EmailLog.EVENT_SSL_EXPIRY).first()
        self.assertEqual(log.threshold_days, 30)
        self.assertEqual(log.status, EmailLog.STATUS_SENT)

    def test_same_threshold_not_repeated_next_day(self):
        send_expiry_warnings(self.site, self._checks(ssl_days=25))
        mail.outbox.clear()
        # следующий ежедневный прогон: осталось на день меньше
        sent = send_expiry_warnings(self.site, self._checks(ssl_days=24))
        self.assertEqual(sent, 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_each_threshold_fires_once(self):
        send_expiry_warnings(self.site, self._checks(ssl_days=25))   # порог 30
        send_expiry_warnings(self.site, self._checks(ssl_days=6))    # порог 7
        send_expiry_warnings(self.site, self._checks(ssl_days=2))    # порог 3
        self.assertEqual(len(mail.outbox), 6)  # 3 порога × 2 получателя
        thresholds = set(
            EmailLog.objects.filter(event=EmailLog.EVENT_SSL_EXPIRY)
            .values_list("threshold_days", flat=True)
        )
        self.assertEqual(thresholds, {30, 7, 3})

    def test_renewal_restarts_warning_cycle(self):
        send_expiry_warnings(self.site, self._checks(ssl_days=25))
        mail.outbox.clear()
        # имитируем: предупреждение слали для прошлого срока действия,
        # сертификат продлили, и новый срок снова подошёл к порогу 30
        EmailLog.objects.all().update(
            expires_on=timezone.localdate() - timedelta(days=60)
        )
        sent = send_expiry_warnings(self.site, self._checks(ssl_days=25))
        self.assertEqual(sent, 2)

    def test_domain_and_ssl_are_independent(self):
        sent = send_expiry_warnings(self.site, self._checks(ssl_days=25, domain_days=5))
        self.assertEqual(sent, 4)  # 2 события × 2 получателя
        events = set(EmailLog.objects.values_list("event", flat=True))
        self.assertEqual(events, {EmailLog.EVENT_SSL_EXPIRY, EmailLog.EVENT_DOMAIN_EXPIRY})

    def test_expired_negative_days_not_emailed(self):
        # об истёкшем сертификате уже кричит Telegram-алерт; писем «истекает» не шлём
        sent = send_expiry_warnings(self.site, self._checks(ssl_days=-1))
        self.assertEqual(sent, 0)

    def test_send_failure_marks_log_failed(self):
        from unittest.mock import patch
        with patch("notifications.tasks.EmailMultiAlternatives.send", side_effect=OSError("smtp down")):
            sent = send_expiry_warnings(self.site, self._checks(ssl_days=25))
        self.assertEqual(sent, 2)  # логи созданы, но со статусом failed
        statuses = set(EmailLog.objects.values_list("status", flat=True))
        self.assertEqual(statuses, {EmailLog.STATUS_FAILED})
        self.assertEqual(len(mail.outbox), 0)


class UnisenderGoBackendTests(TestCase):
    """Бэкенд отправки через HTTPS API Unisender Go (SMTP-порты на VPS заблокированы)."""

    def _send_message(self):
        from django.core.mail import EmailMultiAlternatives
        from notifications.backends import UnisenderGoBackend

        msg = EmailMultiAlternatives(
            subject="Тема",
            body="Текст",
            from_email="SEOdyssey <info@seodyssey.ru>",
            to=["user@example.com"],
        )
        msg.attach_alternative("<b>html</b>", "text/html")
        return UnisenderGoBackend().send_messages([msg])

    @override_settings(UNISENDER_GO_API_KEY="test-key")
    def test_builds_api_payload(self):
        from unittest.mock import MagicMock, patch

        ok = MagicMock(status_code=200)
        ok.json.return_value = {"status": "success"}
        with patch("notifications.backends.requests.post", return_value=ok) as mock_post:
            sent = self._send_message()

        self.assertEqual(sent, 1)
        kwargs = mock_post.call_args.kwargs
        self.assertEqual(kwargs["headers"], {"X-API-KEY": "test-key"})
        message = kwargs["json"]["message"]
        self.assertEqual(message["recipients"], [{"email": "user@example.com"}])
        self.assertEqual(message["subject"], "Тема")
        self.assertEqual(message["from_email"], "info@seodyssey.ru")
        self.assertEqual(message["from_name"], "SEOdyssey")
        self.assertEqual(message["body"], {"plaintext": "Текст", "html": "<b>html</b>"})
        # skip_unsubscribe требует флага у аккаунта — по умолчанию выключен
        self.assertNotIn("skip_unsubscribe", message)

    @override_settings(UNISENDER_GO_API_KEY="test-key", UNISENDER_GO_SKIP_UNSUBSCRIBE=True)
    def test_skip_unsubscribe_when_enabled(self):
        from unittest.mock import MagicMock, patch

        ok = MagicMock(status_code=200)
        ok.json.return_value = {"status": "success"}
        with patch("notifications.backends.requests.post", return_value=ok) as mock_post:
            self._send_message()
        self.assertEqual(mock_post.call_args.kwargs["json"]["message"]["skip_unsubscribe"], 1)

    @override_settings(UNISENDER_GO_API_KEY="test-key")
    def test_api_error_raises(self):
        from unittest.mock import MagicMock, patch
        from notifications.backends import UnisenderGoApiError

        bad = MagicMock(status_code=401, text='{"status":"error"}')
        bad.json.return_value = {"status": "error"}
        with patch("notifications.backends.requests.post", return_value=bad):
            with self.assertRaises(UnisenderGoApiError):
                self._send_message()
