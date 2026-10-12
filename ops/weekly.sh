#!/bin/bash
# 저장소 ops/ — 배포할 때 서버 ~/townsignal/ops/로 복사된다
# 매주 월요일 03:00 cron — 주간 배치(run_weekly). 배포된 버전(.deployed_tag)과 같은 batch 이미지로 돈다.
set -u
export PATH=/usr/local/bin:/usr/bin:/bin
cd "$HOME/townsignal"
echo "===== $(date "+%F %T") weekly 시작"
REG=$(grep ^ECR_REGISTRY= .env | cut -d= -f2)
aws ecr get-login-password --region ap-southeast-2 | docker login --username AWS --password-stdin "$REG" >/dev/null 2>&1
IMAGE_TAG=$(cat .deployed_tag) docker compose -f compose.prod.yml --profile batch run --rm batch batch.jobs.run_weekly
rc=$?
echo "===== $(date "+%F %T") weekly 종료 (exit $rc)"
exit $rc
