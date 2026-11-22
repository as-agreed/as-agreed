"""Quantities in English number words, and the figures a text states.

`from_words("three hundred")` is 300: a value that reads wholly as English number words is read as its number, and
anything else is None. Digits are not words: "300" is None here, because a string of digits is text to tier 1 (an
account or a reference is an identifier), and `commitment.number` already reads the digits a bound declares.

`figures(text)` lists the figures a free text states, in digits and in words, so a gate can fill a figures field from
a draft and let tier 1 compare it with the agreed set: `figures("well over three hundred commitments")` is [300].

Covered: the cardinals zero to the billions ("twenty-one", "one thousand two hundred", "two hundred and seventy-one",
"twelve hundred", "a hundred"). Not covered: ordinals ("first"), fractions, "a dozen", "minus", and vague plurals
("hundreds of"). Stdlib only.
"""
from __future__ import annotations

import re
from typing import Any, List, Optional, Tuple, Union

UNITS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9}
TEENS = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
         "seventeen": 17, "eighteen": 18, "nineteen": 19}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
SCALES = {"thousand": 10 ** 3, "million": 10 ** 6, "billion": 10 ** 9}
VOCABULARY = set(UNITS) | set(TEENS) | set(TENS) | set(SCALES) | {"zero", "hundred", "and", "a"}

_WORDS = re.compile(r"^[a-z]+(?:(?:\s+|\s*-\s*)[a-z]+)*$")
_SPLIT = re.compile(r"[\s-]+")

Figure = Union[int, float, str]


def from_words(value: Any) -> Optional[int]:
    """The number a value states wholly in English number words, or None.

    Case and spacing do not matter, and a hyphen joins as a space does ("twenty-one", "twenty one"). A value with
    anything else in it ("about three", "3 hundred", "300") is None, so only a quantity written out reads as one."""
    if not isinstance(value, str):
        return None
    text = value.strip().casefold()
    if not _WORDS.match(text):
        return None
    tokens = _SPLIT.split(text)
    n, end = _number(tokens, 0)
    return n if n is not None and end == len(tokens) else None


def _below_hundred(tokens: List[str], i: int) -> Tuple[Optional[int], int]:
    """1 to 99 from tokens[i]: a unit, a teen, or a ten with an optional unit."""
    if i >= len(tokens):
        return None, i
    t = tokens[i]
    if t in UNITS:
        return UNITS[t], i + 1
    if t in TEENS:
        return TEENS[t], i + 1
    if t in TENS:
        if i + 1 < len(tokens) and tokens[i + 1] in UNITS:
            return TENS[t] + UNITS[tokens[i + 1]], i + 2
        return TENS[t], i + 1
    return None, i


def _after_and(tokens: List[str], i: int) -> Tuple[int, int]:
    """An optional "and" with the 1 to 99 it joins (British "two hundred and five"); (0, i) when none follows."""
    j = i + 1 if i < len(tokens) and tokens[i] == "and" else i
    rest, k = _below_hundred(tokens, j)
    return (rest, k) if rest is not None else (0, i)


def _below_thousand(tokens: List[str], i: int) -> Tuple[Optional[int], int]:
    """1 to 9999 as one group: "seven", "three hundred", "three hundred and five", "twelve hundred", "a hundred".
    A lone "a" counts as one only directly before "hundred" or a scale word."""
    if i < len(tokens) and tokens[i] == "a":
        if i + 1 < len(tokens) and (tokens[i + 1] == "hundred" or tokens[i + 1] in SCALES):
            head, j = 1, i + 1
        else:
            return None, i
    else:
        head, j = _below_hundred(tokens, i)
        if head is None:
            return None, i
    if j < len(tokens) and tokens[j] == "hundred":
        rest, k = _after_and(tokens, j + 1)
        return head * 100 + rest, k
    return head, j


def _number(tokens: List[str], i: int) -> Tuple[Optional[int], int]:
    """The longest number that starts at tokens[i]: groups under falling scales ("one million two hundred thousand
    five"), or "zero". Returns (None, i) when no number starts there."""
    if i < len(tokens) and tokens[i] == "zero":
        return 0, i + 1
    total, last_scale, j = 0, None, i
    while True:
        group, k = _below_thousand(tokens, j)
        if group is None:
            break
        if k < len(tokens) and tokens[k] in SCALES and (last_scale is None or SCALES[tokens[k]] < last_scale):
            total += group * SCALES[tokens[k]]
            last_scale, j = SCALES[tokens[k]], k + 1
            if j < len(tokens) and tokens[j] == "and":  # "one thousand and five"
                rest, m = _below_hundred(tokens, j + 1)
                if rest is not None:
                    return total + rest, m
            continue
        return total + group, k
    return (total, j) if last_scale is not None else (None, i)


# ---------- figures in free text ----------
URL = re.compile(r"https?://[^\s<>()\"']+", re.ASCII)
_TOKEN = re.compile(
    r"(?P<date>(?<![\w.])\d{4}-\d{2}-\d{2}(?![\w]))"
    r"|(?P<version>(?<![\w.])[vV]\d+(?:\.\d+)+(?!\w)(?!\.\d)|(?<![\w.])\d+(?:\.\d+){2,}(?!\w)(?!\.\d))"
    r"|(?P<percent>(?<![\w.])\d+(?:\.\d+)?%)"
    r"|(?P<num>(?<![\w.])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?!\w)(?!\.\d)(?!,\d{3}))"
    r"|(?P<words>\b[A-Za-z]+(?:(?:\s+|\s*-\s*)[A-Za-z]+)*\b)", re.ASCII)
_RATIO_GAP = re.compile(r"^\s*(?:of|out\s+of|/)\s*$", re.IGNORECASE)


def _digits(text: str) -> Figure:
    """A digit token as a figure: a number where it is one, the text where a leading zero makes it an identifier."""
    plain = text.replace(",", "")
    whole = plain.split(".")[0]
    if len(whole) > 1 and whole.startswith("0"):
        return text
    return float(plain) if "." in plain else int(plain)


def _word_figures(run: str, start: int) -> List[Tuple[int, int, Figure]]:
    """Each number inside a run of words, longest first from the left: "one and two" is 1 and 2, "a new release" none."""
    out = []
    spans = [(m.start() + start, m.end() + start, m.group().casefold()) for m in re.finditer(r"[A-Za-z]+", run)]
    tokens = [s[2] for s in spans]
    i = 0
    while i < len(tokens):
        if tokens[i] not in VOCABULARY:
            i += 1
            continue
        n, j = _number(tokens, i)
        if n is None:
            i += 1
            continue
        out.append((spans[i][0], spans[j - 1][1], n))
        i = j
    return out


def figures(text: Any) -> List[Figure]:
    """The figures a text states, in order, each once: an int or a float for a quantity in digits or in words, and the
    text itself for a date ("2026-11-21"), a version ("0.1.1", read without its "v"), a percent ("45%"), and a ratio
    ("9 of 10", also from "9/10" and "nine out of ten"). A link is not read: its digits are its address."""
    if not isinstance(text, str):
        return []
    bare = URL.sub(lambda m: " " * len(m.group()), text)
    found: List[Tuple[int, int, Figure]] = []
    for m in _TOKEN.finditer(bare):
        kind = m.lastgroup
        if kind == "words":
            found.extend(_word_figures(m.group(), m.start()))
        elif kind == "version":
            found.append((m.start(), m.end(), m.group().lstrip("vV")))
        elif kind == "num":
            found.append((m.start(), m.end(), _digits(m.group())))
        else:
            found.append((m.start(), m.end(), m.group()))
    merged: List[Figure] = []
    i = 0
    while i < len(found):
        s, e, v = found[i]
        if i + 1 < len(found) and _is_quantity(v) and _is_quantity(found[i + 1][2]) \
                and _RATIO_GAP.match(bare[e:found[i + 1][0]]):
            merged.append(f"{_show(v)} of {_show(found[i + 1][2])}")
            i += 2
            continue
        merged.append(v)
        i += 1
    out: List[Figure] = []
    for v in merged:
        if not any(type(v) is type(w) and v == w for w in out):
            out.append(v)
    return out


def _is_quantity(value: Figure) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _show(value: Figure) -> str:
    return repr(value) if isinstance(value, float) else str(value)
