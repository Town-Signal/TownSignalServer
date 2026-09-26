# API 이미지 — 모델 라이브러리를 넣지 않는다.
# 예측은 배치가 채운 prediction 표를 조회할 뿐이라 xgboost 등이 필요 없다.
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Seoul

COPY requirements/ requirements/
RUN pip install --no-cache-dir -r requirements/api.txt

COPY common/ common/
COPY api/ api/

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
