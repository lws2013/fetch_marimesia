from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import folium
from folium import DivIcon

try:
    import searoute as sr
except Exception:
    sr = None


SUMMARY_PATH = "output/summary.json"
MAP_HTML_PATH = "output/map.html"


# 임시 포트 좌표 테이블
# 지금은 next_port 문자열 기반으로 대략적인 목적지 좌표를 잡기 위한 용도
# 나중에 shipments.json에 POL/POD 5자리 포트코드가 들어오면 그쪽 기준으로 교체하면 됨
PORT_COORDS = {
    "SINGAPORE": (1.2903, 103.8198),
    "SAVANNAH": (32.0809, -81.0998),
    "LOS ANGELES": (33.7405, -118.2437),
    "LONG BEACH": (33.7701, -118.1937),
    "BUSAN": (35.1796, 129.0756),
    "HONG KONG": (22.3193, 114.1694),
    "SHANGHAI": (31.2304, 121.4737),
    "NINGBO": (29.8683, 121.5440),
    "QINGDAO": (36.0671, 120.3826),
    "YANTIAN": (22.5550, 114.2560),
    "SHEKOU": (22.4796, 113.9166),
    "KAOHSIUNG": (22.6273, 120.3014),
    "NEW YORK": (40.7128, -74.0060),
    "NORFOLK": (36.8508, -76.2859),
    "CHARLESTON": (32.7765, -79.9311),
    "JACKSONVILLE": (30.3322, -81.6557),
    "OAKLAND": (37.8044, -122.2712),
    "SEATTLE": (47.6062, -122.3321),
    "VANCOUVER": (49.2827, -123.1207),
    "MANZANILLO": (19.1138, -104.3385),
    "PANAMA": (8.9824, -79.5199),
}


def load_summary(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def parse_date_short(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        # 예: 2026-04-23T09:00:00+00:00
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.strftime("%m-%d %H:%M")
    except Exception:
        return str(value)[:16]


def normalize_port_name(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return " ".join(str(value).strip().upper().split())


def lookup_port_coord(port_name: Optional[str]) -> Optional[Tuple[float, float]]:
    """
    return: (lat, lon)
    """
    if not port_name:
        return None

    normalized = normalize_port_name(port_name)

    # exact
    if normalized in PORT_COORDS:
        return PORT_COORDS[normalized]

    # contains
    for key, coord in PORT_COORDS.items():
        if key in normalized or normalized in key:
            return coord

    return None


def build_popup_html(item: Dict[str, Any]) -> str:
    vessel_name = item.get("vessel_name") or ""
    shipment_id = item.get("shipment_id") or ""
    status = item.get("position_status") or ""
    last_seen = parse_date_short(item.get("last_seen_at"))
    next_port = item.get("next_port") or ""
    lat = item.get("current_lat")
    lon = item.get("current_lon")

    return f"""
    <div style="font-size:12px; line-height:1.4;">
      <b>{vessel_name}</b><br>
      Shipment: {shipment_id}<br>
      Status: {status}<br>
      Last Seen: {last_seen}<br>
      Next Port: {next_port}<br>
      Lat/Lon: {lat}, {lon}
    </div>
    """


def add_text_label(m: folium.Map, lat: float, lon: float, vessel_name: str, last_seen_at: Optional[str]) -> None:
    label = f"{vessel_name}<br><span style='font-size:10px'>{parse_date_short(last_seen_at)}</span>"

    folium.map.Marker(
        [lat, lon],
        icon=DivIcon(
            icon_size=(180, 36),
            icon_anchor=(0, 0),
            html=f"""
            <div style="
                font-size: 10px;
                color: #111;
                background-color: rgba(255,255,255,0.8);
                border: 1px solid #999;
                border-radius: 4px;
                padding: 2px 4px;
                white-space: nowrap;
            ">
                {label}
            </div>
            """,
        ),
    ).add_to(m)


def add_vessel_marker(m: folium.Map, item: Dict[str, Any]) -> None:
    lat = item.get("current_lat")
    lon = item.get("current_lon")
    if lat is None or lon is None:
        return

    popup_html = build_popup_html(item)

    folium.CircleMarker(
        location=[lat, lon],
        radius=6,
        popup=folium.Popup(popup_html, max_width=320),
        tooltip=item.get("vessel_name") or "Vessel",
    ).add_to(m)

    add_text_label(
        m,
        lat=float(lat),
        lon=float(lon),
        vessel_name=item.get("vessel_name") or "Vessel",
        last_seen_at=item.get("last_seen_at"),
    )


def build_route_points(start_lat: float, start_lon: float, end_lat: float, end_lon: float) -> List[List[float]]:
    """
    folium용 좌표는 [lat, lon]
    searoute 입력은 보통 [lon, lat]
    """
    if sr is None:
        return [[start_lat, start_lon], [end_lat, end_lon]]

    try:
        route = sr.searoute(
            [start_lon, start_lat],
            [end_lon, end_lat],
        )

        coords = route["geometry"]["coordinates"]
        # searoute -> GeoJSON 순서 [lon, lat] 이므로 folium용 [lat, lon]으로 변환
        return [[lat, lon] for lon, lat in coords]
    except Exception:
        return [[start_lat, start_lon], [end_lat, end_lon]]


def add_route_line(m: folium.Map, item: Dict[str, Any]) -> None:
    start_lat = item.get("current_lat")
    start_lon = item.get("current_lon")
    if start_lat is None or start_lon is None:
        return

    next_port = item.get("next_port")
    dest = lookup_port_coord(next_port)
    if dest is None:
        return

    end_lat, end_lon = dest
    points = build_route_points(float(start_lat), float(start_lon), end_lat, end_lon)

    folium.PolyLine(
        locations=points,
        weight=2,
        opacity=0.8,
        tooltip=f"{item.get('vessel_name') or 'Vessel'} route to {next_port}",
    ).add_to(m)

    folium.CircleMarker(
        location=[end_lat, end_lon],
        radius=4,
        tooltip=f"Next Port: {next_port}",
        popup=folium.Popup(f"<b>Next Port</b><br>{next_port}", max_width=250),
    ).add_to(m)


def get_map_center(items: List[Dict[str, Any]]) -> Tuple[float, float]:
    coords = [
        (float(x["current_lat"]), float(x["current_lon"]))
        for x in items
        if x.get("current_lat") is not None and x.get("current_lon") is not None
    ]

    if not coords:
        return (20.0, 120.0)

    lat_avg = sum(x[0] for x in coords) / len(coords)
    lon_avg = sum(x[1] for x in coords) / len(coords)
    return lat_avg, lon_avg


def build_map(summary: Dict[str, Any]) -> folium.Map:
    map_items = summary.get("map_items", [])
    center_lat, center_lon = get_map_center(map_items)

    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=3,
        control_scale=True,
        tiles="CartoDB positron",
    )

    title_html = f"""
    <div style="
        position: fixed;
        top: 10px;
        left: 50px;
        z-index: 9999;
        background-color: rgba(255,255,255,0.9);
        padding: 8px 12px;
        border: 1px solid #999;
        border-radius: 6px;
        font-size: 14px;
        font-weight: bold;
    ">
        Americas Vessel Position Map<br>
        <span style="font-size:11px; font-weight:normal;">
            Generated: {summary.get("generated_at")}
        </span>
    </div>
    """
    m.get_root().html.add_child(folium.Element(title_html))

    valid_items = [
        x for x in map_items
        if x.get("current_lat") is not None and x.get("current_lon") is not None
    ]

    for item in valid_items:
        add_route_line(m, item)

    for item in valid_items:
        add_vessel_marker(m, item)

    if valid_items:
        bounds = [
            [float(x["current_lat"]), float(x["current_lon"])]
            for x in valid_items
        ]
        m.fit_bounds(bounds, padding=(40, 40))

    return m


def main() -> None:
    ensure_dir("output")
    summary = load_summary(SUMMARY_PATH)
    m = build_map(summary)
    m.save(MAP_HTML_PATH)
    print(f"[INFO] map saved to {MAP_HTML_PATH}")


if __name__ == "__main__":
    main()
