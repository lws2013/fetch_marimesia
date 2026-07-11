from __future__ import annotations

import csv
import json
import os
import time
from datetime import datetime
from html import escape
from typing import Any, Dict, List

import requests


INPUT_PATH = "input/vessels.json"
RAW_PATH = "output/marinesia_raw.json"
CSV_PATH = "output/marinesia_table.csv"
HTML_PATH = "output/marinesia_table.html"
LATEST_PATH = "output/marinesia_latest.json"

MIN_INTERVAL_SECONDS = 13  # 5 req/min 안전 마진


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def load_vessels() -> List[Dict[str, Any]]:
    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("input/vessels.json must be a list")
    return data


def fetch_one(api_key: str, vessel: Dict[str, Any]) -> Dict[str, Any]:
    mmsi_no = str(vessel["mmsi_no"])
    url = f"https://api.marinesia.com/api/v1/vessel/{mmsi_no}/location"
    params = {"key": api_key}

    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    payload = resp.json()

    return {
        "shipment_id": vessel["shipment_id"],
        "vessel_name": vessel["vessel_name"],
        "query_mmsi_no": mmsi_no,
        "fetched_at": now_iso(),
        "response": payload,
    }


def flatten_rows(raw_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    for item in raw_results:
        shipment_id = item["shipment_id"]
        vessel_name = item["vessel_name"]
        query_mmsi_no = item["query_mmsi_no"]
        fetched_at = item["fetched_at"]
        payload = item["response"]

        if payload.get("error") is True:
            rows.append(
                {
                    "shipment_id": shipment_id,
                    "vessel_name": vessel_name,
                    "query_mmsi_no": query_mmsi_no,
                    "mmsi": None,
                    "imo": None,
                    "status": None,
                    "pos_acc": None,
                    "lat": None,
                    "lng": None,
                    "cog": None,
                    "sog": None,
                    "rot": None,
                    "hdt": None,
                    "dest": None,
                    "eta": None,
                    "draught": None,
                    "ts": None,
                    "fetched_at": fetched_at,
                    "api_message": payload.get("message"),
                    "api_error": True,
                }
            )
            continue

        data_list = payload.get("data") or []
        if not isinstance(data_list, list):
            data_list = []

        if not data_list:
            rows.append(
                {
                    "shipment_id": shipment_id,
                    "vessel_name": vessel_name,
                    "query_mmsi_no": query_mmsi_no,
                    "mmsi": None,
                    "imo": None,
                    "status": None,
                    "pos_acc": None,
                    "lat": None,
                    "lng": None,
                    "cog": None,
                    "sog": None,
                    "rot": None,
                    "hdt": None,
                    "dest": None,
                    "eta": None,
                    "draught": None,
                    "ts": None,
                    "fetched_at": fetched_at,
                    "api_message": payload.get("message"),
                    "api_error": False,
                }
            )
            continue

        for d in data_list:
            rows.append(
                {
                    "shipment_id": shipment_id,
                    "vessel_name": vessel_name,
                    "query_mmsi_no": query_mmsi_no,
                    "mmsi": d.get("mmsi"),
                    "imo": d.get("imo"),
                    "status": d.get("status"),
                    "pos_acc": d.get("pos_acc"),
                    "lat": d.get("lat"),
                    "lng": d.get("lng"),
                    "cog": d.get("cog"),
                    "sog": d.get("sog"),
                    "rot": d.get("rot"),
                    "hdt": d.get("hdt"),
                    "dest": d.get("dest"),
                    "eta": d.get("eta"),
                    "draught": d.get("draught"),
                    "ts": d.get("ts"),
                    "fetched_at": fetched_at,
                    "api_message": payload.get("message"),
                    "api_error": False,
                }
            )

    return rows


def build_latest_snapshot(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    latest_map: Dict[str, Dict[str, Any]] = {}

    for row in rows:
        shipment_id = row["shipment_id"]
        ts = row.get("ts") or ""
        current = latest_map.get(shipment_id)

        if current is None:
            latest_map[shipment_id] = row
            continue

        current_ts = current.get("ts") or ""
        if ts > current_ts:
            latest_map[shipment_id] = row

    return list(latest_map.values())


def save_csv(rows: List[Dict[str, Any]], path: str) -> None:
    ensure_dir(os.path.dirname(path) or ".")
    if not rows:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write("")
        return

    fieldnames = list(rows[0].keys())
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def save_json(data: Any, path: str) -> None:
    ensure_dir(os.path.dirname(path) or ".")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def build_html_table(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "<html><body><p>No data</p></body></html>"

    headers = list(rows[0].keys())

    thead = "".join(f"<th>{escape(str(h))}</th>" for h in headers)
    body_rows = []

    for row in rows:
        tds = "".join(f"<td>{escape('' if row.get(h) is None else str(row.get(h)))}</td>" for h in headers)
        body_rows.append(f"<tr>{tds}</tr>")

    html = f"""
    <html>
      <body style="font-family:Arial, sans-serif; font-size:12px;">
        <p><b>Marinesia Vessel Table</b></p>
        <p>Generated at: {escape(now_iso())}</p>
        <table border="1" cellpadding="4" cellspacing="0" style="border-collapse:collapse;">
          <thead>
            <tr>{thead}</tr>
          </thead>
          <tbody>
            {''.join(body_rows)}
          </tbody>
        </table>
      </body>
    </html>
    """
    return html


def main() -> None:
    api_key = os.environ["MARINESIA_API_KEY"]
    vessels = load_vessels()
    ensure_dir("output")

    raw_results: List[Dict[str, Any]] = []
    last_request_started_at: float | None = None

    for i, vessel in enumerate(vessels, start=1):
        if last_request_started_at is not None:
            elapsed = time.time() - last_request_started_at
            if elapsed < MIN_INTERVAL_SECONDS:
                wait_sec = MIN_INTERVAL_SECONDS - elapsed
                print(f"[INFO] waiting {wait_sec:.1f}s for rate limit safety")
                time.sleep(wait_sec)

        print(f"[INFO] ({i}/{len(vessels)}) fetching {vessel['vessel_name']} | MMSI={vessel['mmsi_no']}")
        last_request_started_at = time.time()

        try:
            result = fetch_one(api_key, vessel)
            raw_results.append(result)
            print(f"[INFO] success {vessel['shipment_id']}")
        except Exception as e:
            raw_results.append(
                {
                    "shipment_id": vessel["shipment_id"],
                    "vessel_name": vessel["vessel_name"],
                    "query_mmsi_no": str(vessel["mmsi_no"]),
                    "fetched_at": now_iso(),
                    "response": {
                        "error": True,
                        "message": str(e),
                        "data": [],
                    },
                }
            )
            print(f"[WARN] failed {vessel['shipment_id']}: {e}")

    table_rows = flatten_rows(raw_results)
    latest_rows = build_latest_snapshot(table_rows)
    html = build_html_table(table_rows)

    save_json(raw_results, RAW_PATH)
    save_csv(table_rows, CSV_PATH)
    save_json(latest_rows, LATEST_PATH)

    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"[INFO] saved raw: {RAW_PATH}")
    print(f"[INFO] saved csv: {CSV_PATH}")
    print(f"[INFO] saved html: {HTML_PATH}")
    print(f"[INFO] saved latest: {LATEST_PATH}")


if __name__ == "__main__":
    main()
