import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
import time

import pytest


@pytest.fixture
def upstream_credential(tmp_path, monkeypatch):
    """Local test issuer; never uses a live identity or signing secret."""
    path = tmp_path / "upstream-token.json"
    monkeypatch.setenv("SEICHE_UNDERTOW_MCP_TOKEN_FILE", str(path))

    def write(*, now=None, ttl=3600, name="test", changes=None):
        now = int(time.time() if now is None else now)
        claims = {"sub": "fake-operator", "name": name, "aud": "mcp", "iat": now, "exp": now + ttl}
        claims.update(changes or {})
        part = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
        signature = hmac.new(b"local-test-only", part.encode(), hashlib.sha256).hexdigest()
        token = part + "." + signature
        bundle = {"token": token, "expires_at": datetime.fromtimestamp(claims["exp"], timezone.utc).isoformat(),
                  "product": "undertow", "purpose": "public_mcp_gateway"}
        path.write_text(json.dumps(bundle))
        path.chmod(0o600)
        return token, path

    return write
