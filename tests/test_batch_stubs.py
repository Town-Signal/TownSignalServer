"""배치 모듈이 학습 라이브러리 없이 import되는지 확인한다(기본 CI는 배치 의존성을 설치하지 않는다)."""

import subprocess
import sys


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
