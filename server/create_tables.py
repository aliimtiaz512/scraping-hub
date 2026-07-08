"""Create the database tables. Run once after setting DATABASE_URL in .env:

    .venv/bin/python create_tables.py
"""

from app.config import settings
from app.db import init_db

if __name__ == "__main__":
    # Hide any password when echoing the target.
    url = settings.database_url
    safe = url.split("@")[-1] if "@" in url else url
    print(f"Creating tables on …@{safe}")
    init_db()
    print("Done. Tables: scrape_runs, mfmp_bids, ridemetro_runs, ridemetro_bids, bidnet_runs, bidnet_bids")
