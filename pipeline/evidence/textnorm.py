"""Shared text normalization for dialogue lines and quote queries.

Indexing and querying must normalize identically, so both import this module.
Unicode letters are kept (original-language transcripts stay searchable);
apostrophes are dropped so "don't"/"dont" and "talkin'"/"talking" meet.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

_QUOTES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "`": "'", "“": '"', "”": '"'})
_DROPPED_G = re.compile(r"(\w{2,}in)'(?=\W|$)")     # talkin' -> talking, nothin' -> nothing
_MARKUP = re.compile(r"<[^>]+>|\{[^}]*\}|\[[^\]]*\]|\([A-Z][A-Z .'-]+\)|♪")
_NON_WORD = re.compile(r"[^\w\s]+")


def normalize_line(text: str) -> str:
    """Lowercase words separated by single spaces; subtitle markup, sound tags and punctuation removed."""
    text = unicodedata.normalize("NFKC", str(text or "")).translate(_QUOTES)
    text = _MARKUP.sub(" ", text)
    text = _DROPPED_G.sub(r"\1g", text)
    text = text.casefold().replace("'", "")
    text = _NON_WORD.sub(" ", text).replace("_", " ")
    return " ".join(text.split())


def tokens(text: str) -> list[str]:
    return normalize_line(text).split()


def ordered_match(query: list[str], window: list[str]) -> float:
    """Ordered token overlap in [0, 1]: 1.0 when *window* says exactly *query*.

    Recall of the query's tokens in order, discounted when the window carries
    many extra words, so the line itself beats a long speech that contains it.
    """
    if not query or not window:
        return 0.0
    matched = sum(block.size for block in SequenceMatcher(None, query, window, autojunk=False).get_matching_blocks())
    return (matched / len(query)) * (0.6 + 0.4 * matched / len(window))
