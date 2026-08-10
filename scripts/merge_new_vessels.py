"""
input/new.txt 의 신규 선박을 input/vessels.json 에 병합한다.

new.txt 형식 (탭 구분, 헤더 없음):
    MAERSK TAURUS<TAB>9784089            <- IMO 7자리 (권장)
    UNI PERFECT<TAB>357979000            <- MMSI 9자리도 허용
    ONE STORK<TAB>9784089<TAB>431986000  <- 둘 다 주면 가장 정확

- 자릿수로 IMO(7)와 MMSI(9)를 자동 구분한다
- IMO 는 체크디지트를 검증한다
- 이미 등록된 IMO(없으면 MMSI)는 건너뛴다
- 처리한 new.txt 는 input/archive/ 로 옮긴다
- 검증 실패 행이 있어도 나머지는 정상 처리하고, 실패 목록을 요약에 남긴다
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

VESSELS_PATH = Path("input/vessels.json")
NEW_PATH = Path(os.environ.get("NEW_VESSELS_PATH", "input/new.txt"))
ARCHIVE_DIR = Path("input/archive")
SUMMARY_PATH = Path("output/merge_summary.md")

KST = timezone(timedelta(hours=9))

# Excel 에서 복사하면 MMSI 가 지수 표기로 들어오는 경우가 있다
SCIENTIFIC = re.compile(r"^\d(\.\d+)?[eE]\+?\d+$")


def imo_check_digit_ok(imo: str) -> bool:
    """IMO 앞 6자리 × 가중치(7,6,5,4,3,2) 합의 끝자리 = 7번째 자리"""
    return sum(int(imo[i]) * (7 - i) for i in range(6)) % 10 == int(imo[6])


def read_text_any_encoding(path: Path) -> str:
    """
    Windows 메모장·Excel 에서 저장한 파일을 견디도록
    UTF-8(BOM 포함) → CP949 → UTF-8(치환) 순으로 시도한다.
    """
    raw = path.read_bytes()

    for encoding in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue

    return raw.decode("utf-8", errors="replace")


def clean(value: str) -> str:
    """전각 공백·전각 숫자·제어문자를 정리한다."""
    value = value.replace("\u3000", " ").replace("\ufeff", "")
    # 전각 숫자 → 반각
    value = "".join(
        chr(ord(ch) - 0xFEE0) if "０" <= ch <= "９" else ch
        for ch in value
    )
    return value.strip()


def validate_identifier(raw: str) -> Tuple[str, str, str]:
    """
    (종류, 정규화값, 경고문) 반환. 종류는 "imo" 또는 "mmsi".
    실패 시 ValueError.
    """
    value = clean(raw).upper()

    if value.startswith("IMO"):
        value = value[3:].strip()

    value = value.replace("-", "").replace(" ", "")

    if not value:
        raise ValueError("식별번호가 비어 있음")

    if SCIENTIFIC.match(value):
        raise ValueError(
            f"식별번호가 지수 표기('{value}')로 입력됨. "
            "Excel에서 해당 열을 '텍스트' 서식으로 바꾼 뒤 다시 복사하세요"
        )

    if not value.isdigit():
        raise ValueError(f"숫자가 아닌 문자 포함: '{value}'")

    if len(value) == 7:
        if not imo_check_digit_ok(value):
            raise ValueError(
                f"IMO 체크디지트 불일치: '{value}' · 오타 가능성이 높습니다"
            )
        return "imo", value, ""

    if len(value) == 9:
        warning = ""
        mid = int(value[:3])

        if not (201 <= mid <= 775):
            warning = (
                f"MID {mid} 는 선박용 범위(201-775) 밖입니다. "
                "기지국·항행보조시설 번호일 수 있으니 확인하세요"
            )

        return "mmsi", value, warning

    raise ValueError(
        f"IMO 7자리 또는 MMSI 9자리가 아님 ({len(value)}자리): '{value}'"
    )


def parse_new_file(path: Path) -> Tuple[List[Dict[str, str]], List[str], List[str]]:
    text = read_text_any_encoding(path)

    parsed: List[Dict[str, str]] = []
    errors: List[str] = []
    warnings: List[str] = []

    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue

        parts = line.split("\t")
        if len(parts) < 2:
            parts = re.split(r"\s{2,}", line.strip())

        if len(parts) < 2:
            errors.append(f"{lineno}행: 탭 구분자 없음 → `{line.strip()}`")
            continue

        name = clean(parts[0])

        if not name:
            errors.append(f"{lineno}행: 선박명이 비어 있음")
            continue

        if name.lower() in {"vessel_name", "vessel", "선박명", "name"}:
            continue

        imo_no, mmsi_no = "", ""
        row_errors: List[str] = []

        for token in parts[1:]:
            if not clean(token):
                continue

            try:
                kind, value, warn = validate_identifier(token)
            except ValueError as exc:
                row_errors.append(str(exc))
                continue

            if warn:
                warnings.append(f"{lineno}행 ({name}, {value}): {warn}")

            if kind == "imo":
                imo_no = value
            else:
                mmsi_no = value

        if not imo_no and not mmsi_no:
            errors.append(f"{lineno}행 ({name}): {'; '.join(row_errors)}")
            continue

        if row_errors:
            warnings.append(f"{lineno}행 ({name}): {'; '.join(row_errors)}")

        if not imo_no:
            warnings.append(
                f"{lineno}행 ({name}): IMO 없이 MMSI만 등록됩니다. "
                "bootstrap_imo.py 로 IMO를 보완할 수 있습니다"
            )

        parsed.append(
            {
                "vessel_name": name.upper(),
                "imo_no": imo_no,
                "mmsi_no": mmsi_no,
            }
        )

    return parsed, errors, warnings


def main() -> int:
    lines: List[str] = []
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")

    if not NEW_PATH.exists():
        print(f"[INFO] {NEW_PATH} 없음. 종료.")
        return 0

    vessels: List[Dict[str, str]] = json.loads(
        VESSELS_PATH.read_text(encoding="utf-8")
    )
    by_imo = {
        str(v.get("imo_no") or ""): v
        for v in vessels
        if str(v.get("imo_no") or "")
    }
    by_mmsi = {
        str(v.get("mmsi_no") or ""): v
        for v in vessels
        if str(v.get("mmsi_no") or "")
    }
    before = len(vessels)

    parsed, errors, warnings = parse_new_file(NEW_PATH)

    added: List[Dict[str, str]] = []
    skipped: List[str] = []
    renamed: List[str] = []
    enriched: List[str] = []

    for item in parsed:
        name = item["vessel_name"]
        imo_no = item["imo_no"]
        mmsi_no = item["mmsi_no"]

        # IMO 우선, 없으면 MMSI 로 기존 선박을 찾는다
        found = by_imo.get(imo_no) if imo_no else None

        if found is None and mmsi_no:
            found = by_mmsi.get(mmsi_no)

        if found is not None:
            label = found.get("imo_no") or found.get("mmsi_no")

            if found["vessel_name"] != name:
                renamed.append(
                    f"{label} · 기존 `{found['vessel_name']}` ≠ 신규 `{name}` "
                    "→ 기존 유지 (개명이면 vessels.json 직접 수정)"
                )

            # 기존 선박에 없던 식별번호를 보완한다
            if imo_no and not str(found.get("imo_no") or ""):
                found["imo_no"] = imo_no
                by_imo[imo_no] = found
                enriched.append(f"{found['vessel_name']} · IMO {imo_no} 보완")
            elif mmsi_no and not str(found.get("mmsi_no") or ""):
                found["mmsi_no"] = mmsi_no
                by_mmsi[mmsi_no] = found
                enriched.append(f"{found['vessel_name']} · MMSI {mmsi_no} 보완")
            elif (
                imo_no
                and str(found.get("imo_no") or "")
                and mmsi_no
                and str(found.get("mmsi_no") or "") != mmsi_no
            ):
                renamed.append(
                    f"IMO {imo_no} ({name}) · 기존 MMSI "
                    f"{found.get('mmsi_no')} ≠ 신규 {mmsi_no} "
                    "→ 재선적 가능성. 확인 후 직접 수정하세요"
                )
            else:
                skipped.append(f"{name} ({label})")

            continue

        record = {
            "vessel_name": name,
            "imo_no": imo_no,
            "mmsi_no": mmsi_no,
        }

        vessels.append(record)

        if imo_no:
            by_imo[imo_no] = record
        if mmsi_no:
            by_mmsi[mmsi_no] = record

        added.append(record)

    if added or enriched:
        vessels.sort(
            key=lambda v: (
                v["vessel_name"],
                str(v.get("imo_no") or "") or str(v.get("mmsi_no") or ""),
            )
        )
        VESSELS_PATH.write_text(
            json.dumps(vessels, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    # 처리한 파일 보관
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(KST).strftime("%Y%m%d_%H%M%S")
    archived = ARCHIVE_DIR / f"new_{stamp}.txt"
    archived.write_text(read_text_any_encoding(NEW_PATH), encoding="utf-8")
    NEW_PATH.unlink()

    # 요약
    lines.append(f"# 선박 등록 결과 · {now}")
    lines.append("")
    lines.append(f"- 등록 전 {before}척 → 등록 후 {len(vessels)}척 (신규 {len(added)}척)")
    lines.append(f"- 처리 파일 보관: `{archived}`")
    lines.append("")

    if added:
        lines.append("## 신규 등록")
        for a in added:
            ident = f"IMO {a['imo_no']}" if a["imo_no"] else f"MMSI {a['mmsi_no']} (IMO 미확보)"
            lines.append(f"- {a['vessel_name']} · {ident}")
        lines.append("")

    if enriched:
        lines.append("## 식별번호 보완")
        for e in enriched:
            lines.append(f"- {e}")
        lines.append("")

    if skipped:
        lines.append("## 이미 등록되어 건너뜀")
        for s in skipped:
            lines.append(f"- {s}")
        lines.append("")

    if renamed:
        lines.append("## ⚠ 확인 필요")
        for r in renamed:
            lines.append(f"- {r}")
        lines.append("")

    if warnings:
        lines.append("## ⚠ 경고")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")

    if errors:
        lines.append("## ✕ 처리 실패")
        for e in errors:
            lines.append(f"- {e}")
        lines.append("")
        lines.append("실패한 행은 수정 후 new.txt에 다시 넣어주세요.")
        lines.append("")

    summary = "\n".join(lines)
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(summary, encoding="utf-8")
    print(summary)

    # GitHub Actions 출력
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write(f"added_count={len(added)}\n")
            f.write(f"error_count={len(errors)}\n")
            f.write(f"enriched_count={len(enriched)}\n")
            f.write(
                f"changed={'true' if (added or enriched) else 'false'}\n"
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
