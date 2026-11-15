#!/usr/bin/env bash
# Erstellt Backups der Datenbank: SQLite-Kopie und schemaunabhängigen JSON-Export.
set -euo pipefail

DB_PATH="${1:-./data/o-energy.db}"
BACKUP_DIR="${BACKUP_DIR:-./data/backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"

if [ ! -f "$DB_PATH" ]; then
  echo "Datenbank nicht gefunden: $DB_PATH" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
TARGET="$BACKUP_DIR/o-energy-$STAMP.db"

if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "$DB_PATH" ".backup '$TARGET'"
else
  # Fallback ohne sqlite3-CLI: die WAL-Dateien mit kopieren
  cp "$DB_PATH" "$TARGET"
  for suffix in "-wal" "-shm"; do
    [ -f "${DB_PATH}${suffix}" ] && cp "${DB_PATH}${suffix}" "${TARGET}${suffix}"
  done
fi

gzip -f "$TARGET"
echo "SQLite-Backup: ${TARGET}.gz"

# JSON-Export (schemaunabhängig, mit 'oenergy import' wiederherstellbar)
if command -v uv >/dev/null 2>&1; then
  uv run oenergy export --out "$BACKUP_DIR/o-energy-$STAMP.json.gz" || \
    echo "JSON-Export fehlgeschlagen (oenergy export wird übersprungen)"
else
  echo "uv nicht gefunden - JSON-Export wird übersprungen"
fi

# Alte Backups begrenzen (die 10 neuesten behalten)
if [ -d "$BACKUP_DIR" ]; then
  ls -1t "$BACKUP_DIR"/o-energy-*.db.gz 2>/dev/null | tail -n +11 | xargs -r rm -f
  ls -1t "$BACKUP_DIR"/o-energy-*.json.gz 2>/dev/null | tail -n +11 | xargs -r rm -f
fi

echo "Backups liegen in $BACKUP_DIR"
