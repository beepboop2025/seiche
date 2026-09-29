"""Actual SSH-signature admission and zero-export regression cases."""
from datetime import datetime, timedelta, timezone
import os
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest import mock

import attest
import native_installation_gate as gate
import recurring
import verify


class InstallationGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = tempfile.TemporaryDirectory(prefix='gate-test-keys-')
        cls.key = Path(cls.keys.name).resolve() / 'owner'
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(cls.key)], check=True)
        cls.public = Path(str(cls.key) + '.pub').read_text().strip()

    @classmethod
    def tearDownClass(cls):
        cls.keys.cleanup()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='native-gate-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.controller = self.root / 'controller'
        self.controller.mkdir()
        self.uuid = '11111111-1111-4111-8111-111111111111'
        self.now = datetime.now(timezone.utc)
        self.assembled = {'operation': 'export-recurring', 'controller_source': 'a' * 40,
            **{'controller_' + k + '_id': self.uuid for k in ('project', 'environment', 'service')},
            'execution_public_key': 'b' * 64, 'trusted_source_sha256': {'backend/example.py': 'f' * 64},
            'production_target': {'RAILWAY_PROJECT_ID': self.uuid, 'RAILWAY_ENVIRONMENT_ID': self.uuid,
                                  'RAILWAY_STATEFUL_SERVICE_ID': self.uuid},
            'storage_target': {'S3_ENDPOINT': 'https://fixture.invalid', 'S3_BUCKET': 'fixture', 'S3_PREFIX': 'private/recovery'}}
        members = {'policy.json': (json.dumps(self.assembled, sort_keys=True) + '\n').encode(), 'native_installation_gate.py': b'reviewed gate\n',
                   'attest.py': b'reviewed attest\n', 'verify.py': b'reviewed verify\n', 'requirements.lock': b'pinned\n',
                   'empty.py': b''}
        self.manifest = {name: verify.digest(body) for name, body in members.items()}
        for name, body in members.items():
            (self.controller / name).write_bytes(body)
        # Match prepare.py exactly: assembly JSON has default spaces, unlike signed data.
        (self.controller / 'manifest.json').write_bytes((json.dumps(self.manifest, sort_keys=True) + '\n').encode())
        self.environment = {'RECOVERY_OPERATION': 'export-recurring', 'RECOVERY_CONFIRMATION': 'EXPORT_WITHOUT_AUTHORITY_CHANGE',
                            'RECOVERY_CONTROLLER_IMAGE_DIGEST': 'sha256:' + 'c' * 64,
                            **{'RAILWAY_' + k + '_ID': self.uuid for k in ('PROJECT', 'ENVIRONMENT', 'SERVICE', 'DEPLOYMENT')}}
        self.patch(mock.patch.object(attest, 'OWNER_PUBLIC', self.public))
        self.identity, _, _ = gate.installed_identity(self.environment, self.controller)
        self.installation = {**self.identity, 'schema': 'seiche.railway-recovery-installation.v1',
            'purpose': 'native_recovery_evidence_only', 'observed_at': (self.now - timedelta(seconds=10)).isoformat(),
            'api_proof_sha256': 'd' * 64, 'authority_changed': False, 'can_publish': False, 'can_execute': False}
        self.policy = {**self.identity, 'tail_source': self.identity['controller_source'],
            **{'application_' + k + '_id': self.uuid for k in ('project', 'environment', 'service')},
            'storage_endpoint': 'https://fixture.invalid', 'storage_bucket': 'fixture', 'storage_prefix': 'private/recovery',
            'tail_inputs': {**self.assembled['trusted_source_sha256'], **{
                'deploy/railway-ci/recovery-controller/' + name: self.manifest[name]
                for name in ('attest.py', 'verify.py', 'requirements.lock')}}}
        self.raw = {}
        self.sign_all()

    def patch(self, patch):
        value = patch.start()
        self.addCleanup(patch.stop)
        return value

    def sign(self, body, domain):
        path = self.root / 'signing-input'
        path.write_bytes(body)
        Path(str(path) + '.sig').unlink(missing_ok=True)
        subprocess.run(['ssh-keygen', '-Y', 'sign', '-f', str(self.key), '-n', domain, str(path)],
                       check=True, capture_output=True)
        return Path(str(path) + '.sig').read_bytes()

    def sign_all(self):
        self.raw['installation.json'] = verify.canonical(self.installation)
        self.raw['installation.json.sig'] = self.sign(self.raw['installation.json'], attest.INSTALLATION_DOMAIN)
        self.policy.update(installation_sha256=verify.digest(self.raw['installation.json']),
                           installation_signature_sha256=verify.digest(self.raw['installation.json.sig']))
        self.raw['policy.json'] = verify.canonical(self.policy)
        self.raw['policy.json.sig'] = self.sign(self.raw['policy.json'], gate.TAIL_DOMAIN)

    def validate(self):
        return gate.validate(self.raw, self.identity, self.assembled, self.manifest, self.now)

    def publish(self):
        folder = self.root / 'admissions' / self.uuid
        folder.mkdir(parents=True)
        for name, raw in self.raw.items():
            (folder / name).write_bytes(raw)
        return folder

    def runtime(self):
        self.patch(mock.patch.dict(os.environ, self.environment, clear=True))
        self.patch(mock.patch.object(gate, 'ROOT', self.controller))
        self.patch(mock.patch.object(gate, 'ADMISSIONS', self.root / 'admissions'))
        self.patch(mock.patch.object(gate.os, 'geteuid', return_value=0))
        self.events = self.patch(mock.patch.object(verify, 'event'))

    def test_real_owner_signatures_admit_exact_current_identity(self):
        self.assertEqual(self.validate(), {name: verify.digest(raw) for name, raw in self.raw.items()})
        self.publish()
        self.runtime()
        self.assertEqual(gate.admit(), self.validate())
        self.assertEqual(self.events.call_args.args[0], 'native_recovery_installation_admitted')

    def test_missing_gate_blocks_direct_recurring_before_lock_or_export(self):
        self.runtime()
        self.patch(mock.patch.object(gate, 'WAIT_SECONDS', 0))
        with mock.patch.object(recurring, 'Path') as lock, mock.patch.object(recurring, 'run_locked') as exporter:
            with self.assertRaisesRegex(ValueError, 'timed out'):
                recurring.main()
        lock.assert_not_called()
        exporter.assert_not_called()
        self.assertFalse((self.root / 'admissions').exists())

    def test_malformed_gate_blocks_direct_recurring_without_wait(self):
        folder = self.publish()
        (folder / 'policy.json').write_bytes(b'{"bad":NaN}')
        self.runtime()
        owner_signature = attest.owner_signature

        def signature_with_process_wait(*args, **kwargs):
            # subprocess may briefly wait for ssh-keygen; this is not gate polling.
            time.sleep(0.001)
            return owner_signature(*args, **kwargs)

        with mock.patch.object(gate, 'time', wraps=gate.time) as clock, \
                mock.patch.object(attest, 'owner_signature', side_effect=signature_with_process_wait), \
                mock.patch.object(recurring, 'Path') as lock, \
                mock.patch.object(recurring, 'run_locked') as exporter:
            clock.sleep.return_value = None
            with self.assertRaisesRegex(ValueError, 'nonfinite'):
                recurring.main()
        clock.sleep.assert_not_called()
        lock.assert_not_called()
        exporter.assert_not_called()

    def test_present_malformed_member_fails_before_waiting_for_missing_siblings(self):
        folder = self.publish()
        (folder / 'installation.json').write_bytes(b'{"bad":NaN}')
        (folder / 'policy.json').unlink()
        self.runtime()
        with mock.patch.object(gate, 'time', wraps=gate.time) as clock:
            clock.sleep.return_value = None
            with self.assertRaisesRegex(ValueError, 'nonfinite'):
                gate.admit()
        clock.sleep.assert_not_called()

    def test_missing_gate_wait_is_bounded(self):
        self.runtime()
        self.patch(mock.patch.object(gate, 'WAIT_SECONDS', 3))
        with mock.patch.object(gate, 'time') as clock:
            clock.monotonic.side_effect = [0, 1, 3]
            with self.assertRaisesRegex(ValueError, 'timed out'):
                gate.admit()
        clock.sleep.assert_called_once_with(2)

    def test_bad_signature_wrong_namespace_and_wrong_owner_rejected(self):
        for file in ('installation.json', 'policy.json'):
            original = self.raw[file + '.sig']
            for signature in (b'tampered', self.sign(self.raw[file], 'wrong-namespace')):
                self.raw[file + '.sig'] = signature
                with self.subTest(file=file, signature=signature[:20]), self.assertRaises(ValueError):
                    self.validate()
            self.raw[file + '.sig'] = original
        with mock.patch.object(attest, 'OWNER_PUBLIC', 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBuJV6o8YL2XXR9q4vcwpHuc2z1GEBawSmrJWGrgwzFV'):
            with self.assertRaisesRegex(ValueError, 'signature'):
                self.validate()

    def test_all_signed_identity_substitutions_are_rejected(self):
        for name in attest.INSTALLATION_BINDINGS:
            old = self.installation[name]
            self.installation[name] = 'wrong-identity'
            self.sign_all()
            with self.subTest(field=name), self.assertRaises(ValueError):
                self.validate()
            self.installation[name] = old
        self.sign_all()

    def test_tail_targets_source_inputs_and_installation_pins_are_enforced(self):
        replacements = {'tail_source': 'e' * 40, 'storage_prefix': 'other/scope', 'storage_bucket': 'other',
                        'storage_endpoint': 'https://other.invalid', 'application_service_id': 'other',
                        'controller_image_digest': 'sha256:' + 'e' * 64, 'tail_inputs': {},
                        'installation_sha256': '0' * 64, 'installation_signature_sha256': '0' * 64}
        for name, value in replacements.items():
            old = self.policy[name]
            self.policy[name] = value
            self.raw['policy.json'] = verify.canonical(self.policy)
            self.raw['policy.json.sig'] = self.sign(self.raw['policy.json'], gate.TAIL_DOMAIN)
            with self.subTest(field=name), self.assertRaises(ValueError):
                self.validate()
            self.policy[name] = old

    def test_future_installation_and_authority_change_are_rejected(self):
        for name, value in (('observed_at', (self.now + timedelta(minutes=10)).isoformat()),
                            ('can_execute', True), ('can_publish', True), ('authority_changed', True)):
            old = self.installation[name]
            self.installation[name] = value
            self.sign_all()
            with self.subTest(field=name), self.assertRaises(ValueError):
                self.validate()
            self.installation[name] = old

    def test_noncanonical_duplicate_and_nonfinite_json_rejected(self):
        for raw in (b'{"a":1,"a":2}\n', b'{"a":NaN}\n', b'{"a":1e999}\n', b'{ "a":1 }\n'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                gate.document(raw)

    def test_file_size_symlink_directory_and_hardlink_are_rejected(self):
        folder = self.publish()
        target = folder / 'policy.json'
        with self.assertRaises(ValueError):
            gate.read_regular(folder, target.name, 1)
        target.unlink()
        target.symlink_to(folder / 'installation.json')
        with self.assertRaises(OSError):
            gate.read_regular(folder, target.name, 512 * 1024)
        target.unlink()
        os.link(folder / 'installation.json', target)
        with self.assertRaises(ValueError):
            gate.read_regular(folder, target.name, 512 * 1024)
        target.unlink()
        alias = self.root / 'alias'
        alias.symlink_to(folder, target_is_directory=True)
        with self.assertRaises(OSError):
            gate.read_regular(alias, 'installation.json', 512 * 1024)
        with self.assertRaises(ValueError):
            gate.read_regular(folder, '../installation.json', 512 * 1024)

    def test_image_policy_and_gate_bytes_are_pinned(self):
        (self.controller / 'native_installation_gate.py').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'assembly differs'):
            gate.installed_identity(self.environment, self.controller)

    def test_wrong_runtime_service_and_image_are_not_admitted(self):
        self.environment['RAILWAY_SERVICE_ID'] = 'different-service'
        with self.assertRaisesRegex(ValueError, 'target differs'):
            gate.installed_identity(self.environment, self.controller)
        self.environment['RAILWAY_SERVICE_ID'] = self.uuid
        self.environment['RECOVERY_CONTROLLER_IMAGE_DIGEST'] = 'sha256:' + 'e' * 64
        identity, _, _ = gate.installed_identity(self.environment, self.controller)
        with self.assertRaisesRegex(ValueError, 'identity differs'):
            gate.validate(self.raw, identity, self.assembled, self.manifest, self.now)


if __name__ == '__main__':
    unittest.main()
