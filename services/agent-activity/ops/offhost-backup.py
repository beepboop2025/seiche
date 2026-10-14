"""Copy the latest encrypted service backup after mount verification; no secret output."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time

ROOT = Path("/Users/mrinal/ProductData/agent-activity-backups")
STATUS = Path("/Users/mrinal/SSDWorkspace/artifacts/agent-activity-ops-20261014/offhost-status.json")
SSH = ["/usr/bin/ssh", "-o", "ControlPath=none", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "liquilens-hetzner"]
PATTERN = r"[0-9]{8}T[0-9]{6}Z[.]enc"

def hashed(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()

def run():
    mounted = json.loads(subprocess.check_output(["/Users/mrinal/.local/bin/ssd-data", "status", "--json"], timeout=30))
    if mounted["status"] != "READY":
        raise RuntimeError("product_data_not_ready")
    if ROOT.is_symlink() or not ROOT.is_dir() or ROOT.stat().st_mode & 0o077 or ROOT.stat().st_uid != os.getuid():
        raise RuntimeError("backup_directory_not_private")
    for old in ROOT.glob("*.enc"):
        if re.fullmatch(PATTERN, old.name) and not old.is_symlink():
            stamp = datetime.datetime.strptime(old.stem, "%Y%m%dT%H%M%SZ").replace(tzinfo=datetime.timezone.utc).timestamp()
            if stamp < time.time()-7*86400:
                old.unlink()
    program = """import hashlib,json,pathlib
files=sorted(pathlib.Path('/var/backups/seiche-agent-activity').glob('*.enc'))
if not files: raise SystemExit('no_backup')
p=files[-1]; h=hashlib.sha256()
with p.open('rb') as f:
 for block in iter(lambda:f.read(1048576),b''): h.update(block)
print(json.dumps({'name':p.name,'sha256':h.hexdigest(),'mtime':p.stat().st_mtime,'bytes':p.stat().st_size}))
"""
    record = json.loads(subprocess.check_output(SSH+["python3 -c "+shlex.quote(program)], timeout=90))
    if not re.fullmatch(PATTERN, record["name"]) or time.time()-record["mtime"] > 48*3600:
        raise RuntimeError("server_backup_missing_or_stale")
    target = ROOT / record["name"]
    if target.exists():
        if target.is_symlink() or hashed(target) != record["sha256"]:
            raise RuntimeError("immutable_backup_digest_conflict")
    else:
        temporary = ROOT / (record["name"]+".partial")
        try:
            with os.fdopen(os.open(temporary, os.O_CREAT|os.O_EXCL|os.O_WRONLY, 0o600), "wb") as output:
                result = subprocess.run(SSH+["cat /var/backups/seiche-agent-activity/"+record["name"]], stdout=output, stderr=subprocess.DEVNULL, timeout=600)
            if result.returncode or hashed(temporary) != record["sha256"]:
                raise RuntimeError("archive_transfer_integrity_failed")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return {"status":"verified_offhost_copy", "checked_at":time.time(), "archive":record["name"], "sha256":record["sha256"], "bytes":record["bytes"], "key_location":"separate_owner_private_config"}

if __name__ == "__main__":
    try:
        report=run(); code=0
    except Exception as error:
        report={"status":"attention_required", "checked_at":time.time(), "error":type(error).__name__}; code=1
    # Parent is the already-mounted development SSD, never a fallback volume directory.
    if STATUS.parent.is_dir():
        STATUS.write_text(json.dumps(report,indent=2)+"\n"); STATUS.chmod(0o600)
    print(json.dumps(report)); raise SystemExit(code)
