from dataclasses import dataclass
from typing import Any, Dict

@dataclass
class CheckItem:
    status: str
    details: Dict[str, Any]