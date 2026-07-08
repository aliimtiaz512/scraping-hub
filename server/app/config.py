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

    download_dir: str = "./documents"
    headless: bool = True

    # SQLAlchemy URL for the Postgres database that holds scraped bids.
    database_url: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/scraping-hub"

    @property
    def documents_root(self) -> Path:
        path = Path(self.download_dir)
        if not path.is_absolute():
            path = SERVER_ROOT / path
        path.mkdir(parents=True, exist_ok=True)
        return path


settings = Settings()
