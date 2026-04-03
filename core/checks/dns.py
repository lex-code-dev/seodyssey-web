import socket
from typing import Optional, Tuple


class DnsCheck:
    name = "dns"

    def run(self, *, domain: str) -> Tuple[bool, Optional[str]]:
        try:
            socket.getaddrinfo(domain, 443)
            return True, None
        except Exception as exc:
            return False, str(exc)