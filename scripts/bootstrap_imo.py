"""
기존 vessels.json 의 MMSI 로 Marinesia v2 API 를 조회해 IMO 를 채운다.

1회성 전환 스크립트다. 실행 후에는 fetch_marinesia_table.py 가
IMO 를 기준키로 사용한다.

사용법:
    MARINESIA_API_KEY=xxxx python scripts/bootstrap_imo.py
    MARINESIA_API_KEY=xxxx python scripts/bootstrap_imo.py --dry-run

IMO 를 찾지 못한 선박은 mmsi_no 만 남겨 두며,
fetch_marinesia_table.py 가 그 선박만 MMSI 로 조회한다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import requests

VESSELS_PATH = Path("input/vessels.json")
BULK_URL = "https://api.marinesia.com/api/v2/vessel/location/latest/bulk"
BULK_SIZE = 10
MIN_INTERVAL_SECONDS = 13.0  # Premium 5회/분 대비 여유


def imo_check_digit_ok(imo: str) -> bool:
    if len(imo) != 7 or not imo.isdigit():
        return False
    return sum(int(imo[i]) * (7 - i) for i in range(6)) % 10 == int(imo[6])


def chunked(items: List[str], size: int) -> List[List[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    api_key = os.environ.get("MARINESIA_API_KEY")

    if not api_key:
        print("[ERROR] MARINESIA_API_KEY 환경변수가 없습니다")
        return 1

    vessels: List[Dict[str, Any]] = json.loads(
        VESSELS_PATH.read_text(encoding="utf-8")
    )

    # 이미 IMO 가 있는 건 건너뛴다 (재실행 안전)
    todo = [
        v for v in vessels
        if not str(v.get("imo_no") or "").strip()
        and str(v.get("mmsi_no") or "").strip()
    ]

    print(f"[INFO] 전체 {len(vessels)}척 · IMO 조회 대상 {len(todo)}척")

    if not todo:
        print("[INFO] 모든 선박에 IMO가 이미 있습니다")
        return 0

    mmsi_to_imo: Dict[str, str] = {}
    batches = chunked([v["mmsi_no"] for v in todo], BULK_SIZE)

    with requests.Session() as session:
        session.headers.update({"Accept": "application/json"})

        for i, batch in enumerate(batches, start=1):
            if i > 1:
                time.sleep(MIN_INTERVAL_SECONDS)

            print(f"[INFO] {i}/{len(batches)} · MMSI {len(batch)}건 조회")

            try:
                resp = session.get(
                    BULK_URL,
                    params={"mmsi": ",".join(batch), "key": api_key},
                    timeout=60,
                )
                resp.raise_for_status()
                payload = resp.json()
            except Exception as exc:
                print(f"[WARN] 요청 실패: {exc}")
                continue

            for rec in payload.get("data") or []:
                if not isinstance(rec, dict):
                    continue

                mmsi = str(rec.get("mmsi") or "").strip()
                imo = str(rec.get("imo") or "").strip()

                if not mmsi or not imo or imo == "0":
                    continue

                if not imo_check_digit_ok(imo):
                    print(f"[WARN] MMSI {mmsi}: IMO 체크디지트 불일치 ({imo}) · 건너뜀")
                    continue

                mmsi_to_imo[mmsi] = imo

            missing = payload.get("meta", {}).get("missing") or []
            if missing:
                print(f"[WARN] 응답 없음: {', '.join(str(m) for m in missing)}")

    # 반영
    filled, not_found, conflicts = 0, [], []
    imo_seen: Dict[str, str] = {}

    for v in vessels:
        mmsi = str(v.get("mmsi_no") or "").strip()
        imo = mmsi_to_imo.get(mmsi)

        if not str(v.get("imo_no") or "").strip():
            if imo:
                if imo in imo_seen and imo_seen[imo] != v["vessel_name"]:
                    conflicts.append(
                        f"IMO {imo} 중복: {imo_seen[imo]} / {v['vessel_name']}"
                    )
                imo_seen[imo] = v["vessel_name"]
                v["imo_no"] = imo
                filled += 1
            else:
                v["imo_no"] = ""
                not_found.append(f"{v['vessel_name']} ({mmsi})")

    ordered = [
        {
            "vessel_name": v["vessel_name"],
            "imo_no": v.get("imo_no", ""),
            "mmsi_no": v.get("mmsi_no", ""),
        }
        for v in vessels
    ]
    ordered.sort(key=lambda r: (r["vessel_name"], r["imo_no"] or r["mmsi_no"]))

    print()
    print(f"[RESULT] IMO 확보 {filled}척 / 미확보 {len(not_found)}척")

    if not_found:
        print("[RESULT] IMO 미확보 (MMSI로 계속 조회됩니다):")
        for n in not_found:
            print(f"  - {n}")

    if conflicts:
        print("[ALERT] 동일 IMO에 다른 선박명이 매핑되었습니다. 확인이 필요합니다:")
        for c in conflicts:
            print(f"  - {c}")

    if args.dry_run:
        print("\n[INFO] --dry-run 이므로 파일을 저장하지 않았습니다")
        return 0

    VESSELS_PATH.write_text(
        json.dumps(ordered, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"\n[INFO] {VESSELS_PATH} 저장 완료")

    return 0


if __name__ == "__main__":
    sys.exit(main())
