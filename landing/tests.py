from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.client import RequestFactory

from landing.models import GeoLead
from landing.rate_limit import GEO_LIMIT, TOOL_LIMIT, client_ip, is_rate_limited

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class RateLimitTests(SimpleTestCase):

    def setUp(self):
        cache.clear()
        self.rf = RequestFactory()

    def _request(self, ip="203.0.113.7"):
        return self.rf.post("/tools/robots-txt-check/", HTTP_X_REAL_IP=ip)

    def test_ip_from_nginx_header(self):
        self.assertEqual(client_ip(self._request("198.51.100.4")), "198.51.100.4")

    def test_allows_up_to_limit_then_blocks(self):
        request = self._request()
        for i in range(TOOL_LIMIT):
            self.assertFalse(is_rate_limited(request, "tools"), f"запрос {i + 1} должен пройти")
        self.assertTrue(is_rate_limited(request, "tools"), "запрос сверх лимита блокируется")

    def test_limit_is_per_ip(self):
        for _ in range(TOOL_LIMIT + 1):
            is_rate_limited(self._request("203.0.113.1"), "tools")
        self.assertTrue(is_rate_limited(self._request("203.0.113.1"), "tools"))
        self.assertFalse(is_rate_limited(self._request("203.0.113.2"), "tools"),
                         "соседний IP не должен страдать")

    def test_scopes_are_independent(self):
        request = self._request()
        for _ in range(TOOL_LIMIT + 1):
            is_rate_limited(request, "tools")
        self.assertFalse(is_rate_limited(request, "other"))


# ALLOWED_HOSTS на проде и в dev разные, поэтому тест ходит на дефолтный
# testserver, а не на dev-хост лендинга.
@override_settings(CACHES=LOCMEM, ALLOWED_HOSTS=["testserver"])
class ToolViewRateLimitTests(SimpleTestCase):
    """Вьюха отдаёт понятную ошибку и не идёт наружу, когда лимит исчерпан."""

    def setUp(self):
        cache.clear()

    def _post(self):
        return self.client.post(
            "/tools/robots-txt-check/",
            {"mode": "site", "domain": "example.com"},
            HTTP_X_REAL_IP="203.0.113.9",
        )

    def test_tool_returns_error_after_limit(self):
        for _ in range(TOOL_LIMIT):
            self._post()
        self.assertContains(self._post(), "Слишком много проверок")


@override_settings(CACHES=LOCMEM, ALLOWED_HOSTS=["testserver"])
class GeoCheckRateLimitTests(TestCase):
    """GEO-проверка дороже инструментов: письмо, Celery и запросы к нейросетям."""

    def setUp(self):
        cache.clear()

    def _post(self, url="https://example.com/page/", ip="203.0.113.20"):
        with patch("landing.tasks.run_geo_check.delay") as delay:
            response = self.client.post("/geo-check/", {"url": url}, HTTP_X_REAL_IP=ip)
        return response, delay

    def test_launches_up_to_limit(self):
        for i in range(GEO_LIMIT):
            response, delay = self._post()
            self.assertEqual(response.status_code, 302, f"проверка {i + 1} должна запуститься")
            delay.assert_called_once()

    def test_blocks_after_limit(self):
        for _ in range(GEO_LIMIT):
            self._post()
        response, delay = self._post()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "уже запущено несколько проверок")
        delay.assert_not_called()
        self.assertEqual(GeoLead.objects.count(), GEO_LIMIT, "лид сверх лимита не создаётся")

    def test_invalid_url_does_not_consume_quota(self):
        for _ in range(GEO_LIMIT + 3):
            self._post(url="")
        response, delay = self._post()
        self.assertEqual(response.status_code, 302, "после опечаток квота должна остаться")
        delay.assert_called_once()
