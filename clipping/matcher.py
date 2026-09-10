"""Attribute a post to a marketing campaign using its hashtags."""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Iterable

UNATTRIBUTED = "unattributed"

# \w matches Unicode letters by default for str patterns, so Korean tags such as
# #이유닉 are captured on the same footing as ASCII ones. An [a-z] class would drop them.
_HASHTAG_RE = re.compile(r"#(\w+)")


def normalize(tag: str) -> str:
    """Casefold and NFKC-normalize a tag so '#IUnik' and '＃iunik' compare equal."""
    return unicodedata.normalize("NFKC", tag.lstrip("#")).casefold()


def extract_hashtags(caption: str | None) -> list[str]:
    """Normalized hashtags in caption order, duplicates removed."""
    found: dict[str, None] = {}
    for raw in _HASHTAG_RE.findall(caption or ""):
        found.setdefault(normalize(raw), None)
    return list(found)


@dataclass(frozen=True)
class Campaign:
    id: str
    name: str
    hashtags: frozenset[str]
    start: date
    end: date
    priority: int = 0


def match(hashtags: Iterable[str], taken_on: date, campaigns: Iterable[Campaign]) -> str:
    """Return the id of the best-matching campaign, or UNATTRIBUTED.

    A campaign matches when it shares at least one hashtag with the post and its window
    contains the post date. Ties break on priority, then id, so results are deterministic.
    """
    tags = {normalize(t) for t in hashtags}
    hits = [c for c in campaigns if c.hashtags & tags and c.start <= taken_on <= c.end]
    if not hits:
        return UNATTRIBUTED
    return max(hits, key=lambda c: (c.priority, c.id)).id
