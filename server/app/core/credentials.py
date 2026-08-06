"""Check a credential arrived intact before it is used to log in.

A password full of punctuation can be corrupted between the `.env` file and the
login form without anything looking wrong: an unquoted `#` after a space starts
a comment and the rest of the value is dropped, `${...}` is expanded away, and a
value already exported in the environment silently wins over the file (python-
dotenv does not override by default). Every one of those surfaces as "the portal
rejected our login", which sends you looking at the portal instead of at the
file — and on a real vendor account, repeated failed logins risk a lockout.

So before authenticating we compare what the application loaded against the
literal text of the `.env` line, and refuse to try the login when they disagree.

Nothing here logs a credential. A value is described by its `fingerprint` —
length, character-class counts, and a short SHA-256 prefix. Two fingerprints
being equal means the strings are equal; neither reveals the string. The digest
is truncated to 8 hex characters deliberately: enough to compare two values in a
log, far too little to attack.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Values that mean "nobody filled this in" — the placeholders in .env.example and
# the vendored engine's fallback, all of which would otherwise be typed into a
# real login form.
PLACEHOLDERS = {
    "your_password", "your_password_here", "your_email@example.com",
    "your_email_here", "changeme", "xxx",
}


def fingerprint(value: str) -> str:
    """A non-sensitive description of `value`: length, character mix, digest."""
    if not value:
        return "len=0 (empty)"
    letters = sum(c.isalpha() for c in value)
    digits = sum(c.isdigit() for c in value)
    spaces = sum(c.isspace() for c in value)
    symbols = len(value) - letters - digits - spaces
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return (
        f"len={len(value)} letters={letters} digits={digits} "
        f"symbols={symbols} spaces={spaces} sha8={digest}"
    )


@dataclass
class CredentialCheck:
    """The verdict on one credential."""

    name: str
    loaded: str
    file_value: str | None = None      # the literal text in .env, or None if absent
    errors: list[str] = field(default_factory=list)    # do not attempt the login
    warnings: list[str] = field(default_factory=list)  # suspicious, but proceed

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        return f"{self.name}: {fingerprint(self.loaded)}"


# `KEY=value`, ignoring blank lines, comment lines, and an `export ` prefix.
_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def read_env_literal(name: str, env_path: Path) -> str | None:
    """The value of `name` in `env_path`, read literally.

    A deliberately naive reader: it unwraps one layer of matching quotes and
    otherwise takes the line at its word — no comment stripping, no variable
    expansion. That is the point. Comparing this against what the application
    loaded is what turns a silently truncated password into a reported one.

    The last assignment wins, matching how the parsers treat a repeated key.
    """
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return None

    found: str | None = None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _LINE.match(line)
        if not match or match.group(1) != name:
            continue
        value = match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        found = value
    return found


def verify(name: str, loaded: str, env_path: Path | None = None) -> CredentialCheck:
    """Check one loaded credential against the `.env` file it came from."""
    check = CredentialCheck(name=name, loaded=loaded)

    if not loaded:
        check.errors.append(f"{name} is empty — set it in server/.env")
        return check
    if loaded.strip().lower() in PLACEHOLDERS:
        check.errors.append(
            f"{name} is still the example placeholder — set the real value in server/.env"
        )
        return check

    if env_path is not None:
        check.file_value = read_env_literal(name, env_path)
    literal = check.file_value

    # Only compare when the file actually supplies a value. A key that is absent
    # — or present but blank, the shape of a placeholder line like `FOO=` — is
    # not this credential's source: a real environment variable outranks the file
    # in both pydantic-settings and python-dotenv, and injecting secrets that way
    # beside a .env holding everything else is a normal deployment. Comparing
    # against a blank line would reject exactly that setup.
    if literal and literal != loaded:
        if loaded == literal[: len(loaded)] and len(loaded) < len(literal):
            # The classic symptom: everything from some character onwards is gone.
            cut = literal[len(loaded):][:1]
            check.errors.append(
                f"{name} was truncated on load: {len(literal)} characters in "
                f"server/.env, {len(loaded)} loaded — it stops at {cut!r}. "
                f"Wrap the value in single quotes: {name}='...'"
            )
        elif "${" in literal:
            # No quoting style prevents this one — python-dotenv resolves ${...}
            # in single-quoted values too.
            check.errors.append(
                f"{name} contains `${{...}}`, which python-dotenv expands whatever "
                f"the quoting: the file holds [{fingerprint(literal)}], the app "
                f"loaded [{fingerprint(loaded)}]. Pass this credential as a real "
                f"environment variable instead of through server/.env."
            )
        else:
            check.errors.append(
                f"{name} does not match server/.env: the file holds "
                f"[{fingerprint(literal)}], the app loaded [{fingerprint(loaded)}]. "
                f"Either something in the environment is shadowing the file, or "
                f"the value needs single-quoting: {name}='...'"
            )
        return check

    # The value is right; these are the things that are usually a mistake but
    # could legitimately be part of a password, so they do not block the login.
    if loaded != loaded.strip():
        check.warnings.append(
            f"{name} has leading/trailing whitespace — quote it if that is intended"
        )
    if len(loaded) >= 2 and loaded[0] == loaded[-1] and loaded[0] in ("'", '"'):
        check.warnings.append(
            f"{name} still has surrounding {loaded[0]} quotes in the loaded value — "
            f"it is probably double-quoted in server/.env"
        )
    return check


def verify_all(
    credentials: dict[str, str],
    env_path: Path | None = None,
    portal: str = "",
) -> list[CredentialCheck]:
    """Verify several credentials, logging one non-sensitive line for each.

    Returns every check so the caller can decide what a failure means — the
    scrapers refuse to attempt a login, rather than burning an attempt on a
    credential already known to be wrong.
    """
    label = f"[{portal}] " if portal else ""
    checks = [verify(name, value or "", env_path) for name, value in credentials.items()]
    for check in checks:
        logger.info("%scredential %s", label, check.summary())
        for warning in check.warnings:
            logger.warning("%s%s", label, warning)
        for error in check.errors:
            logger.error("%s%s", label, error)
    return checks


def problems(checks: list[CredentialCheck]) -> list[str]:
    """Every blocking error across `checks`, flattened."""
    return [error for check in checks for error in check.errors]
