"""Pure native publication admission; actual transfer validators, no production I/O."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location("protected_release_fixture",Path(__file__).resolve().parents[3] / "backend/tests/test_hetzner_release.py")
legacy=importlib.util.module_from_spec(spec);spec.loader.exec_module(legacy)
r=legacy.release
SOURCE="1bcf8d7a48b1db59cc25cd93f6b45260a31dc814"
TREE="fd4918ef3e16c8e01643aae22723b4c0fac43bae"
NOW=legacy.NOW
at=legacy.at


def fixture():
    e=json.loads(json.dumps(legacy.chain()).replace('"'+legacy.SOURCE+'"','"'+SOURCE+'"'))
    e={k:v for k,v in e.items() if k in {"grant","acceptance","release","boot","restart"}}
    e["schema"]=r.NATIVE_EVIDENCE_SCHEMA
    e["source_equivalence"]=None;e["source_acceptance"]=None
    grant=e["grant"]["payload"];grant["code"]["tree"]=TREE
    for key in ("acceptance","release","restart"):e[key]["code"]=deepcopy(grant["code"])
    parent={"commit":"b"*40,"handoff_id":"a"*64,"envelope_sha256":"b"*64,"payload_sha256":"c"*64}
    final={"equal":True,"source_manifest_sha256":"6"*64,"restored_manifest_sha256":"6"*64}
    runtime=r.native_runtime_validator(SOURCE)
    from seiche.stateful_migration import absent_agent_room_audit
    e["final_restore"]={"schema":"seiche.hetzner-final-restore.v1","status":"PASS","authority_changed":False,
        "writer_grant_issued":False,"public_origin_changed":False,"handover_id":"2"*64,
        "code":deepcopy(grant["code"]),"image_digest":grant["host"]["image_digest"],
        "host_identity":grant["host"]["identity"],"volume_uuid":grant["host"]["volume_uuid"],"final_state":final,
        "source_backup":{"source":parent["commit"],"snapshot_id":"20261021T070000Z","capture_sha256":"7"*64,"transfer_index_sha256":"9"*64},
        "filesystem":{"status":"PASS","generation_root":grant["data"]["generation_root"],
            "tree_sha256":{n:"e"*64 for n in ("api","market","nbs","palimpsest-china")},
            "palimpsest_environment":{},"validated_parent_handoff":parent,"agent_room_audit":absent_agent_room_audit()},
        "postgres":{"database_name":grant["data"]["database_name"],"postgres_socket":grant["data"]["postgres_socket"],
            "postgres_port":5432,"runtime_database_role":"seiche_runtime","parent":parent,"counts":[4,3,2,1],"count_floor":[4,3,2,1]}}
    grant["restore_sha256"]=r.digest(e["final_restore"])
    e["transfer"]={"schema":"seiche.hetzner-final-transfer.v1","status":"PASS","authority_transferred":False,
        "writer_grant_issued":False,"handover_id":"2"*64,"source":{"commit":parent["commit"],
            "original_pointer_sha256":"3"*64,"terminal_pointer_sha256":"4"*64},
        "fence":{"receipt_sha256":"9"*64,"all_application_writers_absent":True,"database_admissions_closed":True},
        "final_state":final,"capture":{"manifest_sha256":"7"*64,"content_set_sha256":"8"*64,"snapshot_id":"20261021T070000Z"},
        "destination":{"provider":"hetzner","host_identity":grant["host"]["identity"],"volume_uuid":grant["host"]["volume_uuid"],
            "generation_root":grant["data"]["generation_root"],"database_name":grant["data"]["database_name"],
            "restore_receipt_sha256":grant["restore_sha256"]}}
    grant["transfer_sha256"]=r.digest(e["transfer"]);gh=r.digest(e["grant"])
    for key in ("acceptance","release","restart"):e[key]["grant_sha256"]=gh
    for k in ("transfer_sha256","restore_sha256"):e["acceptance"][k]=grant[k]
    sock={"path":"/run/seiche-native/http.sock","uid":10001,"gid":10001,"mode":"0666",
        "directory_acl":"user::rwx,user:999:--x,group::---,mask::--x,other::---",
        "socket_acl":"user::rw-,user:999:rw-,group::---,mask::rw-,other::rw-",
        "caddy_uid":999,"caddy_connect_verified":True,"unrelated_uid_denied":True}
    e["release"]["socket"]=sock;e["restart"]["socket"]=deepcopy(sock)
    for key in ("release","restart"):
        e[key]["systemd"]["unit"]="seiche-native.service"
        e[key]["postgres"]["systemd"]["unit"]="seiche-native-postgres.service"
    e["boot"]["unit"]="seiche-native.service";e["boot"]["postgres"]["unit"]="seiche-native-postgres.service"
    e["restart"]["processes"]["roles"]={"supervisor":{"pid":7,"ppid":1},
        **{key:{"pid":pid,"ppid":7} for key,pid in (("api",8),("market",9),("source",10))}}
    e["boot"]["release_accepted_sha256"]=r.digest(e["release"])
    e["restart"]["release_accepted_sha256"]=r.digest(e["release"])
    e["restart"]["boot_enabled_sha256"]=r.digest(e["boot"])
    artifacts=[{"path":"/private/"+n,"bytes":1000,"sha256":"e"*64,
                "remote":"gcrypt:railway-migration-20261106/configuration/seiche-native-fixture-"+n}
               for n in ("postgres.dump","filesystem.tar.gz")]
    e["backup"]={"schema":"seiche.native-backup.v1","kind":"data","status":"local_readback_verified_cloud_pending",
        "runtime_id":grant["runtime_id"],"source":deepcopy(grant["code"]),"host":deepcopy(grant["host"]),"grant_sha256":gh,
        "transfer_sha256":grant["transfer_sha256"],"restore_sha256":grant["restore_sha256"],"captured_at":at(-20),
        **{k:False for k in ("cross_store_point_in_time","final_fenced_capture","source_writers_stopped","automatic_restore_or_writer_authority","source_deleted")},
        "database_snapshot":{"database_name":grant["data"]["database_name"],"archive_table_of_contents_verified":True,
            "container_id":e["release"]["postgres"]["container_id"],
            "disposable_restore_verified":False,"started_at":at(-40),"finished_at":at(-30)},
        "components":{n:{"files":1,"sqlite_online_snapshots":[],"started_at":at(-30),"finished_at":at(-21),
            "consistency":"per-file stable reads and per-database SQLite transactions; not a global point in time"}
            for n in ("api","market","nbs","palimpsest-china")},
        "audits":{"agent_room":absent_agent_room_audit(),"nbs":"not_onboarded","palimpsest_china":{"audit_schema":"fixture"}},
        "scratch_proof_sha256":"f"*64,"artifacts":artifacts}
    cloud=deepcopy(artifacts)+[{"path":"/private/manifest.json","bytes":len(r.canonical(e["backup"])),
        "sha256":r.digest(e["backup"]),"remote":"gcrypt:railway-migration-20261106/configuration/seiche-native-fixture-manifest.json"}]
    for a in cloud:a.update(independent_decrypted_readback=True,remote_object_id="fixture-"+Path(a["path"]).name)
    e["backup_readback"]={"schema":"seiche.native-backup-cloud.v1","status":"encrypted_remote_readback_verified",
        "kind":"data","runtime_id":grant["runtime_id"],"source":deepcopy(grant["code"]),"source_deleted":False,
        "automatic_restore_or_writer_authority":False,"verified_at":at(-10),"artifacts":cloud}
    obs={"identity":{"provider":"hetzner","source_sha":SOURCE,"runtime_id":grant["runtime_id"],"authority":"production","cache_control":"no-store"},
        "body":{"provider":"hetzner","authority":"production","release_sha":SOURCE,"snapshot_producer_sha":SOURCE,
                "generated_at":at(-2),"version":"0.16.6 estuary","faults":[],"provenance":[{"source":"fred","observed_at":at(-5)}]}}
    e["monitor"]={"schema":"seiche.native-monitor.v1","status":"PASS","runtime_id":grant["runtime_id"],
        "code":deepcopy(grant["code"]),"grant_sha256":gh,"observed_at":at(-1),
        "observer_mutated_runtime":False,"railway_credentials_used":False,"source_provider_observed_live":False,
        "terminal_fence_receipt_validated":True,"strict_rebuilt":True,
        "transfer_sha256":grant["transfer_sha256"],"restore_sha256":grant["restore_sha256"],
        "app_container_id":e["release"]["container_id"],"postgres_container_id":e["release"]["postgres"]["container_id"],
        "docker_restart":{"app":"no","postgres":"no"},
        "postgres_identity":{"cluster_system_identifier":e["release"]["postgres"]["database_identity"]["system_identifier"]},
        "units":{u["unit"]:{"sha256":u["unit_sha256"],"ActiveState":"active","SubState":"running","UnitFileState":"enabled"}
                 for u in (e["release"]["systemd"],e["release"]["postgres"]["systemd"])},
        "processes":{"roles":{key:{"pid":pid,"ppid":1 if key=="supervisor" else 7,"started_at":at(-178)}
                               for key,pid in (("supervisor",7),("api",8),("market",9),("source",10))}},
        "worker_heartbeats":[{"component_id":k,"heartbeat_at":at(-2),"expected_by":at(10)}
                             for k in ("official-market-collector","legacy-source-worker")]}
    headers={"x-seiche-provider":"hetzner","x-seiche-authority":"production","x-seiche-release-sha":SOURCE,
             "x-seiche-runtime":grant["runtime_id"],"cache-control":"no-store"}
    for kind,transport in (("origin","private_unix_socket"),("public","public_https")):
        e["monitor"][kind+"_health"]={"http":200,"headers":deepcopy(headers),"body":deepcopy(obs["body"]),
            "body_sha256":r.digest(obs["body"]),"bytes":len(r.canonical(obs["body"])),"transport":transport,
            "legacy_railway_headers_absent":True}
    return e,runtime


@pytest.fixture
def admitted(monkeypatch):
    e,runtime=fixture()
    # Only the cryptographic primitive is replaced for synthetic receipts; the
    # real runtime grant, transfer and acceptance validators still execute.
    monkeypatch.setattr(r,"verify_grant_signature",lambda *a:None)
    monkeypatch.setattr(runtime,"_verify_signature",lambda *a:None)
    assert r.validate_evidence(e,source=SOURCE,now=NOW)["source"]==SOURCE
    return e


def test_native_drive_semantics_are_explicit(admitted):
    result=r.validate_evidence(admitted,source=SOURCE,now=NOW)
    assert result["backup_guarantees"]=={"provider":"google-drive-rclone-crypt","independent_decrypted_readback":True,
        "object_lock":False,"compliance_retention":False,"cross_store_point_in_time":False,
        "online_backup_disposable_restore_verified":False,"final_migration_restore_verified":True}


def refreshed_restart(evidence):
    restart=evidence["restart"]
    restart.update(schema="seiche.hetzner-restart-release.v2",active_handoff_preserved=False,
                   release_authority_preserved=True,before_active_handoff=deepcopy(restart["active_handoff"]))
    restart["active_handoff"]={"producer_sha":SOURCE,"activation_token":"e"*64}
    proof={"schema":"seiche.same-release-refresh-proof.v1","status":"PASS","producer_sha":SOURCE,
           "transaction_read_only":True,"repeatable_read":True,"operator_promotion_performed":False}
    for side,identity,generated in (("before",restart["before_active_handoff"],at(-179)),
                                     ("after",restart["active_handoff"],at(-175))):
        proof[side]={"identity":deepcopy(identity),"generated_at":generated,"envelope_sha256":"c"*64,
                     "payload_sha256":"d"*64,"full_forward_chain_verified":True,
                     "canonical_rows_match_validated_staging":True,
                     "products":{name:{"forward_record_id":"a"*64,"snapshot_id":"b"*64,"snapshot_row_sha256":"c"*64}
                                 for name in ("gauge","overview")}}
    restart["same_release_refresh"]=proof
    return restart


def test_native_same_release_refresh_keeps_runtime_and_authority_checks(admitted):
    refreshed_restart(admitted)
    assert r.validate_evidence(admitted,source=SOURCE,now=NOW)["source"]==SOURCE
    admitted["restart"]["container_id"]="f"*64
    with pytest.raises(ValueError,match="restart_binding"):
        r.validate_evidence(admitted,source=SOURCE,now=NOW)


@pytest.mark.parametrize("section,field,value",[
    (None,"active_handoff_preserved",True),(None,"release_authority_preserved",False),
    ("proof","producer_sha","f"*40),("proof","transaction_read_only",False),
    ("proof","repeatable_read",False),("proof","operator_promotion_performed",True),
    ("before","full_forward_chain_verified",False),("after","canonical_rows_match_validated_staging",False),
    ("after","generated_at",at(-180)),("after","generated_at",at(-160)),
    ("before","identity",{"producer_sha":SOURCE,"activation_token":"0"*64}),
    ("after","products",{}),
])
def test_native_refresh_missing_binding_never_passes(admitted,section,field,value):
    restart=refreshed_restart(admitted)
    target=restart if section is None else restart["same_release_refresh"] if section=="proof" else restart["same_release_refresh"][section]
    target[field]=value
    with pytest.raises(ValueError):r.validate_evidence(admitted,source=SOURCE,now=NOW)


def test_refresh_cannot_be_relabelled_as_legacy_restart(admitted):
    restart=refreshed_restart(admitted)
    restart["schema"]="seiche.hetzner-restart-release.v1"
    with pytest.raises(ValueError,match="restart_handoff_binding"):
        r.validate_evidence(admitted,source=SOURCE,now=NOW)


@pytest.mark.parametrize("document,field,value",[
    ("transfer","fixture_only",True),("transfer","authority_transferred",True),
    ("final_restore","status","FAIL"),("final_restore","image_digest","sha256:"+"0"*64),
    ("restart","restart_rehearsal_proven",False),("monitor","terminal_fence_receipt_validated",False),
    ("monitor","observed_at",at(-16)),("backup","cross_store_point_in_time",True),
    ("backup","captured_at",at(-1600)),("backup_readback","status","waiting_for_shared_upload_budget"),
])
def test_unqualified_native_claim_is_held(admitted,document,field,value):
    admitted[document][field]=value
    with pytest.raises((ValueError,RuntimeError)):r.validate_evidence(admitted,source=SOURCE,now=NOW)


def test_actual_transfer_validator_rejects_consistent_bad_parent(admitted):
    admitted["final_restore"]["postgres"]["parent"]["commit"]="f"*40
    grant=admitted["grant"]["payload"]
    grant["restore_sha256"]=r.digest(admitted["final_restore"])
    admitted["transfer"]["destination"]["restore_receipt_sha256"]=grant["restore_sha256"]
    grant["transfer_sha256"]=r.digest(admitted["transfer"])
    # Direct actual validator demonstrates the rejection is semantic, not just
    # the first mismatching outer hash in the publication chain.
    runtime=r.native_runtime_validator(SOURCE)
    with pytest.raises((ValueError,RuntimeError)):runtime.validate_transfer(admitted["transfer"],admitted["final_restore"],grant)


def test_cloud_byte_mismatch_is_rejected(admitted):
    admitted["backup_readback"]["artifacts"][0]["sha256"]="0"*64
    with pytest.raises(ValueError,match="artifact_differs"):
        r.validate_native_backup(admitted["backup"],admitted["backup_readback"],admitted["grant"]["payload"],r.digest(admitted["grant"]),
                                 postgres_container=admitted["release"]["postgres"]["container_id"],now=NOW)


def test_native_mode_cannot_masquerade_as_protected_chain(admitted):
    del admitted["schema"]
    with pytest.raises(ValueError,match="not_closed"):r.validate_evidence(admitted,source=SOURCE,now=NOW)


def test_publication_socket_access_cannot_broaden(admitted):
    admitted["release"]["socket"]["directory_acl"]="user::rwx,group::rwx,other::rwx"
    with pytest.raises(ValueError,match="socket_acl"):r.validate_evidence(admitted,source=SOURCE,now=NOW)


def test_old_source_cannot_pass(admitted):
    with pytest.raises(ValueError,match="source_bridge_required"):r.validate_evidence(admitted,source="f"*40,now=NOW)


def test_actual_monitor_wrong_image_container_is_rejected(admitted):
    admitted["monitor"]["app_container_id"]="0"*64
    with pytest.raises(ValueError,match="current_containers"):r.validate_evidence(admitted,source=SOURCE,now=NOW)


def test_actual_monitor_disabled_unit_is_rejected(admitted):
    admitted["monitor"]["units"]["seiche-native.service"]["UnitFileState"]="disabled"
    with pytest.raises(ValueError,match="unit_state"):r.validate_evidence(admitted,source=SOURCE,now=NOW)


def test_actual_monitor_stale_worker_is_rejected(admitted):
    admitted["monitor"]["worker_heartbeats"][0]["heartbeat_at"]=at(-200)
    with pytest.raises(ValueError,match="worker_clock"):r.validate_evidence(admitted,source=SOURCE,now=NOW)


def test_actual_monitor_provider_ambiguity_is_rejected(admitted):
    admitted["monitor"]["public_health"]["headers"]["x-seiche-railway-deployment"]="old"
    with pytest.raises(ValueError,match="ambiguous_provider"):r.validate_evidence(admitted,source=SOURCE,now=NOW)


def test_missing_final_chain_documents_are_rejected(admitted):
    for key in ("transfer","final_restore","release","boot","restart","backup","backup_readback","monitor"):
        candidate=deepcopy(admitted);del candidate[key]
        with pytest.raises(ValueError,match="evidence_shape"):r.validate_evidence(candidate,source=SOURCE,now=NOW)


def test_explicit_native_package_passes_actual_catalog_gate(admitted,monkeypatch):
    c=legacy.catalog
    monkeypatch.setattr(c,"_hetzner_release_gate",lambda:r)
    accepted=r.validate_evidence(admitted,source=SOURCE,now=NOW)
    context={"purpose":"catalog_metadata","intent_id":"2"*64,"controller_source_sha":SOURCE,
        "runtime_source_sha":SOURCE,"provider":"hetzner","runtime_id":admitted["grant"]["payload"]["runtime_id"],
        "target":accepted["target"],"expected_version":"0.16.6","expected_authority":"production",
        "publication_mode":"native_migration","source_equivalence_sha256":accepted["source_equivalence_sha256"],
        "operation":{"publication_request_id":"3"*64,"catalog_sha256":"4"*64,
                     "health_pair_sha256":legacy.health.digest(legacy.health.canonical(accepted["health"]))}}
    package={"schema":r.NATIVE_PACKAGE_SCHEMA,"intent_id":"2"*64,"publication_request_id":"3"*64,
        "body_b64":None,"signature_b64":None,"health_pair":accepted["health"],"runtime_evidence":admitted}
    result=c.verify_catalog_source_health(package,expected_context=context,current_public=accepted["health"]["public"],now=NOW)
    assert result["evidence_status"]=="healthy" and result["source_health_admitted"] is False
    package["schema"]=r.PACKAGE_SCHEMA
    with pytest.raises(c.PublicationGateError,match="explicit package mode"):
        c.verify_catalog_source_health(package,expected_context=context,current_public=accepted["health"]["public"],now=NOW)


def test_unsigned_history_exception_is_rejected_before_git(admitted,monkeypatch):
    c=legacy.catalog
    monkeypatch.setattr(c,"_run_git_bytes",lambda *a:pytest.fail("unsigned bridge reached history/source lookup"))
    package={"schema":r.NATIVE_PACKAGE_SCHEMA,"body_b64":None,"signature_b64":None,"runtime_evidence":admitted}
    with pytest.raises(c.PublicationGateError,match="signed accepted exact-tree bridge"):
        c._native_history_admission(legacy.ROOT,SOURCE,package)


def test_foreign_history_package_never_reaches_native_authority(admitted,monkeypatch):
    c=legacy.catalog
    monkeypatch.setattr(c,"_hetzner_release_gate",lambda **k:pytest.fail("foreign package reached authority"))
    package={"schema":"seiche.catalog-source-health.v1","body_b64":None,"signature_b64":None,"runtime_evidence":admitted}
    with pytest.raises(c.PublicationGateError,match="healthy explicit package"):
        c._native_history_admission(legacy.ROOT,SOURCE,package)


def test_private_package_preserves_actual_health_and_no_fault_exception(admitted):
    operator=legacy.module("native_prepare_test","ops/release/prepare_native_publication.py")
    operator.release=r
    package=operator.health_package(admitted,SOURCE,"2"*64,"3"*64,now=NOW)
    assert package["schema"]==r.NATIVE_PACKAGE_SCHEMA and package["body_b64"] is None
    assert package["signature_b64"] is None and package["runtime_evidence"]==admitted
    assert package["health_pair"]["public"]["body"]==admitted["monitor"]["public_health"]["body"]


def test_private_package_refuses_unverified_cloud_backup(admitted):
    operator=legacy.module("native_prepare_fail_test","ops/release/prepare_native_publication.py")
    operator.release=r
    admitted["backup_readback"]["status"]="waiting_for_shared_upload_budget"
    with pytest.raises(ValueError):operator.health_package(admitted,SOURCE,"2"*64,"3"*64,now=NOW)


def test_foreign_postgres_backup_cannot_pass_matching_outer_runtime(admitted):
    admitted['backup']['database_snapshot']['container_id']='0'*64
    with pytest.raises(ValueError,match='restore_claim'):
        r.validate_native_backup(admitted['backup'],admitted['backup_readback'],admitted['grant']['payload'],
            r.digest(admitted['grant']),postgres_container=admitted['release']['postgres']['container_id'],now=NOW)
