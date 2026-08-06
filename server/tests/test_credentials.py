"""Credential parse verification (app/core/credentials).

Every case here is a way a password with punctuation in it can arrive at a login
form already broken. They are written against the real parser — each test writes
a .env, loads it the way the application does, and checks that the verifier
either passes it or names what went wrong.

    server/.venv/bin/python -m pytest server/tests/test_credentials.py
"""

import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import dotenv_values  # noqa: E402

from app.core import credentials  # noqa: E402

# A password of the shape that started this: symbols throughout, including the
# two characters that break .env parsing.
SECRET = "Abcdef$Ghijklm1n%#opq^2r"


def _env(line: str, directory: Path) -> Path:
    path = directory / ".env"
    path.write_text(line + "\n", encoding="utf-8")
    return path


def _load(path: Path, key: str = "UNISON_PASSWORD") -> str:
    """What the application would end up with, through the real parser."""
    return dotenv_values(path).get(key) or ""


# -- the fix: single quotes survive every reader ------------------------------


def test_single_quoted_value_loads_whole_and_verifies():
    with TemporaryDirectory() as tmp:
        path = _env(f"UNISON_PASSWORD='{SECRET}'", Path(tmp))
        loaded = _load(path)
        assert loaded == SECRET
        check = credentials.verify("UNISON_PASSWORD", loaded, path)
        assert check.ok
        assert not check.warnings


def test_single_quotes_protect_a_hash_a_bare_dollar_and_a_backslash():
    """The three things that actually break a password in this file."""
    with TemporaryDirectory() as tmp:
        directory = Path(tmp)
        for secret in ("word% #hash", "pass$WORD%#1", r"back\slash\#1"):
            path = _env(f"UNISON_PASSWORD='{secret}'", directory)
            assert _load(path) == secret, secret
            assert credentials.verify("UNISON_PASSWORD", _load(path), path).ok


def test_double_quotes_mangle_a_backslash_where_single_quotes_do_not():
    r"""python-dotenv reads escape sequences inside double quotes: `\b` becomes a
    backspace. The usual "just use double quotes" advice is wrong for a password
    with a backslash in it — which is why this project standardises on single."""
    secret = r"back\bslash"
    with TemporaryDirectory() as tmp:
        directory = Path(tmp)
        assert _load(_env(f'UNISON_PASSWORD="{secret}"', directory)) != secret
        assert _load(_env(f"UNISON_PASSWORD='{secret}'", directory)) == secret


def test_no_quoting_protects_a_braced_variable_reference():
    """`${...}` is expanded whatever the quoting — single quotes included. There
    is no way to write one in this file, so the verifier has to catch it."""
    with TemporaryDirectory() as tmp:
        directory = Path(tmp)
        os.environ.pop("SOME_UNSET_NAME", None)
        secret = "pass${SOME_UNSET_NAME}word"
        for line in (
            f"UNISON_PASSWORD={secret}",
            f'UNISON_PASSWORD="{secret}"',
            f"UNISON_PASSWORD='{secret}'",
        ):
            path = _env(line, directory)
            assert _load(path) == "password"
            assert not credentials.verify("UNISON_PASSWORD", _load(path), path).ok


# -- the failure modes it has to catch ---------------------------------------


def test_a_hash_after_whitespace_truncates_and_is_reported_as_truncation():
    secret = "Abcdef$Ghij% #opq^2r"  # space before the #
    with TemporaryDirectory() as tmp:
        path = _env(f"UNISON_PASSWORD={secret}", Path(tmp))
        loaded = _load(path)
        assert loaded != secret  # the parser really does cut it
        check = credentials.verify("UNISON_PASSWORD", loaded, path)
        assert not check.ok
        assert "truncated on load" in check.errors[0]
        assert "single quotes" in check.errors[0]


def test_a_braced_variable_is_expanded_away_and_reported_as_a_mismatch():
    with TemporaryDirectory() as tmp:
        os.environ.pop("SOME_UNSET_NAME", None)
        path = _env("UNISON_PASSWORD=pass${SOME_UNSET_NAME}word", Path(tmp))
        loaded = _load(path)
        assert loaded == "password"  # the reference vanished
        check = credentials.verify("UNISON_PASSWORD", loaded, path)
        assert not check.ok
        # Named for what it is, with the only real remedy: no quoting fixes it.
        assert "${...}" in check.errors[0]
        assert "real environment variable" in check.errors[0]


def test_an_empty_credential_is_refused():
    with TemporaryDirectory() as tmp:
        path = _env("UNISON_PASSWORD=", Path(tmp))
        check = credentials.verify("UNISON_PASSWORD", _load(path), path)
        assert not check.ok
        assert "is empty" in check.errors[0]


def test_the_example_placeholder_is_refused():
    """The vendored engine types this into the form when the variable is unset."""
    check = credentials.verify("UNISON_PASSWORD", "your_password")
    assert not check.ok
    assert "placeholder" in check.errors[0]


def test_a_stale_environment_value_shadowing_the_file_is_caught():
    """python-dotenv does not override what is already exported."""
    with TemporaryDirectory() as tmp:
        path = _env(f"UNISON_PASSWORD='{SECRET}'", Path(tmp))
        check = credentials.verify("UNISON_PASSWORD", "an-old-password", path)
        assert not check.ok
        assert "does not match" in check.errors[0]


def test_surviving_quotes_and_stray_whitespace_warn_without_blocking():
    """Suspicious, but a password may legitimately contain them — so proceed."""
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / ".env"
        path.write_text('UNISON_PASSWORD=\'"quoted"\'\n', encoding="utf-8")
        check = credentials.verify("UNISON_PASSWORD", _load(path), path)
        assert check.ok
        assert any("surrounding" in w for w in check.warnings)


def test_a_missing_env_file_does_not_block_a_credential_from_elsewhere():
    """Deployments that inject credentials as real environment variables have no
    .env to compare against; that is not an error."""
    check = credentials.verify("UNISON_PASSWORD", SECRET, Path("/nonexistent/.env"))
    assert check.ok
    assert check.file_value is None


def test_a_key_absent_from_the_env_file_is_not_a_mismatch():
    """Same deployment, but the file exists and holds other keys."""
    with TemporaryDirectory() as tmp:
        path = _env("SOMETHING_ELSE=x", Path(tmp))
        assert credentials.verify("UNISON_PASSWORD", SECRET, path).ok


def test_a_blank_placeholder_line_does_not_override_a_real_environment_value():
    """`UNISON_PASSWORD=` in the file is an unfilled placeholder, not the source.
    A real environment variable outranks the file in both pydantic-settings and
    python-dotenv, so a value from there must not read as a mismatch."""
    with TemporaryDirectory() as tmp:
        path = _env("UNISON_PASSWORD=", Path(tmp))
        assert credentials.verify("UNISON_PASSWORD", SECRET, path).ok


# -- reading the file literally ----------------------------------------------


def test_read_env_literal_unwraps_one_layer_of_quotes():
    with TemporaryDirectory() as tmp:
        directory = Path(tmp)
        assert credentials.read_env_literal("P", _env(f"P='{SECRET}'", directory)) == SECRET
        assert credentials.read_env_literal("P", _env(f'P="{SECRET}"', directory)) == SECRET
        assert credentials.read_env_literal("P", _env(f"P={SECRET}", directory)) == SECRET


def test_read_env_literal_keeps_hashes_and_dollars_verbatim():
    """The whole point: this reader must not strip comments or expand anything."""
    with TemporaryDirectory() as tmp:
        path = _env("P=keep$THIS% #and-this", Path(tmp))
        assert credentials.read_env_literal("P", path) == "keep$THIS% #and-this"


def test_read_env_literal_skips_comments_handles_export_and_takes_the_last():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / ".env"
        path.write_text(
            "# P=commented-out\n\nexport P=first\nOTHER=x\nP=second\n", encoding="utf-8"
        )
        assert credentials.read_env_literal("P", path) == "second"
        assert credentials.read_env_literal("MISSING", path) is None


# -- the fingerprint is a description, not a leak -----------------------------


def test_fingerprint_describes_without_revealing():
    printed = credentials.fingerprint(SECRET)
    assert "len=24" in printed
    assert "letters=18" in printed and "digits=2" in printed and "symbols=4" in printed
    for fragment in (SECRET, SECRET[:6], "Abcdef"):
        assert fragment not in printed


def test_equal_values_fingerprint_equal_and_a_one_character_change_does_not():
    assert credentials.fingerprint(SECRET) == credentials.fingerprint(SECRET)
    assert credentials.fingerprint(SECRET) != credentials.fingerprint(SECRET[:-1])
    assert credentials.fingerprint("") == "len=0 (empty)"


def test_verify_all_reports_every_credential_and_flattens_the_failures():
    with TemporaryDirectory() as tmp:
        path = _env(f"UNISON_PASSWORD='{SECRET}'", Path(tmp))
        checks = credentials.verify_all(
            {"UNISON_EMAIL": "", "UNISON_PASSWORD": SECRET}, path, portal="unison"
        )
        assert len(checks) == 2
        failures = credentials.problems(checks)
        assert len(failures) == 1 and "UNISON_EMAIL is empty" in failures[0]
