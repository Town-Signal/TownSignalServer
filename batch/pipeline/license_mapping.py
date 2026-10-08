"""4단계의 수작업 seed를 읽는다. 이름 유사도나 사업장명으로 업종을 추측하지 않는다.

license_to_industry_mapping.csv의 coverage 행은 업종당 하나(100개), mapping 행은
인허가 종류·필드·업태 조합당 하나다. coverage=커버됨도 현재 수집 원천의 일부 범위를
뜻하며 전수 관측을 보장하지 않는다. 미수집을 인허가 대상 아님으로 단정하지 않는다.
"""

import csv
import re
import unicodedata
from pathlib import Path

SEED_COLUMNS = [
    "row_type",
    "industry_code",
    "industry_name",
    "coverage",
    "license_type",
    "subtype_field",
    "subtype",
    "decision",
    "reason",
    "reference",
]
COVERAGE_LABELS = {"커버됨", "인허가 대상 아님", "애매함"}
SOURCES = {
    "일반음식점": ("업태구분명", "OA-16094"),
    "휴게음식점": ("업태구분명", "OA-16095"),
    "미용업": ("업태구분명", "OA-16063"),
    "노래연습장업": ("문화체육업종명", "OA-16037"),
    "인터넷컴퓨터게임시설제공업": ("문화체육업종명", "OA-16018"),
    "체력단련장업": ("문화체육업종명", "OA-16142"),
}
CRS_REFERENCE = "https://data.seoul.go.kr/dataList/OA-16094/S/1/datasetView.do"
INDUSTRY_REFERENCE = "https://golmok.seoul.go.kr/images/100_v3.pdf"


def normalize(value: str | None) -> str:
    """원문은 보존하고 매칭용 값의 Unicode/공백만 정리한다. 구두점은 바꾸지 않는다."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value or "")).strip()


def source_type(path: Path) -> str:
    matches = [kind for kind in SOURCES if path.stem == f"서울시 {kind} 인허가 정보"]
    if len(matches) != 1:
        raise ValueError(f"확인되지 않은 인허가 원천 이름: {path.name}")
    return matches[0]


def read_mapping(path: Path, industries: list[dict]) -> tuple[dict, dict]:
    """seed 충돌·누락·잘못된 코드/커버리지 판정은 실행 전에 거부한다."""
    master = {r["industry_code"]: r["name"] for r in industries}
    coverage, rules = {}, {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != SEED_COLUMNS:
            raise ValueError("인허가 seed 칼럼이 지정된 형식과 다릅니다")
        for line, row in enumerate(reader, 2):
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f"seed CSV 너비 오류: {line}")
            if not row["reason"] or not row["reference"]:
                raise ValueError(f"seed 판정 근거/출처가 없습니다: {line}")
            code = row["industry_code"]
            if code and (code not in master or master[code] != row["industry_name"]):
                raise ValueError(f"seed 업종 코드/명칭 불일치: {line}")
            if row["row_type"] == "coverage":
                if not code or code in coverage or row["coverage"] not in COVERAGE_LABELS:
                    raise ValueError(f"seed 커버리지 오류/중복: {line}")
                if any(row[c] for c in ("license_type", "subtype_field", "subtype", "decision")):
                    raise ValueError(f"커버리지 행에 매핑 규칙이 섞였습니다: {line}")
                coverage[code] = row
            elif row["row_type"] == "mapping":
                kind = row["license_type"]
                if kind not in SOURCES or SOURCES[kind][0] != row["subtype_field"]:
                    raise ValueError(f"seed 인허가 종류/업태 필드 오류: {line}")
                if row["coverage"] or row["decision"] not in {"mapped", "unclassified"}:
                    raise ValueError(f"seed 매핑 판정 오류: {line}")
                if bool(code) != (row["decision"] == "mapped"):
                    raise ValueError(f"seed 미분류/코드 모순: {line}")
                if not code and row["industry_name"]:
                    raise ValueError(f"미분류 행에 업종명이 있습니다: {line}")
                key = (kind, normalize(row["subtype"]))
                if key in rules:
                    raise ValueError(f"seed 매핑 규칙이 중복됩니다: {line}")
                rules[key] = row
            else:
                raise ValueError(f"seed 행 종류 오류: {line}")
    if set(coverage) != set(master) or len(coverage) != 100:
        raise ValueError("seed 커버리지에는 마스터의 100개 업종이 각각 한 번씩 필요합니다")
    covered = {code for code, r in coverage.items() if r["coverage"] == "커버됨"}
    if covered - {r["industry_code"] for r in rules.values()}:
        raise ValueError("커버됨 업종에 대응하는 매핑 규칙이 없습니다")
    return coverage, rules
