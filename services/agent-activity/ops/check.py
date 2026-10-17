"""Local status, not an external notification. Exit 2 means attention, never source success."""
import json
import shutil
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:18804/v1/coverage", timeout=15) as response:
    value = json.load(response)
problems = [{"source": r["id"], "status": r["status"], "reason": r["error"]} for r in value["sources"]
            if r["status"] != "ok" and r["error"] != "no_reviewed_public_accounts"]
free = shutil.disk_usage("/var/lib/seiche-agent-activity").free
if free < 3*1024**3:
    problems.append({"disk": "below_3_gib_reserve"})
print(json.dumps({"status": "attention_required" if problems else "sources_within_collection_windows", "problems": problems,
    "private_account_panel": "requires_separate_enrollment", "global_coverage": "unknown"}))
raise SystemExit(2 if problems else 0)
