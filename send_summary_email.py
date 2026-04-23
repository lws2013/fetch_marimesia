from __future__ import annotations

import json
import os
import smtplib
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path


SUMMARY_PATH = "output/summary.json"
MAP_PNG_PATH = "output/map.png"


def parse_recipients(raw: str) -> list[str]:
    return [x.strip() for x in raw.split(",") if x.strip()]


def build_html(summary: dict, map_cid: str | None) -> str:
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
    <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse; font-size:12px;">
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

    map_html = ""
    if map_cid:
        map_html = f"""
        <p><b>Route Map</b></p>
        <p><img src="cid:{map_cid[1:-1]}" style="max-width:100%; border:1px solid #ccc;" /></p>
        """

    html = f"""
    <html>
      <body style="font-family:Arial, sans-serif; font-size:13px;">
        <p><b>Americas Vessel Position Summary</b></p>
        <p>
          Generated at: {summary.get("generated_at")}<br>
          Total: {counts.get("total")} /
          LIVE: {counts.get("live")} /
          STALE: {counts.get("stale")} /
          NO_SIGNAL: {counts.get("no_signal")}<br>
          Remaining Cycles: {counts.get("remaining_cycles")}<br>
          Last Completed Cycle At: {counts.get("cycle_completed_at")}
        </p>
        {map_html}
        {table_html}
      </body>
    </html>
    """
    return html


def main() -> None:
    gmail_user = os.environ["GMAIL_ADDRESS"]
    gmail_app_password = os.environ["GMAIL_APP_PASSWORD"]
    to_addr_raw = os.environ["COMPANY_EMAIL_TO"]
    to_addrs = parse_recipients(to_addr_raw)

    if not to_addrs:
        raise RuntimeError("COMPANY_EMAIL_TO is empty")

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
    msg["To"] = ", ".join(to_addrs)
    msg.set_content("Please view the HTML version of this email.")

    map_cid = None
    if Path(MAP_PNG_PATH).exists():
        map_cid = make_msgid()

    msg.add_alternative(build_html(summary, map_cid), subtype="html")

    if map_cid and Path(MAP_PNG_PATH).exists():
        with open(MAP_PNG_PATH, "rb") as f:
            img_data = f.read()

        html_part = msg.get_payload()[-1]
        html_part.add_related(
            img_data,
            maintype="image",
            subtype="png",
            cid=map_cid,
            filename="map.png",
        )

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

    print(f"[INFO] summary email sent to {to_addrs}")


if __name__ == "__main__":
    main()
