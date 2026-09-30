"""Build RFC 9727 discovery from the existing public interface inventory."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
CATALOG_URL = "https://seiche.info/.well-known/api-catalog"


def build_catalog(inventory):
    entries = {entry["identifier"]: entry for entry in inventory["entries"]}
    rest = entries["urn:air:seiche.info:openapi:funding-stress"]
    mcps = [entries[name] for name in ("urn:air:seiche.info:mcp:funding-stress", "urn:air:seiche.info:mcp:market-corpus")]
    interfaces = [{"anchor":"https://api.seiche.info/api", "service-desc":[{"href":rest['url'], "type":"application/vnd.oai.openapi+json", "title":rest['displayName']}],
        "service-doc":[{"href":"https://seiche.info/developers", "type":"text/html"}], "service-meta":[{"href":"https://seiche.info/terms", "type":"text/html", "title":"Public research use and limits"}]}]
    for entry in mcps:
        server = entry['data']
        url = server['remotes'][0]['url']
        descriptor = ("https://registry.modelcontextprotocol.io/v0.1/servers/" + quote(server['name'], safe='') + '/versions/' + server['version']
                      if entry['identifier'] == 'urn:air:seiche.info:mcp:funding-stress'
                      else 'https://seiche.info/.well-known/mcp-market-corpus.json')
        interfaces.append({"anchor":url,
            "service-desc":[{"href":descriptor, "type":"application/json", "title":server['title']}],
            "service-doc":[{"href":"https://seiche.info/developers", "type":"text/html"}],
            "service-meta":[{"href":"https://api.seiche.info/.well-known/mcp.json", "type":"application/json", "title":"Native MCP transport discovery"}]})
    return {"linkset":[{"anchor":CATALOG_URL, "item":[{"href":item['anchor']} for item in interfaces]}, *interfaces]}


def render(root=ROOT):
    inventory = json.loads((root/'frontend/public/.well-known/ai-catalog.json').read_text())
    return json.dumps(build_catalog(inventory), indent=2) + '\n'


def render_corpus_descriptor(root=ROOT):
    inventory = json.loads((root/'frontend/public/.well-known/ai-catalog.json').read_text())
    entry = next(e for e in inventory['entries'] if e['identifier'] == 'urn:air:seiche.info:mcp:market-corpus')
    # This is an owner-hosted descriptor; it makes no registry publication claim.
    return json.dumps(entry['data'], indent=2) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    outputs = {'api-catalog':render(), 'mcp-market-corpus.json':render_corpus_descriptor()}
    for name, expected in outputs.items():
        destination = ROOT/'frontend/public/.well-known'/name
        if args.check:
            if not destination.exists() or destination.read_text() != expected:
                raise SystemExit('API discovery disagrees with the public interface inventory: ' + name)
        else:
            destination.write_text(expected)
    print('API catalog matches public REST and separate MCP interfaces')


if __name__ == '__main__':
    main()
