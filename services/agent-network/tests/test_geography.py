import hashlib
import hmac
import uuid
from seiche_agent_network.geography import verified_country


def test_untrusted_or_stale_country_is_unknown():
    secret='test-only-secret-'*3
    assert verified_country({'cf-ipcountry':'US'},'POST','/products/seiche/mcp',secret)=='unknown'
    headers={'x-platform-country':'IN','x-platform-geo-time':'1800000000','x-platform-geo-id':str(uuid.uuid4())}
    message='\n'.join([headers['x-platform-country'],headers['x-platform-geo-time'],headers['x-platform-geo-id'],'POST','/products/seiche/mcp'])
    headers['x-platform-geo-signature']=hmac.new(secret.encode(),message.encode(),hashlib.sha256).hexdigest()
    assert verified_country(headers,'POST','/products/seiche/mcp',secret,now=1800000000)=='IN'
    assert verified_country(headers,'POST','/products/undertow/mcp',secret,now=1800000000)=='unknown'
    assert verified_country(headers,'POST','/products/seiche/mcp',secret,now=1800000061)=='unknown'
    headers['x-platform-country']='US'
    assert verified_country(headers,'POST','/products/seiche/mcp',secret,now=1800000000)=='unknown'
