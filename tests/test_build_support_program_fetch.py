"""fetch_kstartup_notices — 가짜 응답으로 쪽 순회 · 본문 재시도 · 건너뛰기를 검증(명세 4.8)."""

import json
from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest

from batch.jobs import build_support_program as job

BASE = "https://www.k-startup.go.kr/web/contents/"


def notice(sn, region, end="20261022", **kw):
    return {
        "biz_pbanc_nm": f"공고{sn}",
        "pbanc_ntrp_nm": f"기관{sn}",
        "detl_pg_url": f"{BASE}bizpbanc-ongoing.do?schM=view&pbancSn={sn}",
        "pbanc_rcpt_bgng_dt": "20261002",
        "pbanc_rcpt_end_dt": end,
        "supt_regin": region,
        **kw,
    }


def page(sn):
    return (
        f"<div class='nav'>메뉴</div><div class='x {job.DETAIL_CLASS}'>"
        f"<div><p>본문{sn}</p><script>var a=1;</script></div><p>대상  청년</p></div><div>푸터</div>"
    )


SHELL = "<html><body>본문 바로가기</body></html>"  # 마감 시각이 지난 공고의 모집중 주소 응답

# (지역, 쪽) → 목록 응답. PER_PAGE=2로 줄여 전국이 2쪽이 되게 한다.
LISTS = {
    # 1번은 공고명 · 기관명 앞뒤에 공백이 붙어 오고, 2번은 기관명과 마감일이 없다
    ("서울", "1"): {
        "matchCount": 1,
        "data": [notice(1, "서울", biz_pbanc_nm=" 공고1 ", pbanc_ntrp_nm="기관1 ")],
    },
    ("전국", "1"): {
        "matchCount": 3,
        "data": [notice(2, "전국", end=None, pbanc_ntrp_nm=None), notice(3, "전국")],
    },
    ("전국", "2"): {"matchCount": 3, "data": [notice(4, "전국")]},
}
PAGES = {
    f"{BASE}bizpbanc-ongoing.do?schM=view&pbancSn=1": page(1),
    f"{BASE}bizpbanc-ongoing.do?schM=view&pbancSn=2": SHELL,  # 모집마감으로 넘어간 공고
    f"{BASE}bizpbanc-deadline.do?schM=view&pbancSn=2": page(2),
    f"{BASE}bizpbanc-ongoing.do?schM=view&pbancSn=3": SHELL,  # 둘 다 빈 공고
    f"{BASE}bizpbanc-deadline.do?schM=view&pbancSn=3": SHELL,
    f"{BASE}bizpbanc-ongoing.do?schM=view&pbancSn=4": page(4),
}


@pytest.fixture
def fake(monkeypatch):
    calls = []

    def fake_get(url):
        calls.append(url)
        if url.startswith(job.KSTARTUP_URL):
            q = parse_qs(urlparse(url).query)
            assert q["cond[rcrt_prgs_yn::EQ]"] == ["Y"]
            return json.dumps(LISTS[(q["cond[supt_regin::EQ]"][0], q["page"][0])]).encode()
        if url not in PAGES:
            raise OSError("연결 실패")
        return PAGES[url].encode()

    monkeypatch.setattr(job, "_get", fake_get)
    monkeypatch.setattr(job, "PER_PAGE", 2)
    monkeypatch.setattr(job.time, "sleep", lambda s: calls.append(("sleep", s)))
    return calls


def test_fetch_collects_retries_and_skips(fake):
    got = job.fetch_kstartup_notices("key")

    assert [n.source_url[-1] for n in got] == ["1", "2", "4"]  # 3번은 본문이 둘 다 비어 건너뜀
    one, two, four = got
    assert (one.title, one.agency, one.region) == ("공고1", "기관1", "서울")
    assert one.raw_text == "본문1\n대상 청년"  # 본문 영역 안의 글자만, script 제외, 공백 정리
    assert one.district_code is None and two.district_code is None
    assert (one.apply_start, one.apply_end) == (date(2026, 10, 2), date(2026, 10, 22))
    assert two.apply_end is None  # 마감일 없음(상시)
    assert two.agency is None  # 기관명 없음
    assert two.raw_text == "본문2\n대상 청년" and two.region == "전국"  # 모집마감 주소로 다시 받은 본문
    assert two.source_url.endswith("pbancSn=2") and "ongoing" in two.source_url  # 적재 키는 API 주소 그대로
    assert four.region == "전국" and four.raw_text == "본문4\n대상 청년"  # 전국 2쪽의 공고도 모인다


def test_fetch_pages_through_list_and_sleeps_per_detail_request(fake):
    job.fetch_kstartup_notices("key")

    lists = [c for c in fake if isinstance(c, str) and c.startswith(job.KSTARTUP_URL)]
    assert len(lists) == 3  # 서울 1쪽 + 전국 2쪽
    sleeps = [c for c in fake if isinstance(c, tuple)]
    assert len(sleeps) == 6 and {s for _, s in sleeps} == {
        job.DETAIL_SLEEP_SEC
    }  # 1 · 4번 각 1회 + 2 · 3번 각 2회


def test_fetch_list_failure_raises(monkeypatch):
    def boom(url):
        raise OSError("목록 실패")

    monkeypatch.setattr(job, "_get", boom)
    with pytest.raises(OSError):
        job.fetch_kstartup_notices("key")


def test_detail_request_failure_skips_notice(monkeypatch):
    monkeypatch.setattr(job.time, "sleep", lambda s: None)

    def fake_get(url):
        if url.startswith(job.KSTARTUP_URL):
            return json.dumps({"matchCount": 1, "data": [notice(9, "서울")]}).encode()
        raise OSError("상세 실패")

    monkeypatch.setattr(job, "_get", fake_get)
    assert job.fetch_kstartup_notices("key") == []


def test_list_region_stops_when_last_page_is_full(monkeypatch):
    """전체 건수가 쪽 크기로 딱 떨어지면 빈 다음 쪽을 더 요청하지 않는다."""
    calls = []

    def fake_get(url):
        calls.append(url)
        return json.dumps({"matchCount": 2, "data": [notice(1, "서울"), notice(2, "서울")]}).encode()

    monkeypatch.setattr(job, "_get", fake_get)
    monkeypatch.setattr(job, "PER_PAGE", 2)
    assert len(job._list_region("key", "서울")) == 2
    assert len(calls) == 1
