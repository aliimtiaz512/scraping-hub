"""The two MyFloridaMarketPlace logins a run can use.

Unlike the RideMetro accounts — where the login decides which supplier network
gets swept — both of these are ordinary MFMP vendor logins over the same
catalogue of ads. The choice is about *whose* account does the searching: two
clients, two registrations, two sets of saved interests. Everything downstream
of the login is identical, which is why this module holds the credential lookup
and none of the flow.

Credentials come from `.env` through `Settings`, with the account's own keys
first and, for Hoope Lab, the original `MFMP_EMAIL`/`MFMP_PASSWORD` keys as a
fallback — so a deployment that predates the account switch keeps running
unchanged and nobody has to re-enter working credentials to upgrade.

Nothing here returns a credential to a caller that has not asked for the account
itself, and nothing sent to the console names the login address. An account is
identified by its label; `describe` gives the UI that label and a `configured`
flag, which is what lets the picker show an unusable account as unavailable
rather than offering a button that fails. The address appears only in the run
log, through `mask`, where knowing which account signed in is worth something.

This mirrors `app/scrapers/ridemetro/accounts.py` deliberately: two portals with
an account switch should not have two different ideas of what an account is.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import ENV_FILE, settings
from app.core import credentials


@dataclass(frozen=True)
class Account:
    """One configured MFMP vendor login."""

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


#: Accounts are named for the client they belong to, not for their position in
#: this dict. "Account 1" tells the person at the dashboard nothing — they know
#: whose bids they are after, not which slot in a config file it occupies — and
#: it goes stale the moment a third is added or the order changes. Hoope Lab is
#: spelled the way `ridemetro/accounts.py` already spells it, so the same client
#: reads the same way wherever it appears.
#:
#: The `.env` keys stay MYFLORIDA_ACC1_*/ACC2_*: they are what is deployed, and
#: renaming a key that already holds a working credential buys nothing.
ACCOUNTS: dict[str, Account] = {
    "hoope_lab": Account(
        key="hoope_lab",
        label="Hoope Lab",
        username_env="MYFLORIDA_ACC1_USERNAME",
        password_env="MYFLORIDA_ACC1_PASSWORD",
        username_field="myflorida_acc1_username",
        password_field="myflorida_acc1_password",
        fallback_username_field="mfmp_email",
        fallback_password_field="mfmp_password",
    ),
    "auston_lucas": Account(
        key="auston_lucas",
        label="Auston Lucas",
        username_env="MYFLORIDA_ACC2_USERNAME",
        password_env="MYFLORIDA_ACC2_PASSWORD",
        username_field="myflorida_acc2_username",
        password_field="myflorida_acc2_password",
    ),
}

#: Used when a run does not name one — the account the scraper used before there
#: was a choice, so an existing caller keeps the behaviour it had.
DEFAULT_ACCOUNT = "hoope_lab"

#: What the keys used to be, when the picker said "Account 1" and "Account 2".
#: Kept so a saved link, a scheduled job or a browser holding the old value
#: keeps working instead of failing with "unknown account".
LEGACY_KEYS: dict[str, str] = {
    "account_1": "hoope_lab",
    "account_2": "auston_lucas",
}


class UnknownAccount(ValueError):
    """The requested account key is not one of the configured accounts."""


class AccountNotConfigured(ValueError):
    """The account exists but its credentials are missing or unusable."""


def get(key: str | None) -> Account:
    """The account for `key`, defaulting when it is blank. Raises UnknownAccount."""
    resolved = (key or "").strip().lower() or DEFAULT_ACCOUNT
    resolved = LEGACY_KEYS.get(resolved, resolved)
    account = ACCOUNTS.get(resolved)
    if account is None:
        raise UnknownAccount(
            f"Unknown MyFlorida account {key!r} — choose one of: {', '.join(ACCOUNTS)}"
        )
    return account


def mask(username: str) -> str:
    """A username with its local part hidden: `ac…@example.com`.

    Enough for a human to tell the two accounts apart in the console and in a
    log, without writing the address itself into either. MFMP's run log is
    streamed to the dashboard, so a full address here would be on screen.
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
    problem rather than later as a portal login failure. On MFMP that matters
    more than most: a bad password gets as far as the one-time password
    challenge, where a person is sitting waiting to type a code for a login that
    was never going to succeed.
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
        portal=f"myflorida/{account.key}",
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
    an account by its label, and the login address is of no use there.
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
