"""Exercise Caddy's actual directive sorting, independent of backend uptime.

Run with `python -m unittest discover -s backend/tests -p test_caddy_undertow_runtime.py`.
The installed Caddy binary handles real requests on an ephemeral loopback port.
Only upstream responses and static fixture roots are replaced after adaptation;
matchers, route order, rewrites, headers, and the default deny remain intact.
"""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import urllib.robotparser

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

        # Consume real request bodies so Caddy's edge ceiling is exercised,
        # rather than merely checking the adapted configuration contains it.
        class BodyReader(BaseHTTPRequestHandler):
            def do_POST(self):
                expected = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(expected)
                if len(body) != expected:
                    return  # The proxy rejected the oversized body mid-stream.
                response = json.dumps({'path': self.path, 'bytes': len(body)}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, *args):
                pass

        upstream = ThreadingHTTPServer(('127.0.0.1', 0), BodyReader)
        upstream.daemon_threads = True
        thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        thread.start()

        def stop_upstream():
            upstream.shutdown()
            upstream.server_close()
            thread.join(timeout=5)
        cls.addClassCleanup(stop_upstream)

        def fixtures(value):
            if isinstance(value, list):
                for child in value:
                    fixtures(child)
            elif isinstance(value, dict):
                if value.get('handler') == 'reverse_proxy':
                    target = value['upstreams'][0]['dial']
                    if target == '127.0.0.1:8787':
                        value['upstreams'][0]['dial'] = f'127.0.0.1:{upstream.server_port}'
                        return
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

    def request(self, path, method='GET', data=None):
        req = urllib.request.Request(f'http://127.0.0.1:{self.port}{path}',
                                     headers={'Host': 'api.seiche.info'}, method=method,
                                     data=data)
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

    def test_seiche_data_robots_is_read_only_and_does_not_change_sibling_policy(self):
        status, headers, body = self.request('/robots.txt')
        self.assertEqual(status, 200)
        self.assertEqual(headers.get_content_type(), 'text/plain')
        policy = urllib.robotparser.RobotFileParser()
        policy.parse(body.splitlines())
        for path in ('/api', '/api/v2/corpus/catalog', '/api/series/data.csv',
                     '/api/dispatch/example', '/mcp', '/mcp/usage',
                     '/.well-known/mcp.json', '/.well-known/api-catalog'):
            self.assertFalse(policy.can_fetch('Google-Extended', path), path)
            self.assertTrue(policy.can_fetch('Googlebot', path), path)
        for path in ('/undertow/articles-feed.json', '/palimpsest/mcp', '/riptide/'):
            self.assertTrue(policy.can_fetch('Google-Extended', path), path)
        self.assertEqual(self.request('/robots.txt', 'HEAD')[::2], (200, ''))
        self.assertEqual(self.request('/robots.txt', 'POST', b'{}')[0], 404)
        self.assertEqual(self.request('/robots.txt/private')[0], 404)

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

    def test_gold_scenario_exact_post_route_has_private_response_headers(self):
        path = '/api/v2/gift-city/gold-carry'
        status, headers, body = self.request(path, 'POST', b'{}')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {'path': path, 'bytes': 2})
        self.assertEqual(headers.get('Cache-Control'), 'no-store')
        self.assertEqual(headers.get('Access-Control-Allow-Origin'), '*')
        self.assertEqual(headers.get('X-Content-Type-Options'), 'nosniff')
        self.assertEqual(self.request(path, 'OPTIONS')[0], 204)
        for method, rejected in [('POST', path + '/'), ('POST', path + '/private'),
                                 ('PUT', path), ('PATCH', path), ('DELETE', path)]:
            with self.subTest(method=method, path=rejected):
                self.assertEqual(self.request(rejected, method, b'{}')[0], 404)

    def test_gold_body_ceiling_preserves_adjacent_post_contracts(self):
        for path, length, expected in [
            ('/api/v2/gift-city/gold-carry', 8192, 200),
            ('/api/v2/gift-city/gold-carry', 8193, 413),
            ('/api/event-analysis', 8193, 413),
            ('/api/auth/login', 8193, 200),
        ]:
            with self.subTest(path=path, length=length):
                status, _, body = self.request(path, 'POST', b'x' * length)
                self.assertEqual(status, expected)
                if expected == 200:
                    self.assertEqual(json.loads(body), {'path': path, 'bytes': length})


if __name__ == '__main__':
    unittest.main()
