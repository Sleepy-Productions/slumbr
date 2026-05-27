"""Tests for the Code-mode spoken-symbol formatter — slumbr/code_grammar.py.

This is the validation gate for Code mode: each row asserts that a dictated
phrase lands as the intended source. The engine is best-effort (not a parser),
so these lock in the contract for symbols, casing commands, code spacing, the
no-trailing-period rule, and light auto-indentation.
"""

import pytest

from slumbr.code_grammar import format_code

CASES = [
    # --- literal symbols ---
    ("open paren", "("),
    ("close paren", ")"),
    ("open brace", "{"),
    ("semicolon", ";"),
    ("comma", ","),
    # --- code spacing: glue calls/indexing, no inner spaces ---
    ("foo open paren bar close paren", "foo(bar)"),
    ("arr open bracket i close bracket", "arr[i]"),
    ("x dot length", "x.length"),
    # --- control-flow keyword keeps a space before "(" ---
    ("if open paren x close paren", "if (x)"),
    # --- infix operators are spaced both sides ---
    ("a equals b plus c", "a = b + c"),
    ("i plus equals one", "i += one"),
    # --- casing commands ---
    ("camel case user name", "userName"),
    ("snake case user name", "user_name"),
    ("pascal case user name", "UserName"),
    ("kebab case main nav", "main-nav"),
    ("constant case max size", "MAX_SIZE"),
    ("camel case user name equals snake case first name", "userName = first_name"),
    # --- structural ---
    ("new line", "\n"),
    # --- prose artifacts cleaned: no auto-capitalize, no forced period ---
    ("hello world.", "hello world"),
    # --- the headline example: full statement with auto-indent ---
    (
        "def foo open paren x close paren colon new line return x",
        "def foo(x):\n    return x",
    ),
]


@pytest.mark.parametrize("spoken,expected", CASES)
def test_format_code(spoken: str, expected: str) -> None:
    assert format_code(spoken) == expected


def test_no_trailing_period_added() -> None:
    assert not format_code("return x").endswith(".")


def test_de_capitalizes_line_initial_word() -> None:
    # Whisper capitalizes the first word of an utterance; code mode undoes it.
    assert format_code("Def foo") == "def foo"


def test_camelcase_identifier_survives_line_start() -> None:
    # The de-cap heuristic must not flatten an intentional camelCase ident.
    assert format_code("camel case my Var") == "myVar"


def test_empty_input_is_empty() -> None:
    assert format_code("") == ""
    assert format_code("   ") == ""


def test_word_replacements_apply_before_grammar() -> None:
    # Replacements run in clean_base, so a mishear can be corrected first.
    assert format_code("foo bar", {"foo": "baz"}) == "baz bar"
