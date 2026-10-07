"""지원사업표(support_program)를 채우는 배치 작업 — 담당: 백엔드(지원사업). 지금은 골격(인터페이스)만 있다.

실행: python -m batch.jobs.build_support_program  (EC2 cron에서는 run_weekly가 부른다)
주기: 주 1회. 명세 4.8 · 6.6 LLM ① · 12.1.

흐름(4.6 run_weekly ① ②)
1. 지난주에 제출한 LLM Batch 결과를 받아 검수 대기 행으로 적재한다(collect_pending_llm_results).
2. 이번 주 새 공고를 모아(fetch_kstartup_notices · 구 전용은 수작업)
   LLM 구조화를 요청한다(submit_structuring_batch).
3. 구조화 결과는 upsert_programs로 support_program에 넣는다. verified_by는 비워 두고 사람이 원문과 대조해
   검수하면 채운다. verified_by IS NOT NULL인 행만 API 매칭에 쓴다(판정 24).

규칙: 공고 원문을 raw_text에 반드시 보관한다. 금액은 원 단위 정수. 모호한 요건은 해석하지 않고 판단보류
(verified_by NULL)로 둔다. eligibility는 7.3 조건 트리 문법이며 자격증 이름은 common.certificate_normalizer의
표준명을 쓴다. 이 모듈은 배치 전용 의존성(xgboost 등)을 import하지 않는다.
"""

import json
import logging
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from typing import Any, Literal

from sqlalchemy import Connection

logger = logging.getLogger(__name__)

KSTARTUP_URL = "https://apis.data.go.kr/B552735/kisedKstartupService01/getAnnouncementInformation01"
KSTARTUP_REGIONS = ("서울", "전국")  # 수집할 지역
PER_PAGE = 100  # 한 쪽에 받는 건수
DETAIL_SLEEP_SEC = 1.0  # 상세 페이지 요청 사이 대기 초
DETAIL_CLASS = "app_notice_details-wrap"  # raw_text 수집을 위한 상세 페이지의 본문 영역
# 마감 시각이 지난 공고는 모집중 주소가 빈 껍데기(JS로 모집마감 주소로 이동)라서 본문이 비면 바꿔 다시 받는다
ONGOING_PATH, DEADLINE_PATH = "bizpbanc-ongoing.do", "bizpbanc-deadline.do"


@dataclass(frozen=True)
class RawNotice:
    """수집한 공고 원문 한 건."""

    source_url: str | None
    title: str
    agency: str | None
    raw_text: str  # 원문 전체(검수 · 정확도 측정의 전제, 4.8)
    district_code: str | None  # 구 전용이면 자치구 코드. K-Startup 수집분은 항상 None(구조화 단계가 판단)
    region: Literal["서울", "전국"]  # K-Startup 응답의 supt_regin. 서울이면 구조화 단계가 구 전용인지 가른다
    apply_start: date | None = None
    apply_end: date | None = None


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.read()


class _DetailText(HTMLParser):
    """본문 영역(DETAIL_CLASS) 안의 글자만 줄 단위로 모은다."""

    def __init__(self) -> None:
        super().__init__()
        self.depth = 0  # 본문 영역 안에서 열려 있는 div 수. 0이면 영역 밖
        self.skip = 0  # script · style 안
        self.lines: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in ("script", "style"):
            self.skip += 1
        elif tag == "div" and (self.depth or DETAIL_CLASS in (dict(attrs).get("class") or "")):
            self.depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self.skip = max(self.skip - 1, 0)
        elif tag == "div" and self.depth:
            self.depth -= 1

    def handle_data(self, data: str) -> None:
        if self.depth and not self.skip and data.strip():
            self.lines.append(" ".join(data.split()))


def _detail_text(url: str) -> str:
    """상세 페이지 본문 글자. 못 읽으면 빈 문자열. 요청마다 DETAIL_SLEEP_SEC만큼 쉰다."""
    try:
        html = _get(url).decode("utf-8", "replace")
    except OSError:  # URLError · HTTPError · 시간 초과
        html = ""
    time.sleep(DETAIL_SLEEP_SEC)
    parser = _DetailText()
    parser.feed(html)
    return "\n".join(parser.lines)


def _strip(value: str | None) -> str | None:
    """앞뒤 공백을 없앤다. 값이 없거나 공백뿐이면 None."""
    return value.strip() or None if value else None


def _ymd(value: str | None) -> date | None:
    return datetime.strptime(value, "%Y%m%d").date() if value else None


def _list_region(api_key: str, region: str) -> list[dict[str, Any]]:
    """한 지역의 모집 중 공고를 모든 쪽에 걸쳐 모은다. 호출이 실패하면 예외를 그대로 올린다."""
    params = {
        "serviceKey": api_key,
        "returnType": "json",
        "perPage": PER_PAGE,
        "cond[supt_regin::EQ]": region,
        "cond[rcrt_prgs_yn::EQ]": "Y",
    }
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        body = json.loads(_get(f"{KSTARTUP_URL}?{urllib.parse.urlencode({**params, 'page': page})}"))
        items += body.get("data", [])
        if page * PER_PAGE >= body.get("matchCount", 0):
            return items
        page += 1


def fetch_kstartup_notices(api_key: str) -> list[RawNotice]:
    """K-Startup 오픈API(PUBLIC_DATA_API_KEY)로 서울 · 전국의 모집 중 공고를 모은다.

    입력: 공공데이터 인증키. 출력: RawNotice 목록. DB에 쓰지 않는다.
    raw_text는 상세 페이지 본문이다. 목록 호출이 실패하면 예외를 올리고(일부만 적재하면 누락이 숨는다),
    상세 본문을 못 읽은 공고는 건너뛴다. district_code는 항상 None이다.
    """
    items: list[dict[str, Any]] = []
    for region in KSTARTUP_REGIONS:
        items += _list_region(api_key, region)

    notices = []
    for item in items:
        url = item["detl_pg_url"]
        # 본문이 비면 마감된 공고라서 모집마감 주소로 한 번 더 받는다(모집중 주소는 빈 껍데기)
        text = _detail_text(url) or _detail_text(url.replace(ONGOING_PATH, DEADLINE_PATH))
        if not text:
            continue
        notices.append(
            RawNotice(
                source_url=url,
                title=item["biz_pbanc_nm"].strip(),
                agency=_strip(item.get("pbanc_ntrp_nm")),
                raw_text=text,
                district_code=None,  # kstartup 수집 데이터는 항상 None
                region=item["supt_regin"],
                apply_start=_ymd(item.get("pbanc_rcpt_bgng_dt")),
                apply_end=_ymd(item.get("pbanc_rcpt_end_dt")),
            )
        )
    return notices


def submit_structuring_batch(notices: list[RawNotice]) -> str:
    """공고 원문 → 구조화(JSON 스키마 강제) LLM Batch 요청을 제출하고 batch id를 돌려준다(6.6 LLM ①).

    출력 필드: name · agency · amount_max(원) · district_code · is_exclusive · apply_start · apply_end ·
    eligibility(7.3 트리) · source_url. 결과는 다음 주 collect_pending_llm_results가 받는다.
    """
    raise NotImplementedError("LLM 구조화 요청은 지원사업 담당이 구현한다(4.8 · 6.6)")


def collect_pending_llm_results(conn: Connection) -> int:
    """지난주에 제출한 Batch 결과를 받아 upsert_programs로 검수 대기 행(verified_by NULL)으로 적재한다.

    출력: 적재한 행 수. 적재 표: support_program.
    """
    raise NotImplementedError("LLM Batch 결과 수집은 지원사업 담당이 구현한다(4.6 ①)")


def upsert_programs(conn: Connection, programs: list[dict[str, Any]]) -> int:
    """구조화된 공고를 support_program에 넣거나 갱신한다.

    적재 키(4.8, 가정): source_url이 같으면 같은 공고, source_url이 없으면 name + agency.
    갱신해도 이미 검수된 행의 verified_by는 원문이 바뀌지 않았으면 유지한다(가정 — 담당이 확정).
    출력: 넣거나 갱신한 행 수.
    """
    raise NotImplementedError("지원사업 적재는 지원사업 담당이 구현한다(4.8)")


def main() -> None:
    """run_weekly ① · ②: 지난주 결과 수집 → 이번 주 공고 수집 · 구조화 요청."""
    raise NotImplementedError("지원사업 수집 · 구조화 · 적재는 아직 구현 전이다(4.8)")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
