"""지원사업표(support_program)를 채우는 배치 작업 — 담당: 백엔드(지원사업).

실행: python -m batch.jobs.build_support_program  (EC2 cron에서는 run_weekly가 부른다)
주기: 주 1회. 명세 4.8 · 6.6 LLM ① · 12.1.

흐름(4.6 run_weekly ① ②). main()이 공고를 한 번만 수집하고 ①과 ②가 그 목록을 함께 쓴다.
1. 지난주에 제출한 LLM Batch 결과를 받아 검수 대기 행으로 적재한다(collect_pending_llm_results).
   결과는 꼬리표(custom_id)로 이번 주 공고와 짝지으며, 그 사이 원문이 바뀌었거나 DB에 이미 같은 원문이 있으면
   적재하지 않는다(검수 결과를 덮어쓰지 않으려고).
2. 이번 주 새 공고 · 원문이 바뀐 공고만 LLM 구조화를 요청한다
   (fetch_kstartup_notices · submit_structuring_batch). 구 전용은 수작업.
3. 구조화 결과는 upsert_programs로 support_program에 넣는다. verified_by는 비워 두고 사람이 원문과 대조해
   검수하면 채운다. verified_by IS NOT NULL인 행만 API 매칭에 쓴다(판정 24).

규칙: 공고 원문을 raw_text에 반드시 보관한다. 금액은 원 단위 정수. 모호한 요건은 해석하지 않고 판단보류
(verified_by NULL)로 둔다. eligibility는 7.3 조건 트리 문법이며 자격증 이름은 common.certificate_normalizer의
표준명을 쓴다. 이 모듈은 배치 전용 의존성(xgboost 등)을 import하지 않는다.
"""

import json
import logging
import os
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from typing import Any, Literal

from sqlalchemy import Connection, Engine, create_engine, text

from batch import config
from batch.llm import client as llm_client
from batch.llm.client import BatchRequest, BatchResult
from batch.llm.structure import build_prompt, custom_id, response_schema, to_program
from batch.pipeline.db import transaction

logger = logging.getLogger(__name__)

KSTARTUP_URL = "https://apis.data.go.kr/B552735/kisedKstartupService01/getAnnouncementInformation01"
KSTARTUP_REGIONS = ("서울", "전국")  # 수집할 지역
PER_PAGE = 100  # 한 쪽에 받는 건수
DETAIL_SLEEP_SEC = 1.0  # 상세 페이지 요청 사이 대기 초
COLLECT_DAYS = 14  # 최근 이만큼 끝난 Batch의 결과를 받는다
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


def _stored_raw_texts(conn: Connection, notices: list[RawNotice]) -> dict[str, str | None]:
    """DB에 이미 있는 공고의 {source_url: raw_text}. 주소가 없는 공고는 조회하지 않는다."""
    urls = [n.source_url for n in notices if n.source_url]
    if not urls:
        return {}
    rows = conn.execute(
        text("SELECT source_url, raw_text FROM support_program WHERE source_url = ANY(:urls)"), {"urls": urls}
    )
    return {url: raw for url, raw in rows}


def _in_db(stored: dict[str, str | None], notice: RawNotice) -> bool:
    """DB에 같은 주소로 같은 원문이 이미 있다. 구조화도 적재도 하지 않는다(검수 결과를 지키려고)."""
    return notice.source_url is not None and stored.get(notice.source_url) == notice.raw_text


def submit_structuring_batch(
    notices: list[RawNotice],
    district_codes: dict[str, str],
    submit: Callable[[list[BatchRequest], dict[str, Any]], str] = llm_client.submit_batch,
) -> str | None:
    """공고 원문 → 구조화(JSON 스키마 강제) LLM Batch 요청을 제출하고 batch id를 돌려준다(6.6 LLM ①).

    입력: 제출할 공고, {구 이름: 구 코드}(프롬프트 · 스키마에 구 이름 목록을 넣는다), 제출 함수(테스트용).
    요청마다 custom_id(structure.custom_id)를 붙인다. 제출할 공고가 없으면 제출하지 않고 None을 돌려준다.
    결과는 다음 주 collect_pending_llm_results가 받는다.
    """
    if not notices:
        return None
    names = list(district_codes)
    requests = [BatchRequest(custom_id(n), build_prompt(n, names)) for n in notices]
    return submit(requests, response_schema(names))


def collect_pending_llm_results(
    conn: Connection,
    notices: list[RawNotice],
    district_codes: dict[str, str],
    since: datetime,
    fetch_results: Callable[[datetime], list[BatchResult]] = llm_client.fetch_batch_results,
) -> int:
    """지난주에 제출한 Batch 결과를 받아 upsert_programs로 검수 대기 행(verified_by NULL)으로 적재한다.

    입력: 이번 주에 수집한 공고(결과와 짝지을 수집 값을 가져온다), {구 이름: 구 코드}, 결과를 볼 시작 시각.
    다음 중 하나면 그 결과는 적재하지 않는다: ① 이번 주 공고에 짝이 없다(사라진 공고) ② 꼬리표가 다르다
    (제출 뒤 원문이 바뀜) ③ 그 요청이 실패했다 ④ DB에 이미 같은 원문이 있다(이미 적재했거나 검수 중).
    출력: 적재한 행 수. 적재 표: support_program.
    """
    by_id = {custom_id(n): n for n in notices}
    stored = _stored_raw_texts(conn, notices)
    programs = []
    for result in fetch_results(since):
        notice = by_id.get(result.custom_id)
        if notice is None or not isinstance(result.data, dict) or _in_db(stored, notice):
            continue
        programs.append(to_program(notice, result.data, district_codes))
    return upsert_programs(conn, programs)


_SAVED = (
    "name = :name, agency = :agency, amount_max = :amount_max, district_code = :district_code, "
    "is_exclusive = :is_exclusive, apply_start = :apply_start, apply_end = :apply_end, "
    "eligibility = CAST(:eligibility AS jsonb), source_url = :source_url, raw_text = :raw_text, "
    "extracted_at = now()"
)
# 같은 공고를 찾는 조건(4.8). 같은 키의 행이 여럿이면 program_id가 가장 작은 한 행만 쓴다.
# 이름 · 기관으로는 주소가 없는 행끼리만 찾는다. 주소가 있는 행은 주소로만 구별한다
# (차수가 다른 같은 이름 공고를 합치거나, 입력에 주소가 없다고 기존 행의 주소를 지우는 일을 막는다)
_SAME_BY_URL = "source_url = :source_url"
_SAME_BY_NAME = "source_url IS NULL AND name = :name AND agency IS NOT DISTINCT FROM CAST(:agency AS varchar)"


def upsert_programs(conn: Connection, programs: list[dict[str, Any]]) -> int:
    """구조화된 공고를 support_program에 넣거나 갱신한다.

    입력: 공고 한 건당 딕셔너리. 키는 support_program 칼럼 이름이다. name · amount_max · eligibility는
    필수(없으면 KeyError)이고 나머지는 없으면 None(is_exclusive는 False)이다. eligibility는 7.3 조건 트리
    딕셔너리여야 한다(JSON 글자 등은 TypeError). extracted_at은 이 함수가
    now()로 넣고, verified_by는 입력으로 받지 않는다. 그 밖의 키는 무시한다.
    적재 키(4.8, 가정): source_url이 같으면 같은 공고. 입력에 source_url이 없으면 source_url이 없는 행 중
    name + agency가 같은 행이 같은 공고다(주소가 있는 행은 이름 · 기관이 같아도 다른 공고로 본다).
    갱신할 때 verified_by는 raw_text가 이전과 같으면 유지하고 다르면 비운다(가정 — 담당이 확정).
    새 행의 verified_by는 비운다.
    전부 한 트랜잭션이라 한 건이라도 실패하면 이 호출에서 한 일이 모두 되돌아가고 예외가 올라간다.
    출력: 넣거나 갱신한 행 수(입력 건수).
    한계: support_program에 source_url 유일 제약이 없어 배치가 동시에 둘 돌면 같은 공고가 중복될 수 있다.
    """
    with transaction(conn):
        for p in programs:
            # JSON 글자를 그대로 넣으면 글자 값으로 저장돼 자격 판정이 깨진다
            if not isinstance(p["eligibility"], dict):
                raise TypeError(f"eligibility는 딕셔너리여야 한다: {type(p['eligibility']).__name__}")
            values = {
                "name": p["name"],
                "agency": p.get("agency"),
                "amount_max": p["amount_max"],
                "district_code": p.get("district_code"),
                "is_exclusive": p.get("is_exclusive", False),
                "apply_start": p.get("apply_start"),
                "apply_end": p.get("apply_end"),
                "eligibility": json.dumps(p["eligibility"]),
                "source_url": p.get("source_url"),
                "raw_text": p.get("raw_text"),
            }
            same = _SAME_BY_URL if values["source_url"] else _SAME_BY_NAME
            updated = conn.execute(
                text(
                    f"UPDATE support_program SET {_SAVED}, "
                    "verified_by = CASE WHEN raw_text IS NOT DISTINCT FROM CAST(:raw_text AS text) "
                    "THEN verified_by END "
                    f"WHERE program_id = (SELECT program_id FROM support_program WHERE {same} "
                    "ORDER BY program_id LIMIT 1)"
                ),
                values,
            ).rowcount
            if not updated:
                conn.execute(
                    text(
                        "INSERT INTO support_program (name, agency, amount_max, district_code, is_exclusive, "
                        "apply_start, apply_end, eligibility, source_url, raw_text, extracted_at) "
                        "VALUES (:name, :agency, :amount_max, :district_code, :is_exclusive, :apply_start, "
                        ":apply_end, CAST(:eligibility AS jsonb), :source_url, :raw_text, now())"
                    ),
                    values,
                )
    return len(programs)


def run(
    conn: Connection,
    notices: list[RawNotice],
    now: datetime | None = None,
    submit: Callable[[list[BatchRequest], dict[str, Any]], str] = llm_client.submit_batch,
    fetch_results: Callable[[datetime], list[BatchResult]] = llm_client.fetch_batch_results,
) -> tuple[int, str | None]:
    """수집한 공고로 ① 지난주 결과를 적재하고 ② 새 · 바뀐 공고를 제출한다.

    출력: (적재한 행 수, batch id). 제출할 공고가 없으면 batch id는 None이다.
    """
    district_codes = {
        name: code for code, name in conn.execute(text("SELECT district_code, name FROM district"))
    }
    since = (now or datetime.now(UTC)) - timedelta(days=COLLECT_DAYS)
    saved = collect_pending_llm_results(conn, notices, district_codes, since, fetch_results)
    stored = _stored_raw_texts(conn, notices)  # ①에서 적재한 공고는 이제 DB에 있어 제출 대상에서 빠진다
    batch_id = submit_structuring_batch([n for n in notices if not _in_db(stored, n)], district_codes, submit)
    return saved, batch_id


def main(
    engine: Engine | None = None,
    fetch: Callable[[str], list[RawNotice]] = fetch_kstartup_notices,
    submit: Callable[[list[BatchRequest], dict[str, Any]], str] = llm_client.submit_batch,
    fetch_results: Callable[[datetime], list[BatchResult]] = llm_client.fetch_batch_results,
) -> None:
    """run_weekly ① · ②: 공고를 한 번 수집해 지난주 Batch 결과를 적재하고 새 · 바뀐 공고를 Batch로 제출한다.

    인자는 모두 테스트에서 바꿔 끼우는 용도라 run_weekly처럼 인자 없이 불러도 된다. DB 작업은 한 트랜잭션이다.
    """
    api_key = os.getenv("PUBLIC_DATA_API_KEY", "")
    if not api_key:
        raise RuntimeError("PUBLIC_DATA_API_KEY가 비어 있다")
    notices = fetch(api_key)
    with (engine or create_engine(config.DATABASE_URL)).begin() as conn:
        saved, batch_id = run(conn, notices, submit=submit, fetch_results=fetch_results)
    logger.info("지원사업: 수집 %d건 · 적재 %d건 · 제출 batch %s", len(notices), saved, batch_id or "없음")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
