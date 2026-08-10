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

# v2는 IMO 조회를 지원한다. imo와 mmsi를 동시에 보내면 mmsi가 우선되므로
# 반드시 둘 중 하나만 보낸다.
BULK_URL = (
    "https://api.marinesia.com/api/v2/"
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
    "vessel_name",
    "imo_no",
    "registered_mmsi_no",
    "query_key",
    "mmsi_changed",
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


def normalize_imo(value: Any) -> str:
    """IMO 7자리 + 체크디지트 검증. 실패 시 ValueError."""
    imo = str(value or "").strip().upper()

    if imo.startswith("IMO"):
        imo = imo[3:].strip()

    if not imo:
        raise ValueError("IMO is empty")

    if not imo.isdigit():
        raise ValueError(f"IMO must contain digits only: {imo}")

    if len(imo) != 7:
        raise ValueError(f"IMO must be 7 digits: {imo}")

    # 앞 6자리에 가중치 7,6,5,4,3,2를 곱한 합의 끝자리가 7번째 자리와 같아야 한다
    checksum = sum(int(imo[i]) * (7 - i) for i in range(6))

    if checksum % 10 != int(imo[6]):
        raise ValueError(f"IMO check digit mismatch: {imo}")

    return imo


def load_vessels() -> List[Dict[str, str]]:
    """
    input/vessels.json을 읽어 IMO 기준으로 중복을 제거한 목록을 반환한다.

    IMO가 없는 선박(소형선 등)은 MMSI로 조회한다.
    """
    path = Path(INPUT_PATH)

    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {INPUT_PATH}")

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("input/vessels.json must contain a JSON list")

    vessels: List[Dict[str, str]] = []
    seen: Dict[str, str] = {}
    no_imo = 0

    for index, row in enumerate(data, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"Row {index} must be a JSON object")

        vessel_name = str(row.get("vessel_name") or "").strip()

        if not vessel_name:
            raise ValueError(f"Row {index}: vessel_name is empty")

        raw_imo = str(row.get("imo_no") or "").strip()
        mmsi_no = ""

        if row.get("mmsi_no"):
            try:
                mmsi_no = normalize_mmsi(row.get("mmsi_no"))
            except ValueError as exc:
                print(f"[WARN] Row {index} ({vessel_name}): {exc}")

        if raw_imo:
            imo_no = normalize_imo(raw_imo)
            key = imo_no
            query_by = "imo"
        else:
            # IMO 미확보 선박은 MMSI로 조회한다 (bootstrap_imo.py로 보완 가능)
            if not mmsi_no:
                raise ValueError(
                    f"Row {index} ({vessel_name}): imo_no와 mmsi_no가 모두 없음"
                )
            imo_no = ""
            key = mmsi_no
            query_by = "mmsi"
            no_imo += 1
            print(f"[WARN] {vessel_name}: IMO 없음. MMSI {mmsi_no}로 조회합니다")

        if key in seen:
            print(f"[INFO] Duplicate key skipped: {key} ({vessel_name})")
            continue

        seen[key] = vessel_name
        vessels.append(
            {
                "vessel_name": vessel_name,
                "imo_no": imo_no,
                "mmsi_no": mmsi_no,
                "query_key": key,
                "query_by": query_by,
            }
        )

    print(
        f"[INFO] Loaded {len(vessels)} unique vessels from {len(data)} rows "
        f"(IMO 조회 {len(vessels) - no_imo}척, MMSI 조회 {no_imo}척)"
    )

    return vessels


def group_vessels_by_key(
    vessels: List[Dict[str, str]],
) -> Dict[str, List[Dict[str, str]]]:
    """조회 키(IMO 우선, 없으면 MMSI)별 선박 매핑을 만든다."""
    grouped: Dict[str, List[Dict[str, str]]] = {}

    for vessel in vessels:
        grouped.setdefault(vessel["query_key"], []).append(
            {
                "vessel_name": vessel["vessel_name"],
                "imo_no": vessel["imo_no"],
                "mmsi_no": vessel["mmsi_no"],
                "query_by": vessel["query_by"],
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


def select_latest_by_key(
    records: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """
    응답 레코드를 IMO와 MMSI 양쪽 키로 색인한다.

    IMO로 조회해도 응답에는 mmsi와 imo가 함께 오므로
    두 키 모두로 찾을 수 있게 해 둔다.
    같은 키가 여러 번 오면 ts 기준 최신 1건만 남긴다.
    """
    result: Dict[str, Dict[str, Any]] = {}

    def put(key: str, record: Dict[str, Any]) -> None:
        if not key:
            return

        current = result.get(key)

        if current is None:
            result[key] = record
            return

        record_ts = str(record.get("ts") or "")
        current_ts = str(current.get("ts") or "")

        if record_ts and (not current_ts or record_ts > current_ts):
            result[key] = record

    for record in records:
        put(str(record.get("imo") or "").strip(), record)
        put(str(record.get("mmsi") or "").strip(), record)

    return result


def fetch_bulk(
    session: requests.Session,
    api_key: str,
    key_batch: List[str],
    query_by: str,
) -> Dict[str, Any]:
    """
    최대 10척을 한 번의 v2 Bulk 요청으로 조회한다.

    query_by가 "imo"이면 imo 파라미터만, "mmsi"이면 mmsi 파라미터만 보낸다.
    (v2는 둘 다 채우면 mmsi를 우선 적용하므로 반드시 하나만 보낸다.)
    """
    if not key_batch:
        raise ValueError("key_batch is empty")

    if len(key_batch) > BULK_SIZE:
        raise ValueError(
            f"Bulk request supports max {BULK_SIZE} vessels, "
            f"received {len(key_batch)}"
        )

    if query_by not in ("imo", "mmsi"):
        raise ValueError(f"Unsupported query_by: {query_by}")

    response = session.get(
        BULK_URL,
        params={
            query_by: ",".join(key_batch),
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
    key_batch: List[str],
    grouped: Dict[str, List[Dict[str, str]]],
    fetched_at: str,
) -> List[Dict[str, Any]]:
    """Bulk 응답을 기존 raw_results 구조로 변환한다."""
    result: List[Dict[str, Any]] = []

    api_error = bool(payload.get("error"))
    api_message = payload.get("message")
    records = normalize_bulk_data(payload)
    records_by_key = select_latest_by_key(records)

    for query_key in key_batch:
        record = records_by_key.get(query_key)

        if api_error:
            vessel_response = {
                "error": True,
                "message": api_message or "Bulk API returned error",
                "data": [],
            }

        elif record is None:
            vessel_response = {
                "error": False,
                "message": f"No latest location returned for {query_key}",
                "data": [],
            }

        else:
            vessel_response = {
                "error": False,
                "message": api_message or "Successfully fetched data",
                "data": [record],
            }

        result.append(
            {
                "query_key": query_key,
                "fetched_at": fetched_at,
                "mappings": grouped.get(query_key, []),
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
    조회 키별 API 응답을 테이블 행으로 펼친다.

    등록된 MMSI와 API가 반환한 MMSI가 다르면 mmsi_changed 플래그를 세운다.
    선박이 재선적(reflag)되어 MMSI가 재발급된 경우를 잡아내기 위한 것으로,
    IMO를 기준키로 쓸 때만 가능한 자가 점검이다.
    """
    rows: List[Dict[str, Any]] = []

    for item in raw_results:
        query_key = item["query_key"]
        fetched_at = item["fetched_at"]
        mappings = item.get("mappings") or []
        payload = item.get("response") or {}

        api_error = bool(payload.get("error"))
        api_message = payload.get("message")

        data_list = payload.get("data") or []

        if not isinstance(data_list, list):
            data_list = []

        if api_error or not data_list:
            for mapping in mappings:
                rows.append(
                    {
                        "vessel_name": mapping.get("vessel_name"),
                        "imo_no": mapping.get("imo_no"),
                        "registered_mmsi_no": mapping.get("mmsi_no"),
                        "query_key": query_key,
                        "mmsi_changed": None,
                        "record_index": None,
                        **build_empty_api_values(),
                        "fetched_at": fetched_at,
                        "api_message": api_message,
                        "api_error": api_error,
                    }
                )

            continue

        for mapping in mappings:
            for record_index, data in enumerate(data_list, start=1):
                if not isinstance(data, dict):
                    continue

                registered_mmsi = str(mapping.get("mmsi_no") or "")
                api_mmsi = str(data.get("mmsi") or "").strip()

                mmsi_changed = bool(
                    registered_mmsi
                    and api_mmsi
                    and registered_mmsi != api_mmsi
                )

                if mmsi_changed and record_index == 1:
                    print(
                        f"[ALERT] {mapping.get('vessel_name')} "
                        f"(IMO {mapping.get('imo_no')}): "
                        f"MMSI 변경 감지 {registered_mmsi} -> {api_mmsi}. "
                        "재선적 가능성이 있으니 vessels.json을 갱신하세요"
                    )

                row = {
                    "vessel_name": mapping.get("vessel_name"),
                    "imo_no": mapping.get("imo_no"),
                    "registered_mmsi_no": registered_mmsi,
                    "query_key": query_key,
                    "mmsi_changed": mmsi_changed,
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
    vessel_name + 조회키별 최신 위치 1건을 남긴다.
    """
    latest_map: Dict[str, Dict[str, Any]] = {}

    for row in rows:
        vessel_name = str(row.get("vessel_name") or "")
        query_key = str(row.get("query_key") or row.get("mmsi") or "")

        vessel_key = f"{vessel_name}|{query_key}"

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
            str(row.get("vessel_name") or ""),
            str(row.get("query_key") or ""),
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
    grouped = group_vessels_by_key(vessels)

    imo_keys = [v["query_key"] for v in vessels if v["query_by"] == "imo"]
    mmsi_keys = [v["query_key"] for v in vessels if v["query_by"] == "mmsi"]

    # (query_by, batch) 튜플 목록. IMO와 MMSI는 파라미터가 달라 섞을 수 없다.
    batches: List[tuple] = [
        ("imo", b) for b in chunked(imo_keys, BULK_SIZE)
    ] + [
        ("mmsi", b) for b in chunked(mmsi_keys, BULK_SIZE)
    ]

    print(f"[INFO] Total vessels: {len(vessels)}")
    print(f"[INFO] IMO 조회: {len(imo_keys)}척 / MMSI 조회: {len(mmsi_keys)}척")
    print(f"[INFO] Bulk request size: {BULK_SIZE}")
    print(f"[INFO] Total Bulk API requests: {len(batches)}")

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

        for batch_index, (query_by, key_batch) in enumerate(
            batches,
            start=1,
        ):
            limiter.wait()

            print(
                f"[INFO] Bulk request "
                f"{batch_index}/{len(batches)} | "
                f"by={query_by} | "
                f"vessels={len(key_batch)} | "
                f"keys={','.join(key_batch)}"
            )

            fetched_at = now_iso()

            try:
                payload = fetch_bulk(
                    session=session,
                    api_key=api_key,
                    key_batch=key_batch,
                    query_by=query_by,
                )

                batch_results = expand_bulk_response(
                    payload=payload,
                    key_batch=key_batch,
                    grouped=grouped,
                    fetched_at=fetched_at,
                )

                raw_results.extend(batch_results)

                returned_records = normalize_bulk_data(
                    payload
                )

                print(
                    f"[INFO] Bulk request success | "
                    f"requested={len(key_batch)}, "
                    f"returned={len(returned_records)}"
                )

            except Exception as exc:
                print(
                    f"[WARN] Bulk request failed | "
                    f"keys={','.join(key_batch)} | "
                    f"error={exc}"
                )

                # Bulk 요청 하나가 실패하더라도 다른 batch는 계속 진행한다.
                for query_key in key_batch:
                    raw_results.append(
                        {
                            "query_key": query_key,
                            "fetched_at": fetched_at,
                            "mappings": grouped.get(
                                query_key,
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
