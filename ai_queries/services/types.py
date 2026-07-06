from __future__ import annotations
from dataclasses import dataclass
from typing import List


@dataclass
class PageData:
    url: str
    title: str
    description: str
    h1: str
    h2: List[str]
    text: str