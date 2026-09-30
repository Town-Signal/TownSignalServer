# -*- coding: utf-8 -*-
"""시스템 구조 v0.9 그림 3장 (matplotlib, Malgun Gothic)

structure_v0.9.png  — 전체 구조 (배치 실행 위치 구분 · 요청 시 흐름)
example_v0.9.png    — 요청 예시 (전체 명세 v1.6 수치)
ai_runtime_v0.9.png — AI는 어디서 도는가
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

plt.rcParams["font.family"] = "Malgun Gothic"
plt.rcParams["axes.unicode_minus"] = False

NAVY = "#1A355E"; BLUE = "#2B6CB0"; LBLUE = "#E8F0F9"
TEAL = "#0F766E"; LTEAL = "#DCF2EF"
AMBER = "#B45309"; LAMBER = "#FDF0DC"
PURPLE = "#6D28D9"; LPURPLE = "#EDE7FB"
RED = "#B91C1C"; GREEN = "#15803D"
GRAY = "#64748B"; LGRAY = "#F1F5F9"; WHITE = "#FFFFFF"; INK = "#1F2937"
NL = chr(10)


def rbox(ax, x, y, w, h, fc=WHITE, ec=BLUE, lw=1.3, r=0.12, z=2):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=%s" % r,
                                linewidth=lw, edgecolor=ec, facecolor=fc, zorder=z))


def box(ax, x, y, w, h, title, sub=None, fc=WHITE, ec=BLUE, tc=NAVY, ts=10.5, ss=8.4):
    rbox(ax, x, y, w, h, fc, ec)
    if sub:
        ax.text(x + w / 2, y + h * 0.70, title, ha="center", va="center", fontsize=ts, color=tc,
                fontweight="bold", zorder=3)
        ax.text(x + w / 2, y + h * 0.33, sub, ha="center", va="center", fontsize=ss, color=GRAY,
                zorder=3, linespacing=1.45)
    else:
        ax.text(x + w / 2, y + h / 2, title, ha="center", va="center", fontsize=ts, color=tc,
                fontweight="bold", zorder=3, linespacing=1.45)


def band(ax, x, y, w, h, text, fc=LGRAY, ec="#CBD5E1", tcol=GRAY, ts=9.8):
    rbox(ax, x, y, w, h, fc, ec, lw=1.1, r=0.15, z=1)
    ax.text(x + 0.25, y + h - 0.32, text, ha="left", va="center", fontsize=ts, color=tcol,
            fontweight="bold", zorder=10,
            bbox=dict(boxstyle="square,pad=0.2", facecolor=fc, edgecolor="none"))


def arrow(ax, p1, p2, color=GRAY, lw=1.4, rad=0.0, ls="-", z=4):
    ax.add_patch(FancyArrowPatch(p1, p2, arrowstyle="-|>", mutation_scale=13, linewidth=lw,
                                 color=color, zorder=z, linestyle=ls,
                                 connectionstyle="arc3,rad=%s" % rad, shrinkA=2, shrinkB=2))


def label(ax, x, y, text, size=8, color=GRAY, weight="normal", ha="center", bg=None):
    kw = {}
    if bg:
        kw["bbox"] = dict(boxstyle="round,pad=0.25", facecolor=bg, edgecolor="none")
    ax.text(x, y, text, ha=ha, va="center", fontsize=size, color=color, fontweight=weight,
            zorder=6, linespacing=1.4, **kw)


def tag(ax, x, y, text, fc, tc):
    label(ax, x, y, text, size=7.8, color=tc, weight="bold", bg=fc)


def new_ax(w, h, xlim, ylim):
    fig, ax = plt.subplots(figsize=(w, h))
    ax.set_xlim(*xlim); ax.set_ylim(*ylim); ax.axis("off")
    return fig, ax


def save(fig, name):
    plt.tight_layout(pad=0.3)
    fig.savefig(name, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)


# =====================================================================
# 그림 1. 전체 구조
# =====================================================================
fig, ax = new_ax(12.4, 11.6, (0, 24.8), (0, 23.2))

label(ax, 12.4, 22.65, "어려운 계산은 ① 배치에서 미리 끝내고, ② 요청 시에는 만들어둔 표를 조회와 사칙연산으로만 쓴다",
      size=10.4, color=NAVY, weight="bold")

# 원천 데이터
band(ax, 0.3, 18.6, 24.2, 3.5, "원천 데이터 · 공공데이터")
src = [
    ("지원사업 공고", "K-Startup API" + NL + "구청 공고(수작업)"),
    ("상가 임대료", "한국부동산원" + NL + "서울 조사 상권 59개"),
    ("매출 · 점포", "OA-22175" + NL + "OA-22172"),
    ("유동인구", "OA-22178" + NL + "2021년 이후"),
    ("인허가 개·폐업", "LOCALDATA" + NL + "서울 전체"),
    ("행정동 · 업종", "마스터 · seed" + NL + "코드 8자리 통일"),
]
SW, SG, SX = 3.72, 0.26, 0.62
for i, (t, s) in enumerate(src):
    box(ax, SX + i * (SW + SG), 18.9, SW, 2.35, t, s, ec=GRAY, ts=9.6, ss=7.8)

# ① 배치
band(ax, 0.3, 11.0, 24.2, 7.1, "①  배치 — 미리 만들어두는 서빙 표 4개   (사용자와 무관하게 실행)",
     fc="#EEF8F8", ec="#9FD4D0", tcol=TEAL)
RW, RG, RX = 5.6, 0.4, 0.75
TX = [RX + i * (RW + RG) for i in range(4)]
tables = [
    ("지원사업표", "support_program", "수백 행",
     "금액 · 적용 자치구 · 중복수혜" + NL + "자격조건 트리 · 검수분만 매칭", "EC2 cron · 매주", LBLUE, BLUE),
    ("임대료표", "district_rent", "25 행",
     "자치구별 ㎡당 월세" + NL + "보증금 배수 · 신뢰도", "팀원 PC · 분기 1회", LAMBER, AMBER),
    ("예측표", "prediction", "42,600 행",
     "행정동 426 × 업종 100" + NL + "매출 · 생존 80% 구간 · 종합점수", "팀원 PC · 분기 1회", LAMBER, AMBER),
    ("요약 캐시", "summary_cache", "조합 수",
     "행정동 × 업종 요약문" + NL + "LLM으로 미리 생성", "팀원 PC · 분기 1회", LAMBER, AMBER),
]
for x, (t, en, rows, desc, where, tfc, ttc) in zip(TX, tables):
    rbox(ax, x, 11.85, RW, 4.9, WHITE, TEAL, lw=1.4)
    cx = x + RW / 2
    label(ax, cx, 16.2, t, size=12, color=NAVY, weight="bold")
    label(ax, cx, 15.65, en, size=8, color=GRAY)
    label(ax, cx, 14.95, rows, size=11, color=TEAL, weight="bold")
    label(ax, cx, 13.85, desc, size=8, color=GRAY)
    tag(ax, cx, 12.4, where, tfc, ttc)

label(ax, 12.4, 11.4, "팀원 PC에서 만든 표는 SSH 터널로 운영 DB에 적재한다.  운영 서버에는 모델 파일도, 추론도 없다",
      size=8.4, color=AMBER, weight="bold", bg="#EEF8F8")

arrow(ax, (2.48, 18.85), (TX[0] + RW / 2, 16.8))
arrow(ax, (6.46, 18.85), (TX[1] + RW / 2, 16.8))
arrow(ax, (10.44, 18.85), (TX[2] + RW / 2 - 0.6, 16.8))
arrow(ax, (14.42, 18.85), (TX[2] + RW / 2, 16.8))
arrow(ax, (18.4, 18.85), (TX[2] + RW / 2 + 0.6, 16.8))
label(ax, 22.38, 18.35, "모든 표의 결합 기준", size=8)
arrow(ax, (TX[2] + RW + 0.04, 14.3), (TX[3] - 0.04, 14.3), color=TEAL)

# common
rbox(ax, 0.3, 9.5, 24.2, 1.1, "#EDF2FA", NAVY, lw=1.2, z=2)
ax.text(0.75, 10.05, "common/   evaluator · budget · scoring · constants   —   배치와 API가 같은 코드를 import해 판정 결과가 어긋나지 않는다",
        ha="left", va="center", fontsize=8.8, color=NAVY, fontweight="bold", zorder=10)

# ② 요청 시
band(ax, 0.3, 2.6, 24.2, 6.4, "②  요청 시 — 사용자가 버튼을 누를 때   (FastAPI 동기식 · 1초 이내 · 조회와 사칙연산뿐)",
     fc="#FFF6EC", ec="#F2C49B", tcol=AMBER)
steps = [
    ("조건 입력", "나이 · 자본금 · 경력" + NL + "자격증 · 업종 · 면적"),
    ("지원금 매칭", "자치구 25개 각각 판정" + NL + "중복수혜 불가는 큰 것 하나"),
    ("가용예산 · 임대비용", "예산 = 자본금 + 지원금" + NL + "임대비용 = 월세 × 18"),
    ("통과 판정", "통과 · 제외 · 확인불가" + NL + "rec_id 스냅샷 저장"),
    ("정렬 · 근거", "선계산 종합점수로 정렬" + NL + "요약 캐시 · 템플릿"),
]
PW, PG, PX = 4.44, 0.37, 0.75
PXS = [PX + i * (PW + PG) for i in range(5)]
for i, (x, (t, s)) in enumerate(zip(PXS, steps)):
    box(ax, x, 3.35, PW, 3.8, t, s, ec=AMBER, ts=10.6, ss=8.1)
    if i < 4:
        arrow(ax, (x + PW + 0.04, 5.25), (x + PW + PG - 0.04, 5.25), color=AMBER)
label(ax, PXS[1] + PW / 2, 2.95, "사전에 없는 자격증만 LLM ③ (최대 2초)", size=7.6, color=PURPLE, weight="bold")

tc = [x + RW / 2 for x in TX]
arrow(ax, (tc[0], 11.8), (PXS[1] + PW / 2, 7.2), color=TEAL, z=1.5)
arrow(ax, (tc[1], 11.8), (PXS[2] + PW / 2, 7.2), color=TEAL, z=1.5)
arrow(ax, (tc[2], 11.8), (PXS[4] + PW / 2 - 0.5, 7.2), color=TEAL, z=1.5)
arrow(ax, (tc[3], 11.8), (PXS[4] + PW / 2 + 0.5, 7.2), color=TEAL, z=1.5)
label(ax, (tc[0] + PXS[1] + PW / 2) / 2 + 0.3, 9.2, "지원사업 · 자격조건", size=7.8, color=TEAL, bg="#FFF6EC")
label(ax, (tc[1] + PXS[2] + PW / 2) / 2 + 0.25, 9.2, "㎡당 월세", size=7.8, color=TEAL, bg="#FFF6EC")
label(ax, (tc[2] + PXS[4] + PW / 2) / 2 - 0.2, 9.2, "종합점수 · 예측 구간", size=7.8, color=TEAL, bg="#FFF6EC")

box(ax, 6.2, 0.3, 12.4, 1.6, "추천 행정동 순위  +  항목별 근거  +  예산 여유", fc=WHITE, ec=NAVY, ts=11.2)
arrow(ax, (PXS[4] + PW / 2, 3.3), (18.65, 1.15), color=NAVY, rad=-0.15)

save(fig, "structure_v0.9.png")

# =====================================================================
# 그림 2. 요청 예시
# =====================================================================
fig, ax = new_ax(12.4, 11.2, (0, 24.8), (0, 22.4))

label(ax, 12.4, 21.8, "예시 — 27세 · 자본금 3,000만 원 · 경력 0년 · 조리기능사 · 한식음식점 · 33㎡   (수치는 설명용 가상값)",
      size=10.2, color=NAVY, weight="bold")


def row(y, h, title, fc=LBLUE, ec=BLUE):
    box(ax, 0.5, y, 4.4, h, title, fc=fc, ec=ec, ts=10.6)
    rbox(ax, 5.3, y, 19.0, h, WHITE, "#CBD5E1", lw=1.1)


# ① 지원금 매칭
row(15.4, 5.6, "①  지원금 매칭" + NL + "(자치구마다 따로)")
label(ax, 5.8, 20.45, "중복수혜 불가 사업은 금액이 가장 큰 하나만, 가능한 사업은 전부 더한다", size=9, color=NAVY, ha="left", weight="bold")
for cx, t in [(9.2, "사업"), (13.4, "금액"), (15.9, "범위"), (18.2, "중복수혜"), (21.6, "결과")]:
    label(ax, cx, 19.5, t, size=8.6, color=GRAY, weight="bold")
progs = [
    ("청년창업사관학교", "2,000만", "전국", "불가", "선택", GREEN),
    ("서울시 청년도약지원금", "1,000만", "전국", "불가", "제외 (더 큰 사업과 중복)", RED),
    ("관악구 청년창업지원금", "500만", "관악구", "가능", "관악구만 선택", GREEN),
]
for i, (n, a, sc, ex, r, c) in enumerate(progs):
    yy = 18.4 - i * 1.0
    label(ax, 9.2, yy, n, size=9.2)
    label(ax, 13.4, yy, a, size=9.2)
    label(ax, 15.9, yy, sc, size=9.2)
    label(ax, 18.2, yy, ex, size=9.2)
    label(ax, 21.6, yy, r, size=9.2, color=c, weight="bold")

# ② 가용예산 · 임대비용
row(8.3, 6.6, "②  가용예산 ·" + NL + "임대비용 · 판정")
label(ax, 5.8, 14.35, "가용 예산 = 자본금 + 지원금     임대비용 = 보증금(월세 × 15) + 월세 × 3 = 월세 × 18",
      size=9, color=NAVY, ha="left", weight="bold")
label(ax, 5.8, 13.6, "월세 = 자치구 ㎡당 월세 × 1,000 × 33㎡   ·   같은 구의 행정동은 같은 값   ·   권리금 · 인테리어 · 집기 제외",
      size=8.2, color=GRAY, ha="left")
cols = [(7.4, "자치구"), (10.3, "가용 예산"), (13.1, "㎡당 월세"), (16.0, "임대비용"), (19.1, "예산 여유"), (22.3, "판정")]
for cx, t in cols:
    label(ax, cx, 12.6, t, size=8.6, color=GRAY, weight="bold")
dist = [
    ("관악구", "5,500만", "28.5천원", "1,692.9만", "+3,807.1만", "통과", GREEN),
    ("중구", "5,000만", "95.0천원", "5,643만", "-643만", "제외", RED),
    ("도봉구", "5,000만", "자료 없음", "—", "—", "확인불가 (후보 유지)", AMBER),
]
for i, (g, b, r, c, m, p, col) in enumerate(dist):
    yy = 11.55 - i * 1.05
    for (cx, _), v in zip(cols, (g, b, r, c, m)):
        label(ax, cx, yy, v, size=9.3)
    label(ax, 22.3, yy, p, size=9.3, color=col, weight="bold")
label(ax, 5.8, 8.75, "임대료가 없는 구를 0원으로 두면 가장 싼 곳으로 1위가 된다 → 탈락시키지 않고 '정보 없음' 배지",
      size=8.2, color=AMBER, ha="left")

# ③ 정렬
row(2.9, 4.9, "③  추천 행정동" + NL + "정렬", fc=LTEAL, ec=TEAL)
label(ax, 5.8, 7.25, "통과 · 확인불가 구의 행정동을 예측표의 종합점수(배치가 미리 계산)로 정렬", size=9, color=NAVY, ha="left", weight="bold")
label(ax, 5.8, 6.35, "종합점수 = 100 × (0.4 × 매출 백분위 + 0.4 × 생존 백분위 + 0.2 × 성장세 백분위)",
      size=9, color=INK, ha="left")
label(ax, 5.8, 5.55, "백분위: 같은 업종의 서울 전체 행정동 안 위치  ·  값이 없는 항목은 빼고 가중치 재배분  ·  예산 여유는 점수에 넣지 않음",
      size=8.2, color=GRAY, ha="left")
label(ax, 5.8, 4.45, "신림동(관악구)  0.4 × 0.88 + 0.4 × 0.75 + 0.2 × 0.65 = 0.782  →  78.2점",
      size=9.2, color=TEAL, ha="left", weight="bold")
label(ax, 5.8, 3.55, "월 매출 3,200만 (2,400만 ~ 4,100만)  ·  예상 생존 42.5개월 (24 ~ 65)  ·  예산 여유 3,807만",
      size=8.4, color=GRAY, ha="left")

box(ax, 6.2, 0.3, 12.4, 1.6, "추천 행정동 순위  +  항목별 근거  +  예산 여유", fc=WHITE, ec=NAVY, ts=11)
arrow(ax, (14.8, 2.85), (12.4, 1.95), color=NAVY)

save(fig, "example_v0.9.png")

# =====================================================================
# 그림 3. AI는 어디서 도는가
# =====================================================================
fig, ax = new_ax(12.4, 9.0, (0, 24.8), (0, 18.0))

label(ax, 12.4, 17.45, "AI는 어디서 도는가 — 학습은 팀원 PC, LLM은 Anthropic API, EC2는 결과 표만 읽는다",
      size=10.4, color=NAVY, weight="bold")

# 팀원 PC
band(ax, 0.3, 1.2, 8.6, 15.6, "팀원 PC  ·  분기 1회", fc="#FFF6EC", ec="#F2C49B", tcol=AMBER)
box(ax, 0.8, 13.4, 7.6, 2.2, "원천 데이터 정리", "매출 · 점포 · 인구 · 인허가" + NL + "행정동 코드로 결합",
    fc=LGRAY, ec=GRAY, ts=10.2, ss=8.1)
box(ax, 0.8, 9.9, 3.7, 2.6, "모델 A  매출", "XGBoost 분위수 회귀" + NL + "점포당 분기 매출" + NL + "p10 · p50 · p90",
    fc=LTEAL, ec=TEAL, ts=10.2, ss=7.8)
box(ax, 4.7, 9.9, 3.7, 2.6, "모델 B  생존", "XGBoost AFT" + NL + "생존기간(개월)" + NL + "p10 · p50 · p90",
    fc=LTEAL, ec=TEAL, ts=10.2, ss=7.8)
box(ax, 0.8, 6.4, 7.6, 2.2, "성장세 · 종합점수", "서울 전체 백분위 · 40 · 40 · 20 가중합" + NL + "→ prediction 표",
    fc=LTEAL, ec=TEAL, ts=10.2, ss=8.1)
box(ax, 0.8, 2.0, 7.6, 2.2, "LLM ② 요약 요청", "표본 충분한 조합만 · Batch API" + NL + "→ summary_cache 표",
    fc=LPURPLE, ec=PURPLE, ts=10.2, ss=8.1)
arrow(ax, (2.65, 13.35), (2.65, 12.55), color=TEAL)
arrow(ax, (6.55, 13.35), (6.55, 12.55), color=TEAL)
arrow(ax, (2.65, 9.85), (2.65, 8.65), color=TEAL)
arrow(ax, (6.55, 9.85), (6.55, 8.65), color=TEAL)
label(ax, 4.6, 5.3, "모델 파일 · 지표 → 팀 드라이브", size=7.8, color=GRAY)

# Anthropic
band(ax, 9.4, 1.2, 5.6, 15.6, "Anthropic API", fc=LPURPLE, ec="#C4B5FD", tcol=PURPLE)
llm = [
    ("LLM ①", "공고 → JSON 구조화" + NL + "매주"),
    ("LLM ②", "행정동 × 업종 요약" + NL + "분기 1회"),
    ("LLM ③", "자격증 표준명 매핑" + NL + "요청 중 · 드물게"),
]
ly = [12.6, 4.9, 1.6]
for (t, s), y in zip(llm, ly):
    box(ax, 9.9, y, 4.6, 2.6, t, s, fc=WHITE, ec=PURPLE, tc=PURPLE, ts=10.4, ss=8.1)
label(ax, 12.2, 11.4, "GPU · 모델 서버 없음" + NL + "쓴 토큰만큼 비용", size=8, color=PURPLE)

# EC2
band(ax, 15.5, 1.2, 9.0, 15.6, "AWS EC2  ·  작은 CPU 인스턴스", fc=LBLUE, ec="#A9C4E6", tcol=BLUE)
box(ax, 16.0, 12.3, 8.0, 2.9, "cron  ·  매주", "지원사업 수집 → LLM ① 요청" + NL + "→ support_program 적재", ec=BLUE, ts=10.4, ss=8.1)
box(ax, 16.0, 7.2, 8.0, 3.6, "운영 DB (PostgreSQL)", "서빙 표 4개" + NL + "support_program · district_rent" + NL + "prediction · summary_cache", ec=NAVY, ts=10.6, ss=8.1)
box(ax, 16.0, 2.3, 8.0, 3.4, "API (FastAPI)", "표 조회 · 사칙연산만" + NL + "모델 파일 없음 · 추론 없음", ec=BLUE, ts=10.6, ss=8.1)

# 화살표
arrow(ax, (8.45, 7.9), (15.95, 9.8), color=AMBER, lw=2.0)
label(ax, 12.2, 9.85, "SSH 터널 · 분기 1회" + NL + "prediction · summary_cache 적재", size=8.2, color=AMBER, weight="bold", bg=WHITE)
arrow(ax, (8.45, 3.1), (9.85, 5.9), color=PURPLE, rad=-0.15)
arrow(ax, (15.95, 13.6), (14.55, 13.9), color=PURPLE)
arrow(ax, (15.95, 3.4), (14.55, 3.0), color=PURPLE)
arrow(ax, (20.0, 12.25), (20.0, 10.85), color=NAVY)
arrow(ax, (20.0, 5.75), (20.0, 7.15), color=NAVY)
label(ax, 20.0, 6.45, "읽기", size=7.8, color=NAVY, bg=LBLUE)

label(ax, 20.0, 0.65, "사용자 → Vercel(프론트) → API", size=8.4, color=GRAY, weight="bold")
label(ax, 4.6, 0.65, "학습 · 적재할 때만 켜져 있으면 된다", size=8.4, color=GRAY, weight="bold")

save(fig, "ai_runtime_v0.9.png")
print("done")
