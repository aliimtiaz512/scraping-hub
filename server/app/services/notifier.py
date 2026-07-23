"""Post-scrape notification: upload the run's Excel to S3 and email recipients
via AWS SES SMTP.

This is the same mechanism as the sam-septa project's services/notifier.py,
adapted to the hub: config comes from the hub `settings` (pydantic-settings)
rather than os.getenv. The attachment is the run's archive ZIP (cumulative
Excel + all bid documents, built by exports.archive_run) when it fits in an
email, else just the cumulative Excel — with the ZIP's download link in the
body either way. Wired into every scraping portal's completion.

Everything here is best-effort — a notification failure never affects a scrape.
"""

import logging
import smtplib
import threading
import zipfile
from datetime import datetime
from pathlib import Path
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import settings
from app.core import exports, run_manager

logger = logging.getLogger(__name__)

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _recipients() -> list[str]:
    return [e.strip() for e in (settings.recipient_emails or "").split(",") if e.strip()]


def _upload_to_s3(data: bytes, filename: str, content_type: str = _XLSX_MIME) -> str | None:
    """Upload bytes to S3 under exports/<filename>; return the URL or None."""
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
        s3.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)
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


# SES caps a whole message around 10 MB — leave headroom for encoding overhead.
_MAX_ATTACHMENT_BYTES = 7 * 1024 * 1024


def _excel_from_zip(path: Path) -> tuple[bytes, str] | None:
    """Pull the cumulative Excel report (a root-level .xlsx) out of the run ZIP."""
    try:
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if "/" not in name and name.lower().endswith(".xlsx"):
                    return zf.read(name), Path(name).name
    except (OSError, zipfile.BadZipFile) as exc:
        logger.error("could not read excel out of %s: %s", path, exc)
    return None


def _attachment_for(run: dict) -> tuple[bytes, str, str] | None:
    """(bytes, filename, content_type) to attach: the full ZIP when it fits in
    an email, else just the cumulative Excel (the ZIP stays downloadable via
    the link). Falls back to a DB-regenerated Excel for runs with no archive."""
    zip_path = run.get("zip_path")
    if zip_path and Path(zip_path).is_file():
        p = Path(zip_path)
        try:
            if p.stat().st_size <= _MAX_ATTACHMENT_BYTES:
                return p.read_bytes(), p.name, "application/zip"
        except OSError as exc:
            logger.error("could not read archive %s: %s", zip_path, exc)
        payload = _excel_from_zip(p)
        if payload:
            return (*payload, _XLSX_MIME)
    payload = exports.excel_bytes(run)
    if payload:
        return (*payload, _XLSX_MIME)
    logger.warning("run %s has nothing to attach — skipping notification", run.get("run_id"))
    return None


def _notify(run_id: str, scraper: str, record_count: int) -> None:
    recipients = _recipients()
    if not recipients:
        logger.info("RECIPIENT_EMAILS not configured — skipping notification for %s", run_id)
        return

    run = run_manager.get_run(run_id)
    if not run:
        return

    payload = _attachment_for(run)
    if not payload:
        return
    data, filename, content_type = payload
    attached_zip = content_type == "application/zip"

    now = datetime.now()
    hr = now.strftime("%I").lstrip("0") or "12"
    ts = now.strftime(f"%Y-%m-%d, {hr}:%M %p")

    s3_url = _upload_to_s3(data, filename, content_type)
    s3_link = f' You can also <a href="{s3_url}">download it from S3</a>.' if s3_url else ""

    download_url = f"{settings.public_base_url.rstrip('/')}/runs/{run_id}/download"
    attach_note = (
        "The complete ZIP (cumulative Excel report + all bid documents) is attached."
        if attached_zip
        else (
            "The cumulative Excel report is attached; the complete ZIP with all bid "
            "documents was too large to email — use the download link below."
        )
    )

    subject = f"{scraper.upper()} Scrape Complete — {record_count} records ({ts})"
    body_html = f"""\
<html><body>
<p>The <strong>{scraper.upper()}</strong> scraper has finished.</p>
<ul>
  <li><strong>Run ID:</strong> {run_id}</li>
  <li><strong>Records scraped:</strong> {record_count}</li>
  <li><strong>Completed at:</strong> {ts}</li>
</ul>
<p>{attach_note}{s3_link}</p>
<p><a href="{download_url}">Download the full ZIP from the Scraping Hub</a></p>
</body></html>"""

    _send_email(recipients, subject, body_html, data, filename)


def notify_scrape_completion(run_id: str, scraper: str, record_count: int) -> None:
    """Fire a completion notification in a background thread (best-effort).

    Safe to call from a scraper's run(): it returns immediately and never raises,
    so email/S3 latency or failure can't affect the scrape.
    """
    threading.Thread(
        target=_notify, args=(run_id, scraper, record_count), daemon=True
    ).start()
