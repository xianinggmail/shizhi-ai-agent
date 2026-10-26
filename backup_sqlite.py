"""Read-only consistent SQLite backup; no model or secret access."""
from pathlib import Path
import sqlite3,sys
source=Path(sys.argv[1]).resolve();destination=Path(sys.argv[2]).resolve()
if not source.is_file():raise SystemExit('Source database missing')
if destination.exists():raise SystemExit('Backup destination already exists')
with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as original:
    with sqlite3.connect(destination) as backup:original.backup(backup)
print('SQLite backup completed')
