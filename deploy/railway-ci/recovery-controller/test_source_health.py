"""Signed health admission integration and atomic claim boundaries."""
import base64
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import tempfile
import sys
import unittest
from unittest import mock

import source_health as bridge
import source_health_claim as claims
import attest
import native_installation_gate as gate
import test_native_installation_gate
import test_attest


UUID = '11111111-1111-4111-8111-111111111111'


class SourceHealthTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='source-health-integration-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.module = bridge.helper()
        self.key = self.root / 'owner'
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(self.key)], check=True)
        self.module.OWNER_PUBLIC = self.key.with_suffix('.pub').read_text().strip()
        patch = mock.patch.object(bridge, 'helper', return_value=self.module)
        patch.start()
        self.addCleanup(patch.stop)
        self.now = datetime.now(timezone.utc)
        self.environment = {'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_SHA': 'a' * 40,
            'RECOVERY_SOURCE_SHA': 'b' * 40, 'RECOVERY_OPERATION': 'export-recovery',
            'GITHUB_REPOSITORY': 'beepboop2025/seiche', 'GITHUB_REF': 'refs/heads/main',
            'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1',
            **{'RAILWAY_' + k + '_ID': UUID for k in ('PROJECT', 'ENVIRONMENT', 'SERVICE')}}
        self.context = {'purpose': 'recovery', 'intent_id': 'c' * 64,
            'controller_source_sha': 'a' * 40, 'runtime_source_sha': 'b' * 40,
            'target': {k + '_id': UUID for k in ('project', 'environment', 'service')},
            'deployment_id': UUID, 'expected_version': '0.16.5', 'expected_authority': 'production',
            'operation': {'repository': 'beepboop2025/seiche',
                          'workflow_path': '.github/workflows/railway-stateful-recovery.yml',
                          'workflow_ref': 'refs/heads/main', 'operation': 'export-recovery'}}
        self.faults = [{'source': 'fred', 'status': 'FAILED', 'category': 'HTTP_ERROR',
                        'detail': self.module.HTTP_DETAIL}]
        self.observation = {'identity': {'source_sha': 'b' * 40, 'deployment_id': UUID, 'authority': 'production'},
            'body': {'version': '0.16.5', 'generated_at': self.now.isoformat(),
                     'faults': self.faults, 'provenance': [{'source': 'fred', 'as_of': '2026-10-19'}]}}
        self.value = self.sign(self.context)

    def sign(self, context):
        body = self.module.canonical({'schema': self.module.SCHEMA, 'context': context,
            'issued_at': (self.now - timedelta(minutes=1)).isoformat(),
            'expires_at': (self.now + timedelta(hours=2)).isoformat(),
            'faults': self.faults, 'faults_sha256': self.module.digest(self.module.canonical(self.faults)),
            'limits': self.module.LIMITS})
        path = self.root / 'admission'
        path.write_bytes(body)
        Path(str(path) + '.sig').unlink(missing_ok=True)
        subprocess.run(['ssh-keygen', '-Y', 'sign', '-f', str(self.key), '-n',
                        self.module.SIGNATURE_NAMESPACE, str(path)], check=True, capture_output=True)
        return {'intent_id': context['intent_id'], 'body_b64': base64.b64encode(body).decode(),
                'signature_b64': base64.b64encode(Path(str(path) + '.sig').read_bytes()).decode()}

    def assessment(self):
        return bridge.manual_assessment(self.value, self.environment, self.observation, self.observation, self.now)

    def test_signed_manual_intent_preserves_faults_and_clocks_without_consuming(self):
        before = copy.deepcopy(self.observation)
        result = bridge.workflow_assessment({**self.environment, 'SOURCE_HEALTH_ADMISSION': json.dumps(self.value)},
                                             self.observation, self.observation, self.now)
        self.assertEqual(result['evidence_status'], 'degraded')
        self.assertEqual(result['admitted_source_faults'], self.faults)
        self.assertFalse(result['claim_consumed'])
        self.assertEqual(self.observation, before)
        self.assertEqual(result['observed_execution']['run_id'], '123')

    def test_independent_manual_operation_fields_cannot_be_replayed(self):
        for key, value in [('GITHUB_SHA', 'd' * 40), ('RECOVERY_SOURCE_SHA', 'd' * 40),
                           ('RAILWAY_SERVICE_ID', '22222222-2222-4222-8222-222222222222'),
                           ('GITHUB_REF', 'refs/heads/other'), ('RECOVERY_OPERATION', 'monitor'),
                           ('GITHUB_EVENT_NAME', 'schedule')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                bridge.manual_assessment(self.value, {**self.environment, key: value},
                                         self.observation, self.observation, self.now)
        with self.assertRaises(ValueError):
            bridge.manual_assessment({**self.value, 'intent_id': 'd' * 64}, self.environment,
                                     self.observation, self.observation, self.now)

    def test_native_context_binds_actual_installation_manifest_and_day(self):
        policy = {'controller_source': 'a' * 40, 'application_source_sha': 'b' * 40,
                  'application_deployment_id': UUID, 'application_expected_version': '0.16.5',
                  'controller_deployment_id': UUID, 'controller_manifest_sha256': 'd' * 64,
                  **{'application_' + k + '_id': UUID for k in ('project', 'environment', 'service')},
                  'source_health_admission': self.value}
        context = bridge.native_context(policy, self.now)
        policy['source_health_admission'] = self.sign(context)
        self.assertEqual(bridge.native_assessment(policy, self.observation, self.observation, self.now)['evidence_status'], 'degraded')
        for name, value in [('controller_manifest_sha256', 'e' * 64),
                            ('application_expected_version', '0.16.6'),
                            ('controller_deployment_id', '22222222-2222-4222-8222-222222222222')]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                bridge.native_admission({**policy, name: value}, self.now)
        with self.assertRaises(ValueError):
            bridge.native_admission(policy, self.now + timedelta(days=1))

    def signed_claim(self, assessment, actual, *, namespace=claims.CLAIM_NAMESPACE):
        claim = self.module.claim_document(assessment, execution=actual)
        body = self.module.canonical(claim)
        path = self.root / 'execution-claim'
        path.write_bytes(body)
        Path(str(path) + '.sig').unlink(missing_ok=True)
        subprocess.run(['ssh-keygen', '-Y', 'sign', '-f', str(self.key), '-n',
                        namespace, str(path)], check=True, capture_output=True)
        return self.module.canonical({'schema': claims.CLAIM_SCHEMA,
            'claim_b64': base64.b64encode(body).decode(),
            'signature_b64': base64.b64encode(Path(str(path) + '.sig').read_bytes()).decode()})

    def test_owner_claim_reuses_only_exact_original_run_and_attempt(self):
        assessment = self.assessment()
        actual = claims.execution(self.environment)
        store = mock.Mock()
        store.read.return_value = (self.signed_claim(assessment, actual), {'version_id': 'fixture'})
        first = claims.consume(store, assessment, actual, self.now, clock=lambda: self.now)
        self.assertEqual(first, claims.consume(store, assessment, actual, self.now, clock=lambda: self.now))
        self.assertEqual(store.read.call_args.args[0], 'source-health-claims/' + 'c' * 64 + '/github-actions/123/1/claim.json')
        for changed in ({**actual, 'run_id': '124'}, {**actual, 'run_attempt': '2'}):
            with self.assertRaisesRegex(ValueError, 'different operation'):
                claims.consume(store, assessment, changed, self.now, clock=lambda: self.now)
        store.create_once.assert_not_called()

    def test_wrong_claim_namespace_or_admission_digest_never_proceeds(self):
        assessment, actual = self.assessment(), claims.execution(self.environment)
        raw = self.signed_claim(assessment, actual, namespace=self.module.SIGNATURE_NAMESPACE)
        with self.assertRaisesRegex(ValueError, 'signature'):
            claims.validate_envelope(raw, assessment, actual, self.now)
        raw = self.signed_claim({**assessment, 'admission_sha256': 'f' * 64}, actual)
        with self.assertRaisesRegex(ValueError, 'different operation'):
            claims.validate_envelope(raw, assessment, actual, self.now)

    def test_expired_admission_and_uncertain_missing_claim_never_proceed(self):
        store = mock.Mock()
        with self.assertRaisesRegex(ValueError, 'expired'):
            claims.consume(store, self.assessment(), claims.execution(self.environment), self.now + timedelta(hours=3))
        store.read.assert_not_called()
        store.read.side_effect = TimeoutError('uncertain')
        with self.assertRaises(TimeoutError):
            claims.consume(store, self.assessment(), claims.execution(self.environment), self.now, clock=lambda: self.now)
        store.read.assert_called_once()
        store.create_once.assert_not_called()

    def test_only_explicit_missing_claim_waits_and_wait_is_bounded(self):
        store = mock.Mock()
        store.read.side_effect = claims.MissingClaim('not arrived')
        sleep = mock.Mock()
        monotonic = mock.Mock(side_effect=[0, 0, 601])
        with self.assertRaisesRegex(ValueError, 'wait expired'):
            claims.consume(store, self.assessment(), claims.execution(self.environment), self.now,
                           clock=lambda: self.now, monotonic=monotonic, sleep=sleep)
        store.read.assert_called_once()
        sleep.assert_not_called()
        store.create_once.assert_not_called()

    def test_claim_that_arrives_after_admission_expiry_is_rejected(self):
        assessment, actual = self.assessment(), claims.execution(self.environment)
        store = mock.Mock()
        store.read.return_value = (self.signed_claim(assessment, actual), {'version_id': 'fixture'})
        with self.assertRaisesRegex(ValueError, 'expired'):
            claims.consume(store, assessment, actual, self.now,
                           clock=mock.Mock(side_effect=[self.now, self.now + timedelta(hours=3)]))

    def test_claim_read_finishing_after_wait_deadline_never_exports(self):
        assessment, actual = self.assessment(), claims.execution(self.environment)
        store = mock.Mock()
        store.read.return_value = (self.signed_claim(assessment, actual), {'version_id': 'fixture'})
        with self.assertRaisesRegex(ValueError, 'exceeded wait deadline'):
            claims.consume(store, assessment, actual, self.now, clock=lambda: self.now,
                           monotonic=mock.Mock(side_effect=[0, 0, 601]))

    def test_storage_missing_and_authorization_failure_are_distinct(self):
        store = object.__new__(claims.S3Claim)
        store.private = self.root
        store.environment = {'S3_BUCKET': 'fixture'}
        store.prefix, store.base, store.sse = 'recovery', ['aws', 's3api'], []
        for message, error in [(b'An error occurred (404) when calling the HeadObject operation', claims.MissingClaim),
                               (b'An error occurred (403) when calling the HeadObject operation', ValueError)]:
            with mock.patch.object(claims.subprocess, 'run', return_value=mock.Mock(returncode=1, stderr=message)) as run:
                with self.assertRaises(error):
                    store.read('source-health-claims/' + 'c' * 64 + '/github-actions/123/1/claim.json')
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0][2], 'head-object')

    def test_admission_cannot_hide_new_fault_or_mutate_snapshot(self):
        for edit in ('fault', 'time', 'version', 'identity'):
            public = copy.deepcopy(self.observation)
            if edit == 'fault':
                public['body']['faults'][0]['source'] = 'ofr'
            elif edit == 'time':
                public['body']['generated_at'] = (self.now - timedelta(seconds=1)).isoformat()
            elif edit == 'version':
                public['body']['version'] = '0.16.6'
            else:
                public['identity']['deployment_id'] = '22222222-2222-4222-8222-222222222222'
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                bridge.manual_assessment(self.value, self.environment, self.observation, public, self.now)

    def test_default_monitor_is_strict_and_has_no_claim_side_effect(self):
        self.assertIsNone(bridge.workflow_assessment(self.environment, self.observation, self.observation, self.now))


    def test_actual_workflow_validator_accepts_signed_faults_and_keeps_original_headroom_gate(self):
        import importlib.util
        import yaml
        here = Path(__file__).resolve().parent
        if (here / 'source-health-workflow.yml').is_file():
            workflow_path = here / 'source-health-workflow.yml'
            fixture_path = here / 'source-health-monitor-fixtures.py'
        else:
            repository = here.parents[2]
            workflow_path = repository / '.github/workflows/railway-stateful-recovery.yml'
            fixture_path = repository / 'deploy/railway-ci/recovery-monitor/test_monitor.py'
        spec = importlib.util.spec_from_file_location('monitor_fixture', fixture_path)
        fixtures_module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, {'monitor': mock.Mock()}):
            spec.loader.exec_module(fixtures_module)
        evidence = fixtures_module.fixtures()
        evidence['origin.json'] = copy.deepcopy(self.observation['body'])
        evidence['public.json'] = copy.deepcopy(self.observation['body'])
        headers = ('x-seiche-railway-authority: production\n'
                   'x-seiche-railway-deployment: ' + UUID + '\n'
                   'x-seiche-release-sha: ' + 'b' * 40 + '\n')
        evidence['origin.headers'] = evidence['public.headers'] = headers
        document = yaml.safe_load(workflow_path.read_bytes())
        step = next(step for step in document['jobs']['monitor']['steps']
                    if step.get('name') == 'Prove native backups, PITR coverage, volume headroom, and both edges')
        validator = step['run'].split('OUTPUT="$GITHUB_OUTPUT"', 1)[1].split("<<'PY'\n", 1)[1].split('\nPY\n', 1)[0]
        (self.root / 'validator.py').write_text(validator)
        workspace = self.root / 'workspace'
        helper_path = workspace / 'ops/release/source_degradation.py'
        helper_path.parent.mkdir(parents=True)
        helper_path.write_text(Path(self.module.__file__).read_text().replace(
            'OWNER_PUBLIC = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBuJV6o8YL2XXR9q4vcwpHuc2z1GEBawSmrJWGrgwzFV"',
            'OWNER_PUBLIC = ' + repr(self.module.OWNER_PUBLIC)))
        bridge_path = workspace / 'deploy/railway-ci/recovery-controller/source_health.py'
        bridge_path.parent.mkdir(parents=True)
        bridge_path.write_bytes(Path(bridge.__file__).read_bytes())
        environment = {**self.environment, 'PATH': '/usr/bin:/bin', 'HOME': str(self.root),
            'TMPDIR': str(self.root), 'PYTHONDONTWRITEBYTECODE': '1', 'GITHUB_WORKSPACE': str(workspace),
            'SOURCE_HEALTH_ADMISSION': json.dumps(self.value), 'OUTPUT': str(self.root / 'outputs'),
            'EXPECTED_VOLUME_ID': 'volume', 'EXPECTED_ENVIRONMENT_ID': 'environment',
            'EXPECTED_SERVICE_ID': 'service', 'EXPECTED_POSTGRES_ID': 'postgres'}
        # Native backup proof scope remains independent of source admission.
        evidence['native-proof.json']['data']['environment']['projectId'] = UUID
        def run():
            for name, value in evidence.items():
                (self.root / name).write_text(value if isinstance(value, str) else json.dumps(value))
            return subprocess.run([sys.executable, '-I', '-S', str(self.root / 'validator.py')],
                                  cwd=self.root, env=environment, capture_output=True, text=True)
        result = run()
        self.assertEqual(result.returncode, 0, result.stderr)
        recorded = json.loads((self.root / 'source-health-assessment.json').read_bytes())
        self.assertEqual(recorded['admitted_source_faults'], self.faults)
        self.assertFalse(recorded['claim_consumed'])
        evidence['native-proof.json']['data']['environment']['volumeInstances']['edges'][0]['node']['currentSizeMB'] = 801
        result = run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('twenty percent headroom', result.stderr)


class NativeGateSourceHealthTests(unittest.TestCase):
    def setUp(self):
        fixture_class = test_native_installation_gate.InstallationGateTests
        fixture_class.setUpClass()
        self.addCleanup(fixture_class.tearDownClass)
        self.fixture = fixture_class()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_signed_outer_policy_passes_captured_health_context_only(self):
        self.fixture.assembled['source'] = 'a' * 40
        self.fixture.policy.update(source_health_admission={'fixture': 'signed nested data'},
            application_source_sha='a' * 40, application_deployment_id=self.fixture.uuid,
            application_expected_version='0.16.5')
        self.fixture.sign_all()
        with mock.patch.object(gate.source_health, 'native_admission') as admission:
            self.fixture.validate()
        admission.assert_called_once_with(self.fixture.policy, self.fixture.now)
        self.fixture.policy['application_source_sha'] = 'e' * 40
        self.fixture.sign_all()
        with self.assertRaisesRegex(ValueError, 'runtime source'):
            self.fixture.validate()


class AttestSourceHealthTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_attest.TailTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_degraded_health_still_requires_provider_identity_and_admission(self):
        _storage, live = self.fixture.transport()
        original = live.health.return_value
        def degraded(url):
            body, headers = original
            body = {**body, 'faults': [{'source': 'fred'}]}
            return body, headers
        live.health.side_effect = degraded
        with self.assertRaisesRegex(ValueError, 'unhealthy'):
            attest.runtime_identity(live, self.fixture.policy, self.fixture.now)
        policy = {**self.fixture.policy, 'source_health_admission': {'fixture': 'verified separately'}}
        with mock.patch.object(bridge, 'native_assessment', return_value={'evidence_status': 'degraded'}) as admission:
            result = attest.runtime_identity(live, policy, self.fixture.now)
            self.assertEqual(result['deployment_id'], self.fixture.payload['application_deployment_id'])
            admission.assert_called_once()
        with mock.patch.object(bridge, 'native_assessment', side_effect=ValueError('wrong context')):
            with self.assertRaisesRegex(ValueError, 'wrong context'):
                attest.runtime_identity(live, policy, self.fixture.now)


if __name__ == '__main__':
    unittest.main()
