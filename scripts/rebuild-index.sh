#!/usr/bin/env bash
set -euo pipefail

VAULT_PATH="${1:-.}"

echo "Rebuilding FTS5 search index for vault: $VAULT_PATH"

PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON="$cmd"
        break
    fi
done

$PYTHON -c "
from secondbrain.database import Database
from secondbrain.vault.manager import VaultManager
from secondbrain.indexes.search import rebuild_index
from pathlib import Path
import time

vault = VaultManager(Path('$VAULT_PATH'))
db = Database(vault.db_path)
db.init_schema()

t0 = time.time()
count = rebuild_index(db, vault)
elapsed = time.time() - t0
print(f'Indexed {count} notes in {elapsed:.1f}s')
"
