"""fetch_bizinfo_notices — 가짜 XML 응답으로 서울 선별 · region · raw_text · 첨부 읽기를 검증(명세 4.8)."""

import io
import zipfile
from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest

from batch.jobs import build_support_program as job

FILE_URL = "https://www.bizinfo.go.kr/cmm/fms/getImageFile.do?atchFileId=FILE_{}&fileSn=0"
ALL_SIDO = ",".join(job.SIDO)


def hwpx(*texts: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        body = "".join(f"<hp:p><hp:t>{t}</hp:t></hp:p>" for t in texts)
        z.writestr("Contents/section0.xml", f'<root xmlns:hp="urn:x">{body}</root>')
    return buf.getvalue()


def item(n, tags, period="2026-10-01 ~ 2026-10-23", file_name="공고문.hwpx", **kw):
    fields = {
        "pblancNm": f"공고{n}",
        "pblancUrl": f"https://www.bizinfo.go.kr/detail?pblancId={n}",
        "jrsdInsttNm": "서울특별시",
        "excInsttNm": f"기관{n}",
        "bsnsSumryCn": "<p>요약  &amp; 개요</p>",
        "reqstBeginEndDe": period,
        "trgetNm": "소상공인",
        "hashtags": tags,
        "printFlpthNm": FILE_URL.format(n),
        "printFileNm": file_name,
        "reqstMthPapersCn": "이메일 접수",
        **kw,
    }
    body = "".join(f"<{k}><![CDATA[{v}]]></{k}>" for k, v in fields.items())
    return f"<item>{body}</item>"


def xml(items, total=None):
    total = len(items) if total is None else total
    return (
        "<response><header><resultCode>00</resultCode><resultMsg>NORMAL_SERVICE</resultMsg></header>"
        f"<body><items>{''.join(items)}</items><totalCount>{total}</totalCount></body></response>"
    ).encode()


@pytest.fixture
def get(monkeypatch):
    """_get을 가짜로 바꾼다. files에 넣은 주소는 그 내용을, 나머지는 OSError를 낸다."""

    class Fake:
        list_pages: list[bytes] = []
        files: dict[str, bytes] = {}
        calls: list[str] = []

        def __call__(self, url):
            self.calls.append(url)
            if url.startswith(job.BIZINFO_URL):
                page = int(parse_qs(urlparse(url).query)["pageNo"][0])
                return self.list_pages[page - 1]
            if url in self.files:
                return self.files[url]
            raise OSError("연결 실패")

    fake = Fake()
    fake.list_pages, fake.files, fake.calls = [], {}, []
    monkeypatch.setattr(job, "_get", fake)
    monkeypatch.setattr(job.time, "sleep", lambda s: None)
    return fake


def test_keeps_only_seoul_and_sets_region(get):
    get.list_pages = [
        xml(
            [
                item(1, "내수,서울,경영"),  # 서울 전용
                item(2, "서울,인천,경기"),  # 서울 포함 여러 시도
                item(3, ALL_SIDO),  # 전국
                item(4, "충남,경영"),  # 서울 사용자는 못 받음
            ]
        )
    ]
    get.files[FILE_URL.format(1)] = hwpx("지원금 최대 500만원", "자격 조건")

    got = job.fetch_bizinfo_notices("key")

    assert [(n.title, n.region) for n in got] == [("공고1", "서울"), ("공고2", "전국"), ("공고3", "전국")]
    one = got[0]
    assert one.source_url == "https://www.bizinfo.go.kr/detail?pblancId=1"
    assert one.agency == "기관1" and one.district_code is None
    assert (one.apply_start, one.apply_end) == (date(2026, 10, 1), date(2026, 10, 23))
    assert one.raw_text == (
        "[공고명]\n공고1\n[신청기간]\n2026-10-01 ~ 2026-10-23\n[지원대상]\n소상공인\n"
        "[사업개요]\n요약 & 개요\n[신청방법]\n이메일 접수\n[공고문 본문]\n지원금 최대 500만원\n자격 조건"
    )


def test_skips_known_urls_without_downloading(get):
    """DB에 이미 있는 source_url의 공고는 첨부를 받지 않고 목록에서도 뺀다."""
    get.list_pages = [xml([item(1, "서울"), item(2, "서울")])]
    for n in (1, 2):
        get.files[FILE_URL.format(n)] = hwpx(f"본문{n}")

    got = job.fetch_bizinfo_notices("key", known={"https://www.bizinfo.go.kr/detail?pblancId=1"})

    assert [n.title for n in got] == ["공고2"]
    assert FILE_URL.format(1) not in get.calls and FILE_URL.format(2) in get.calls


def test_period_without_dates_and_missing_executor(get):
    get.list_pages = [xml([item(1, "서울", period="예산 소진시까지", excInsttNm="")])]

    (n,) = job.fetch_bizinfo_notices("key")

    assert (n.apply_start, n.apply_end) == (None, None)  # 상시 · 예산 소진 공고
    assert n.agency == "서울특별시"  # 수행기관이 비면 소관 기관


def test_attachment_failures_fall_back_to_summary(get):
    get.list_pages = [
        xml(
            [
                item(1, "서울"),  # 내려받기 실패
                item(2, "서울"),  # 확장자는 hwpx지만 실제는 구형 HWP
                item(3, "서울", file_name="공고문.hwp"),  # 구형 HWP는 받지도 않음
                item(4, "서울"),  # 깨진 zip
            ]
        )
    ]
    get.files[FILE_URL.format(2)] = b"\xd0\xcf\x11\xe0" + b"\x00" * 20
    get.files[FILE_URL.format(3)] = hwpx("읽으면 안 된다")
    get.files[FILE_URL.format(4)] = b"PK\x03\x04broken"

    got = job.fetch_bizinfo_notices("key")

    assert len(got) == 4 and all("[공고문 본문]" not in n.raw_text for n in got)
    assert all("[사업개요]\n요약 & 개요" in n.raw_text for n in got)
    assert FILE_URL.format(3) not in get.calls


def test_pdf_attachment_text(get, monkeypatch):
    pypdf = pytest.importorskip("pypdf")  # 기본 CI에는 없다. ci-batch가 돌린다

    class FakePage:
        def extract_text(self):
            return "PDF 본문"

    class FakeReader:
        def __init__(self, stream):
            assert stream.read(4) == b"%PDF"
            self.pages = [FakePage(), FakePage()]

    monkeypatch.setattr(pypdf, "PdfReader", FakeReader)
    get.list_pages = [xml([item(1, "서울", file_name="공고문.pdf")])]
    get.files[FILE_URL.format(1)] = b"%PDF-1.4 ..."

    (n,) = job.fetch_bizinfo_notices("key")

    assert n.raw_text.endswith("[공고문 본문]\nPDF 본문\nPDF 본문")


def test_pages_through_list(get, monkeypatch):
    monkeypatch.setattr(job, "BIZINFO_PER_PAGE", 2)
    get.list_pages = [xml([item(1, "서울"), item(2, "서울")], total=3), xml([item(3, "서울")], total=3)]

    got = job.fetch_bizinfo_notices("key")

    assert [n.title for n in got] == ["공고1", "공고2", "공고3"]
    assert len([c for c in get.calls if c.startswith(job.BIZINFO_URL)]) == 2


def test_service_error_raises(get):
    get.list_pages = [
        b"<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</errMsg>"
        b"</cmmMsgHeader></OpenAPI_ServiceResponse>"
    ]
    with pytest.raises(RuntimeError, match="SERVICE_KEY_IS_NOT_REGISTERED_ERROR"):
        job.fetch_bizinfo_notices("key")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-10-01 ~ 2026-10-23", (date(2026, 10, 1), date(2026, 10, 23))),
        ("2020.01.01 ~ 2026.12.31", (date(2020, 1, 1), date(2026, 12, 31))),
        ("2026-10-01~2026-10-23", (date(2026, 10, 1), date(2026, 10, 23))),
        ("예산 소진시까지", (None, None)),
        ("상시 접수", (None, None)),
        ("", (None, None)),
        ("2026-10-01 ~ 상시", (date(2026, 10, 1), None)),  # 시작일만 정확
        ("상시 ~ 2026-10-23", (None, date(2026, 10, 23))),
        ("2026-13-45 ~ 2026-10-23", (None, date(2026, 10, 23))),  # 없는 날짜는 그쪽만 None
        ("2026-10-01 ~ 2026-14-01", (date(2026, 10, 1), None)),
        ("2026-10-23 ~ 2026-10-01", (date(2026, 10, 23), date(2026, 10, 1))),  # 읽은 대로 둔다
    ],
)
def test_period_parsing(value, expected):
    assert job._period(value) == expected


def test_attachment_text_is_capped(get, monkeypatch):
    """첨부 본문은 앞에서 ATTACH_MAX_CHARS자까지만 raw_text에 붙는다(PDF · HWPX 모두)."""
    pypdf = pytest.importorskip("pypdf")
    monkeypatch.setattr(job, "ATTACH_MAX_CHARS", 10)
    get.list_pages = [xml([item(1, "서울"), item(2, "서울", file_name="공고문.pdf")])]
    get.files[FILE_URL.format(1)] = hwpx("가" * 8, "나" * 8)  # 줄바꿈 포함 17자

    class FakePage:
        def extract_text(self):
            return "다" * 30

    class FakeReader:
        def __init__(self, stream):
            self.pages = [FakePage()]

    monkeypatch.setattr(pypdf, "PdfReader", FakeReader)
    get.files[FILE_URL.format(2)] = b"%PDF-1.4 ..."

    one, two = job.fetch_bizinfo_notices("key")

    assert one.raw_text.endswith("[공고문 본문]\n" + "가" * 8 + "\n나")  # 17자 중 앞 10자
    assert two.raw_text.endswith("[공고문 본문]\n" + "다" * 10)


def test_notice_without_url_is_skipped(get):
    """주소가 적재 키라서 주소가 없는 공고는 수집하지 않는다."""
    get.list_pages = [xml([item(1, "서울", pblancUrl=""), item(2, "서울")])]

    got = job.fetch_bizinfo_notices("key")

    assert [n.title for n in got] == ["공고2"]
