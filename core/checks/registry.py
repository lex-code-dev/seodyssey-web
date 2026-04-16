from .http import HttpCheck
from .dns import DnsCheck
from .ssl import SslCheck
from .metrics import MetricsCheck
from .domain import WhoisXmlDomainCheck
from .webmaster import WebmasterDiagnosticsCheck

CHECKS_PIPELINE = [
    HttpCheck(),
    DnsCheck(),
    SslCheck(),
    WhoisXmlDomainCheck(),
    MetricsCheck(),
    WebmasterDiagnosticsCheck(),
]