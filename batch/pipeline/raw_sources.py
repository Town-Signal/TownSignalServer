"""데이터 파이프라인 1단계의 원천 목록. 경로는 TownSignalServer 기준이다."""

from dataclasses import dataclass
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class RawSource:
    code: str
    directory: str
    kind: str = "csv"
    encoding: str = "cp949"
    header_rows: int = 1


SOURCES = (
    RawSource("d1", "d1_sales_est"),
    RawSource("d2", "d2_store"),
    RawSource("d3", "d3_population"),
    RawSource("d4", "d4_licences"),
    RawSource("d5", "d5_rent", header_rows=3),
    RawSource("d6", "d6_map", kind="shapefile", encoding="utf-8"),
)


def select_sources(codes: list[str] | None = None) -> list[RawSource]:
    """CLI에 지정한 원천만 선택한다. 생략하면 d1~d6 전부다."""
    requested = set(codes or [source.code for source in SOURCES])
    unknown = requested - {source.code for source in SOURCES}
    if unknown:
        raise ValueError(f"알 수 없는 데이터셋: {sorted(unknown)}")
    return [source for source in SOURCES if source.code in requested]
