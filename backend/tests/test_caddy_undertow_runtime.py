"""Exercise Caddy's actual directive sorting, independent of backend uptime.

Run with `python -m unittest discover -s backend/tests -p test_caddy_undertow_runtime.py`.
The installed Caddy binary handles real requests on an ephemeral loopback port.
Only upstream responses and static fixture roots are replaced after adaptation;
matchers, route order, rewrites, headers, and the default deny remain intact.
"""
import copy
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(shutil.which('caddy'), 'runtime routing test requires Caddy')
class UndertowRoutingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='caddy-undertow-routing-')
        cls.addClassCleanup(cls.temp.cleanup)
        directory = Path(cls.temp.name)
        static = directory / 'packs'
        live = directory / 'live'
        static.mkdir()
        live.mkdir()
        (static / 'articles-feed.json').write_text('{"fixture":"static-pack"}')
        (live / 'quotes.json').write_text('{"fixture":"live-quotes"}')
        env = dict(os.environ)
        env.pop('SEICHE_API_UPSTREAM', None)
        env.pop('SEICHE_RAILWAY_EDGE_TOKEN', None)
        adapted = subprocess.run(
            ['caddy', 'adapt', '--config', str(ROOT / 'ops/Caddyfile'), '--adapter', 'caddyfile'],
            env=env, capture_output=True, text=True, check=True,
        )
        document = json.loads(adapted.stdout)
        matches = [route for server in document['apps']['http']['servers'].values()
                   for route in server['routes']
                   if any('api.seiche.info' in match.get('host', [])
                          for match in route.get('match', []))]
        assert len(matches) == 1, 'expected one shared API origin'
        route = copy.deepcopy(matches[0])

        def fixtures(value):
            if isinstance(value, list):
                for child in value:
                    fixtures(child)
            elif isinstance(value, dict):
                if value.get('handler') == 'reverse_proxy':
                    target = value['upstreams'][0]['dial']
                    value.clear()
                    value.update(handler='static_response', status_code=200,
                                 body='proxy:' + target + ' {http.request.uri}')
                elif value.get('handler') == 'vars':
                    if value.get('root') == '/opt/undertow-public':
                        value['root'] = str(static)
                    elif value.get('root') == '/var/lib/undertow-relay':
                        value['root'] = str(live)
                for child in value.values():
                    fixtures(child)

        fixtures(route)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            cls.port = sock.getsockname()[1]
        config = {'admin': {'disabled': True}, 'apps': {'http': {'servers': {
            'routing-test': {'listen': [f'127.0.0.1:{cls.port}'],
                             'automatic_https': {'disable': True}, 'routes': [route]}
        }}}}
        config_path = directory / 'runtime.json'
        config_path.write_text(json.dumps(config))
        log = (directory / 'caddy.log').open('w+')
        cls.addClassCleanup(log.close)
        cls.process = subprocess.Popen(['caddy', 'run', '--config', str(config_path)],
                                       env=env, stdout=log, stderr=log)

        def stop():
            cls.process.terminate()
            cls.process.wait(timeout=10)
        cls.addClassCleanup(stop)
        for _ in range(50):
            if cls.process.poll() is not None:
                log.seek(0)
                raise AssertionError(log.read())
            try:
                with socket.create_connection(('127.0.0.1', cls.port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise AssertionError('test Caddy did not open its loopback listener')

    def request(self, path, method='GET'):
        req = urllib.request.Request(f'http://127.0.0.1:{self.port}{path}',
                                     headers={'Host': 'api.seiche.info'}, method=method)
        try:
            response = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, response.headers, response.read().decode()

    def test_service_namespaces_reach_their_backends(self):
        for path, port in [('/undertow/mcp', 8794), ('/undertow/x402/summary', 8791),
                           ('/undertow/desk/health', 8792)]:
            with self.subTest(path=path):
                status, _, body = self.request(path, 'POST' if path.endswith('/mcp') else 'GET')
                self.assertEqual(status, 200)
                self.assertEqual(body, f'proxy:127.0.0.1:{port} {path}')

    def test_live_and_static_files_keep_distinct_cache_policies(self):
        for path, fixture, cache in [('/undertow/live/quotes.json', 'live-quotes', 'no-store'),
                                     ('/undertow/articles-feed.json', 'static-pack', 'public, max-age=600')]:
            with self.subTest(path=path):
                status, headers, body = self.request(path)
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body)['fixture'], fixture)
                self.assertEqual(headers.get('Cache-Control'), cache)

    def test_index_preflight_and_dormant_rail_keep_their_contracts(self):
        for path in ['/undertow', '/undertow/']:
            status, _, body = self.request(path)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)['product'], 'Undertow')
        self.assertEqual(self.request('/undertow/mcp', 'OPTIONS')[0], 204)
        status, _, body = self.request('/undertow/paypal/health')
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body)['status'], 'DORMANT')
        self.assertEqual(self.request('/undertow/unpublished-private-file.json')[0], 404)


if __name__ == '__main__':
    unittest.main()
