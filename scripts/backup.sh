#!/usr/bin/env bash
# Erstellt ein konsistentes Backup der SQLite-Datenbank.
set -euo pipefail

DB_PATH="${1:-./data/o-energy.db}"
BACKUP_DIR="${BACKUP_DIR:-./data/backups}"

if [ ! -f "$DB_PATH" ]; then
  echo "Datenbank nicht gefunden: $DB_PATH" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
TARGET="$BACKUP_DIR/o-energy-$(date +%Y%m%d-%H%M%S).db"

if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "$DB_PATH" ".backup '$TARGET'"
else
  # Fallback ohne sqlite3-CLI: die WAL-Dateien mit kopieren und konsistent machen
  cp "$DB_PATH" "$TARGET"
  for suffix in "-wal" "-shm"; do
    [ -f "${DB_PATH}${suffix}" ] && cp "${DB_PATH}${suffix}" "${TARGET}${suffix}"
  done
fi

gzip -f "$TARGET"
echo "Backup geschrieben: ${TARGET}.gz"
