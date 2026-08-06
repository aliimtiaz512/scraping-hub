"""The two RideMetro logins a run can use.

Each account is a separate Bonfire login into a separate Euna Supplier Network,
so the choice decides which agencies a run sweeps — not just who it signs in as.
Everything downstream of the login is identical, which is why this module holds
only the credential lookup and none of the flow.

Credentials come from `.env` through `Settings`, with the account's own keys
first and (for Hoope Lab) the original `RIDEMETRO_*` keys as a fallback, so a
deployment that predates the account switch keeps running unchanged.

Nothing here returns a credential, and nothing sent to the console names the
login address — an account is identified by its label. `describe` gives the UI a
label and a `configured` flag, which is what lets it show an account that cannot
run as unavailable instead of offering a button that fails. The address appears
only in the run log, via `mask`, where knowing which one signed in is useful and
is not on anyone's screen.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import ENV_FILE, settings
from app.core import credentials


@dataclass(frozen=True)
class Account:
    """One configured RideMetro login."""

    key: str                  # the API/UI value, e.g. "hoope_lab"
    label: str                # what the console shows, e.g. "Hoope Lab"
    username_env: str         # the .env key, named in errors so it can be fixed
    password_env: str
    # Where the value is read from on Settings, and the pre-accounts fallback.
    username_field: str
    password_field: str
    fallback_username_field: str | None = None
    fallback_password_field: str | None = None

    @property
    def username(self) -> str:
        value = getattr(settings, self.username_field, "") or ""
        if not value and self.fallback_username_field:
            value = getattr(settings, self.fallback_username_field, "") or ""
        return value

    @property
    def password(self) -> str:
        value = getattr(settings, self.password_field, "") or ""
        if not value and self.fallback_password_field:
            value = getattr(settings, self.fallback_password_field, "") or ""
        return value

    @property
    def is_configured(self) -> bool:
        return bool(self.username and self.password)


ACCOUNTS: dict[str, Account] = {
    "hoope_lab": Account(
        key="hoope_lab",
        label="Hoope Lab",
        username_env="HOOPE_LAB_USERNAME",
        password_env="HOOPE_LAB_PASSWORD",
        username_field="hoope_lab_username",
        password_field="hoope_lab_password",
        fallback_username_field="ridemetro_email",
        fallback_password_field="ridemetro_password",
    ),
    "fedpints": Account(
        key="fedpints",
        label="Fedpints",
        username_env="FEDPINTS_USERNAME",
        password_env="FEDPINTS_PASSWORD",
        username_field="fedpints_username",
        password_field="fedpints_password",
    ),
}

#: Used when a run does not name one — the account the scraper used before there
#: was a choice, so an existing caller keeps the behaviour it had.
DEFAULT_ACCOUNT = "hoope_lab"


class UnknownAccount(ValueError):
    """The requested account key is not one of the configured accounts."""


class AccountNotConfigured(ValueError):
    """The account exists but its credentials are missing or unusable."""


def get(key: str | None) -> Account:
    """The account for `key`, defaulting when it is blank. Raises UnknownAccount."""
    resolved = (key or "").strip().lower() or DEFAULT_ACCOUNT
    account = ACCOUNTS.get(resolved)
    if account is None:
        raise UnknownAccount(
            f"Unknown RideMetro account {key!r} — choose one of: "
            f"{', '.join(ACCOUNTS)}"
        )
    return account


def mask(username: str) -> str:
    """A username with its local part hidden: `Ra…@hoopoelabs.com`.

    Enough for a human to tell the two accounts apart in the console and in a
    log, without writing the address itself into either.
    """
    if not username:
        return "(not set)"
    local, at, domain = username.partition("@")
    if not at:
        return f"{local[:2]}…" if len(local) > 2 else "…"
    return f"{local[:2]}…@{domain}" if len(local) > 2 else f"…@{domain}"


def problems(account: Account) -> list[str]:
    """Why this account cannot run, or an empty list if it can.

    Beyond "is it set", this runs the shared credential check (see
    app/core/credentials), so a password mangled by the `.env` parse — the
    classic being a `#` read as a comment — is reported here as a credential
    problem rather than later as a portal login failure.
    """
    missing = [
        env for env, value in (
            (account.username_env, account.username),
            (account.password_env, account.password),
        ) if not value
    ]
    if missing:
        return [
            f"The {account.label} account is not configured — set "
            f"{' and '.join(missing)} in server/.env"
        ]
    checks = credentials.verify_all(
        {account.username_env: account.username, account.password_env: account.password},
        ENV_FILE,
        portal=f"ridemetro/{account.key}",
    )
    return credentials.problems(checks)


def require(key: str | None) -> Account:
    """The account for `key`, guaranteed usable. Raises if it is not.

    Called both when a run is requested (so the console reports the problem
    before a run exists) and again as the run logs in.
    """
    account = get(key)
    found = problems(account)
    if found:
        raise AccountNotConfigured(" ".join(found))
    return account


def describe(account: Account) -> dict:
    """What the console needs to render the account picker.

    Deliberately no username, not even the masked form: the console identifies
    an account by its label, and the login address is of no use there. It stays
    server-side, in the run log, where knowing which address signed in is worth
    something.
    """
    return {
        "key": account.key,
        "label": account.label,
        "configured": account.is_configured,
        "username_env": account.username_env,
        "password_env": account.password_env,
    }


def catalog() -> list[dict]:
    """Every account, in display order."""
    return [describe(account) for account in ACCOUNTS.values()]
