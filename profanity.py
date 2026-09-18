"""
Profanity detection engine — WEIGHTED, BYPASS-RESISTANT.

Design goals (per server rules):
  * Profanity itself is ALLOWED. Single words — even harsh ones like
    "motherfucker" or "son of a bitch" — never trigger anything.
  * Only EXTREME profanity spam is flagged: several insults crammed into one
    message, obscene walls of text, profanity flooding across many messages,
    or bypass attempts.

Two severity tiers:
  * weight 1 (mild)   — everyday profanity: "fuck", "shit", "bitch", ...
  * weight 2 (EXTREME) — heavy insults & slurs: "motherfucker", "cunt",
    "son of a bitch", "fuck you", "kys", slurs, ...

A message counts as PROFANITY SPAM when EITHER:
  * weighted score >= PROFANITY_SPAM_MIN_SCORE  AND
    profanity ratio >= PROFANITY_SPAM_MIN_RATIO,        OR
  * it contains >= PROFANITY_EXTREME_MIN *extreme* insults
    (e.g. "son of a bitch motherfucker fuck you" -> 3 extreme -> flagged).

A single "son of a bitch" alone: score 2, extreme count 1 -> NOT flagged.

Bypass resistance — the matcher sees through:
  * repeated letters ......... "bittttch", "fuuuuuck"  (run-collapsing)
  * spaced letters ........... "b i t c h"
  * dotted/dashed letters .... "b.i.t.c.h", "b-i-t-c-h", "f_u_c_k"
  * leetspeak ................ "b1tch", "fuck y0u", "$lvt", "m0therfucker"
  * letter swaps ............. "phuck" (ph->f), "fvck" (v->u), "vvitch"
  * Cyrillic/Greek lookalikes  "bіtch" (Cyrillic і), "сunt" (Cyrillic с)
  * joined phrases ........... "motherfucker", "mother fucker", "fuckyou"
  * one letter per message ... "b" / "i" / "t" / "c" / "h" over 5 messages
    (stitching handled in detectors.py using recent message history)

Editing the lists below instantly changes what the bot sees — no other
code changes needed.
"""

from __future__ import annotations

import re
from typing import NamedTuple

# ===========================================================================
# TERM LISTS — edit freely. weight 1 = mild, weight 2 = extreme.
# ===========================================================================

MILD_TERMS: list[str] = [
    "fuck", "fucking", "fucked", "fuckin", "fucker", "fck", "fuk", "fucc",
    "shit", "shitting", "shitty", "shite", "bullshit",
    "bitch", "bitches", "biatch", "btch",
    "asshole", "arsehole", "ass", "arse",
    "dick", "dicks", "dickhead",
    "pussy", "hoe", "hoes", "whore", "slut", "sluts",
    "bastard", "crap", "damn", "dumbass", "jackass",
    "wanker", "twat", "stfu", "prick", "jerkoff",
]

HEAVY_TERMS: list[str] = [
    # heavy insults
    "motherfucker", "mtherfucker", "mfer", "mf",
    "cunt", "cunts", "cocksucker", "fucktard",
    "son of a bitch", "sonofa bitch",
    "fuck you", "fk you", "fck you", "fuq you",
    # self-harm / threats
    "kys", "kill yourself", "kill urself", "kill yourselfs",
    "neck yourself", "rope yourself", "go die", "drink bleach", "kms",
    # slurs & hate
    "nigger", "nigga", "niglet", "nig nog",
    "faggot", "fag", "fags",
    "retard", "retarded",
    "nazi",
    "rape", "raped", "raping", "rapist",
    "pedo", "pedophile",
]

# ===========================================================================
# TEXT CLEANING PIPELINE
# ===========================================================================

_ZERO_WIDTH_RE = re.compile(r"[​-‍⁠﻿️-️󠄀-󠇿]")

# Cyrillic/Greek lookalikes commonly used to bypass filters -> their Latin
# twins. (Unambiguous, safe to apply globally.)
_CONFUSABLES = {
    # Cyrillic
    "а": "a", "с": "c", "е": "e", "і": "i", "ј": "j", "о": "o", "р": "p",
    "ѕ": "s", "һ": "h", "х": "x", "у": "y", "к": "k", "м": "m", "т": "t",
    "в": "b", "н": "h", "ԁ": "d", "ɡ": "g", "ѵ": "v", "ѡ": "w", "ԝ": "w",
    "з": "3", "ч": "4", "і": "i",
    # Greek
    "α": "a", "ε": "e", "ι": "i", "ο": "o", "ρ": "p", "τ": "t", "υ": "u",
    "χ": "x", "κ": "k", "ν": "v", "η": "n",
    # Fullwidth trickery is handled by NFKC below instead.
}




def _strip_zw(text: str) -> str:
    return _ZERO_WIDTH_RE.sub("", text)


def _map_confusables(text: str) -> str:
    out = []
    for ch in text:
        if ch in _CONFUSABLES:
            out.append(_CONFUSABLES[ch])
        elif "＀" <= ch <= "￯":  # fullwidth ASCII -> ascii
            code = ord(ch) - 0xFEE0
            out.append(chr(code) if 33 <= code <= 126 else ch)
        else:
            out.append(ch)
    return "".join(out)


def clean_variants(text: str) -> list[str]:
    """Produce normalized copies of `text` the term matcher runs on.

    - lowercases, strips zero-width chars, folds Cyrillic/Greek/fullwidth
      lookalikes, applies common letter-swap rewrites (vv->w, ph->f).
      Repeated letters ("bittttch") are handled by the term regexes
      themselves (every letter class is quantified), so no collapsing
      happens here — important so terms with legit double letters
      ("ass", "pussy", "nigger") keep full-length patterns and don't
      false-positive on words like "as" or "pus".
    """
    import unicodedata

    text = unicodedata.normalize("NFKC", text)
    text = _strip_zw(text).lower()
    text = _map_confusables(text)
    text = text.replace("vv", "w")
    variants = [text, text.replace("ph", "f")]
    return list(dict.fromkeys(variants))


# ===========================================================================
# TERM MATCHING — leet-tolerant char classes + flexible separators
# ===========================================================================

# Every letter position accepts its leet lookalikes. Both `o` and `u`
# accept "0" on purpose ("f0ck" reads as "fuck"), `u` also accepts "v"
# ("fvck"), etc.
_LETTERS: dict[str, str] = {
    "a": "a@4",
    "b": "b8",
    "c": "c([{",
    "d": "d",
    "e": "e3€",
    "f": "f",
    "g": "g96",
    "h": "h#",
    "i": "i1!|l",
    "j": "j",
    "k": "k",
    "l": "l1|i",
    "m": "m",
    "n": "n",
    "o": "o0",
    "p": "p",
    "q": "q",
    "r": "r",
    "s": "s5$z",
    "t": "t7+",
    "u": "uv0",
    "v": "vu",
    "w": "w",
    "x": "x",
    "y": "y",
    "z": "z2",
}


def _term_regex(term: str) -> re.Pattern:
    """Build a bypass-resistant regex for one term.

    - between any two letters any non-alphanumeric garbage is allowed:
      spaces ("b i t c h"), dots ("b.i.t.c.h"), dashes, underscores, stars...
    - each letter accepts its leet lookalikes ("b1tch", "fuck y0u")
    - every letter class is "+"-quantified, so elongated spellings
      ("bittttch", "fuuuuuck") match the plain term
    - hard boundaries keep inside-word false positives out: "Scunthorpe"
      does NOT match "cunt", "class" does NOT match "ass".
    """
    letters = [c for c in term.lower() if c.isalnum()]
    body = r"[\W_]*".join(
        "[%s]+" % re.escape(_LETTERS.get(c, c)) for c in letters
    )
    return re.compile(r"(?<![a-z0-9])" + body + r"(?![a-z0-9])")


def _build_terms() -> list[tuple[str, int, re.Pattern]]:
    terms: list[tuple[str, int, re.Pattern]] = []
    for label in MILD_TERMS:
        terms.append((label, 1, _term_regex(label)))
    for label in HEAVY_TERMS:
        terms.append((label, 2, _term_regex(label)))
    # Longest first so "motherfucker" consumes its letters before "fuck".
    letters_of = lambda t: sum(1 for c in t[0] if c.isalnum())
    terms.sort(key=letters_of, reverse=True)
    return terms


_TERMS = _build_terms()


class ProfanityMatch(NamedTuple):
    label: str
    weight: int
    matched_text: str


class ProfanityReport(NamedTuple):
    matches: list[ProfanityMatch]
    score: int          # total weighted score
    count: int          # number of matches
    extreme_count: int  # matches with weight >= 2
    words: int          # total words in the message
    ratio: float        # count / words

    @property
    def has_profanity(self) -> bool:
        return self.count > 0


_WORD_RE = re.compile(r"[a-z0-9']+")


def scan_profanity(text: str) -> ProfanityReport:
    """Scan a message, returning every profanity match (bypass-aware)."""
    if not text:
        return ProfanityReport([], 0, 0, 0, 0, 0.0)

    accepted: list[ProfanityMatch] = []
    spans_by_variant: list[list[tuple[int, int]]] = []

    for variant in clean_variants(text):
        occupied: list[tuple[int, int]] = []
        for label, weight, pattern in _TERMS:
            for m in pattern.finditer(variant):
                span = (m.start(), m.end())
                if any(not (span[1] <= o[0] or span[0] >= o[1])
                       for o in occupied):
                    continue
                occupied.append(span)
                # cross-variant de-dup: identical match on the same surface
                if not any(a.matched_text == m.group(0) and a.label == label
                           for a in accepted):
                    accepted.append(ProfanityMatch(label, weight, m.group(0)))
        spans_by_variant.append(occupied)

    words = max(len(_WORD_RE.findall(clean_variants(text)[0])), 1)
    count = len(accepted)
    score = sum(m.weight for m in accepted)
    extreme = sum(1 for m in accepted if m.weight >= 2)
    return ProfanityReport(accepted, score, count, extreme, words,
                           round(count / words, 3))


def spell_match(joined: str) -> str | None:
    """Given concatenated single letters (e.g. 'bitch' from 5 messages),
    return the profanity term it spells, or None."""
    for variant in clean_variants(joined):
        compact = re.sub(r"[\W_]+", "", variant)
        if not compact:
            continue
        for label, _weight, pattern in _TERMS:
            m = pattern.search(compact)
            if m:
                return label
    return None
