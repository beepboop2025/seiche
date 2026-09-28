"""Require owner-signed installation and tail policy before recurring export.

The image owns this code. The evidence volume contains only signed data, delivered
onto the same waiting deployment after the operator verifies provider image and
protected tail settings. Installation is identity evidence, not authority to
publish or execute; the original explicit mode and confirmation remain required.
"""
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time

import attest
import verify

ROOT = Path('/controller')
ADMISSIONS = Path('/evidence/native/admissions')
WAIT_SECONDS = 1800
TAIL_DOMAIN = 'seiche-railway-recovery-tail-policy-v1'
FILES = {'installation.json': 512 * 1024, 'installation.json.sig': 8192,
         'policy.json': 512 * 1024, 'policy.json.sig': 8192}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def document(raw, *, canonical=True):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate admission JSON field')
            result[key] = value
        return result

    def finite(token):
        value = float(token)
        require(math.isfinite(value), 'nonfinite admission JSON number')
        return value

    def reject(token):
        raise ValueError('nonfinite admission JSON number')

    value = json.loads(raw, object_pairs_hook=pairs, parse_float=finite, parse_constant=reject)
    require(isinstance(value, dict) and (not canonical or raw == verify.canonical(value)),
            'admission JSON is not a canonical object')
    return value


def read_regular(root, name, maximum, *, allow_empty=False):
    """Open each component without following symlinks; bound the exact bytes read."""
    path = root / name
    require(path.is_absolute() and '..' not in path.parts, 'admission path escapes its root')
    descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:-1]:
            following = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
        file_descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        with os.fdopen(file_descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and 0 <= info.st_size <= maximum
                    and (allow_empty or info.st_size > 0),
                    'admission member is not a bounded private regular file')
            raw = stream.read(maximum + 1)
        require(len(raw) <= maximum and (allow_empty or raw), 'admission member exceeds its bound')
        return raw
    finally:
        os.close(descriptor)


def tail_signature(body, signature):
    require(0 < len(signature) <= 8192, 'tail policy signature is missing or oversized')
    with tempfile.TemporaryDirectory(prefix='native-tail-signature-') as name:
        root = Path(name)
        (root / 'signers').write_text('owner ' + attest.OWNER_PUBLIC + '\n')
        (root / 'signature').write_bytes(signature)
        result = subprocess.run(['ssh-keygen', '-Y', 'verify', '-f', str(root / 'signers'), '-I', 'owner',
                                 '-n', TAIL_DOMAIN, '-s', str(root / 'signature')], input=body,
                                capture_output=True, timeout=30, check=False,
                                env={'PATH': '/usr/bin:/bin', 'HOME': name})
        require(result.returncode == 0, 'owner tail policy signature verification failed')


def installed_identity(environment, root):
    require(environment.get('RECOVERY_OPERATION') == 'export-recurring'
            and environment.get('RECOVERY_CONFIRMATION') == 'EXPORT_WITHOUT_AUTHORITY_CHANGE',
            'native recurring recovery is not explicitly armed')
    manifest_raw = read_regular(root, 'manifest.json', 512 * 1024)
    manifest = document(manifest_raw, canonical=False)
    require(manifest and all(isinstance(key, str) and not Path(key).is_absolute()
                            and '..' not in Path(key).parts and isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value)
                            for key, value in manifest.items()), 'invalid native assembly manifest')
    policy_raw = None
    for name, expected in manifest.items():
        member = read_regular(root, name, 8 * 1024 * 1024, allow_empty=True)
        require(verify.digest(member) == expected, 'native gate assembly differs from its signed preparation')
        if name == 'policy.json':
            policy_raw = member
    require('policy.json' in manifest and 'native_installation_gate.py' in manifest,
            'native gate is not bound by the image manifest')
    require(policy_raw is not None and len(policy_raw) <= 512 * 1024, 'native policy exceeds its bound')
    assembled = document(policy_raw, canonical=False)
    require(assembled.get('operation') == 'export-recurring', 'assembly cannot admit recurring recovery')
    identity = {name: assembled[name] for name in ('controller_source', 'controller_project_id',
                'controller_environment_id', 'controller_service_id', 'execution_public_key')}
    for field in ('project', 'environment', 'service'):
        require(environment.get('RAILWAY_' + field.upper() + '_ID') == identity['controller_' + field + '_id'],
                'native gate installation target differs')
    deployment = environment.get('RAILWAY_DEPLOYMENT_ID', '')
    require(attest.UUID.fullmatch(deployment) is not None, 'native gate deployment identity is missing')
    image = environment.get('RECOVERY_CONTROLLER_IMAGE_DIGEST', '')
    require(re.fullmatch(r'sha256:[0-9a-f]{64}', image) is not None, 'native gate image identity is missing')
    identity.update(controller_deployment_id=deployment, controller_image_digest=image,
                    controller_manifest_sha256=verify.digest(manifest_raw))
    return identity, assembled, manifest


def validate(raw, identity, assembled, manifest, now):
    """Authenticate captured data bytes; never re-open a verified member."""
    installation = document(raw['installation.json'])
    policy = document(raw['policy.json'])
    tail_signature(raw['policy.json'], raw['policy.json.sig'])
    extra = {'tail_source', 'installation_sha256', 'installation_signature_sha256', 'tail_inputs',
             'application_project_id', 'application_environment_id', 'application_service_id',
             'storage_endpoint', 'storage_bucket', 'storage_prefix'}
    require(set(policy) == set(attest.INSTALLATION_BINDINGS) | extra, 'tail policy fields differ')
    require(all(policy.get(name) == value for name, value in identity.items()), 'tail policy installation identity differs')
    require(policy['tail_source'] == identity['controller_source'], 'tail policy source differs')
    for field in ('project', 'environment', 'service'):
        name = 'RAILWAY_STATEFUL_SERVICE_ID' if field == 'service' else 'RAILWAY_' + field.upper() + '_ID'
        require(policy['application_' + field + '_id'] == assembled['production_target'][name],
                'tail policy application target differs')
    for field, name in (('endpoint', 'S3_ENDPOINT'), ('bucket', 'S3_BUCKET'), ('prefix', 'S3_PREFIX')):
        require(policy['storage_' + field] == assembled['storage_target'][name], 'tail policy storage target differs')
    inputs = policy['tail_inputs']
    require(isinstance(inputs, dict) and inputs, 'tail source inputs are missing')
    for name, expected in assembled['trusted_source_sha256'].items():
        require(inputs.get(name) == expected, 'tail executable input differs')
    for name in ('attest.py', 'verify.py', 'requirements.lock'):
        require(inputs.get('deploy/railway-ci/recovery-controller/' + name) == manifest.get(name)
                and name in manifest, 'tail verifier differs from native image')
    payload = {**identity, 'observed_at': now.isoformat()}
    attest.installation_identity(raw['installation.json'], raw['installation.json.sig'], payload, policy, now)
    # Keep the exact signed installation closed contract; never treat its false
    # can_execute/can_publish flags as a grant to change production authority.
    require(installation['controller_deployment_id'] == identity['controller_deployment_id'],
            'installation is for a different deployment')
    return {name: hashlib.sha256(value).hexdigest() for name, value in raw.items()}


def admit():
    require(os.geteuid() == 0, 'native gate must run under the reviewed root image')
    identity, assembled, manifest = installed_identity(os.environ, ROOT)
    folder = ADMISSIONS / identity['controller_deployment_id']
    deadline = time.monotonic() + WAIT_SECONDS
    verify.event('native_recovery_installation_wait', deployment=identity['controller_deployment_id'],
                 source=identity['controller_source'], manifest_sha256=identity['controller_manifest_sha256'],
                 timeout_seconds=WAIT_SECONDS)
    while True:
        try:
            raw = {}
            for name, maximum in FILES.items():
                raw[name] = read_regular(folder, name, maximum)
                if name.endswith('.json'):
                    document(raw[name])
                elif name == 'installation.json.sig':
                    attest.owner_signature(raw['installation.json'], raw[name])
                else:
                    tail_signature(raw['policy.json'], raw[name])
        except FileNotFoundError:
            remaining = deadline - time.monotonic()
            require(remaining > 0, 'signed native installation admission timed out; no export requested')
            time.sleep(min(2, remaining))
            continue
        hashes = validate(raw, identity, assembled, manifest, datetime.now(timezone.utc))
        require(time.monotonic() <= deadline, 'signed native installation admission exceeded its deadline')
        verify.event('native_recovery_installation_admitted', deployment=identity['controller_deployment_id'],
                     source=identity['controller_source'], hashes=hashes)
        return hashes
