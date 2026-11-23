#!/usr/bin/env python3
"""Minimal Solana-compatible keypair helpers (ed25519 + base58)."""
import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(raw: bytes) -> str:
    n = int.from_bytes(raw, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = ALPHABET[r] + out
    pad = 0
    for b in raw:
        if b:
            break
        pad += 1
    return "1" * pad + out


def new_keypair() -> dict:
    """Return {'seed_b64', 'address'} for a fresh ed25519 keypair."""
    seed = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    return {"seed_b64": base64.b64encode(seed).decode(), "address": address_of(seed)}


def load_key(seed_b64: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(base64.b64decode(seed_b64))


def address_of(seed: bytes) -> str:
    sk = Ed25519PrivateKey.from_private_bytes(seed)
    pub = sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return b58encode(pub)


def sign_b64(seed_b64: str, message: str) -> str:
    return base64.b64encode(load_key(seed_b64).sign(message.encode())).decode()
