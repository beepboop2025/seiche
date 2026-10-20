"""Signed source-health qualification shared by recovery entry points.

This is evidence admission only. Existing production commands, confirmations,
provider identity checks, storage scopes and daily locks remain authoritative.
"""
import base64
import importlib.util
import json
from pathlib import Path


NATIVE_FIELDS = ('source_health_admission', 'application_source_sha',
                 'application_deployment_id', 'application_expected_version')

def helper():
    here = Path(__file__).resolve().parent
    path = here / 'trusted/ops/release/source_degradation.py'
    if not path.is_file():
        path = here.parents[2] / 'ops/release/source_degradation.py'
    spec = importlib.util.spec_from_file_location('recovery_source_degradation', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def envelope(value):
    """Decode only bounded data; parsing does not authenticate the admission."""
    if isinstance(value, str):
        if len(value.encode()) > 128 * 1024:
            raise ValueError('source health envelope exceeds bound')
        value = json.loads(value)
    if not isinstance(value, dict) or set(value) != {'intent_id', 'body_b64', 'signature_b64'}:
        raise ValueError('source health envelope fields differ')
    if not isinstance(value['intent_id'], str) or not value['intent_id']:
        raise ValueError('source health intent is missing')
    try:
        body = base64.b64decode(value['body_b64'], validate=True)
        signature = base64.b64decode(value['signature_b64'], validate=True)
    except (TypeError, ValueError) as exc:
        raise ValueError('source health envelope encoding differs') from exc
    if not 0 < len(body) <= 64 * 1024 or not 0 < len(signature) <= 8192:
        raise ValueError('source health envelope members exceed bounds')
    return value['intent_id'], body, signature


def native_context(policy, now):
    value = policy['source_health_admission']
    intent, _, _ = envelope(value)
    return {
        'purpose': 'native_recovery', 'intent_id': intent,
        'controller_source_sha': policy['controller_source'],
        'runtime_source_sha': policy['application_source_sha'],
        'target': {field + '_id': policy['application_' + field + '_id']
                   for field in ('project', 'environment', 'service')},
        'deployment_id': policy['application_deployment_id'],
        'expected_version': policy['application_expected_version'],
        'expected_authority': 'production',
        'operation': {'controller_deployment_id': policy['controller_deployment_id'],
                      'controller_manifest_sha256': policy['controller_manifest_sha256'],
                      'execution_day': now.date().isoformat()},
    }


def native_admission(policy, now):
    if 'source_health_admission' not in policy:
        return None
    _, body, signature = envelope(policy['source_health_admission'])
    return helper().verify_admission(body, signature,
        expected_context=native_context(policy, now), now=now)


def observation(body, headers):
    return {'body': body, 'identity': {
        'source_sha': headers.get('x-seiche-release-sha'),
        'deployment_id': headers.get('x-seiche-railway-deployment'),
        'authority': headers.get('x-seiche-railway-authority'),
    }}


def native_assessment(policy, origin, public, now):
    _, body, signature = envelope(policy['source_health_admission'])
    return helper().assess(body, signature, expected_context=native_context(policy, now),
                           origin=origin, public=public, now=now)


def manual_context(value, environment, deployment, runtime_source):
    intent, body, _ = envelope(value)
    # The signed body supplies the owner's version constraint, never the live
    # response. Every operation/identity field is independently reconstructed.
    version = helper().document(body)['context']['expected_version']
    if environment.get('GITHUB_EVENT_NAME') != 'workflow_dispatch':
        raise ValueError('manual source health admission requires workflow dispatch')
    operation = environment.get('RECOVERY_OPERATION', '')
    if operation not in {'monitor', 'export-recovery'}:
        raise ValueError('source health admission is not valid for this operation')
    return {
        'purpose': 'recovery', 'intent_id': intent,
        'controller_source_sha': environment['GITHUB_SHA'],
        'runtime_source_sha': runtime_source,
        'target': {field + '_id': environment['RAILWAY_' + field.upper() + '_ID']
                   for field in ('project', 'environment', 'service')},
        'deployment_id': deployment, 'expected_version': version,
        'expected_authority': 'production',
        'operation': {'repository': environment['GITHUB_REPOSITORY'],
                      'workflow_path': '.github/workflows/railway-stateful-recovery.yml',
                      'workflow_ref': environment['GITHUB_REF'], 'operation': operation},
    }


def manual_assessment(value, environment, origin, public, now):
    _, body, signature = envelope(value)
    identity = origin['identity']
    context = manual_context(value, environment, identity['deployment_id'], environment['RECOVERY_SOURCE_SHA'])
    return helper().assess(body, signature, expected_context=context, origin=origin, public=public, now=now)


def workflow_assessment(environment, origin, public, now):
    """Monitor-only qualification; deliberately no storage credentials or claim."""
    value = environment.get('SOURCE_HEALTH_ADMISSION', '')
    if not value:
        return None
    if environment.get('NATIVE_SOURCE_HEALTH_POLICY'):
        policy = json.loads(environment['NATIVE_SOURCE_HEALTH_POLICY'])
        if policy['controller_deployment_id'] != environment['RAILWAY_DEPLOYMENT_ID']:
            raise ValueError('native monitor installation differs')
        result = native_assessment(policy, origin, public, now)
        execution = {'platform': 'railway',
                     'deployment_id': environment['RAILWAY_DEPLOYMENT_ID'],
                     'replica_id': environment['RAILWAY_REPLICA_ID'],
                     'execution_day': now.date().isoformat()}
    else:
        result = manual_assessment(value, environment, origin, public, now)
        execution = {'platform': 'github_actions', 'run_id': environment['GITHUB_RUN_ID'],
                     'run_attempt': environment['GITHUB_RUN_ATTEMPT']}
    return {**result, 'observed_execution': execution, 'claim_consumed': False}
