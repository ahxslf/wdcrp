"""
Standalone unit tests for the detection engine (no Discord needed).

Run:  python test_detectors.py
"""

import types
import time

import detectors
import profanity


THRESHOLDS = types.SimpleNamespace(
    FLOOD_MAX_MESSAGES=6,
    FLOOD_WINDOW_SECONDS=6,
    REPEAT_MSG_COUNT=3,
    REPEAT_MSG_WINDOW_SECONDS=60,
    CHAR_RUN_LIMIT=12,
    LOW_DIVERSITY_MIN_LEN=24,
    LOW_DIVERSITY_MAX_UNIQUE=4,
    WORD_REPEAT_LIMIT=6,
    MAX_EMOJI_PER_MESSAGE=10,
    MAX_SAME_EMOJI=6,
    PROFANITY_SPAM_MIN_SCORE=6,
    PROFANITY_SPAM_MIN_RATIO=0.5,
    PROFANITY_EXTREME_MIN=3,
    PROFANITY_HISTORY_WINDOW=60,
    PROFANITY_HISTORY_MIN_SCORE=12,
    PROFANITY_HISTORY_MIN_MESSAGES=4,
    PROFANITY_STITCH_MAX_GAP=15,
    PROFANITY_STITCH_MIN_LETTERS=4,
)


class Ctx:
    """Fake per-user message context (mirrors what the bot builds)."""

    def __init__(self):
        self.items = []  # (ts, norm, raw, score, count)

    def send(self, text, gap=1.0):
        now = time.time() if not self.items else self.items[-1][0] + gap
        rep = profanity.scan_profanity(text)
        self.items.append((now, detectors.normalize(text), text,
                           rep.score, rep.count))
        return self.analyze(text, now, rep)

    def analyze(self, text, now=None, rep=None):
        now = self.items[-1][0] if now is None else now
        rep = rep or profanity.scan_profanity(text)
        return detectors.analyze(
            text,
            [n for _, n, *_ in self.items],
            [t for t, *_ in self.items],
            THRESHOLDS,
            now,
            raw_history=[(t, r) for t, _, r, *_ in self.items],
            prof_history=[(t, s, c) for t, _, _, s, c in self.items],
            prof_report=rep,
        )


def check(name, cond):
    assert cond, f"FAILED: {name}"
    print(f"  ok - {name}")


def has(dets, cat):
    return any(c == cat for c, _ in dets)


def main():
    print("== invite detection ==")
    check("discord.gg", detectors.find_invite_codes(
        "join https://discord.gg/abc123 now") == ["abc123"])
    check("d.com/invite", detectors.find_invite_codes(
        "discord.com/invite/XYZ-987") == ["XYZ-987"])
    check("no scheme", detectors.find_invite_codes("go to discord.gg/Friends") == ["Friends"])
    check("no invite", detectors.find_invite_codes("hello there") == [])
    check("extract", detectors.extract_code("https://discord.gg/abc123") == "abc123")

    print("== emoji ==")
    check("11 emojis = spam", has(Ctx().send("spam 😀😀😀😀😀🔥🔥🔥🔥🔥💀"),
                                  detectors.CAT_EMOJI_SPAM))
    check("same emoji 7x = spam", has(Ctx().send("yooo 😂😂😂😂😂😂😂"),
                                      detectors.CAT_EMOJI_SPAM))
    check("5 emojis in a message = FINE",
          not Ctx().send("that was so good 😂😂😂😂😂"))
    check("mixed emoji reactions = fine",
          not Ctx().send("nice one bro 😂🔥💯"))
    check("normal emoji ok", not Ctx().send("good game gg 😂"))

    print("== word / char spam ==")
    check("word repeat", has(Ctx().send("spam spam spam spam spam spam spam"),
                             detectors.CAT_WORD_SPAM))
    check("keyboard hold", has(Ctx().send("aaaaaaaaaaaaaaaaaaaa"),
                               detectors.CAT_CHAR_SPAM))
    check("mash wall", has(Ctx().send("asdfasdfasdfasdfasdfasdfasdf"),
                           detectors.CAT_CHAR_SPAM))
    check("normal text ok",
          not Ctx().send("this is a perfectly normal sentence about game balance"))

    print("== char spam: natural speech is fine ==")
    for text in [
        "dogggyyyyy",
        "gooooooooooooooooooooooood",
        "noooooooooooooooo",
        "omg yeeeeeeeees",
        "HAHAHAHAHA",
        "HAHAHAHAHAHAHAHAHAHAHAHAHAHA",
        "ajajajajajajajajaj",
        "adahjadghjgadj",
        "xd xd xd xd xd xd",
        "kehskehskehs asjdhasjkd no way",
        "bruuuuuuuuuuuuuuuuuuuh",
    ]:
        check(f"allowed: {text!r}", not Ctx().send(text))

    print("== flood & repeats ==")
    c = Ctx()
    for i in range(5):
        dets = c.send(f"msg{i}", gap=0.5)
    dets = c.send("one more", gap=0.5)
    check("flood", has(dets, detectors.CAT_FLOOD))

    c2 = Ctx()
    dets2 = None
    for _ in range(3):
        dets2 = c2.send("hello everyone", gap=10)
    check("repeated message", has(dets2, detectors.CAT_REPEAT))

    # ---------------------------------------------------------------
    print("== PROFANITY: normal use is ALWAYS fine ==")
    for text in [
        "fuck that guy lmao",
        "shit this update is fucking terrible honestly",
        "son of a bitch",                    # single extreme — allowed!
        "motherfucker",                      # single extreme — allowed!
        "fuck you",                          # single extreme — allowed!
        "damn that hurt",
        "what the hell is going on here",
    ]:
        check(f"allowed: {text!r}", not Ctx().send(text, gap=30))

    print("== PROFANITY: extreme spam is flagged ==")
    # user's exact example: 3 extreme insults in one message
    check("son of a bitch motherfucker fuck you -> SPAM",
          has(Ctx().send("son of a bitch motherfucker fuck you"),
              detectors.CAT_PROFANITY_SPAM))
    check("profanity wall", has(
        Ctx().send("fuck shit bitch cunt whore slut fuck shit"),
        detectors.CAT_PROFANITY_SPAM))
    check("kys wall", has(Ctx().send("kys kys kys faggot retard nigger"),
                          detectors.CAT_PROFANITY_SPAM))

    print("== PROFANITY: bypass resistance ==")
    scan_cases = [
        ("bittttch", "bitch"),
        ("b i t c h", "bitch"),
        ("b.i.t.c.h", "bitch"),
        ("b-i-t-c-h", "bitch"),
        ("b_i_t_c_h", "bitch"),
        ("b1tch", "bitch"),
        ("B!TCH", "bitch"),
        ("bіtch", "bitch"),        # Cyrillic і
        ("сunt", "cunt"),          # Cyrillic с
        ("fuuuuuck youuu", "fuck you"),
        ("phuck", "fuck"),
        ("fvck", "fuck"),
        ("fuck y0u", "fuck you"),
        ("fuckyou", "fuck you"),
        ("mother fucker", "motherfucker"),
        ("m0th3rfuck3r", "motherfucker"),
        ("$lvt", "slut"),
        ("s0n 0f a b1tch", "son of a bitch"),
        ("k.y.s", "kys"),
        ("f.a.g", "fag"),
    ]
    for text, term in scan_cases:
        rep = profanity.scan_profanity(text)
        check(f"bypass caught: {text!r} -> {term}",
              any(m.label == term for m in rep.matches))

    print("== PROFANITY: no false positives on innocent words ==")
    for text in [
        "class assignment",
        "Scunthorpe United won the match",
        "as soon as possible",
        "shitake mushrooms taste great",
        "pass the ball",
        "Cumberland avenue",
        "can you give me a hand with this",
        "the bass guitar sounds amazing",
        "push it harder",
        "read the docs",
        "peacock drawing",
        "fan club meeting",
    ]:
        rep = profanity.scan_profanity(text)
        check(f"innocent: {text!r}", rep.count == 0)

    print("== PROFANITY: letter-by-letter bypass (one per message) ==")
    c = Ctx()
    dets = None
    for letter in "bitch":  # 'b','i','t','c','h' as 5 separate messages
        dets = c.send(letter, gap=10)   # spaced out -> NOT flooding
    check("b/i/t/c/h stitch caught", any(
        c_ == detectors.CAT_PROFANITY_BYPASS for c_, _ in dets))
    check("stitch reason names the word",
          any("bitch" in r for _, r in dets))

    c = Ctx()
    dets = None
    for letter in "kysok":  # stops at 'o' — run too short / mismatch reset
        dets = c.send(letter, gap=60)   # gaps too big -> no stitch
    check("slow letters not stitched",
          not any(c_ == detectors.CAT_PROFANITY_BYPASS for c_, _ in dets))

    print("== PROFANITY: cross-message flooding ==")
    c = Ctx()
    dets = None
    # each message alone: score 4 (< 6) — but 4 of them inside 60s = 16 >= 12
    for i in range(4):
        dets = c.send("fuck shit bitch what", gap=10)
    check("profanity flood caught",
          any("flood" in r for c_, r in dets
              if c_ == detectors.CAT_PROFANITY_SPAM))

    c = Ctx()
    dets = None
    for i in range(4):
        dets = c.send("fuck", gap=25)  # mild, low score, spaced out
    check("light casual cursing over time is fine",
          not any(c_ == detectors.CAT_PROFANITY_SPAM for c_, _ in dets))

    print("\nALL DETECTOR TESTS PASSED")


if __name__ == "__main__":
    main()
