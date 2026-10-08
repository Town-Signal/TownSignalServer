"""다른 팀원이 구현할 배치 작업의 골격.

인터페이스만 있고 본문은 NotImplementedError(명세 4.6 · 4.8 · 6.2 ~ 6.6).
"""

import subprocess
import sys

import pytest

from batch.jobs import build_summary_cache, build_support_program, train_and_predict
from batch.llm import client as llm_client

STUBS = [
    (llm_client.generate_json, ("프롬프트", {})),
    (build_support_program.submit_structuring_batch, ([],)),
    (build_support_program.collect_pending_llm_results, (None,)),
    (build_support_program.main, ()),
    (train_and_predict.load_training_frames, (None,)),
    (train_and_predict.train_sales_model, ({},)),
    (train_and_predict.train_survival_model, ({},)),
    (train_and_predict.compute_growth, (None,)),
    (train_and_predict.predict_all, ({}, {}, "xgb_v1")),
    (train_and_predict.main, ()),
    (build_summary_cache.select_targets, (None,)),
    (build_summary_cache.build_prompt, ({},)),
    (build_summary_cache.request_summaries, ([],)),
    (build_summary_cache.upsert_summaries, (None, [])),
    (build_summary_cache.main, ()),
]


@pytest.mark.parametrize(("func", "args"), STUBS, ids=lambda x: getattr(x, "__qualname__", ""))
def test_stub_raises_not_implemented(func, args):
    assert func.__doc__  # 입력 · 출력 · 명세 절을 적은 docstring이 있다
    with pytest.raises(NotImplementedError):
        func(*args)


def test_batch_modules_import_without_training_libraries():
    """CI는 배치 의존성(xgboost · pandas 등)을 설치하지 않는다 — 모듈 import만으로 끌어오면 안 된다."""
    code = (
        "import sys, batch.jobs.run_weekly, batch.jobs.train_and_predict, batch.jobs.build_summary_cache, "
        "batch.jobs.build_support_program; "
        "print(sorted(m for m in ('xgboost', 'pandas', 'sksurv', 'sklearn') if m in sys.modules))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert out == "[]"
