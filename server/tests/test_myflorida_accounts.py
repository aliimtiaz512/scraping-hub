"""MyFlorida's two accounts: resolution, credential routing, and the API gate.

No browser and no portal — what these pin down is that the *right* credentials
reach the login step, that an account which cannot work is refused before a run
exists, and that choosing an account changes nothing else about the flow.

The gate matters more here than on most portals: an MFMP run opens a *visible*
browser and stops at a one-time password for a person to type in. A run started
with credentials that were never going to work does not just waste a process, it
wastes somebody sitting in front of it.

    server/.venv/bin/python -m pytest server/tests/test_myflorida_accounts.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import settings  # noqa: E402
from app.scrapers.myflorida import accounts  # noqa: E402

CREDENTIAL_FIELDS = (
    "myflorida_acc1_username", "myflorida_acc1_password",
    "myflorida_acc2_username", "myflorida_acc2_password",
    "mfmp_email", "mfmp_password",
)


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Both accounts unset, with a setter for the fields under test.

    Patches Settings rather than os.environ: that is where accounts.py reads
    from. `ENV_FILE` is pointed at a path that does not exist for the same
    reason — the credential check compares a loaded value against the literal
    `.env`, so without this every assertion here would depend on whatever the
    developer happens to have in their own file.
    """
    monkeypatch.setattr(accounts, "ENV_FILE", tmp_path / "absent.env")
    for field in CREDENTIAL_FIELDS:
        monkeypatch.setattr(settings, field, "", raising=False)

    def configure(**fields: str) -> None:
        for field, value in fields.items():
            monkeypatch.setattr(settings, field, value, raising=False)

    return configure


# -- resolution --------------------------------------------------------------


def test_both_accounts_are_offered_with_stable_keys():
    """Named for the client, not for a slot in a config file — and Hoope Lab
    is spelled as `ridemetro/accounts.py` already spells it, so one client reads
    the same way on both portals."""
    assert list(accounts.ACCOUNTS) == ["hoope_lab", "auston_lucas"]
    assert [a.label for a in accounts.ACCOUNTS.values()] == ["Hoope Lab", "Auston Lucas"]


def test_a_blank_choice_falls_back_to_the_default_account():
    """An existing caller that names no account keeps the behaviour it had."""
    for value in (None, "", "   "):
        assert accounts.get(value).key == accounts.DEFAULT_ACCOUNT
    assert accounts.DEFAULT_ACCOUNT == "hoope_lab"


def test_the_key_is_matched_regardless_of_case_or_padding():
    assert accounts.get(" Auston_Lucas ").key == "auston_lucas"
    assert accounts.get("HOOPE_LAB").key == "hoope_lab"


def test_an_unknown_account_names_the_valid_choices():
    with pytest.raises(accounts.UnknownAccount) as excinfo:
        accounts.get("nobody")
    assert "hoope_lab" in str(excinfo.value)
    assert "auston_lucas" in str(excinfo.value)


# -- credential routing ------------------------------------------------------


def test_each_account_reads_its_own_env_keys(env):
    """The whole point: the selected account's credentials, and no others."""
    env(
        myflorida_acc1_username="one@example.com", myflorida_acc1_password="pw-one",
        myflorida_acc2_username="two@example.com", myflorida_acc2_password="pw-two",
    )

    first, second = accounts.get("hoope_lab"), accounts.get("auston_lucas")
    assert (first.username, first.password) == ("one@example.com", "pw-one")
    assert (second.username, second.password) == ("two@example.com", "pw-two")


def test_account_one_falls_back_to_the_keys_from_before_the_switch(env):
    """A deployment that predates the account picker keeps running untouched —
    nobody has to re-enter working credentials to take the upgrade."""
    env(mfmp_email="legacy@example.com", mfmp_password="legacy-pw")

    first = accounts.get("hoope_lab")
    assert (first.username, first.password) == ("legacy@example.com", "legacy-pw")
    assert first.is_configured


def test_the_accounts_own_keys_win_over_the_fallback(env):
    env(
        mfmp_email="legacy@example.com", mfmp_password="legacy-pw",
        myflorida_acc1_username="one@example.com", myflorida_acc1_password="pw-one",
    )

    first = accounts.get("hoope_lab")
    assert (first.username, first.password) == ("one@example.com", "pw-one")


def test_auston_lucas_has_no_fallback(env):
    """The `MFMP_*` keys are Hoope Lab's history, not a shared default — routing
    them to the other account would sign in as the wrong client."""
    env(mfmp_email="legacy@example.com", mfmp_password="legacy-pw")

    second = accounts.get("auston_lucas")
    assert second.username == ""
    assert not second.is_configured


def test_the_keys_the_picker_used_to_send_still_resolve():
    """The picker said "Account 1"/"Account 2" before the accounts were named
    for their clients. A saved link or a browser holding the old value should
    keep working rather than fail with "unknown account"."""
    assert accounts.get("account_1").key == "hoope_lab"
    assert accounts.get("account_2").key == "auston_lucas"


def test_an_account_with_only_half_its_credentials_is_not_configured(env):
    env(myflorida_acc2_username="two@example.com")

    assert not accounts.get("auston_lucas").is_configured


# -- the gate ----------------------------------------------------------------


def test_an_unconfigured_account_is_refused_and_names_the_keys_to_set(env):
    with pytest.raises(accounts.AccountNotConfigured) as excinfo:
        accounts.require("auston_lucas")

    message = str(excinfo.value)
    assert "MYFLORIDA_ACC2_USERNAME" in message
    assert "MYFLORIDA_ACC2_PASSWORD" in message
    assert "server/.env" in message


def test_a_configured_account_passes_the_gate(env):
    env(myflorida_acc2_username="two@example.com", myflorida_acc2_password="pw-two")

    assert accounts.require("auston_lucas").key == "auston_lucas"


# -- what reaches the screen -------------------------------------------------


def test_the_picker_is_told_which_accounts_can_actually_run(env):
    env(myflorida_acc1_username="one@example.com", myflorida_acc1_password="pw-one")

    catalog = {entry["key"]: entry for entry in accounts.catalog()}
    assert catalog["hoope_lab"]["configured"] is True
    assert catalog["auston_lucas"]["configured"] is False
    assert catalog["auston_lucas"]["username_env"] == "MYFLORIDA_ACC2_USERNAME"


def test_no_credential_ever_reaches_the_console(env):
    """The console identifies an account by its label; the login address is of
    no use there and a password has no business leaving the server."""
    env(myflorida_acc1_username="one@example.com", myflorida_acc1_password="pw-one")

    served = str(accounts.catalog())
    assert "one@example.com" not in served
    assert "pw-one" not in served


def test_a_masked_username_still_tells_the_two_accounts_apart():
    """It goes to the run log, which is streamed to the dashboard — enough to
    know which account signed in, not enough to be the address."""
    masked = accounts.mask("account2_user@example.com")

    assert masked == "ac…@example.com"
    assert "account2_user" not in masked
    assert accounts.mask("") == "(not set)"


# -- the endpoints -----------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    """The API with the job runner stubbed — nothing here launches a browser."""
    from fastapi.testclient import TestClient

    from app.core import jobs
    from main import app

    monkeypatch.setattr(jobs, "submit", lambda *a, **k: None)
    from app.scrapers.myflorida import router as niche_router
    from app.scrapers.myflorida.sweep import router as sweep_router
    monkeypatch.setattr(niche_router.jobs, "submit", lambda *a, **k: None)
    monkeypatch.setattr(sweep_router.jobs, "submit", lambda *a, **k: None)
    return TestClient(app)


@pytest.mark.parametrize("path", ["/myflorida/accounts", "/myflorida/sweep/accounts"])
def test_both_flows_offer_the_same_accounts(client, path):
    """A sweep signs in through the same form, so it is the same two logins."""
    body = client.get(path).json()

    assert [a["key"] for a in body["accounts"]] == ["hoope_lab", "auston_lucas"]
    assert body["default"] == "hoope_lab"


def test_starting_a_niche_run_records_the_chosen_account(client, env):
    env(myflorida_acc2_username="two@example.com", myflorida_acc2_password="pw-two")
    from app.scrapers.myflorida.commodity_codes import CATEGORIES

    category = next(iter(CATEGORIES))
    response = client.post(
        "/myflorida/scrape",
        json={"category": category, "mode": "codes", "account": "auston_lucas"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["account"] == "auston_lucas"

    from app.core import run_manager

    run = run_manager.get_run(response.json()["run_id"])
    assert run["account"] == "auston_lucas"
    assert run["account_label"] == "Auston Lucas"


def test_starting_a_sweep_records_the_chosen_account(client, env):
    env(myflorida_acc2_username="two@example.com", myflorida_acc2_password="pw-two")

    response = client.post(
        "/myflorida/sweep/scrape",
        json={"ad_statuses": ["open"], "account": "auston_lucas"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["account"] == "auston_lucas"


def test_an_unconfigured_account_is_refused_before_a_run_exists(client, env):
    """503 rather than a run that opens a visible browser and fails at the login
    form with someone sitting there waiting to type a one-time password."""
    from app.scrapers.myflorida.commodity_codes import CATEGORIES

    response = client.post(
        "/myflorida/scrape",
        json={"category": next(iter(CATEGORIES)), "mode": "codes", "account": "auston_lucas"},
    )

    assert response.status_code == 503
    assert "MYFLORIDA_ACC2_USERNAME" in response.json()["detail"]


def test_an_unknown_account_is_a_bad_request(client, env):
    from app.scrapers.myflorida.commodity_codes import CATEGORIES

    response = client.post(
        "/myflorida/scrape",
        json={"category": next(iter(CATEGORIES)), "mode": "codes", "account": "nobody"},
    )

    assert response.status_code == 400
    assert "hoope_lab" in response.json()["detail"]


def test_a_caller_that_names_no_account_still_works(client, env):
    """The endpoint predates the picker; an existing caller keeps working."""
    env(myflorida_acc1_username="one@example.com", myflorida_acc1_password="pw-one")
    from app.scrapers.myflorida.commodity_codes import CATEGORIES

    response = client.post(
        "/myflorida/scrape",
        json={"category": next(iter(CATEGORIES)), "mode": "codes"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["account"] == "hoope_lab"
