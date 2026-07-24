"""Post-scrape notification: upload the run's Excel to S3 and email recipients
via AWS SES SMTP.

This is the same mechanism as the sam-septa project's services/notifier.py,
adapted to the hub: config comes from the hub `settings` (pydantic-settings)
rather than os.getenv, and the Excel attachment is the run's already-generated
spreadsheet (run["excel_path"]) rather than a re-query. Wired into SAM and SEPTA
completions for now; other scrapers can opt in later.

Everything here is best-effort — a notification failure never affects a scrape.
"""

import logging
import smtplib
import threading
from datetime import datetime
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from app.config import settings
from app.core import run_manager

logger = logging.getLogger(__name__)

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _recipients() -> list[str]:
    return [e.strip() for e in (settings.recipient_emails or "").split(",") if e.strip()]


def _upload_to_s3(data: bytes, filename: str) -> str | None:
    """Upload Excel bytes to S3 under exports/<filename>; return the URL or None."""
    bucket = settings.aws_s3_bucket_name
    if not bucket:
        logger.info("AWS_S3_BUCKET_NAME not set — skipping S3 upload")
        return None
    try:
        import boto3

        s3 = boto3.client(
            "s3",
            aws_access_key_id=settings.aws_access_key_id or None,
            aws_secret_access_key=settings.aws_secret_access_key or None,
            region_name=settings.aws_region or "us-east-1",
        )
        key = f"exports/{filename}"
        s3.put_object(Bucket=bucket, Key=key, Body=data, ContentType=_XLSX_MIME)
        url = f"https://{bucket}.s3.amazonaws.com/{key}"
        logger.info("S3 upload OK: %s", url)
        return url
    except Exception as exc:  # noqa: BLE001 — S3 is optional; never fatal
        logger.error("S3 upload failed: %s", exc)
        return None


def _send_email(recipients: list[str], subject: str, body_html: str, attachment: bytes, filename: str) -> bool:
    """Send an HTML email with the Excel attached, via the AWS SES SMTP interface."""
    sender = settings.aws_ses_from_email
    username = settings.aws_ses_username
    password = settings.aws_ses_password
    region = settings.aws_region or "us-east-1"
    host = f"email-smtp.{region}.amazonaws.com"
    port = 587

    if not all([sender, username, password]):
        logger.warning("AWS SES SMTP credentials incomplete — skipping email notification")
        return False

    try:
        msg = MIMEMultipart("mixed")
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = ", ".join(recipients)
        msg.attach(MIMEText(body_html, "html"))

        part = MIMEBase("application", "octet-stream")
        part.set_payload(attachment)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
        msg.attach(part)

        with smtplib.SMTP(host, port) as server:
            server.ehlo()
            server.starttls()
            server.login(username, password)
            server.sendmail(sender, recipients, msg.as_bytes())

        logger.info("Email sent via SES SMTP to %s", recipients)
        return True
    except Exception as exc:  # noqa: BLE001 — email is best-effort
        logger.error("SES SMTP email failed: %s", exc)
        return False


def _excel_bytes(run: dict) -> tuple[bytes, str] | None:
    """Read the run's generated Excel from disk. Returns (bytes, filename) or None."""
    path = run.get("excel_path")
    if not path:
        logger.warning("run %s has no excel_path — skipping notification", run.get("run_id"))
        return None
    p = Path(path)
    if not p.is_file():
        logger.warning("run %s excel not found at %s — skipping notification", run.get("run_id"), path)
        return None
    try:
        return p.read_bytes(), p.name
    except OSError as exc:
        logger.error("could not read excel %s: %s", path, exc)
        return None


def _notify(run_id: str, scraper: str, record_count: int) -> None:
    recipients = _recipients()
    if not recipients:
        logger.info("RECIPIENT_EMAILS not configured — skipping notification for %s", run_id)
        return

    run = run_manager.get_run(run_id)
    if not run:
        return

    payload = _excel_bytes(run)
    if not payload:
        return
    excel, filename = payload

    now = datetime.now()
    hr = now.strftime("%I").lstrip("0") or "12"
    ts = now.strftime(f"%Y-%m-%d, {hr}:%M %p")

    s3_url = _upload_to_s3(excel, filename)
    s3_link = f'<a href="{s3_url}">Download from S3</a>' if s3_url else "<em>S3 upload unavailable</em>"

    subject = f"{scraper.upper()} Scrape Complete — {record_count} records ({ts})"
    body_html = f"""\
<html><body>
<p>The <strong>{scraper.upper()}</strong> scraper has finished.</p>
<ul>
  <li><strong>Run ID:</strong> {run_id}</li>
  <li><strong>Records scraped:</strong> {record_count}</li>
  <li><strong>Completed at:</strong> {ts}</li>
</ul>
<p>The Excel report is attached to this email. {s3_link}</p>
</body></html>"""

    _send_email(recipients, subject, body_html, excel, filename)


def notify_scrape_completion(run_id: str, scraper: str, record_count: int) -> None:
    """Fire a completion notification in a background thread (best-effort).

    Safe to call from a scraper's run(): it returns immediately and never raises,
    so email/S3 latency or failure can't affect the scrape.
    """
    threading.Thread(
        target=_notify, args=(run_id, scraper, record_count), daemon=True
    ).start()
