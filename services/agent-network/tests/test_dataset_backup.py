import json
from pathlib import Path

from test_backup import backup, make_backup, source
from seiche_agent_network import demand
from seiche_agent_network.analytics import Analytics
from seiche_agent_network.data_catalog import tool_registry
from seiche_agent_network.store import Store


def test_encrypted_full_backup_preserves_transactional_dataset_counts(source):
    store = source['store']
    principal = store.authenticate(source['tokens'][0])
    analytics = Analytics(store, tool_registry=tool_registry())
    analytics.record(principal, product='seiche', tool='money_market_context', method='tools/call',
                     http_status=200, duration_ms=5, traffic_class='synthetic', tool_outcome='success')
    made = make_backup(source)
    assert b'network_dataset_daily' not in Path(made['bundle']).read_bytes()
    target, keys = source['root']/'dataset-restored.sqlite3', source['root']/'dataset-restored-keys.json'
    backup.restore(str(target), made['bundle'], source['backup_key'], keys)
    reopened = Store(target, **json.loads(keys.read_text()))
    try:
        principal = reopened.authenticate(source['tokens'][0])
        result = demand.report(reopened, principal)
        assert result['totals']['requests'] == 1 and result['totals']['tool_successes'] == 1
        assert result['rows'][0]['dataset_id'] == 'seiche.money_markets'
        assert Analytics(reopened).report(principal)['totals']['requests'] == 1
    finally:
        reopened.close()
