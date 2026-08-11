"""
new.txt 후보 파일을 검증하고, 통과하면 UTF-8/LF로 정규화한 사본을
input/new.txt.normalized 로 저장한다.

02_Push-NewVessels.cmd 에서 호출한다.
단독 실행도 가능:  python scripts/validate_new_txt.py <파일경로>

종료코드 0 = 통과, 1 = 실패
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# ── 설정 ──────────────────────────────────────────────────────────────
OWNER = "lws2013"
REPO = "fetch_marimesia"
BRANCH = "main"
TARGET_PATH = "input/new.txt"
COMMIT_MESSAGE = "신규 선박등록"

DEFAULT_DIR = r"C:\Work\AIS"
API = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{TARGET_PATH}"

SCIENTIFIC = re.compile(r"^\d(\.\d+)?[eE]\+?\d+$")



NORMALIZED_PATH = Path("input/new.txt.normalized")

# ── 파일 읽기 ─────────────────────────────────────────────────────────
def read_any_encoding(path: Path) -> str:
    """메모장·Excel 저장본을 견디도록 여러 인코딩을 시도한다."""
    raw = path.read_bytes()

    for encoding in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue

    return raw.decode("utf-8", errors="replace")


def clean(value: str) -> str:
    value = value.replace("\u3000", " ").replace("\ufeff", "")
    value = "".join(
        chr(ord(ch) - 0xFEE0) if "０" <= ch <= "９" else ch
        for ch in value
    )
    return value.strip()


def imo_check_digit_ok(imo: str) -> bool:
    return sum(int(imo[i]) * (7 - i) for i in range(6)) % 10 == int(imo[6])


def classify(token: str) -> tuple[str, str, str]:
    """(종류, 값, 오류문). 종류는 imo / mmsi / '' """
    value = clean(token).upper()

    if value.startswith("IMO"):
        value = value[3:].strip()

    value = value.replace("-", "").replace(" ", "")

    if not value:
        return "", "", "식별번호 없음"

    if SCIENTIFIC.match(value):
        return "", value, (
            f"지수 표기 '{value}' · Excel에서 해당 열을 '텍스트' 서식으로 "
            "바꾼 뒤 다시 복사하세요"
        )

    if not value.isdigit():
        return "", value, f"숫자가 아닌 문자 포함 '{value}'"

    if len(value) == 7:
        if not imo_check_digit_ok(value):
            return "", value, f"IMO 체크디지트 불일치 '{value}' · 오타 가능성"
        return "imo", value, ""

    if len(value) == 9:
        mid = int(value[:3])
        if not (201 <= mid <= 775):
            return "mmsi", value, (
                f"MID {mid} 는 선박용 범위(201-775) 밖 · 확인 필요"
            )
        return "mmsi", value, ""

    return "", value, f"IMO 7자리 / MMSI 9자리가 아님 ({len(value)}자리) '{value}'"


def validate(text: str) -> tuple[list[tuple], list[str], list[str]]:
    rows, errors, warnings = [], [], []

    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue

        parts = line.split("\t")
        if len(parts) < 2:
            parts = re.split(r"\s{2,}", line.strip())

        if len(parts) < 2:
            errors.append(f"{lineno}행 · 탭 구분자 없음 → {line.strip()}")
            continue

        name = clean(parts[0])

        if not name:
            errors.append(f"{lineno}행 · 선박명 없음")
            continue

        if name.lower() in {"vessel_name", "vessel", "선박명", "name"}:
            continue

        imo, mmsi, msgs = "", "", []

        for token in parts[1:]:
            if not clean(token):
                continue

            kind, value, err = classify(token)

            if kind == "imo":
                imo = value
            elif kind == "mmsi":
                mmsi = value

            if err:
                msgs.append(err)

        if not imo and not mmsi:
            errors.append(f"{lineno}행 ({name}) · {'; '.join(msgs) or '식별번호 없음'}")
            continue

        for m in msgs:
            warnings.append(f"{lineno}행 ({name}) · {m}")

        if not imo:
            warnings.append(f"{lineno}행 ({name}) · IMO 없이 MMSI만 등록됩니다")

        rows.append((name.upper(), imo, mmsi))

    return rows, errors, warnings



def main() -> int:
    if len(sys.argv) < 2:
        print("사용법: python scripts/validate_new_txt.py <파일경로>")
        return 1

    path = Path(sys.argv[1])

    if not path.exists():
        print(f"[오류] 파일이 없습니다: {path}")
        return 1

    rows, errors, warnings = validate(read_any_encoding(path))

    print(f"     유효 {len(rows)}행 · 오류 {len(errors)}건 · 경고 {len(warnings)}건")

    if rows:
        print()
        print("     선박명                          IMO        MMSI")
        print("     " + "-" * 51)
        for name, imo, mmsi in rows[:30]:
            print(f"     {name[:30]:<30}  {imo or '-':<9}  {mmsi or '-'}")
        if len(rows) > 30:
            print(f"     ... 외 {len(rows) - 30}행")

    if warnings:
        print()
        print("     [경고]")
        for w in warnings:
            print(f"       · {w}")

    if errors:
        print()
        print("     [오류] 아래 행은 처리되지 않습니다.")
        for e in errors:
            print(f"       · {e}")

    if not rows:
        print()
        print("     유효한 행이 없습니다.")
        return 1

    NORMALIZED_PATH.parent.mkdir(parents=True, exist_ok=True)
    normalized = "\n".join(
        f"{name}\t{imo}\t{mmsi}" if (imo and mmsi) else f"{name}\t{imo or mmsi}"
        for name, imo, mmsi in rows
    ) + "\n"

    NORMALIZED_PATH.write_text(normalized, encoding="utf-8", newline="\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
