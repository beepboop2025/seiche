#!/usr/bin/env python3
"""Copy encrypted archives to mounted development storage; never fetch keys."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--destination',required=True)
    p.add_argument('--ssd-check',required=True)
    p.add_argument('--ssh-host',default='liquilens-hetzner')
    p.add_argument('--kind', choices=['network', 'data'], default='network')
    a=p.parse_args()
    state=json.loads(subprocess.check_output([a.ssd_check,'status','--json']))
    if state.get('status')!='READY':raise SystemExit('SSD is not ready; sync skipped')
    destination=Path(a.destination)
    workspace=Path(state['alias']).resolve()
    if not destination.resolve().is_relative_to(workspace):raise SystemExit('Destination must be on the verified workspace')
    destination.mkdir(mode=0o700,parents=True,exist_ok=True)
    if destination.stat().st_mode&0o077:raise SystemExit('Destination must be private')
    prefix, folder, schema = ('backup', 'backups', 'seiche-agent-network.backup-receipt.v1') if a.kind == 'network' else ('data', 'data-backups', 'agent-platform.data-backup.v1')
    subprocess.run(['rsync','-az','--chmod=go-rwx','--include='+prefix+'-*.json','--include='+prefix+'-*.sanbackup','--exclude=*','-e','ssh -o BatchMode=yes -o ConnectTimeout=15',a.ssh_host+':/var/lib/seiche-agent-network/'+folder+'/',str(destination)+'/'],check=True,timeout=300)
    verified=removed=0;cutoff=datetime.now(timezone.utc)-timedelta(days=7)
    for receipt in destination.glob(prefix+'-*.json'):
        if not re.fullmatch(prefix+r'-\d{8}T\d{6}Z-[a-f0-9]{12}\.json',receipt.name) or receipt.is_symlink():continue
        record=json.loads(receipt.read_text());bundle=receipt.with_suffix('.sanbackup')
        if record.get('schema')!=schema or not bundle.is_file() or bundle.is_symlink():raise SystemExit('Incomplete backup pair')
        digest=hashlib.sha256()
        with bundle.open('rb') as f:
            for chunk in iter(lambda:f.read(1024*1024),b''):digest.update(chunk)
        if digest.hexdigest()!=record['ciphertext_sha256']:raise SystemExit('Backup hash mismatch')
        if datetime.fromisoformat(record['created_at'])<cutoff:
            bundle.unlink();receipt.unlink();removed+=1
        else:verified+=1
    report={'status':'verified' if verified else 'no_current_backups','verified_bundles':verified,'expired_bundles_removed':removed,'checked_at':datetime.now(timezone.utc).isoformat(),'retention_days':7,'scope':'encrypted '+a.kind+' database backups only; backup decryption key held separately'}
    fd=os.open(destination/'sync-status.json',os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as f:json.dump(report,f,indent=2)
    print(json.dumps(report))
    return 0 if verified else 1


if __name__=='__main__':raise SystemExit(main())
