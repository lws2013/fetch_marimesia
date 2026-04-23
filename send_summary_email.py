from __future__ import annotations

import json
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path


SUMMARY_PATH = "output/summary.json"


def build_html(summary: dict) -> str:
    counts = summary["summary"]
    rows = []

    for item in summary["map_items"]:
        rows.append(
            f"""
            <tr>
              <td>{item.get("shipment_id") or ""}</td>
              <td>{item.get("vessel_name") or ""}</td>
              <td>{item.get("position_status") or ""}</td>
              <td>{item.get("current_lat") if item.get("current_lat") is not None else ""}</td>
              <td>{item.get("current_lon") if item.get("current_lon") is not None else ""}</td>
              <td>{item.get("last_seen_at") or ""}</td>
              <td>{item.get("next_port") or ""}</td>
              <td>{item.get("error") or ""}</td>
            </tr>
            """
        )

    table_html = f"""
    <table border="1" cellpadding="6" cellspacing="0">
      <thead>
        <tr>
          <th>Shipment ID</th>
          <th>Vessel</th>
          <th>Status</th>
          <th>Lat</th>
          <th>Lon</th>
          <th>Last Seen</th>
          <th>Next Port</th>
          <th>Error</th>
        </tr>
      </thead>
      <tbody>
        {''.join(rows)}
      </tbody>
    </table>
    """

    html = f"""
    <html>
      <body>
        <p><b>Americas Vessel Position Summary</b></p>
        <p>
          Generated at: {summary.get("generated_at")}<br>
          Total: {counts.get("total")} /
          LIVE: {counts.get("live")} /
          STALE: {counts.get("stale")} /
          NO_SIGNAL: {counts.get("no_signal")}
        </p>
        {table_html}
      </body>
    </html>
    """
    return html


def main() -> None:
    gmail_user = os.environ["GMAIL_ADDRESS"]
    gmail_app_password = os.environ["GMAIL_APP_PASSWORD"]
    to_addr = os.environ["COMPANY_EMAIL_TO"]

    with open(SUMMARY_PATH, "r", encoding="utf-8") as f:
        summary = json.load(f)

    counts = summary["summary"]
    subject = (
        f"Americas Vessel Position Summary | "
        f"LIVE {counts['live']} / STALE {counts['stale']} / NO_SIGNAL {counts['no_signal']}"
    )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = gmail_user
    msg["To"] = to_addr
    msg.set_content("Please view the HTML version of this email.")
    msg.add_alternative(build_html(summary), subtype="html")

    with open(SUMMARY_PATH, "rb") as f:
        data = f.read()
        msg.add_attachment(
            data,
            maintype="application",
            subtype="json",
            filename="summary.json",
        )

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(gmail_user, gmail_app_password)
        smtp.send_message(msg)

    print("[INFO] summary email sent")


if __name__ == "__main__":
    main()
