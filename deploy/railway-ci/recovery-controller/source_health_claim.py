"""Verify the owner's actual-run claim before an original manual export.

The operator atomically binds the admission intent locally before signing and
uploading this claim. The workflow only reads the signed immutable object; it
never relies on unsupported conditional writes in the protected storage store.
"""
import base64
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.request
import time

import source_health


ORIGIN = 'https://seiche-stateful-core-production.up.railway.app'
PUBLIC = 'https://api.seiche.info'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def live_pair(environment):
    if environment['RAILWAY_ORIGIN'] != ORIGIN:
        raise ValueError('source health origin differs from fixed production target')
    pair = []
    for base in (ORIGIN, PUBLIC):
        headers = {'User-Agent': 'Seiche-Recovery-Source-Health/1.0'}
        if base == ORIGIN:
            headers['X-Seiche-Edge-Token'] = environment['RAILWAY_EDGE_TOKEN']
        request = urllib.request.Request(base + '/api/health', headers=headers)
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            if response.status != 200:
                raise ValueError('source health requires HTTP 200')
            raw = response.read(512 * 1024 + 1)
            if len(raw) > 512 * 1024:
                raise ValueError('source health response exceeds bound')
            pair.append(source_health.observation(json.loads(raw),
                        {key.lower(): value for key, value in response.headers.items()}))
    return pair


def execution(environment):
    return {'kind': 'github_actions', 'repository': environment['GITHUB_REPOSITORY'],
            'run_id': environment['GITHUB_RUN_ID'], 'run_attempt': environment['GITHUB_RUN_ATTEMPT']}


CLAIM_SCHEMA = 'seiche.source-degradation-execution-envelope.v1'
CLAIM_NAMESPACE = 'seiche-source-degradation-execution-claim-v1'
WAIT_SECONDS = 600


class MissingClaim(ValueError):
    """Only the provider's explicit missing-object result permits waiting."""


def claim_key(assessment, actual_execution):
    # Validate all path components through the shared closed claim contract.
    source_health.helper().claim_document(assessment, execution=actual_execution)
    return ('source-health-claims/' + assessment['context']['intent_id'] +
            '/github-actions/' + actual_execution['run_id'] + '/' +
            actual_execution['run_attempt'] + '/claim.json')


def verify_signature(body, signature):
    if not isinstance(signature, bytes) or not 0 < len(signature) <= 8192:
        raise ValueError('execution claim signature is missing or oversized')
    with tempfile.TemporaryDirectory(prefix='source-health-claim-signature-') as directory:
        root = Path(directory)
        (root / 'signers').write_text('owner ' + source_health.helper().OWNER_PUBLIC + '\n')
        (root / 'signature').write_bytes(signature)
        result = subprocess.run(['ssh-keygen', '-Y', 'verify', '-f', str(root / 'signers'),
                                 '-I', 'owner', '-n', CLAIM_NAMESPACE, '-s', str(root / 'signature')],
                                input=body, capture_output=True, check=False, timeout=15,
                                env={'PATH': '/usr/bin:/bin', 'HOME': directory})
        if result.returncode != 0:
            raise ValueError('owner execution claim signature is invalid')


def validate_envelope(raw, assessment, actual_execution, now):
    module = source_health.helper()
    value = module.document(raw)
    if set(value) != {'schema', 'claim_b64', 'signature_b64'} or value['schema'] != CLAIM_SCHEMA:
        raise ValueError('execution claim envelope fields differ')
    try:
        body = base64.b64decode(value['claim_b64'], validate=True)
        signature = base64.b64decode(value['signature_b64'], validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError('execution claim encoding differs') from exc
    claim = module.document(body)
    verify_signature(body, signature)
    module.verify_claim(claim, assessment, execution=actual_execution)
    if not now < datetime.fromisoformat(assessment['expires_at'].replace('Z', '+00:00')):
        raise ValueError('source health admission expired before export')
    return claim


def consume(store, assessment, actual_execution, now, *, clock=None, monotonic=time.monotonic, sleep=time.sleep):
    """Read the exact immutable owner claim; no workflow storage mutation."""
    if not assessment['source_health_admitted']:
        return None
    clock = clock or (lambda: datetime.now(timezone.utc))
    expires = datetime.fromisoformat(assessment['expires_at'].replace('Z', '+00:00'))
    if not now < expires:
        raise ValueError('source health admission expired before claim')
    key = claim_key(assessment, actual_execution)
    deadline = monotonic() + WAIT_SECONDS
    while True:
        observed_at = clock()
        if observed_at >= expires or monotonic() > deadline:
            raise ValueError('owner execution claim wait expired; no export requested')
        try:
            raw, receipt = store.read(key)
        except MissingClaim:
            remaining = min(deadline - monotonic(), (expires - clock()).total_seconds())
            if remaining <= 0:
                raise ValueError('owner execution claim wait expired; no export requested') from None
            sleep(min(2, remaining))
            continue
        if monotonic() > deadline:
            raise ValueError('owner execution claim read exceeded wait deadline; no export requested')
        claim = validate_envelope(raw, assessment, actual_execution, clock())
        return {'schema': 'seiche.source-health-claim-receipt.v1', 'claim': claim,
                'claim_sha256': hashlib.sha256(source_health.helper().canonical(claim)).hexdigest(),
                'envelope_sha256': hashlib.sha256(raw).hexdigest(), 'storage': receipt}


class S3Claim:
    def __init__(self, environment, private):
        self.private = private
        self.environment = {name: environment[name] for name in (
            "PATH", "HOME", "RUNNER_TEMP", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
            "AWS_DEFAULT_REGION", "S3_ENDPOINT", "S3_BUCKET", "S3_PREFIX")}
        workspace = Path(environment['GITHUB_WORKSPACE'])
        spec = importlib.util.spec_from_file_location('recovery_claim_location', workspace / 'ops/railway/resume_recovery.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.validate_location(environment)
        self.validation = module
        key = base64.b64decode(environment['S3_SSE_C_KEY_B64'], validate=True)
        if len(key) != 32:
            raise ValueError('source health claim encryption key differs')
        self.key_md5 = base64.b64encode(hashlib.md5(key).digest()).decode()
        key_path = private / 'sse.key'
        key_path.write_bytes(key)
        key_path.chmod(0o600)
        self.prefix = environment['S3_PREFIX']
        self.base = ['aws', '--endpoint-url', environment['S3_ENDPOINT'], '--no-cli-pager',
                     '--cli-connect-timeout', '10', '--cli-read-timeout', '30', 's3api']
        self.sse = ['--sse-customer-algorithm', 'AES256', '--sse-customer-key', 'fileb://' + str(key_path)]
        self.environment.update(AWS_MAX_ATTEMPTS='1', AWS_RETRY_MODE='standard', AWS_PAGER='')
        self.environment.pop('S3_SSE_C_KEY_B64', None)

    def command(self, operation, key, extra=()):
        return [*self.base, operation, '--bucket', self.environment['S3_BUCKET'],
                '--key', self.prefix + '/' + key, *self.sse, *extra]

    def read(self, key):
        result = subprocess.run(self.command('head-object', key), env=self.environment,
                                capture_output=True, timeout=60, check=False)
        if result.returncode:
            error = result.stderr.decode('utf-8', errors='replace')
            if ('(404)' in error or '(NoSuchKey)' in error or '(NotFound)' in error) and 'HeadObject' in error:
                raise MissingClaim('owner execution claim has not arrived')
            raise ValueError('owner execution claim read failed')
        head = json.loads(result.stdout)
        version = head.get('VersionId')
        size = head.get('ContentLength')
        if not isinstance(version, str) or not version or version == 'null' or not isinstance(size, int) or not 0 < size <= 16 * 1024:
            raise ValueError('source health claim version or size is invalid')
        destination = self.private / 'readback.json'
        subprocess.run(self.command('get-object', key, ['--version-id', version, str(destination)]),
                       env=self.environment, capture_output=True, timeout=60, check=True)
        raw = destination.read_bytes()
        if len(raw) != size:
            raise ValueError('source health claim readback size differs')
        digest = hashlib.sha256(raw).hexdigest()
        self.validation.validate_head(head, version=version, sha256=digest, size=size,
                                      key_md5=self.key_md5, now=datetime.now(timezone.utc))
        return raw, {'endpoint': self.environment['S3_ENDPOINT'], 'bucket': self.environment['S3_BUCKET'],
                     'key': self.prefix + '/' + key, 'version_id': version, 'sha256': digest, 'size': size}


def main():
    value = os.environ.get('SOURCE_HEALTH_ADMISSION', '')
    if not value:
        return
    os.umask(0o077)
    environment = dict(os.environ)
    environment['RECOVERY_SOURCE_SHA'] = environment['RELEASE_SHA']
    if sys.argv[1:] == ['--recheck']:
        _, body, signature = source_health.envelope(value)
        context = source_health.manual_context(value, environment, environment['DEPLOYMENT_ID'], environment['RELEASE_SHA'])
        source_health.helper().verify_admission(body, signature, expected_context=context, now=datetime.now(timezone.utc))
        return
    if sys.argv[1:]:
        raise ValueError('unknown source health claim operation')
    origin, public = live_pair(environment)
    if origin['identity']['deployment_id'] != environment['DEPLOYMENT_ID']:
        raise ValueError('source health export deployment moved')
    assessment = source_health.manual_assessment(value, environment, origin, public, datetime.now(timezone.utc))
    destination = Path(environment['EVIDENCE_ROOT']) / 'proof/source-health-claim.json'
    if not assessment['source_health_admitted']:
        destination.write_bytes(source_health.helper().canonical({'assessment': assessment, 'claim_consumed': False}))
        return
    with tempfile.TemporaryDirectory(prefix='source-health-claim-', dir=environment['RUNNER_TEMP']) as directory:
        store = S3Claim(environment, Path(directory))
        receipt = consume(store, assessment, execution(environment), datetime.now(timezone.utc))
        origin, public = live_pair(environment)
        if origin['identity']['deployment_id'] != environment['DEPLOYMENT_ID']:
            raise ValueError('source health export deployment moved during claim wait')
        assessment = source_health.manual_assessment(value, environment, origin, public, datetime.now(timezone.utc))
        source_health.helper().verify_claim(receipt['claim'], assessment, execution=execution(environment))
        destination.write_bytes(source_health.helper().canonical({'assessment': assessment, 'receipt': receipt, 'claim_consumed': True}))


if __name__ == '__main__':
    main()
