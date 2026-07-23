import ipaddress
import socket
from unittest import mock

import httpx
from django.test import SimpleTestCase

from audits.checks.indexability import (
    _evaluate_ai_bots,
    _evaluate_page_html,
    _evaluate_page_robots,
    _evaluate_robots_txt,
    _normalize_page_url,
)
from audits.checks.seo import evaluate_seo_html
from audits.net_guard import (
    BlockedTargetError,
    TargetError,
    normalize_domain,
    normalize_url,
    safe_get,
)
from audits.robots_rules import parse_robots

OPEN_ROBOTS = "User-agent: *\nDisallow: /admin/\nSitemap: https://a.ru/sitemap.xml\n"


def _status(results, check):
    return next(r["status"] for r in results if r["check"] == check)


class EvaluateRobotsTxtTests(SimpleTestCase):
    """
    Кейсы, на которых спотыкался прежний поиск подстроки «Disallow: /».
    """

    def assert_site_blocked(self, content, msg):
        self.assertEqual(_status(_evaluate_robots_txt(content, 200), "robots_txt"), "fail", msg)

    def test_open_site(self):
        results = _evaluate_robots_txt(OPEN_ROBOTS, 200)
        self.assertEqual(_status(results, "robots_txt"), "ok")
        self.assertEqual(_status(results, "robots_sitemap"), "ok")

    def test_blocked_lf(self):
        self.assert_site_blocked("User-agent: *\nDisallow: /\n", "обычный запрет")

    def test_blocked_crlf(self):
        self.assert_site_blocked("User-agent: *\r\nDisallow: /\r\n", "виндовые переносы строк")

    def test_blocked_without_trailing_newline(self):
        self.assert_site_blocked("User-agent: *\nDisallow: /", "нет перевода строки в конце")

    def test_blocked_with_trailing_space(self):
        self.assert_site_blocked("User-agent: *\nDisallow: / \n", "пробел в конце правила")

    def test_blocked_for_single_bot_only_is_not_sitewide(self):
        """Запрет для стороннего бота не должен считаться запретом для всех."""
        content = "User-agent: *\nAllow: /\n\nUser-agent: BadBot\nDisallow: /\n"
        self.assertEqual(_status(_evaluate_robots_txt(content, 200), "robots_txt"), "ok")

    def test_blocked_for_yandex_is_reported(self):
        content = "User-agent: *\nAllow: /\n\nUser-agent: YandexBot\nDisallow: /\n"
        results = _evaluate_robots_txt(content, 200)
        self.assertEqual(_status(results, "robots_txt"), "fail")
        self.assertIn("Яндекс", results[0]["message"])

    def test_no_sitemap(self):
        results = _evaluate_robots_txt("User-agent: *\nDisallow: /admin/\n", 200)
        self.assertEqual(_status(results, "robots_sitemap"), "warning")

    def test_forbidden_robots_means_whole_site_closed(self):
        self.assertEqual(_status(_evaluate_robots_txt("", 403), "robots_txt"), "fail")

    def test_missing_robots_is_warning(self):
        self.assertEqual(_status(_evaluate_robots_txt("", 404), "robots_txt"), "warning")

    def test_server_error_is_warning(self):
        self.assertEqual(_status(_evaluate_robots_txt("", 503), "robots_txt"), "warning")


class RobotsRulesTests(SimpleTestCase):
    """Разбор правил: то, чего не умеет стандартный urllib.robotparser."""

    def check(self, rules, path, agent="*"):
        return parse_robots(rules).check(agent, path)[0]

    def test_wildcard_inside_path(self):
        rules = "User-agent: *\nDisallow: /*?sort="
        self.assertFalse(self.check(rules, "/catalog/?sort=price"))
        self.assertTrue(self.check(rules, "/catalog/"))

    def test_dollar_anchor(self):
        rules = "User-agent: *\nDisallow: /catalog$"
        self.assertFalse(self.check(rules, "/catalog"), "сама страница закрыта")
        self.assertTrue(self.check(rules, "/catalog/sub/"), "вложенные открыты")

    def test_longest_rule_wins(self):
        rules = "User-agent: *\nDisallow: /geo-check/\nAllow: /geo-check/public/"
        self.assertFalse(self.check(rules, "/geo-check/report/abc/"))
        self.assertTrue(self.check(rules, "/geo-check/public/page/"))

    def test_allow_wins_on_equal_length(self):
        rules = "User-agent: *\nDisallow: /page\nAllow: /page"
        self.assertTrue(self.check(rules, "/page"))

    def test_blank_line_does_not_end_group(self):
        """Google группу пустой строкой не обрывает — правило ниже действует."""
        rules = "User-agent: *\nDisallow: /admin/\n\nDisallow: /secret/"
        self.assertFalse(self.check(rules, "/secret/"))

    def test_specific_agent_group_wins_over_star(self):
        rules = "User-agent: *\nDisallow: /\n\nUser-agent: Yandex\nAllow: /\nDisallow: /admin/"
        self.assertTrue(self.check(rules, "/", agent="YandexBot"))
        self.assertFalse(self.check(rules, "/admin/", agent="YandexBot"))
        self.assertFalse(self.check(rules, "/", agent="Googlebot"))

    def test_empty_disallow_means_allowed(self):
        self.assertTrue(self.check("User-agent: *\nDisallow:", "/anything/"))

    def test_comments_ignored(self):
        rules = "User-agent: *  # всем\nDisallow: /admin/  # админка"
        self.assertFalse(self.check(rules, "/admin/"))
        self.assertTrue(self.check(rules, "/"))

    def test_sitemaps_collected(self):
        robots = parse_robots("User-agent: *\nSitemap: https://a.ru/sitemap.xml\n")
        self.assertEqual(robots.sitemaps, ["https://a.ru/sitemap.xml"])


class PageCheckTests(SimpleTestCase):

    def test_normalize_url(self):
        self.assertEqual(_normalize_page_url("example.ru/page/"), "https://example.ru/page/")
        self.assertEqual(_normalize_page_url("example.ru"), "https://example.ru/")
        self.assertEqual(
            _normalize_page_url("https://example.ru/p/?a=1#anchor"),
            "https://example.ru/p/?a=1",
        )

    def test_page_blocked_by_rule_is_explained(self):
        results = _evaluate_page_robots(
            "User-agent: *\nDisallow: /geo-check/report/", 200, "/geo-check/report/tok/"
        )
        self.assertEqual(results[0]["status"], "fail")
        self.assertIn("Disallow: /geo-check/report/", results[0]["message"])

    def test_page_blocked_only_for_yandex(self):
        results = _evaluate_page_robots(
            "User-agent: *\nAllow: /\n\nUser-agent: Yandex\nDisallow: /price/", 200, "/price/"
        )
        self.assertEqual(results[0]["status"], "fail")
        self.assertIn("Яндекс", results[0]["message"])

    def test_page_open(self):
        results = _evaluate_page_robots("User-agent: *\nDisallow: /admin/", 200, "/page/")
        self.assertEqual(results[0]["status"], "ok")

    def test_page_allowed_by_explicit_allow(self):
        results = _evaluate_page_robots(
            "User-agent: *\nDisallow: /geo-check/\nAllow: /geo-check/$", 200, "/geo-check/"
        )
        self.assertEqual(results[0]["status"], "ok")
        self.assertIn("Allow: /geo-check/$", results[0]["message"])

    def test_meta_noindex_detected(self):
        html = '<html><head><meta name="robots" content="noindex, nofollow"></head></html>'
        results = _evaluate_page_html("https://a.ru/p/", 200, {}, html)
        self.assertEqual(_status(results, "page_meta_robots"), "fail")

    def test_x_robots_tag_detected(self):
        results = _evaluate_page_html(
            "https://a.ru/p/", 200, {"x-robots-tag": "noindex"}, "<html></html>"
        )
        self.assertEqual(_status(results, "page_x_robots"), "fail")

    def test_canonical_to_other_page_is_warning(self):
        html = '<html><head><link rel="canonical" href="https://a.ru/other/"></head></html>'
        results = _evaluate_page_html("https://a.ru/p/", 200, {}, html)
        self.assertEqual(_status(results, "page_canonical"), "warning")

    def test_canonical_self_with_www_is_ok(self):
        html = '<html><head><link rel="canonical" href="https://www.a.ru/p/"></head></html>'
        results = _evaluate_page_html("https://a.ru/p/", 200, {}, html)
        self.assertEqual(_status(results, "page_canonical"), "ok")

    def test_404_page(self):
        results = _evaluate_page_html("https://a.ru/p/", 404, {}, "")
        self.assertEqual(_status(results, "page_status"), "fail")


class AiBotsTests(SimpleTestCase):

    def test_all_bots_allowed(self):
        results = _evaluate_ai_bots("User-agent: *\nDisallow: /admin/", 200)
        self.assertEqual(results[0]["status"], "ok")
        self.assertTrue(all(b["allowed"] for b in results[0]["bots"]))

    def test_critical_bot_blocked_is_fail(self):
        results = _evaluate_ai_bots("User-agent: GPTBot\nDisallow: /\n\nUser-agent: OAI-SearchBot\nDisallow: /", 200)
        self.assertEqual(results[0]["status"], "fail")
        self.assertIn("ChatGPT Search", results[0]["message"])

    def test_only_minor_bot_blocked_is_warning(self):
        results = _evaluate_ai_bots("User-agent: CCBot\nDisallow: /", 200)
        self.assertEqual(results[0]["status"], "warning")
        self.assertIn("Common Crawl", results[0]["message"])

    def test_wildcard_rule_blocks_bot_on_page(self):
        """Правило с `*` раньше терялось — теперь ловится и для AI-ботов."""
        results = _evaluate_ai_bots("User-agent: *\nDisallow: /*/private/", 200, "/blog/private/post/")
        self.assertEqual(results[0]["status"], "fail")

    def test_no_robots_gives_no_verdict(self):
        self.assertEqual(_evaluate_ai_bots("", 404), [])


class NetGuardTests(SimpleTestCase):
    """Публичные инструменты не должны ходить во внутреннюю сеть."""

    def test_domain_normalized(self):
        self.assertEqual(normalize_domain("https://example.com/page/?a=1"), "example.com")

    def test_url_normalized(self):
        self.assertEqual(normalize_url("example.com/page"), "https://example.com/page")

    def test_localhost_rejected(self):
        for target in ("localhost", "127.0.0.1", "http://127.0.0.1:8000/admin/"):
            with self.assertRaises(TargetError, msg=target):
                normalize_url(target)

    def test_private_ip_rejected(self):
        for target in ("10.0.0.5", "192.168.1.1", "169.254.169.254"):
            with self.assertRaises(TargetError, msg=target):
                normalize_domain(target)

    def test_empty_and_garbage_rejected(self):
        for target in ("", "   ", "ftp://example.com"):
            with self.assertRaises(TargetError):
                normalize_domain(target)


class SafeGetRedirectTests(SimpleTestCase):
    """
    Проверка на входе бесполезна, если потом идти по редиректам: публичный
    хост отвечает 302 на внутренний адрес. safe_get проверяет каждый хоп.
    """

    def setUp(self):
        # Свой резолвер: тесты не должны зависеть от настоящего DNS.
        # Литеральный IP отдаём как есть, имя — всегда публичный адрес,
        # иначе несуществующий evil.example отсекался бы ещё до редиректа
        # и проверка хопов оставалась бы непроверенной.
        def fake_getaddrinfo(host, *args, **kwargs):
            try:
                ip = str(ipaddress.ip_address(host))
            except ValueError:
                ip = "93.184.216.34"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

        patcher = mock.patch(
            "audits.net_guard.socket.getaddrinfo", side_effect=fake_getaddrinfo
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _response(self, status, location=None, url="https://evil.example/"):
        headers = {"location": location} if location else {}
        return httpx.Response(
            status, headers=headers, request=httpx.Request("GET", url)
        )

    def _patch(self, responses):
        """Подменяет httpx.get цепочкой заранее заданных ответов."""
        calls = []

        def fake_get(url, **kwargs):
            calls.append(str(url))
            return responses[len(calls) - 1]

        patcher = mock.patch("audits.net_guard.httpx.get", side_effect=fake_get)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def test_redirect_to_internal_blocked(self):
        calls = self._patch([
            self._response(302, "http://169.254.169.254/latest/meta-data/"),
        ])
        with self.assertRaises(BlockedTargetError):
            safe_get("https://evil.example/")
        # первый хоп выполнен, второй — уже нет
        self.assertEqual(len(calls), 1)

    def test_redirect_to_localhost_blocked(self):
        self._patch([self._response(302, "http://127.0.0.1:6379/")])
        with self.assertRaises(BlockedTargetError):
            safe_get("https://evil.example/")

    def test_relative_redirect_followed(self):
        calls = self._patch([
            self._response(302, "/next"),
            self._response(200),
        ])
        response = safe_get("https://example.com/start")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls, ["https://example.com/start", "https://example.com/next"])

    def test_redirect_loop_blocked(self):
        self._patch([self._response(302, f"https://example.com/{i}") for i in range(10)])
        with self.assertRaises(BlockedTargetError):
            safe_get("https://example.com/")

    def test_non_http_scheme_blocked(self):
        self._patch([self._response(302, "file:///etc/passwd")])
        with self.assertRaises(BlockedTargetError):
            safe_get("https://evil.example/")

    def test_blocked_error_is_also_httpx_error(self):
        """Чекеры ловят httpx.RequestError — блокировка не должна ронять задачу."""
        self.assertTrue(issubclass(BlockedTargetError, httpx.RequestError))
        self.assertTrue(issubclass(BlockedTargetError, TargetError))

    def test_history_preserved(self):
        self._patch([
            self._response(302, "https://example.com/b"),
            self._response(200),
        ])
        response = safe_get("https://example.com/a")
        self.assertEqual(len(response.history), 1)

    def test_no_follow_does_not_chase_location(self):
        calls = self._patch([self._response(302, "http://127.0.0.1/")])
        response = safe_get("https://example.com/", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(calls), 1)


class SeoHtmlTests(SimpleTestCase):

    GOOD = ('<html><head><title>Что такое GEO и зачем это нужно вашему сайту</title>'
            '<meta name="description" content="' + "Разбираем, что такое generative engine optimization и чем она отличается от классического SEO для владельца сайта." + '">'
            '</head><body><h1>Что такое GEO</h1></body></html>')

    def test_meta_values_returned(self):
        results, meta = evaluate_seo_html(self.GOOD)
        self.assertEqual(meta["h1"], "Что такое GEO")
        self.assertTrue(meta["title"].startswith("Что такое GEO"))
        self.assertEqual(_status(results, "title"), "ok")
        self.assertEqual(_status(results, "description"), "ok")

    def test_missing_tags(self):
        results, meta = evaluate_seo_html("<html><head></head><body></body></html>")
        self.assertEqual(_status(results, "title"), "fail")
        self.assertEqual(_status(results, "description"), "fail")
        self.assertEqual(_status(results, "h1"), "fail")
        self.assertEqual(meta["title"], "")

    def test_short_title_is_warning(self):
        results, _ = evaluate_seo_html("<html><head><title>Главная</title></head></html>")
        self.assertEqual(_status(results, "title"), "warning")

    def test_multiple_h1(self):
        results, _ = evaluate_seo_html("<html><body><h1>Раз</h1><h1>Два</h1></body></html>")
        self.assertEqual(_status(results, "h1"), "warning")
