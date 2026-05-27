"""Spoken-symbol → code formatter for Slumbr's **Code** mode.

Where the prose formatter (``polish.py``) shapes dictation into sentences,
this turns dictation into source code. You speak the symbols and casing you
want and it lays them out with code spacing instead of prose spacing::

    "def foo open paren x close paren colon new line return x"
        ->  def foo(x):
                return x

    "camel case user name equals snake case first name"
        ->  userName = first_name

The engine is intentionally data-driven: the spoken→symbol tables below are
plain dicts so the vocabulary is easy to read, extend, and unit-test. It is
"best-effort" by design — it never tries to be a parser. It:

* maps spoken phrases / single words to literal symbols,
* runs casing commands ("camel case", "snake case", …) over the words that
  follow them,
* applies code spacing rules (no space inside ``foo(x)``; spaces around
  infix operators),
* and does light auto-indentation after a line ending in ``:`` or ``{``.

It deliberately does NOT capitalize sentences or append a trailing period —
that's prose behavior and wrong for code.
"""

from __future__ import annotations

import re

from .polish import clean_base

# --------------------------------------------------------------- vocabulary

# Multi-word spoken phrases → literal symbol. Matched longest-first so
# "less than or equal" wins over "less than". Keys are tuples of the
# lowercased words.
_PHRASES: dict[tuple[str, ...], str] = {
    ("open", "paren"): "(",
    ("left", "paren"): "(",
    ("open", "parenthesis"): "(",
    ("open", "parentheses"): "(",
    ("close", "paren"): ")",
    ("right", "paren"): ")",
    ("close", "parenthesis"): ")",
    ("close", "parentheses"): ")",
    ("open", "bracket"): "[",
    ("left", "bracket"): "[",
    ("open", "square", "bracket"): "[",
    ("close", "bracket"): "]",
    ("right", "bracket"): "]",
    ("close", "square", "bracket"): "]",
    ("open", "brace"): "{",
    ("left", "brace"): "{",
    ("open", "curly"): "{",
    ("open", "curly", "brace"): "{",
    ("close", "brace"): "}",
    ("right", "brace"): "}",
    ("close", "curly"): "}",
    ("close", "curly", "brace"): "}",
    ("open", "angle"): "<",
    ("close", "angle"): ">",
    ("less", "than"): "<",
    ("greater", "than"): ">",
    ("less", "than", "or", "equal"): "<=",
    ("greater", "than", "or", "equal"): ">=",
    ("new", "line"): "\n",
    ("double", "equals"): "==",
    ("double", "equal"): "==",
    ("triple", "equals"): "===",
    ("not", "equals"): "!=",
    ("not", "equal"): "!=",
    ("bang", "equals"): "!=",
    ("fat", "arrow"): "=>",
    ("thin", "arrow"): "->",
    ("double", "quote"): '"',
    ("single", "quote"): "'",
    ("double", "colon"): "::",
    ("plus", "plus"): "++",
    ("minus", "minus"): "--",
    ("plus", "equals"): "+=",
    ("minus", "equals"): "-=",
    ("times", "equals"): "*=",
    ("divide", "equals"): "/=",
    ("and", "and"): "&&",
    ("or", "or"): "||",
    ("forward", "slash"): "/",
    ("back", "slash"): "\\",
    ("at", "sign"): "@",
    ("dollar", "sign"): "$",
    ("pound", "sign"): "#",
    ("hash", "tag"): "#",
    ("question", "mark"): "?",
    ("exclamation", "mark"): "!",
    ("exclamation", "point"): "!",
}

# Casing commands. The value is the casing style; the engine then consumes the
# following plain words (up to the next symbol/command/newline) and joins them.
_CASING: dict[tuple[str, ...], str] = {
    ("camel", "case"): "camel",
    ("pascal", "case"): "pascal",
    ("snake", "case"): "snake",
    ("kebab", "case"): "kebab",
    ("constant", "case"): "constant",
    ("screaming", "snake"): "constant",
    ("screaming", "snake", "case"): "constant",
    ("all", "caps"): "upper",
    ("upper", "case"): "upper",
    ("lower", "case"): "lower",
    ("title", "case"): "title",
}

# Single spoken words → literal symbol.
_WORDS: dict[str, str] = {
    "semicolon": ";",
    "colon": ":",
    "comma": ",",
    "period": ".",
    "dot": ".",
    "equals": "=",
    "equal": "=",
    "plus": "+",
    "minus": "-",
    "dash": "-",
    "star": "*",
    "asterisk": "*",
    "times": "*",
    "slash": "/",
    "backslash": "\\",
    "percent": "%",
    "caret": "^",
    "tilde": "~",
    "underscore": "_",
    "hash": "#",
    "pound": "#",
    "hashtag": "#",
    "octothorpe": "#",
    "at": "@",
    "dollar": "$",
    "ampersand": "&",
    "pipe": "|",
    "bang": "!",
    "exclamation": "!",
    "backtick": "`",
    "tab": "\t",
    "newline": "\n",
    "space": " ",
    "quote": '"',
    "tick": "'",
    "apostrophe": "'",
}

# Characters Whisper may already have emitted as punctuation (e.g. "foo,") —
# we peel them off words and treat them as their own symbol tokens.
_PUNCT_CHARS = set("()[]{},;:.!?=+-*/\\%&|<>#@$~`\"'^")

# Per-symbol spacing glue: (suppress_space_left, suppress_space_right).
_GLUE: dict[str, tuple[bool, bool]] = {
    "(": (True, True),
    "[": (True, True),
    "{": (False, True),
    ")": (True, False),
    "]": (True, False),
    "}": (True, False),
    ";": (True, False),
    ",": (True, False),
    ":": (True, False),
    ".": (True, True),
    "::": (True, True),
    "_": (True, True),
    "#": (False, True),
    "@": (False, True),
    "$": (False, True),
    "!": (False, True),
    "~": (False, True),
}

# Operators that read best with a space on both sides. Everything not in
# ``_GLUE`` and not a quote defaults to spaced-both-sides too.
_INFIX = {
    "=",
    "==",
    "!=",
    "===",
    "<",
    ">",
    "<=",
    ">=",
    "+",
    "-",
    "*",
    "/",
    "%",
    "^",
    "+=",
    "-=",
    "*=",
    "/=",
    "&&",
    "||",
    "->",
    "=>",
    "&",
    "|",
    "++",
    "--",
}

# Keywords that want a space before "(" so we get ``if (x)`` / ``return (y)``
# instead of ``if(x)`` — while ``foo(x)`` stays glued.
_KEYWORDS = {
    "if",
    "elif",
    "else",
    "while",
    "for",
    "return",
    "and",
    "or",
    "not",
    "in",
    "with",
    "assert",
    "del",
    "yield",
    "await",
    "case",
    "switch",
    "raise",
    "import",
    "from",
    "lambda",
    "is",
}

_QUOTES = {'"', "'", "`"}

_TRAILING_TERMINAL = re.compile(r"[.!?]+$")


# ------------------------------------------------------------------- lexing


def _lex(text: str) -> list[str]:
    """Split into lexemes: words keep their original casing; punctuation that
    Whisper attached to a word (``foo,`` / ``(x)``) is peeled into its own
    single-char lexeme."""
    out: list[str] = []
    for tok in text.split():
        lead = 0
        while lead < len(tok) and tok[lead] in _PUNCT_CHARS:
            out.append(tok[lead])
            lead += 1
        core = tok[lead:]
        trail: list[str] = []
        while core and core[-1] in _PUNCT_CHARS:
            trail.append(core[-1])
            core = core[:-1]
        if core:
            out.append(core)
        out.extend(reversed(trail))
    return out


def _match(lexemes: list[str], i: int, table: dict[tuple[str, ...], str]):
    """Longest-first phrase match at position ``i``. Returns (value, length)
    or (None, 0)."""
    for length in (4, 3, 2):
        if i + length <= len(lexemes):
            key = tuple(w.lower() for w in lexemes[i : i + length])
            if key in table:
                return table[key], length
    return None, 0


def _is_command(lexemes: list[str], i: int) -> bool:
    """True if the lexeme(s) at ``i`` start any command or are punctuation —
    i.e. they should STOP a casing run rather than be absorbed into it."""
    lx = lexemes[i]
    if lx in _PUNCT_CHARS:
        return True
    if lx.lower() in _WORDS:
        return True
    if _match(lexemes, i, _PHRASES)[0] is not None:
        return True
    if _match(lexemes, i, _CASING)[0] is not None:
        return True
    return False


def _apply_casing(words: list[str], style: str) -> str:
    parts = [w.lower() for w in words]
    if not parts:
        return ""
    if style == "camel":
        return parts[0] + "".join(w.capitalize() for w in parts[1:])
    if style == "pascal":
        return "".join(w.capitalize() for w in parts)
    if style == "snake":
        return "_".join(parts)
    if style == "kebab":
        return "-".join(parts)
    if style == "constant":
        return "_".join(w.upper() for w in parts)
    if style == "upper":
        return " ".join(w.upper() for w in parts)
    if style == "lower":
        return " ".join(parts)
    if style == "title":
        return " ".join(w.capitalize() for w in parts)
    return " ".join(parts)


# ------------------------------------------------------- units + rendering


def _to_units(lexemes: list[str]) -> list[tuple[str, bool, bool, bool]]:
    """Turn lexemes into render units: (text, no_left, no_right, is_word)."""
    units: list[tuple[str, bool, bool, bool]] = []
    i = 0
    n = len(lexemes)
    while i < n:
        # 1) casing command — consume the following plain words.
        style, length = _match(lexemes, i, _CASING)
        if style is not None:
            i += length
            collected: list[str] = []
            while i < n and not _is_command(lexemes, i):
                collected.append(lexemes[i])
                i += 1
            ident = _apply_casing(collected, style)
            if ident:
                units.append((ident, False, False, True))
            continue

        # 2) multi-word symbol phrase.
        sym, length = _match(lexemes, i, _PHRASES)
        if sym is not None:
            units.append(_symbol_unit(sym))
            i += length
            continue

        # 3) single-word command.
        low = lexemes[i].lower()
        if low in _WORDS:
            units.append(_symbol_unit(_WORDS[low]))
            i += 1
            continue

        # 4) punctuation char Whisper attached.
        if lexemes[i] in _PUNCT_CHARS:
            units.append(_symbol_unit(lexemes[i]))
            i += 1
            continue

        # 5) literal word.
        units.append((lexemes[i], False, False, True))
        i += 1
    return units


def _symbol_unit(sym: str) -> tuple[str, bool, bool, bool]:
    if sym == "\n":
        return ("\n", True, True, False)
    if sym == "\t":
        return ("\t", True, True, False)
    if sym == " ":
        return (" ", True, True, False)
    if sym in _QUOTES:
        # Glue resolved at render time (open vs close).
        return (sym, False, False, False)
    no_left, no_right = _GLUE.get(sym, (False, False) if sym in _INFIX else (False, False))
    return (sym, no_left, no_right, False)


def _render(units: list[tuple[str, bool, bool, bool]]) -> str:
    parts: list[str] = []
    prev_no_right = True  # suppress any leading space at the start
    prev_low = ""  # lowercased previous emitted word
    prev_is_word = False
    line_started = True
    open_quotes: set[str] = set()

    for text, no_left, no_right, is_word in units:
        if text == "\n":
            parts.append("\n")
            prev_no_right = True
            prev_low = ""
            prev_is_word = False
            line_started = True
            continue
        if text == " ":
            if not prev_no_right:
                parts.append(" ")
            prev_no_right = True
            line_started = False
            continue

        # Undo Whisper's sentence-start capitalization on line-initial plain
        # words ("Def foo" -> "def foo"); leave mid-line identifiers alone.
        if is_word and line_started and text[:1].isupper() and text[1:].islower():
            text = text.lower()

        # Quote toggling: opening quote glues to the right, closing to the left.
        if text in _QUOTES:
            if text in open_quotes:
                no_left, no_right = True, False
                open_quotes.discard(text)
            else:
                no_left, no_right = False, True
                open_quotes.add(text)

        # Control-flow keyword wants a space before "(".
        if text == "(" and prev_is_word and prev_low in _KEYWORDS:
            no_left = False

        sep = "" if (prev_no_right or no_left) else " "
        parts.append(sep + text)
        prev_no_right = no_right
        prev_is_word = is_word
        prev_low = text.lower() if is_word else ""
        line_started = False

    return "".join(parts)


def _reindent(text: str) -> str:
    """Best-effort auto-indent: +1 level after a line ending in ``:`` or ``{``;
    a line that starts with a closer drops back a level."""
    level = 0
    out: list[str] = []
    for raw in text.split("\n"):
        s = raw.strip()
        if s[:1] in ("}", ")", "]"):
            level = max(0, level - 1)
        out.append(("    " * level + s) if s else "")
        if s.endswith((":", "{")):
            level += 1
    return "\n".join(out)


def format_code(
    text: str,
    replacements: dict[str, str] | None = None,
    *,
    strip_filler: bool = False,
) -> str:
    """Format a dictated transcript as source code."""
    text = clean_base(text, replacements, strip_filler=strip_filler)
    if not text:
        return text
    # Drop a single sentence-terminal Whisper tacks on the end — explicit
    # "dot"/"period" still produce a literal "." since those are spoken words.
    text = _TRAILING_TERMINAL.sub("", text).rstrip()
    if not text:
        return text
    units = _to_units(_lex(text))
    return _reindent(_render(units))
