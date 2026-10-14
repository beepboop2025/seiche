import json
from pathlib import Path
import shutil
import time

from agent_activity.cli import backup
from agent_activity.store import Store

root = Path("/var/backups/seiche-agent-activity")
if shutil.disk_usage(root).free < 3*1024**3:
    raise SystemExit("Backup refused: less than 3 GiB free")
store = Store("/var/lib/seiche-agent-activity")
store.expire()
result = backup(store, root / (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())+".enc"), "/etc/seiche-agent-activity/backup.key")
for path in root.glob("*.enc"):
    if not path.is_symlink() and path.stat().st_mtime < time.time()-7*86400:
        path.unlink()
print(json.dumps(result))
