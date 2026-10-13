"""Country of the connecting network exit, authenticated by our edge worker.

Never treat an arbitrary CF-IPCountry header as evidence. No IP address is stored.
"""
import hashlib
import hmac
import re
import time
from .analytics import COUNTRIES


def verified_country(headers, method, path, secret, *, now=None):
    if not isinstance(secret, str) or len(secret) < 32:
        return "unknown"
    country = headers.get("x-platform-country", "")
    timestamp = headers.get("x-platform-geo-time", "")
    request_id = headers.get("x-platform-geo-id", "")
    signature = headers.get("x-platform-geo-signature", "")
    if (country not in COUNTRIES
            or not re.fullmatch(r"[0-9]{10}", timestamp)
            or not re.fullmatch(r"[a-f0-9-]{36}", request_id)
            or not re.fullmatch(r"[a-f0-9]{64}", signature)):
        return "unknown"
    if abs((time.time() if now is None else now)-int(timestamp)) > 60:
        return "unknown"
    message = "\n".join([country, timestamp, request_id, method, path]).encode()
    expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return country if hmac.compare_digest(expected, signature) else "unknown"
