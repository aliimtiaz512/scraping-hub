import tempfile
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

SERVER_ROOT = Path(__file__).resolve().parent.parent
# The one .env every loader in the project reads: pydantic-settings below, and
# app/core/credentials.py, which re-reads it literally to confirm a credential
# survived the parse.
ENV_FILE = SERVER_ROOT / ".env"


class Settings(BaseSettings):
    # Static portal URLs (login pages, portal endpoints) live here as defaults —
    # they are identical across deployments, so this file is their single source
    # of truth and they are intentionally NOT listed in .env / .env.example.
    # Only secrets and per-deployment values (credentials, DATABASE_URL, AWS_*,
    # PUBLIC_BASE_URL) belong in .env. A field can still be overridden by an env
    # var of the same name if a specific deployment ever needs to.
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # MyFloridaMarketPlace. Two vendor logins, chosen per run — same flow either
    # way, so only the credentials differ. `mfmp_email`/`mfmp_password` are the
    # keys from before there was a choice and stand in for Hoope Lab, so a
    # deployment that predates the switch keeps running untouched.
    mfmp_email: str = ""
    mfmp_password: str = ""
    myflorida_acc1_username: str = ""
    myflorida_acc1_password: str = ""
    myflorida_acc2_username: str = ""
    myflorida_acc2_password: str = ""
    mfmp_login_url: str = "https://vendor.myfloridamarketplace.com/login"
    # MFMP challenges a login with a one-time password sent to the account's
    # email/phone. Nothing on this side can produce that code, so the browser is
    # forced visible and the login step waits for a human to type it into the
    # open window; the run continues the instant the ads dashboard loads. Same
    # shape as `northdakota_manual_login` above. Set false only for an account
    # whose OTP has been disabled — the run then fails at the challenge instead
    # of waiting for someone who is not there.
    mfmp_manual_otp: bool = True
    mfmp_otp_wait_seconds: int = 120  # seconds to wait for the human

    # RideMetro (Bonfire) — the login is RideMetro's own portal, but a run
    # sweeps the whole Euna Supplier Network the account belongs to: every
    # agency listed under My Network whose registration Status is Complete.
    # The bonfirehub session is shared across all three hosts, so the network
    # and agency portals need no second login.
    #
    # Two accounts are configured, and each run picks one (see
    # app/scrapers/ridemetro/accounts.py). They are separate logins into separate
    # supplier networks, so which one runs decides which agencies are swept.
    hoope_lab_username: str = ""
    hoope_lab_password: str = ""
    fedpints_username: str = ""
    fedpints_password: str = ""
    # The pre-accounts keys. Still read as the Hoope Lab account's credentials so
    # a deployment whose .env predates the switch keeps working; the account's
    # own HOOPE_LAB_* keys win when both are set.
    ridemetro_email: str = ""
    ridemetro_password: str = ""
    ridemetro_login_url: str = "https://ridemetro.bonfirehub.com/login"
    # The post-login landing page, and where the "My Euna Supplier Network"
    # button lives.
    ridemetro_opportunities_url: str = "https://ridemetro.bonfirehub.com/portal/?tab=openOpportunities"
    # Where that button goes, and the My Network tab within it. Used directly
    # when the button/tab isn't clickable (it is feature-flagged in the portal).
    ridemetro_supplier_network_url: str = "https://vendor.bonfirehub.com"
    ridemetro_agencies_url: str = "https://vendor.bonfirehub.com/agencies"

    # BidNet Direct
    bidnet_direct_link: str = "https://www.bidnetdirect.com/"
    bidnet_username: str = ""
    bidnet_password: str = ""
    # Some solicitations sit behind a "required acknowledgement" page that must
    # be Accepted before the bid is readable — attesting to something on the
    # account's behalf (e.g. "this company is US-based") or recording that an
    # addendum was read, which the issuing agency can see.
    #
    # On by default at the account holder's instruction: runs click Accept and
    # then read the bid normally. Set false to leave those bids untouched, in
    # which case they are exported flagged ACKNOWLEDGEMENT_REQUIRED with their
    # detail URL for a human to accept on the portal.
    bidnet_auto_accept_acknowledgements: bool = True

    # Wisconsin eSupplier (PeopleSoft) — public bidder portal, no login.
    wisconsin_url: str = "https://esupplier.wi.gov/psp/esupplier/SUPPLIER/ERP/h/?tab=WI_BIDDER"

    # North Dakota (ND Buys / Ivalua) — supplier login via ND OAuth (Azure AD B2C).
    northdakota_username: str = ""
    northdakota_password: str = ""
    northdakota_login_url: str = (
        "https://public.ndbuys.nd.gov/page.aspx/en/usr/login"
        "?ReturnUrl=%2Fpage.aspx%2Fen%2Fbuy%2Fhomepage%2Fsup"
    )
    # Supplier homepage the B2C sign-in returns to; also used to resolve the
    # post-login landing directly. `base_url` stays the bare origin so _abs_url
    # can turn relative hrefs into absolute links.
    northdakota_homepage_url: str = "https://public.ndbuys.nd.gov/page.aspx/en/buy/homepage/sup"
    northdakota_base_url: str = "https://public.ndbuys.nd.gov"
    # The B2C sign-in carries an (often invisible) reCAPTCHA that can challenge an
    # automated session. In manual-login mode the browser is forced visible and
    # the login step waits (up to the timeout below) for a human to solve the
    # challenge in the open Chrome window; the run continues the instant the
    # supplier homepage loads. Set to false only for an unattended/solver setup.
    northdakota_manual_login: bool = True
    northdakota_manual_login_timeout: int = 300  # seconds to wait for the human
    # A persistent Chrome user-data-dir so the ND session/cookies survive between
    # runs — once the CAPTCHA is solved, later runs usually skip B2C entirely.
    # Kept outside server/ so it doesn't trip the uvicorn --reload watcher.
    northdakota_profile_dir: str = "../data/chrome_profiles/northdakota"

    # City of Philadelphia — PHLContracts, a Periscope Holdings BSO portal. The
    # Open Bids list is behind a vendor login, so a run needs an account.
    phila_email: str = ""
    phila_password: str = ""
    phila_login_url: str = "https://www.phlcontracts.phila.gov/bso/view/login/login.xhtml"

    # SEPTA (Southeastern Pennsylvania Transportation Authority) vendor portal —
    # ASP.NET procurement site; login then scrape the Open Quotes grid.
    septa_username: str = ""
    septa_password: str = ""
    septa_login_url: str = "https://epsadmin.septa.org/vendor/login/"
    # The portal splits searching across two pages: this form (keyword,
    # commodity code, bid number, opens/closes date ranges) and a results list
    # at /vendor/requisitions/list/ that carries no filter inputs at all. Every
    # search lands on the list, so each new term has to come back here first.
    septa_search_url: str = "https://epsadmin.septa.org/vendor/requisitions/search/"
    # The Bid module, which sits alongside Quotes in the vendor menu. Same shape
    # as the Quotes form (filters here, results on a separate list page), and a
    # run searches it with the same optional opens-from date.
    #
    # This default is a best guess at the portal's URL and is deliberately
    # overridable from .env: if it 404s or lands on a page with no search form,
    # the scraper falls back to finding the "Open Bids" link in the menu, so a
    # wrong value here costs a redirect rather than the whole Bids pass.
    septa_bids_search_url: str = "https://epsadmin.septa.org/vendor/bids/search/"

    # Cal eProcure (California eProcurement / BidSync "BS3") — supplier login on
    # an ASP.NET page (#userid / #pwd), plain username+password (no SSO/MFA).
    # Field names are lowercase to match the mixed-case Cal_ePROCURE_* keys in
    # .env (pydantic-settings matches env vars case-insensitively).
    cal_eprocure_link: str = "https://caleprocure.ca.gov/pages/BS3/login.aspx"
    cal_eprocure_username: str = ""
    cal_eprocure_password: str = ""

    # EMMA (eMaryland Marketplace Advantage) — Maryland's procurement portal on
    # the Ivalua platform (same product as North Dakota's ND Buys). Unlike ND,
    # login is a plain form on the page itself (#body_x_txtLogin /
    # #body_x_txtPass / #body_x_btnLogin) — no OAuth/B2C redirect.
    emma_link: str = "https://emma.maryland.gov/page.aspx/en/usr/login"
    emma_username: str = ""
    emma_password: str = ""

    # Unison Marketplace — the vendored engine (app/scrapers/unison/engine/) reads
    # these straight from os.environ via its own load_dotenv(); declared here too
    # so the .env keys are documented in one place, and so the run's credential
    # check can confirm the two loaders agree. SAM.gov needs no creds; NAICS is a
    # public reference page.
    #
    # This password carries $ / % / # / ^, so its .env line must be SINGLE-quoted
    # — see the QUOTING note in .env.example and app/core/credentials.py.
    unison_email: str = ""
    unison_password: str = ""

    # Post-scrape notifications (SAM + SEPTA for now) — same mechanism as the
    # sam-septa project: on a successful run, upload the run's Excel to S3 and
    # email it to RECIPIENT_EMAILS via AWS SES SMTP. All optional: if
    # recipient_emails is blank the notifier is a no-op; if the S3 bucket is
    # blank the upload is skipped but the email (with attachment) still sends.
    recipient_emails: str = ""
    aws_s3_bucket_name: str = ""
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "us-east-1"
    aws_ses_from_email: str = ""
    aws_ses_username: str = ""
    aws_ses_password: str = ""

    # Legacy output root (data/documents). New runs no longer write here — they
    # work inside work_root and archive into archive_root — but the path is kept
    # so downloads of runs made before the switch still resolve.
    download_dir: str = "../data/documents"

    # How many scrapes may run at once; the rest queue (see app/core/jobs.py).
    # Chrome is what sets this — each run drives its own browser at roughly
    # 300–500 MB, and an over-subscribed host loses one to the OOM killer
    # mid-run rather than running them all slowly. Raise it on a bigger box.
    scrape_concurrency: int = 3

    # Scratch workspace for in-flight runs: bid documents (and the browser's
    # download staging) land here while a run is going, get zipped into the
    # run's archive on completion, and the whole folder is deleted. Defaults to
    # the system temp dir so nothing accumulates in the repo's data/ tree.
    work_dir: str = ""

    # Where each finished run's final ZIP (cumulative Excel + documents) is
    # stored, so the Download button and email link keep working long after the
    # workspace has been cleaned up.
    archive_dir: str = "../data/archives"

    # Base URL the notification email uses for the run's download link. Set to
    # the address recipients can actually reach (e.g. a tunnel or LAN address).
    public_base_url: str = "http://localhost:9000"

    # Browser visibility is decided per-run, not globally: every run is headless
    # unless it was started from the "Live preview" button (which sets the run's
    # live_preview flag). See BaseScraper.start_driver.

    # Resolve portal hostnames in Chrome via DNS-over-HTTPS instead of the
    # machine's resolver. Some ISP resolvers answer certain domains (notably the
    # Google-run .app / .dev TLDs) with an empty record set, which surfaces as
    # net::ERR_NAME_NOT_RESOLVED mid-run — EMMA is affected because it is a CNAME
    # to maryland.ivalua.app. Talking DoH straight to a public resolver makes runs
    # independent of however this machine's DNS happens to be configured.
    # Set dns_over_https=false to fall back to the system resolver.
    dns_over_https: bool = True
    dns_over_https_templates: str = (
        "https://dns.google/dns-query https://cloudflare-dns.com/dns-query"
    )

    # SQLAlchemy URL for the Postgres database that holds scraped bids.
    database_url: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/scraping-hub"

    @property
    def documents_root(self) -> Path:
        path = Path(self.download_dir)
        if not path.is_absolute():
            path = SERVER_ROOT / path
        path = path.resolve()  # normalize away '..' so downloads land cleanly outside server/
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def work_root(self) -> Path:
        if self.work_dir:
            path = Path(self.work_dir)
            if not path.is_absolute():
                path = SERVER_ROOT / path
        else:
            path = Path(tempfile.gettempdir()) / "scraping-hub-runs"
        path = path.resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def archive_root(self) -> Path:
        path = Path(self.archive_dir)
        if not path.is_absolute():
            path = SERVER_ROOT / path
        path = path.resolve()  # normalize away '..' so archives land outside server/
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def northdakota_profile_path(self) -> Path:
        path = Path(self.northdakota_profile_dir)
        if not path.is_absolute():
            path = SERVER_ROOT / path
        path = path.resolve()  # normalize away '..' so the profile lands outside server/
        path.mkdir(parents=True, exist_ok=True)
        return path


settings = Settings()
