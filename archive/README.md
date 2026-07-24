# archive

은퇴 파일 보관소.

## regenerate_index.py

- 은퇴일: 2026-07-24
- 사유: 비활성 writer. 스케줄(cron/launchd) 미등록으로 실제 실행 이력 없음.
  활성 _INDEX writer는 ~/wf-sync/regen_index_v2.py (집맥 cron, 매시 2,12,22,32,42,52분).
- 복구법: git log --all -- archive/regenerate_index.py 로 이력 확인 후
  git checkout <hash> -- archive/regenerate_index.py 로 복원 가능.
