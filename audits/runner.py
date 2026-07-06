from audits.checks.technical import check_technical
from audits.checks.indexability import check_indexability
from audits.checks.seo import check_seo
from audits.checks.trust import check_trust
from audits.checks.ai_geo import check_ai_geo
from audits.checks.sitemap_pages import check_sitemap_pages


def run_audit(domain: str) -> dict:
    results = {}

    results["technical"] = check_technical(domain)
    results["indexability"] = check_indexability(domain)
    results["seo"] = check_seo(domain)
    results["trust"] = check_trust(domain)
    results["ai_geo"] = check_ai_geo(domain)

    sitemap_results = check_sitemap_pages(domain)
    if sitemap_results:
        results["sitemap_pages"] = sitemap_results

    return results