"""모델 개선 과정 그림 2장 (matplotlib, Malgun Gothic)

docs/figures/model_a_improvement.png — 모델 A(매출) naive 대비 오차 개선율의 변화
docs/figures/model_b_improvement.png — 모델 B(생존) C-index의 변화

수치 출처는 노션 「분석계획서」 v0.13의 '실험 기록' 표(A2 · A5 · A7, B2 ~ B12).
실행: python docs/tools/model_improvement.py
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import FancyArrowPatch

plt.rcParams["font.family"] = "Malgun Gothic"
plt.rcParams["axes.unicode_minus"] = False  # 맑은 고딕에는 U+2212가 없어 '-'를 쓴다

OUT = Path(__file__).resolve().parents[1] / "figures"

INK = "#1F2937"
SUB = "#4B5563"
MUTED = "#9CA3AF"
GRID = "#E5E7EB"
LINE = "#0F766E"  # 저장소 그림의 TEAL
DEPLOY = "#0B4F49"
CHIP = "#E6F4F1"
REJECT = "#9CA3AF"
BAD_ZONE = "#F3F4F6"
GOAL = "#B45309"  # 저장소 그림의 AMBER — 목표선

W, H, DPI = 13.0, 8.2, 200
AX = [0.075, 0.30, 0.86, 0.46]  # 본 그래프 위치(그림 비율)
WHITE_BOX = dict(boxstyle="round,pad=0.2", fc="white", ec="none")


def base_axes(fig, title, subtitle):
    fig.patch.set_facecolor("white")
    ax = fig.add_axes(AX)
    fig.text(AX[0], 0.935, title, fontsize=26, fontweight="bold", color=INK, va="top")
    fig.text(AX[0], 0.862, subtitle, fontsize=15, color=SUB, va="top")
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(axis="y", length=0, labelsize=13.5, labelcolor=MUTED, pad=8)
    ax.tick_params(axis="x", length=0)
    ax.grid(axis="y", color=GRID, linewidth=1)
    ax.set_axisbelow(True)
    return ax


def point(ax, x, y, deployed=False):
    ax.plot(x, y, "o", ms=18 if deployed else 15, mfc=DEPLOY if deployed else LINE,
            mec="white", mew=3, zorder=5)


def value(ax, x, y, text, dy, color=INK, size=21):
    ax.text(x, y + dy, text, ha="center", va="bottom" if dy > 0 else "top", fontsize=size,
            fontweight="bold", color=color, zorder=6)


def xlabels(ax, labels):
    """x축 아래 두 줄 이름 + 날짜. 날짜는 모든 칸이 같은 높이에 오게 한다."""
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels([""] * len(labels))
    max_lines = max(name.count("\n") + 1 for name, _ in labels)
    for i, (name, date) in enumerate(labels):
        ax.annotate(name, (i, 0), xycoords=("data", "axes fraction"), xytext=(0, -14),
                    textcoords="offset points", ha="center", va="top", fontsize=15,
                    fontweight="bold", color=INK, linespacing=1.2)
        ax.annotate(date, (i, 0), xycoords=("data", "axes fraction"),
                    xytext=(0, -14 - 22 * max_lines - 6), textcoords="offset points",
                    ha="center", va="top", fontsize=12.5, color=MUTED)


def footer(fig, note, items):
    fig.text(AX[0], 0.135, note, fontsize=13, color=SUB, va="top", linespacing=1.55)
    x, y = AX[0], 0.04
    for kind, text in items:
        style = {
            "main": dict(ms=12, mfc=LINE, mec="white"),
            "deploy": dict(ms=14, mfc=DEPLOY, mec="white"),
            "reject": dict(ms=12, mfc="white", mec=REJECT),
        }[kind]
        fig.lines.append(Line2D([x + 0.007], [y], marker="o", mew=2.5,
                                transform=fig.transFigure, **style))
        t = fig.text(x + 0.02, y, text, fontsize=13, color=SUB, va="center")
        fig.canvas.draw()
        x = t.get_window_extent().transformed(fig.transFigure.inverted()).x1 + 0.035


# ── 모델 A ────────────────────────────────────────────────────────────────
def model_a():
    fig = plt.figure(figsize=(W, H), dpi=DPI)
    ax = base_axes(
        fig,
        "모델 A · 매출 예측은 어떻게 좋아졌나",
        "‘직전 분기 매출을 그대로 쓰는 규칙’보다 오차(MAE)가 몇 % 적은가  —  "
        "0%보다 위여야 모델을 쓸 이유가 있다",
    )
    xs, ys = [0, 1, 2], [-4.8, 7.0, 13.2]
    cover = ["범위 적중 74%", "범위 적중 75%", "범위 적중 83%"]

    ax.set_xlim(-0.5, 2.5)
    ax.set_ylim(-9, 18)
    ax.set_yticks([-5, 0, 5, 10, 15])
    ax.set_yticklabels(["-5%", "0%", "+5%", "+10%", "+15%"])

    ax.axhspan(-9, 0, color=BAD_ZONE, zorder=0)
    ax.axhline(0, color=SUB, lw=1.6, zorder=1)
    ax.text(2.47, -0.6, "규칙(직전 분기 그대로) = 0%", ha="right", va="top", fontsize=13, color=SUB)
    ax.text(2.47, -8.3, "규칙보다 못함", ha="right", va="bottom", fontsize=13, color=MUTED)

    ax.plot(xs, ys, color=LINE, lw=4, zorder=3, solid_capstyle="round")
    for x, y, c in zip(xs, ys, cover, strict=True):
        point(ax, x, y, deployed=True)
        if x == 0:  # 0% 선과 겹치지 않게 범위 적중은 점 아래
            value(ax, x, y, f"{y:+.1f}%", 1.5)
            ax.text(x, y - 1.4, c, ha="center", va="top", fontsize=13, color=SUB, zorder=6)
        else:  # 점 위에 범위 적중, 그 위에 큰 값
            ax.text(x, y + 1.3, c, ha="center", va="bottom", fontsize=13, color=SUB, zorder=6,
                    bbox=WHITE_BOX)
            value(ax, x, y, f"{y:+.1f}%", 3.3)

    # 사이에 한 점검 두 가지
    for y, text in [(4.6, "11/19  신경망과 비교 → XGBoost 유지"),
                    (2.2, "11/23  범위 적중률 점검 → 보정 방식은 정상")]:
        ax.text(1.62, y, text, ha="center", va="center", fontsize=12.5, fontweight="bold",
                color=LINE, zorder=6,
                bbox=dict(boxstyle="round,pad=0.45,rounding_size=0.7", fc=CHIP, ec="none"))

    # 분해 실험 — A5의 +7.0%는 어디서 왔나
    inset = fig.add_axes([0.205, 0.585, 0.2, 0.135])
    parts = [("피처만 추가", -1.7, REJECT), ("출발점만 바꿈", 5.6, REJECT), ("둘 다 (A5)", 7.0, LINE)]
    for i, (lab, v, col) in enumerate(parts):
        yy = len(parts) - 1 - i
        inset.barh(yy, v, height=0.56, color=col, zorder=2)
        inset.text(-2.6, yy, lab, ha="right", va="center", fontsize=12.5, color=INK)
        vx = v + 0.35 if v > 0 else 0.35  # 음수 값도 0선 오른쪽에 써서 이름과 안 겹치게
        inset.text(vx, yy, f"{v:+.1f}%", ha="left", va="center", fontsize=12.5,
                   fontweight="bold", color=INK)
    inset.axvline(0, color=SUB, lw=1.2)
    inset.set_xlim(-2.6, 9.5)
    inset.set_ylim(-0.6, 2.6)
    inset.set_xticks([])
    inset.set_yticks([])
    for sp in inset.spines.values():
        sp.set_visible(False)
    inset.set_facecolor("white")
    fig.text(0.075, 0.745, "A5의 +7.0%는 어디서 왔나", fontsize=13.5, fontweight="bold", color=INK)

    xlabels(ax, [
        ("첫 학습\n매출을 직접 예측", "10/12 · A2"),
        ("직전 분기에서\n‘변화’만 학습", "10/13 · A5"),
        ("데이터를 2026Q2까지\n늘려 다시 학습", "11/24 · A7"),
    ])
    footer(
        fig,
        "개선은 피처보다 구조(직전 분기를 출발점으로 두고 변화만 학습)에서 나왔다. "
        "A7은 모델 구조는 그대로이고 테스트 구간이\n"
        "2026Q1 · Q2로 바뀐 값이다.  "
        "범위 적중 = 실제 매출이 예측 80% 범위(p10 ~ p90)에 든 비율, 목표 80%.",
        [("deploy", "운영 예측표에 적재한 모델"), ("reject", "비교용 분해 실험")],
    )
    fig.savefig(OUT / "model_a_improvement.png", dpi=DPI, facecolor="white")
    plt.close(fig)


# ── 모델 B ────────────────────────────────────────────────────────────────
def model_b():
    fig = plt.figure(figsize=(W, H), dpi=DPI)
    ax = base_axes(
        fig,
        "모델 B · 생존 예측은 어떻게 좋아졌나",
        "C-index = 두 점포 중 어느 쪽이 먼저 닫을지 맞히는 비율  —  아무렇게나 찍으면 0.5, 완벽하면 1.0",
    )
    xs = list(range(7))
    ys = [0.608, 0.619, 0.668, 0.653, 0.643, 0.656, 0.666]
    deployed = {1, 6}

    ax.set_xlim(-0.55, 6.55)
    ax.set_ylim(0.555, 0.735)
    ax.set_yticks([0.57, 0.60, 0.65, 0.70])
    ax.set_yticklabels(["0.57", "0.60", "0.65", "0.70"])

    for y, lab, col, ls, bold in [
        (0.70, "목표 0.70", GOAL, (0, (6, 4)), True),
        (0.65, "하한 0.65", SUB, (0, (6, 4)), True),
        (0.57, "기준선 0.57 · 업종 평균 생존기간으로만 찍기(Kaplan–Meier)", MUTED, (0, (2, 3)), False),
    ]:
        ax.axhline(y, color=col, lw=1.7, ls=ls, zorder=1)
        lx, ha = (-0.52, "left") if bold else (6.52, "right")  # 목표 · 하한은 왼쪽(점과 안 겹침)
        ax.text(lx, y + 0.0025, lab, ha=ha, va="bottom", fontsize=13, color=col,
                fontweight="bold" if bold else "normal")

    ax.plot(xs, ys, color=LINE, lw=4, zorder=3, solid_capstyle="round")

    # 기각한 실험 — 숫자를 올렸다가 스스로 내린 지점
    rx, ry = 2.5, 0.693
    ax.plot([2, rx], [0.668, ry], color=REJECT, lw=2, ls=(0, (4, 3)), zorder=2)
    ax.add_patch(FancyArrowPatch(
        (rx + 0.04, ry - 0.006), (3 - 0.05, 0.653 + 0.007),
        arrowstyle="-|>,head_length=8,head_width=4.5", connectionstyle="arc3,rad=-0.3",
        color=REJECT, lw=2, ls=(0, (4, 3)), zorder=4,
    ))
    ax.plot(rx, ry, "o", ms=16, mfc="white", mec=REJECT, mew=3, zorder=5)
    ax.text(rx + 0.13, ry, "0.693", ha="left", va="center", fontsize=21, fontweight="bold",
            color=REJECT, zorder=6, bbox=WHITE_BOX)
    ax.text(rx - 0.05, 0.7045, "같은 자리 이력을 넣었더니 팝업 점포를 알아본 효과 → 기각",
            ha="center", va="bottom", fontsize=13, color=SUB, zorder=6)

    below = {1, 3, 4}
    for x, y in zip(xs, ys, strict=True):
        point(ax, x, y, deployed=x in deployed)
        value(ax, x, y, f"{y:.3f}", -0.0062 if x in below else 0.0062)
    for x, y, text in [(3, 0.653, "팝업 빼고 다시 잼\n= 정직한 값"),
                       (4, 0.643, "운영 DB로 재현\n→ 하한 미달, 보류")]:
        ax.text(x, y - 0.0205, text, ha="center", va="top", fontsize=13, color=SUB,
                linespacing=1.35)

    xlabels(ax, [
        ("첫 학습", "10/12 · B2"),
        ("인허가\n이력 피처", "10/13 · B3"),
        ("점포 면적 등\n점포 특성", "11/10~11 · B5~7"),
        ("팝업 점포\n제외", "11/12 · B9"),
        ("운영 데이터\n재학습", "11/19 · B10"),
        ("좌표 위치\n피처", "11/22 · B11"),
        ("운영 반영", "11/24 · B12"),
    ])
    footer(
        fig,
        "‘같은 동네 · 업종 안에서 가게를 구분하는’ 정보(점포 면적 · 위치)가 성능을 올렸다. "
        "0.693은 팝업 · 한시 점포가 섞여 부풀린\n"
        "값이라 버리고, 일반 점포 기준으로 다시 잰 값부터 쌓았다.  "
        "화면 출력은 1 · 3년 생존 확률(1년 예측 84% / 실제 87%).",
        [("main", "실험 결과"), ("deploy", "운영 예측표에 적재"), ("reject", "기각한 실험")],
    )
    fig.savefig(OUT / "model_b_improvement.png", dpi=DPI, facecolor="white")
    plt.close(fig)


if __name__ == "__main__":
    model_a()
    model_b()
    print("saved:", OUT / "model_a_improvement.png", OUT / "model_b_improvement.png")
