"""Private service keys are provisioned outside the database and legacy backend."""
import base64
import hashlib
import json
import secrets

from cryptography.fernet import Fernet
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def new_token() -> str:
    return "san_" + secrets.token_urlsafe(40)


def generate_keys() -> dict[str, str]:
    key = Ed25519PrivateKey.generate()
    return {"encryption_key": Fernet.generate_key().decode(), "signing_key": base64.urlsafe_b64encode(
        key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                          serialization.NoEncryption())).decode()}


class Crypto:
    def __init__(self, encryption_key: str | bytes, signing_key: str | bytes):
        self.cipher = Fernet(encryption_key)
        raw = signing_key.encode() if isinstance(signing_key, str) else signing_key
        if raw.startswith(b"-----BEGIN"):
            self.signer = serialization.load_pem_private_key(raw, password=None)
            if not isinstance(self.signer, Ed25519PrivateKey):
                raise ValueError("Signing key must be Ed25519")
        else:
            self.signer = Ed25519PrivateKey.from_private_bytes(base64.urlsafe_b64decode(raw))
        self.public_key = base64.urlsafe_b64encode(self.signer.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
        raw_encryption = encryption_key.encode() if isinstance(encryption_key, str) else encryption_key
        self.fingerprint = digest(raw_encryption + self.public_key.encode())

    def encrypt(self, value) -> str:
        return self.cipher.encrypt(canonical(value)).decode()

    def decrypt(self, value: str):
        return json.loads(self.cipher.decrypt(value.encode()))

    def sign(self, value) -> str:
        return base64.urlsafe_b64encode(self.signer.sign(canonical(value))).decode()

    def verify(self, value, signature: str) -> bool:
        try:
            self.signer.public_key().verify(base64.urlsafe_b64decode(signature), canonical(value))
            return True
        except (ValueError, TypeError, InvalidSignature):
            return False
