from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Tuple

import requests


INPUT_PATH = "input/shipments.json"
LATEST_PATH = "output/latest_positions.json"
SUMMARY_PATH = "output/summary.json"
STATE_PATH = "output/rotation_state.json"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def load_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data: Any) -> None:
    ensure_dir(os.path.dirname(path) or ".")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_shipments() -> List[Dict[str, Any]]:
    data = load_json(INPUT_PATH, [])
    if not isinstance(data, list):
        raise ValueError("input/shipments.json must be a list")
    return data


def load_state() -> Dict[str, Any]:
    return load_json(
        STATE_PATH,
        {
            "next_index": 0,
            "cycle_no": 1,
            "last_completed_cycle_at": None,
        },
    )


def load_latest_positions(shipments: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    existing = load_json(LATEST_PATH, [])
    result_map: Dict[str, Dict[str, Any]] = {}

    if isinstance(existing, list):
        for row in existing:
            shipment_id = row.get("shipment_id")
            if shipment_id:
                result_map[shipment_id] = row

    # 입력에 있는 선박은 최소 skeleton 보장
    for row in shipments:
        shipment_id = row["shipment_id"]
        if shipment_id not in result_map:
            result_map[shipment_id] = {
                "shipment_id": shipment_id,
                "vessel_name": row.get("vessel_name"),
                "mmsi_no": row.get("mmsi_no"),
                "imo_no": row.get("imo_no"),
                "call_sign": row.get("call_sign"),
                "ok": False,
                "position_status": "NO_SIGNAL",
                "source": "marinesia",
                "collected_at": None,
                "current_lat": None,
                "current_lon": None,
                "speed": None,
                "heading": None,
                "last_seen_at": None,
                "next_port": None,
                "eta_ais": None,
                "error": "Not fetched yet",
            }

    return result_map


def marinesia_fetch_latest(row: Dict[str, Any], api_key: str) -> Dict[str, Any]:
    """
    Free plan 기준: v2 latest location endpoint 사용
    https://api.marinesia.com/api/v2/vessel/location/latest?imo=...&key=...
    또는
    https://api.marinesia.com/api/v2/vessel/location/latest?mmsi=...&key=...
    """
    imo_no = row.get("imo_no")
    mmsi_no = row.get("mmsi_no")

    base_url = "https://api.marinesia.com/api/v2/vessel/location/latest"
    params = {"key": api_key}

    if imo_no:
        params["imo"] = str(imo_no)
    elif mmsi_no:
        params["mmsi"] = str(mmsi_no)
    else:
        return {
            "shipment_id": row["shipment_id"],
            "vessel_name": row.get("vessel_name"),
            "mmsi_no": mmsi_no,
            "imo_no": imo_no,
            "call_sign": row.get("call_sign"),
            "ok": False,
            "position_status": "NO_SIGNAL",
            "source": "marinesia",
            "collected_at": now_iso(),
            "current_lat": None,
            "current_lon": None,
            "speed": None,
            "heading": None,
            "last_seen_at": None,
            "next_port": None,
            "eta_ais": None,
            "error": "imo_no and mmsi_no are both missing",
        }

    r = requests.get(base_url, params=params, timeout=30)
    r.raise_for_status()
    payload = r.json()

    if payload.get("error") is True:
        return {
            "shipment_id": row["shipment_id"],
            "vessel_name": row.get("vessel_name"),
            "mmsi_no": mmsi_no,
            "imo_no": imo_no,
            "call_sign": row.get("call_sign"),
            "ok": False,
            "position_status": "NO_SIGNAL",
            "source": "marinesia",
            "collected_at": now_iso(),
            "current_lat": None,
            "current_lon": None,
            "speed": None,
            "heading": None,
            "last_seen_at": None,
            "next_port": None,
            "eta_ais": None,
            "error": payload.get("message") or "Marinesia returned error=true",
        }

    data = payload.get("data") or {}

    lat = data.get("lat")
    lng = data.get("lng")
    ts = data.get("ts")

    ok = lat is not None and lng is not None

    return {
        "shipment_id": row["shipment_id"],
        "vessel_name": row.get("vessel_name"),
        "mmsi_no": str(data.get("mmsi") or mmsi_no) if (data.get("mmsi") or mmsi_no) else None,
        "imo_no": str(data.get("imo") or imo_no) if (data.get("imo") or imo_no) else None,
        "call_sign": row.get("call_sign"),
        "ok": ok,
        "position_status": "LIVE" if ok else "NO_SIGNAL",
        "source": "marinesia",
        "collected_at": now_iso(),
        "current_lat": lat,
        "current_lon": lng,
        "speed": data.get("sog"),
        "heading": data.get("hdt") if data.get("hdt") is not None else data.get("cog"),
        "last_seen_at": ts,
        "next_port": data.get("dest"),
        "eta_ais": data.get("eta"),
        "error": None if ok else "No latest position in response",
    }


def build_summary(results: List[Dict[str, Any]], cycle_completed_at: str | None) -> Dict[str, Any]:
    live_count = sum(1 for x in results if x.get("position_status") == "LIVE")
    stale_count = sum(1 for x in results if x.get("position_status") == "STALE")
    no_signal_count = sum(1 for x in results if x.get("position_status") == "NO_SIGNAL")

    map_items = []
    for row in results:
        map_items.append(
            {
                "shipment_id": row.get("shipment_id"),
                "vessel_name": row.get("vessel_name"),
                "mmsi_no": row.get("mmsi_no"),
                "imo_no": row.get("imo_no"),
                "call_sign": row.get("call_sign"),
                "position_status": row.get("position_status"),
                "current_lat": row.get("current_lat"),
                "current_lon": row.get("current_lon"),
                "speed": row.get("speed"),
                "heading": row.get("heading"),
                "last_seen_at": row.get("last_seen_at"),
                "next_port": row.get("next_port"),
                "eta_ais": row.get("eta_ais"),
                "source": row.get("source"),
                "collected_at": row.get("collected_at"),
                "error": row.get("error"),
            }
        )

    return {
        "generated_at": now_iso(),
        "summary": {
            "total": len(results),
            "live": live_count,
            "stale": stale_count,
            "no_signal": no_signal_count,
            "cycle_completed_at": cycle_completed_at,
        },
        "map_items": map_items,
    }


def process_once() -> Tuple[bool, Dict[str, Any]]:
    api_key = os.environ["MARINESIA_API_KEY"]

    shipments = load_shipments()
    if not shipments:
        raise RuntimeError("input/shipments.json is empty")

    state = load_state()
    latest_map = load_latest_positions(shipments)

    next_index = int(state.get("next_index", 0))
    cycle_no = int(state.get("cycle_no", 1))

    if next_index < 0 or next_index >= len(shipments):
        next_index = 0

    target = shipments[next_index]
    print(
        f"[INFO] cycle={cycle_no}, next_index={next_index}, "
        f"shipment_id={target['shipment_id']}, vessel={target.get('vessel_name')}"
    )

    fetched = marinesia_fetch_latest(target, api_key)
    latest_map[target["shipment_id"]] = fetched

    # 다음 index 계산
    completed_cycle = False
    if next_index + 1 >= len(shipments):
        completed_cycle = True
        next_index = 0
        cycle_no += 1
        state["last_completed_cycle_at"] = now_iso()
    else:
        next_index += 1

    state["next_index"] = next_index
    state["cycle_no"] = cycle_no

    latest_results = [latest_map[row["shipment_id"]] for row in shipments]
    summary = build_summary(latest_results, state.get("last_completed_cycle_at"))

    save_json(LATEST_PATH, latest_results)
    save_json(SUMMARY_PATH, summary)
    save_json(STATE_PATH, state)

    print(f"[INFO] fetched shipment_id={target['shipment_id']}, ok={fetched['ok']}, error={fetched['error']}")
    print(f"[INFO] completed_cycle={completed_cycle}, next_index={next_index}, cycle_no={cycle_no}")

    return completed_cycle, summary


if __name__ == "__main__":
    completed_cycle, summary = process_once()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
