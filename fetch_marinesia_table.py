from __future__ import annotations

import csv
import json
import os
import time
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Dict, List

import requests


INPUT_PATH = "input/vessels.json"

RAW_PATH = "output/marinesia_raw.json"
CSV_PATH = "output/marinesia_table.csv"
HTML_PATH = "output/marinesia_table.html"
LATEST_PATH = "output/marinesia_latest.json"

BULK_SIZE = 10

BULK_URL = (
    "https://api.marinesia.com/api/v1/"
    "vessel/location/latest/bulk"
)

# 분당 최대 5회보다 여유 있게 약 4.6회/분으로 호출
MIN_INTERVAL_SECONDS = 13.0

API_FIELDS = [
    "mmsi",
    "imo",
    "com_state",
    "status",
    "pos_acc",
    "raim",
    "lat",
    "lng",
    "cog",
    "sog",
    "rot",
    "spare",
    "hdt",
    "dest",
    "eta",
    "draught",
    "repeat",
    "smi",
    "valid",
    "ts",
]

OUTPUT_FIELDS = [
    "company",
    "vessel_name",
    "query_mmsi_no",
    "record_index",
    *API_FIELDS,
    "fetched_at",
    "api_message",
    "api_error",
]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def normalize_mmsi(value: Any) -> str:
    mmsi = str(value or "").strip()

    if not mmsi:
        raise ValueError("MMSI is empty")

    if not mmsi.isdigit():
        raise ValueError(f"MMSI must contain digits only: {mmsi}")

    if len(mmsi) != 9:
        raise ValueError(f"MMSI must be 9 digits: {mmsi}")

    return mmsi


def load_vessels() -> List[Dict[str, str]]:
    path = Path(INPUT_PATH)

    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {INPUT_PATH}")

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("input/vessels.json must contain a JSON list")

    vessels: List[Dict[str, str]] = []

    for index, row in enumerate(data, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"Row {index} must be a JSON object")

        company = str(row.get("company") or "").strip().upper()
        vessel_name = str(row.get("vessel_name") or "").strip()
        mmsi_no = normalize_mmsi(row.get("mmsi_no"))

        if not company:
            raise ValueError(f"Row {index}: company is empty")

        if not vessel_name:
            raise ValueError(f"Row {index}: vessel_name is empty")

        vessels.append(
            {
                "company": company,
                "vessel_name": vessel_name,
                "mmsi_no": mmsi_no,
            }
        )

    return vessels


def group_vessels_by_mmsi(
    vessels: List[Dict[str, str]],
) -> Dict[str, List[Dict[str, str]]]:
    """
    동일 MMSI를 법인·선박명 매핑 목록으로 묶는다.

    예:
    {
      "414195000": [
        {"company": "SKBA", "vessel_name": "REN JIAN 8"},
        {"company": "SKBM", "vessel_name": "REN JIAN 8"}
      ]
    }
    """
    grouped: Dict[str, List[Dict[str, str]]] = {}

    for vessel in vessels:
        mmsi_no = vessel["mmsi_no"]

        grouped.setdefault(mmsi_no, []).append(
            {
                "company": vessel["company"],
                "vessel_name": vessel["vessel_name"],
            }
        )

    return grouped


class RequestRateLimiter:
    def __init__(self, minimum_interval_seconds: float) -> None:
        self.minimum_interval_seconds = minimum_interval_seconds
        self.last_request_started_at: float | None = None

    def wait(self) -> None:
        if self.last_request_started_at is not None:
            elapsed = time.monotonic() - self.last_request_started_at
            remaining = self.minimum_interval_seconds - elapsed

            if remaining > 0:
                print(
                    f"[INFO] Waiting {remaining:.1f} seconds "
                    f"for Marinesia rate limit"
                )
                time.sleep(remaining)

        self.last_request_started_at = time.monotonic()

def chunked(
    values: List[str],
    size: int,
) -> List[List[str]]:
    return [
        values[index:index + size]
        for index in range(0, len(values), size)
    ]


def normalize_bulk_data(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Bulk API의 data를 항상 위치정보 dict 목록으로 정규화한다.
    """
    data = payload.get("data")

    if isinstance(data, list):
        return [
            row
            for row in data
            if isinstance(row, dict)
        ]

    if isinstance(data, dict):
        # data가 MMSI별 dict 형태로 반환되는 경우도 방어적으로 처리
        records: List[Dict[str, Any]] = []

        for value in data.values():
            if isinstance(value, dict):
                records.append(value)
            elif isinstance(value, list):
                records.extend(
                    row
                    for row in value
                    if isinstance(row, dict)
                )

        return records

    return []


def select_latest_by_mmsi(
    records: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """
    응답에 같은 MMSI가 여러 번 들어오더라도 ts 기준 최신 1건만 선택한다.
    """
    result: Dict[str, Dict[str, Any]] = {}

    for record in records:
        mmsi = str(record.get("mmsi") or "").strip()

        if not mmsi:
            continue

        current = result.get(mmsi)

        if current is None:
            result[mmsi] = record
            continue

        record_ts = str(record.get("ts") or "")
        current_ts = str(current.get("ts") or "")

        if record_ts and (
            not current_ts
            or record_ts > current_ts
        ):
            result[mmsi] = record

    return result


def fetch_bulk(
    session: requests.Session,
    api_key: str,
    mmsi_batch: List[str],
) -> Dict[str, Any]:
    """
    최대 10개의 MMSI를 한 번의 API 요청으로 조회한다.
    """
    if not mmsi_batch:
        raise ValueError("mmsi_batch is empty")

    if len(mmsi_batch) > BULK_SIZE:
        raise ValueError(
            f"Bulk request supports max {BULK_SIZE} MMSIs, "
            f"received {len(mmsi_batch)}"
        )

    response = session.get(
        BULK_URL,
        params={
            "mmsi": ",".join(mmsi_batch),
            "key": api_key,
        },
        timeout=60,
    )

    response.raise_for_status()

    payload = response.json()

    if not isinstance(payload, dict):
        raise ValueError(
            "Unexpected Bulk API response type: "
            f"{type(payload).__name__}"
        )

    return payload


def expand_bulk_response(
    payload: Dict[str, Any],
    mmsi_batch: List[str],
    grouped: Dict[str, List[Dict[str, str]]],
    fetched_at: str,
) -> List[Dict[str, Any]]:
    """
    Bulk 응답을 기존 raw_results 구조로 변환한다.

    따라서 flatten_rows()와 build_latest_snapshot()은
    수정하지 않고 그대로 사용할 수 있다.
    """
    result: List[Dict[str, Any]] = []

    api_error = bool(payload.get("error"))
    api_message = payload.get("message")
    records = normalize_bulk_data(payload)
    records_by_mmsi = select_latest_by_mmsi(records)

    for mmsi_no in mmsi_batch:
        record = records_by_mmsi.get(mmsi_no)

        if api_error:
            vessel_response = {
                "error": True,
                "message": api_message or "Bulk API returned error",
                "data": [],
            }

        elif record is None:
            vessel_response = {
                "error": False,
                "message": (
                    f"No latest location returned for MMSI "
                    f"{mmsi_no}"
                ),
                "data": [],
            }

        else:
            vessel_response = {
                "error": False,
                "message": (
                    api_message
                    or "Successfully fetched data"
                ),
                # 기존 flatten_rows()가 data 배열을 처리하므로
                # 최신 위치 1건도 배열 형태로 유지
                "data": [record],
            }

        result.append(
            {
                "query_mmsi_no": mmsi_no,
                "fetched_at": fetched_at,
                "mappings": grouped.get(mmsi_no, []),
                "response": vessel_response,
            }
        )

    return result

def build_empty_api_values() -> Dict[str, Any]:
    return {field: None for field in API_FIELDS}


def flatten_rows(
    raw_results: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    고유 MMSI별 API 응답을 법인·선박 매핑별 테이블 행으로 확장한다.

    동일 MMSI가 SKBA와 SKBM 양쪽에 있으면 동일 응답이
    각 법인 행에 각각 포함된다.
    """
    rows: List[Dict[str, Any]] = []

    for item in raw_results:
        query_mmsi_no = item["query_mmsi_no"]
        fetched_at = item["fetched_at"]
        mappings = item.get("mappings") or []
        payload = item.get("response") or {}

        api_error = bool(payload.get("error"))
        api_message = payload.get("message")

        data_list = payload.get("data") or []

        if not isinstance(data_list, list):
            data_list = []

        # API 오류 또는 위치 이력이 없는 경우에도
        # 입력 선박별로 한 행을 생성한다.
        if api_error or not data_list:
            for mapping in mappings:
                rows.append(
                    {
                        "company": mapping.get("company"),
                        "vessel_name": mapping.get("vessel_name"),
                        "query_mmsi_no": query_mmsi_no,
                        "record_index": None,
                        **build_empty_api_values(),
                        "fetched_at": fetched_at,
                        "api_message": api_message,
                        "api_error": api_error,
                    }
                )

            continue

        # API가 반환한 모든 위치 이력을 법인별로 확장한다.
        for mapping in mappings:
            for record_index, data in enumerate(data_list, start=1):
                if not isinstance(data, dict):
                    continue

                row = {
                    "company": mapping.get("company"),
                    "vessel_name": mapping.get("vessel_name"),
                    "query_mmsi_no": query_mmsi_no,
                    "record_index": record_index,
                }

                for field in API_FIELDS:
                    row[field] = data.get(field)

                row["fetched_at"] = fetched_at
                row["api_message"] = api_message
                row["api_error"] = api_error

                rows.append(row)

    return rows


def build_latest_snapshot(
    rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    company + vessel_name + MMSI별 최신 위치 1건을 남긴다.

    같은 MMSI가 서로 다른 법인에 등록되어 있어도
    법인별 최신 행이 각각 보존된다.
    """
    latest_map: Dict[str, Dict[str, Any]] = {}

    for row in rows:
        company = str(row.get("company") or "")
        vessel_name = str(row.get("vessel_name") or "")
        mmsi_no = str(
            row.get("query_mmsi_no")
            or row.get("mmsi")
            or ""
        )

        vessel_key = f"{company}|{vessel_name}|{mmsi_no}"

        current = latest_map.get(vessel_key)

        if current is None:
            latest_map[vessel_key] = row
            continue

        row_ts = str(row.get("ts") or "")
        current_ts = str(current.get("ts") or "")

        # ISO 형식 timestamp이므로 문자열 비교로 최신값 선택 가능
        if row_ts and (not current_ts or row_ts > current_ts):
            latest_map[vessel_key] = row

    latest_rows = list(latest_map.values())

    latest_rows.sort(
        key=lambda row: (
            str(row.get("company") or ""),
            str(row.get("vessel_name") or ""),
            str(row.get("query_mmsi_no") or ""),
        )
    )

    return latest_rows


def save_json(data: Any, path: str) -> None:
    ensure_dir(os.path.dirname(path) or ".")

    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
        )


def save_csv(
    rows: List[Dict[str, Any]],
    path: str,
) -> None:
    ensure_dir(os.path.dirname(path) or ".")

    # utf-8-sig로 저장하면 한국어 Excel에서도 바로 열기 편하다.
    with open(
        path,
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=OUTPUT_FIELDS,
            extrasaction="ignore",
        )

        writer.writeheader()

        for row in rows:
            writer.writerow(
                {
                    field: row.get(field)
                    for field in OUTPUT_FIELDS
                }
            )


def html_value(value: Any) -> str:
    if value is None:
        return ""

    if value is True:
        return "true"

    if value is False:
        return "false"

    return escape(str(value))


def build_html_table(
    rows: List[Dict[str, Any]],
) -> str:
    header_html = "".join(
        f"<th>{escape(field)}</th>"
        for field in OUTPUT_FIELDS
    )

    body_rows: List[str] = []

    for row in rows:
        cells = "".join(
            f"<td>{html_value(row.get(field))}</td>"
            for field in OUTPUT_FIELDS
        )

        body_rows.append(f"<tr>{cells}</tr>")

    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Marinesia Vessel Table</title>
</head>
<body style="
  margin: 0;
  padding: 20px;
  font-family: Arial, Helvetica, sans-serif;
  color: #222;
">
  <h2 style="margin: 0 0 8px 0;">
    Marinesia Vessel Position Table
  </h2>

  <p style="margin: 0 0 16px 0; color: #666;">
    Generated at: {escape(now_iso())}<br>
    Total table rows: {len(rows)}
  </p>

  <div style="overflow-x: auto;">
    <table style="
      border-collapse: collapse;
      white-space: nowrap;
      font-size: 12px;
    ">
      <thead>
        <tr style="background-color: #f0f0f0;">
          {header_html}
        </tr>
      </thead>
      <tbody>
        {''.join(body_rows)}
      </tbody>
    </table>
  </div>
</body>
</html>
""".replace(
        "<th>",
        '<th style="border:1px solid #bbb;padding:5px 7px;">',
    ).replace(
        "<td>",
        '<td style="border:1px solid #ccc;padding:4px 6px;">',
    )


def main() -> None:
    api_key = os.environ.get(
        "MARINESIA_API_KEY",
        "",
    ).strip()

    if not api_key:
        raise RuntimeError(
            "MARINESIA_API_KEY environment variable is empty"
        )

    ensure_dir("output")

    vessels = load_vessels()
    grouped = group_vessels_by_mmsi(vessels)

    unique_mmsi_list = list(grouped.keys())
    batches = chunked(
        unique_mmsi_list,
        BULK_SIZE,
    )

    print(f"[INFO] Total vessel mappings: {len(vessels)}")
    print(f"[INFO] Unique MMSIs: {len(unique_mmsi_list)}")
    print(f"[INFO] Bulk request size: {BULK_SIZE}")
    print(f"[INFO] Total Bulk API requests: {len(batches)}")
    print(
        f"[INFO] Duplicate MMSI requests avoided: "
        f"{len(vessels) - len(unique_mmsi_list)}"
    )

    raw_results: List[Dict[str, Any]] = []

    limiter = RequestRateLimiter(
        minimum_interval_seconds=MIN_INTERVAL_SECONDS
    )

    with requests.Session() as session:
        session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": (
                    "SK-On-Marinesia-GitHub-Action/2.0"
                ),
            }
        )

        for batch_index, mmsi_batch in enumerate(
            batches,
            start=1,
        ):
            limiter.wait()

            print(
                f"[INFO] Bulk request "
                f"{batch_index}/{len(batches)} | "
                f"vessels={len(mmsi_batch)} | "
                f"MMSI={','.join(mmsi_batch)}"
            )

            fetched_at = now_iso()

            try:
                payload = fetch_bulk(
                    session=session,
                    api_key=api_key,
                    mmsi_batch=mmsi_batch,
                )

                batch_results = expand_bulk_response(
                    payload=payload,
                    mmsi_batch=mmsi_batch,
                    grouped=grouped,
                    fetched_at=fetched_at,
                )

                raw_results.extend(batch_results)

                returned_records = normalize_bulk_data(
                    payload
                )

                print(
                    f"[INFO] Bulk request success | "
                    f"requested={len(mmsi_batch)}, "
                    f"returned={len(returned_records)}"
                )

            except Exception as exc:
                print(
                    f"[WARN] Bulk request failed | "
                    f"MMSI={','.join(mmsi_batch)} | "
                    f"error={exc}"
                )

                # Bulk 요청 하나가 실패하더라도 다른 batch는 계속 진행한다.
                for mmsi_no in mmsi_batch:
                    raw_results.append(
                        {
                            "query_mmsi_no": mmsi_no,
                            "fetched_at": fetched_at,
                            "mappings": grouped.get(
                                mmsi_no,
                                [],
                            ),
                            "response": {
                                "error": True,
                                "message": str(exc),
                                "data": [],
                            },
                        }
                    )

    # 아래 부분은 기존 구조를 그대로 사용
    table_rows = flatten_rows(raw_results)
    latest_rows = build_latest_snapshot(table_rows)

    save_json(raw_results, RAW_PATH)
    save_csv(table_rows, CSV_PATH)
    save_json(latest_rows, LATEST_PATH)

    html = build_html_table(table_rows)

    with open(
        HTML_PATH,
        "w",
        encoding="utf-8",
    ) as f:
        f.write(html)

    success_count = sum(
        1
        for row in latest_rows
        if row.get("api_error") is not True
        and row.get("lat") is not None
        and row.get("lng") is not None
    )

    error_count = sum(
        1
        for row in latest_rows
        if row.get("api_error") is True
    )

    no_location_count = (
        len(latest_rows)
        - success_count
        - error_count
    )

    print(f"[INFO] Saved raw JSON: {RAW_PATH}")
    print(f"[INFO] Saved full CSV: {CSV_PATH}")
    print(f"[INFO] Saved full HTML: {HTML_PATH}")
    print(f"[INFO] Saved latest JSON: {LATEST_PATH}")
    print(f"[INFO] Full table rows: {len(table_rows)}")
    print(
        f"[INFO] Latest vessel mappings: "
        f"{len(latest_rows)}"
    )
    print(
        f"[INFO] Location success: {success_count}, "
        f"no location: {no_location_count}, "
        f"API error: {error_count}"
    )


if __name__ == "__main__":
    main()
