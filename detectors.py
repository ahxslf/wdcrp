"""
Pure detection logic — no discord.py imports here, so every function can be
unit-tested standalone (see test_detectors.py).

The bot collects a rolling per-user message history and feeds it into
``analyze``, which returns a list of (category, reason) detections in
priority order.
"""

from __future__ import annotations

import re
import time
import unicodedata

import profanity

try:  # optional dependency — much more reliable unicode emoji counting
    import emoji as _emoji_lib
except Exception:  # pragma: no cover - fallback path
    _emoji_lib = None

# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

_ZERO_WIDTH_RE = re.compile(r"[​‌‍⁠﻿︀-️️]")
_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Normalize a message for duplicate comparisons."""
    text = _ZERO_WIDTH_RE.sub("", text)
    text = _WS_RE.sub(" ", text).strip().lower()
    return text


# ---------------------------------------------------------------------------
# Invites
# ---------------------------------------------------------------------------

INVITE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?"
    r"(?:discord\.gg/(?:invite/)?|discord(?:app)?\.com/invite/)"
    r"([A-Za-z0-9-]{2,64})",
    re.IGNORECASE,
)


def find_invite_codes(text: str) -> list[str]:
    """Return invite codes found in a message (deduplicated, order kept)."""
    seen: list[str] = []
    for match in INVITE_RE.finditer(text):
        code = match.group(1)
        if code not in seen:
            seen.append(code)
    return seen


def extract_code(link_or_code: str) -> str:
    """Turn 'https://discord.gg/abc123' (or just 'abc123') into 'abc123'."""
    codes = find_invite_codes(link_or_code)
    if codes:
        return codes[0]
    return link_or_code.strip().rstrip("/").split("/")[-1].strip()


# ---------------------------------------------------------------------------
# Emoji counting (unicode + custom discord emoji)
# ---------------------------------------------------------------------------

_CUSTOM_EMOJI_RE = re.compile(r"<a?:\w+:\d+>")


def _is_emoji_char_fallback(ch: str) -> bool:
    """Crude emoji check used only when the `emoji` package isn't installed.

    Counts chars whose name/category look like emoji/pictographs. Only used
    for threshold detection, so occasional misses are acceptable."""
    if ch in "☺☹♥♦♣♠•◘○◙":
        return True
    try:
        name = unicodedata.name(ch, "")
    except ValueError:
        return False
    if unicodedata.category(ch) != "So":
        return False
    keywords = ("FACE", "HEART", "HAND", "SMILE", "PERSON", "CAT", "DOG",
                "HUNDRED", "FIRE", "STAR", "POOP", "CLAPPING", "PARTY")
    return any(k in name for k in keywords)


def count_emojis(text: str) -> tuple[int, int]:
    """Return (total_emoji_count, max_occurrences_of_a_single_emoji)."""
    counts: dict[str, int] = {}
    if _emoji_lib is not None:
        for token in _emoji_lib.emoji_list(text):
            symbol = token["emoji"]
            counts[symbol] = counts.get(symbol, 0) + 1
    else:  # pragma: no cover - fallback when `emoji` isn't installed
        for ch in text:
            if _is_emoji_char_fallback(ch):
                counts[ch] = counts.get(ch, 0) + 1
    for custom in _CUSTOM_EMOJI_RE.findall(text):
        counts[custom] = counts.get(custom, 0) + 1
    total = sum(counts.values())
    max_same = max(counts.values(), default=0)
    return total, max_same


# ---------------------------------------------------------------------------
# Message-history based detection (flooding / identical repeats)
# ---------------------------------------------------------------------------

def is_flooding(timestamps: list[float], window: float, max_messages: int,
                now: float | None = None) -> bool:
    """True if `max_messages` or more messages were sent inside `window`."""
    now = time.time() if now is None else now
    recent = [t for t in timestamps if now - t <= window]
    return len(recent) >= max_messages


def is_repeated_message(norm_history: list[str], current_norm: str,
                        timestamps: list[float], window: float,
                        count: int, now: float | None = None) -> bool:
    """True if the same normalized message appeared `count` times in window."""
    now = time.time() if now is None else now
    if not current_norm:
        return False
    hits = sum(
        1
        for norm, ts in zip(norm_history, timestamps)
        if norm == current_norm and now - ts <= window
    )
    return hits >= count


# ---------------------------------------------------------------------------
# Single-message pattern detection
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[a-z0-9']+", re.IGNORECASE)

# Short syllables Discord users type when laughing ("haha", "ahah", "ajaj",
# "xd xd", "keke"...). Messages made only of these are NEVER character spam.
_LAUGH_SYLLABLES = (
    "ha", "he", "hi", "ho", "hu", "ah", "eh", "oh", "uh",
    "ja", "je", "ji", "aj", "ej", "ba", "be", "xd", "lo", "ol",
    "ke", "ka", "le", "ra", "re", "ya", "ye", "yo",
)


def _longest_run(s: str) -> int:
    """Length of the longest run of the same character in `s`."""
    best = cur = 0
    prev = None
    for ch in s:
        if ch == prev:
            cur += 1
        else:
            cur, prev = 1, ch
        if cur > best:
            best = cur
    return best


def _looks_like_laughter(s: str) -> bool:
    """True if `s` is just a laugh syllable repeated ("hahahaha", "ajajaj",
    "xd xd xd", possibly truncated at the end like "hahahahah")."""
    if len(s) < 4:
        return False
    for period in (2, 3, 4):
        if len(s) < period * 3:
            continue
        unit = s[:period]
        if unit not in _LAUGH_SYLLABLES:
            continue
        k = len(s) // period
        whole = s[: period * k]
        tail = s[period * k:]
        if whole == unit * k and (not tail or unit.startswith(tail)):
            return True
    return False


def char_spam_reason(text: str, run_limit: int, min_len: int,
                     max_unique: int) -> str | None:
    """Detect REAL character spam while leaving normal speech alone.

    Flagged:
      * keyboard holds   -> "aaaaaaaaaaaaaaaaaaaa"
      * mash walls       -> "asdfasdfasdfasdfasdfasdfasdf"

    Explicitly NOT flagged:
      * stretched words  -> "dogggyyyyy", "gooooooooooooooood", "noooooooo"
      * laughter         -> "HAHAHAHAHA", "ajajajaj", "adahjadghjgadj"
    """
    body = re.sub(r"\s+", "", text).lower()
    if not body:
        return None
    n = len(body)
    unique = len(set(body))
    longest = _longest_run(body)

    # 1) Keyboard-hold / monocord spam.
    #    a) PURE single character, held down: "aaaaaaaaaaaaaaaaaaaa"
    if unique == 1 and longest >= run_limit:
        return f"keyboard-hold spam ({longest}x the same character in a row)"
    #    b) Short lead + long stretch ("noooooo...", "yooooo...") is natural
    #       speech — only flag when it gets *excessively* long (whole message
    #       is basically one letter spam with a token lead).
    if (unique == 2 and n >= 24 and longest >= 16
            and longest >= n * 0.6):
        return f"keyboard-hold spam ({longest}x the same character in a row)"

    # 2) Low-diversity keyboard mash — but laughter is always fine.
    if _looks_like_laughter(body):
        return None
    if n >= min_len and unique <= max_unique and longest < n * 0.6:
        return f"low-diversity character spam ({n} chars, only {unique} unique)"
    return None


# Laugh words that are never "repeated word spam" ("xd xd xd xd xd xd",
# "lol lol lol"... is just how people laugh on Discord).
_LAUGH_WORDS = {
    "xd", "lol", "lul", "lulz", "lmao", "lmfao", "rofl", "mfao",
    "haha", "hahaha", "hahah", "ahaha", "ahahah", "ahha", "hehe", "heehee",
    "hehehe", "hihi", "aja", "ajaj", "aj", "kek", "kekw", "keke", "oml",
    "jaja", "jeje", "hue", "huehue", "oi",
}


def word_spam_reason(text: str, repeat_limit: int) -> str | None:
    """Detect the same word repeated many times in one message
    (laughter words are exempt: 'xd xd xd xd xd xd' is fine)."""
    words = [w.lower() for w in _WORD_RE.findall(text)
             if w.lower() not in _LAUGH_WORDS]
    if not words:
        return None
    counts: dict[str, int] = {}
    for w in words:
        counts[w] = counts.get(w, 0) + 1
    worst = max(counts.values())
    if worst >= repeat_limit:
        offender = max(counts.items(), key=lambda kv: kv[1])[0]
        return f"word spam ({offender!r} repeated {worst} times)"
    return None


# ---------------------------------------------------------------------------
# Profanity — ONLY for spam detection. Normal profanity (even strong words)
# is allowed and never punished. See profanity.py for the full weighted,
# bypass-resistant engine.
# ---------------------------------------------------------------------------

CAT_PROFANITY_SPAM = "Profanity spam"
CAT_PROFANITY_BYPASS = "Profanity bypass"


def profanity_spam_reason(report: "profanity.ProfanityReport", thresholds,
                          terms: str) -> str | None:
    """Single-message rule: extreme density OR several extreme insults."""
    if (report.score >= thresholds.PROFANITY_SPAM_MIN_SCORE
            and report.ratio >= thresholds.PROFANITY_SPAM_MIN_RATIO):
        return (f"extreme profanity spam (score {report.score}, "
                f"{report.count} terms, {report.ratio:.0%} of message: {terms})")
    if report.extreme_count >= thresholds.PROFANITY_EXTREME_MIN:
        return (f"{report.extreme_count}x extreme insults in one message "
                f"({terms})")
    return None


def cross_message_profanity_reason(
        prof_history: list[tuple[float, int, int]], thresholds,
        now: float) -> str | None:
    """Profanity flooding across many messages within the window."""
    window = thresholds.PROFANITY_HISTORY_WINDOW
    recent = [h for h in prof_history if now - h[0] <= window]
    total_score = sum(s for _, s, _ in recent)
    profane_msgs = sum(1 for _, _, c in recent if c > 0)
    if (total_score >= thresholds.PROFANITY_HISTORY_MIN_SCORE
            and profane_msgs >= thresholds.PROFANITY_HISTORY_MIN_MESSAGES):
        return (f"profanity flood across {profane_msgs} messages in "
                f"{window}s (combined score {total_score})")
    return None


def letter_stitch_reason(raw_history: list[tuple[float, str]], thresholds,
                         now: float) -> str | None:
    """One-letter-per-message bypass: 'b','i','t','c','h' as 5 messages."""
    if len(raw_history) < thresholds.PROFANITY_STITCH_MIN_LETTERS:
        return None
    max_gap = thresholds.PROFANITY_STITCH_MAX_GAP
    run: list[str] = []
    prev_ts: float | None = None
    for ts, raw in reversed(raw_history[-12:]):
        ch = raw.strip()
        if len(ch) != 1 or not ch.isalnum():
            break
        if prev_ts is not None and prev_ts - ts > max_gap:
            break
        run.append(ch)
        prev_ts = ts
    if len(run) < thresholds.PROFANITY_STITCH_MIN_LETTERS:
        return None
    joined = "".join(reversed(run))
    spelled = profanity.spell_match(joined)
    if spelled:
        return (f"letter-by-letter profanity bypass (spelled {spelled!r} "
                f"across {len(run)} messages)")
    return None


# ---------------------------------------------------------------------------
# Top-level analyzer
# ---------------------------------------------------------------------------

# Stable category labels used for logging.
CAT_FLOOD = "Message flooding"
CAT_REPEAT = "Repeated messages"
CAT_CHAR_SPAM = "Character spam"
CAT_WORD_SPAM = "Repeated words"
CAT_EMOJI_SPAM = "Emoji spam"


def analyze(
    content: str,
    norm_history: list[str],
    timestamps: list[float],
    thresholds,
    now: float | None = None,
    raw_history: list[tuple[float, str]] | None = None,
    prof_history: list[tuple[float, int, int]] | None = None,
    prof_report: "profanity.ProfanityReport | None" = None,
) -> list[tuple[str, str]]:
    """Analyze one message (+ recent history) and return detections.

    ``thresholds`` is any object exposing the tunables from config.py; the
    bot passes the config module itself, tests pass a simple namespace.
    Returns a list of (category, reason) in priority order.

    History lists must INCLUDE the current message:
      raw_history  — [(ts, raw_content), ...]
      prof_history — [(ts, profanity_score, profanity_count), ...]
    """
    now = time.time() if now is None else now
    detections: list[tuple[str, str]] = []

    # 1) Flooding
    if is_flooding(timestamps, thresholds.FLOOD_WINDOW_SECONDS,
                   thresholds.FLOOD_MAX_MESSAGES, now):
        detections.append((
            CAT_FLOOD,
            f"sent {sum(1 for t in timestamps if now - t <= thresholds.FLOOD_WINDOW_SECONDS)}"
            f" messages in {thresholds.FLOOD_WINDOW_SECONDS}s",
        ))

    current_norm = normalize(content)

    # 2) Repeated identical messages
    if is_repeated_message(norm_history, current_norm, timestamps,
                           thresholds.REPEAT_MSG_WINDOW_SECONDS,
                           thresholds.REPEAT_MSG_COUNT, now):
        detections.append((
            CAT_REPEAT,
            f"same message {thresholds.REPEAT_MSG_COUNT}+ times within "
            f"{thresholds.REPEAT_MSG_WINDOW_SECONDS}s",
        ))

    # 3) Character spam
    reason = char_spam_reason(content, thresholds.CHAR_RUN_LIMIT,
                              thresholds.LOW_DIVERSITY_MIN_LEN,
                              thresholds.LOW_DIVERSITY_MAX_UNIQUE)
    if reason:
        detections.append((CAT_CHAR_SPAM, reason))

    # 4) Repeated words
    reason = word_spam_reason(content, thresholds.WORD_REPEAT_LIMIT)
    if reason:
        detections.append((CAT_WORD_SPAM, reason))

    # 5) Emoji spam
    total, max_same = count_emojis(content)
    if total > thresholds.MAX_EMOJI_PER_MESSAGE:
        detections.append((CAT_EMOJI_SPAM,
                           f"{total} emojis in one message"))
    elif max_same > thresholds.MAX_SAME_EMOJI:
        detections.append((CAT_EMOJI_SPAM,
                           f"same emoji used {max_same} times"))

    # 6) Profanity — single-message "extreme spam" rule
    report = prof_report or profanity.scan_profanity(content)
    terms = ", ".join(dict.fromkeys(m.label for m in report.matches))
    reason = profanity_spam_reason(report, thresholds, terms)
    if reason:
        detections.append((CAT_PROFANITY_SPAM, reason))

    # 7) Profanity flooding across many recent messages
    if prof_history:
        reason = cross_message_profanity_reason(prof_history, thresholds, now)
        if reason:
            detections.append((CAT_PROFANITY_SPAM, reason))

    # 8) Letter-by-letter profanity bypass (one letter per message)
    if raw_history:
        reason = letter_stitch_reason(raw_history, thresholds, now)
        if reason:
            detections.append((CAT_PROFANITY_BYPASS, reason))

    return detections
