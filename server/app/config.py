from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

SERVER_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=SERVER_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # MyFloridaMarketPlace
    mfmp_email: str = ""
    mfmp_password: str = ""
    mfmp_login_url: str = "https://vendor.myfloridamarketplace.com/login"

    # RideMetro (Bonfire)
    ridemetro_email: str = ""
    ridemetro_password: str = ""
    ridemetro_login_url: str = "https://ridemetro.bonfirehub.com/login"
    ridemetro_opportunities_url: str = "https://ridemetro.bonfirehub.com/portal/?tab=openOpportunities"

    # BidNet Direct
    bidnet_direct_link: str = "https://www.bidnetdirect.com"
    bidnet_username: str = ""
    bidnet_password: str = ""

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

    # SEPTA (Southeastern Pennsylvania Transportation Authority) vendor portal —
    # ASP.NET procurement site; login then scrape the Open Quotes grid.
    septa_username: str = ""
    septa_password: str = ""
    septa_login_url: str = "https://epsadmin.septa.org/vendor/login/"

    # Kept outside the server/ tree so downloads don't trip the uvicorn --reload
    # file watcher (which would restart the process mid-scrape). Resolved against
    # SERVER_ROOT when relative — see documents_root below.
    download_dir: str = "../data/documents"
    headless: bool = True

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
    def northdakota_profile_path(self) -> Path:
        path = Path(self.northdakota_profile_dir)
        if not path.is_absolute():
            path = SERVER_ROOT / path
        path = path.resolve()  # normalize away '..' so the profile lands outside server/
        path.mkdir(parents=True, exist_ok=True)
        return path


settings = Settings()
