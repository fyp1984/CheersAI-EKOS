#!/usr/bin/env bash
# EKOS shared kernel · backup & restore skeleton (Q6 operational minimum).
# Secrets come from environment variables, never hardcoded in this script.
set -euo pipefail

ACTION="${1:-backup}"
BACKUP_DIR="${EKOS_BACKUP_DIR:-./backups}"
TS="$(date +%Y%m%d-%H%M%S)"

# 口令只从环境取，不给默认值：给了默认值就是把它提交进 git。
# 用法：set -a; . deploy/compose/.env; set +a; ./deploy/scripts/backup_restore.sh backup
: "${PGPASSWORD:?PGPASSWORD 未设置（可从 deploy/compose/.env 载入），拒绝以默认口令继续}"

case "$ACTION" in
  backup)
    mkdir -p "$BACKUP_DIR"
    PGPASSWORD="$PGPASSWORD" pg_dump \
      -h "${PGHOST:-127.0.0.1}" -p "${PGPORT_HOST:-15432}" \
      -U "${PGUSER:-ekos}" -F c -f "$BACKUP_DIR/pg-$TS.dump" "${PGDATABASE:-ekos}"
    echo "backup written: $BACKUP_DIR/pg-$TS.dump"
    ;;
  restore)
    SRC="${2:?usage: backup_restore.sh restore <dump-file>}"
    PGPASSWORD="$PGPASSWORD" pg_restore \
      -h "${PGHOST:-127.0.0.1}" -p "${PGPORT_HOST:-15432}" \
      -U "${PGUSER:-ekos}" -d "${PGDATABASE:-ekos}" --clean --if-exists "$SRC"
    echo "restored from $SRC"
    ;;
  *) echo "usage: $0 {backup|restore <file>}"; exit 2 ;;
esac
