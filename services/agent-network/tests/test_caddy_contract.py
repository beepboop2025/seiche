"""Keep the deployable proxy exception aligned with the application's bound."""
from pathlib import Path
import re

from seiche_agent_network.hosted_surfaces import NOISEFLOOR_POST, body_limit


def test_caddy_exception_names_only_the_reviewed_post_routes():
    config = Path(__file__).parents[1].joinpath('ops/caddy-route.example').read_text()
    general = config.split('handle_path /agents/* {\n', 1)[1]
    matcher = re.search(r'@agent_small_body\s*\{\s*not\s*\{\s*method (\w+)\s*path ([^\n]+)\s*\}\s*\}', general)
    assert matcher is not None, 'The small edge cap needs a reviewed method/path exception'
    method, raw_paths = matcher.groups()
    paths = raw_paths.split()
    assert method == 'POST'
    assert len(paths) == len(set(paths))
    assert set(paths) == {'/v1/noisefloor/' + path for path in NOISEFLOOR_POST}
    assert re.search(r'request_body @agent_small_body\s*\{\s*max_size 64KiB\s*\}', general)
    for path in paths:
        assert body_limit({'method': method, 'path': path}, 65536) == 2 * 1024 * 1024
        assert body_limit({'method': 'PUT', 'path': path}, 65536) == 65536
        assert body_limit({'method': method, 'path': path + '/extra'}, 65536) == 65536


def test_isolated_services_keep_their_existing_edge_body_caps():
    config = Path(__file__).parents[1].joinpath('ops/caddy-route.example').read_text()
    for service in ('commerce', 'learning'):
        handler = config.split('handle_path /agents/' + service + '/* {\n', 1)[1].split('handle_path ', 1)[0]
        assert re.search(r'request_body\s*\{\s*max_size 64KiB\s*\}', handler)
        assert '@agent_small_body' not in handler
