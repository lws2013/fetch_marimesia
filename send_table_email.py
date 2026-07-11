from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from pathlib import Path


HTML_PATH = "output/marinesia_table.html"
CSV_PATH = "output/marinesia_table.csv"
RAW_PATH = "output/marinesia_raw.json"
LATEST_PATH = "output/marinesia_latest.json"


def parse_recipients(raw: str) -> list[str]:
    return [x.strip() for x in raw.split(",") if x.strip()]


def main() -> None:
    gmail_user = os.environ["GMAIL_ADDRESS"]
    gmail_app_password = os.environ["GMAIL_APP_PASSWORD"]
    to_addrs = parse_recipients(os.environ["COMPANY_EMAIL_TO"])

    if not to_addrs:
        raise RuntimeError("COMPANY_EMAIL_TO is empty")

    html_body = Path(HTML_PATH).read_text(encoding="utf-8")

    msg = EmailMessage()
    msg["Subject"] = "Marinesia Vessel Table"
    msg["From"] = gmail_user
    msg["To"] = ", ".join(to_addrs)
    msg.set_content("Please view the HTML version of this email.")
    msg.add_alternative(html_body, subtype="html")

    for path_str, mime_subtype in [
        (CSV_PATH, "csv"),
        (RAW_PATH, "json"),
        (LATEST_PATH, "json"),
    ]:
        path = Path(path_str)
        if path.exists():
            with open(path, "rb") as f:
                msg.add_attachment(
                    f.read(),
                    maintype="application",
                    subtype=mime_subtype,
                    filename=path.name,
                )

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(gmail_user, gmail_app_password)
        smtp.send_message(msg)

    print(f"[INFO] email sent to {to_addrs}")


if __name__ == "__main__":
    main()
