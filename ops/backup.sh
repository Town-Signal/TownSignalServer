#!/bin/bash
# 저장소 ops/ — 배포할 때 서버 ~/townsignal/ops/로 복사된다
# 매주 월요일 03:30 cron — 사람이 손댄 지원사업표 · 검수 기록만 pg_dump, 최근 4개 유지. 나머지 표는 배치로 다시 만든다.
set -euo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin
cd "$HOME/townsignal"
out="backups/support_program_$(date +%Y%m%d).dump"
docker compose -f compose.prod.yml exec -T db pg_dump -U townsignal -d townsignal -Fc -t support_program -t program_verification > "$out.tmp"
mv "$out.tmp" "$out"
ls -1t backups/support_program_*.dump | tail -n +5 | xargs -r rm
echo "$(date "+%F %T") 백업 완료: $out ($(du -h "$out" | cut -f1))"
