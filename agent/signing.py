"""Signed /api/stream URLs for clients that can't send headers (<video src>, vlc://).

Run `python -m agent.signing` for the self-check.
"""
import hashlib
import hmac
import time

TTL = 6 * 3600


def sign(path, exp, secret):
    return hmac.new(secret.encode(), f"{path}\n{exp}".encode(), hashlib.sha256).hexdigest()


def verify(path, exp, sig, secret, now=None):
    if exp < (time.time() if now is None else now):
        return False
    return hmac.compare_digest(sign(path, exp, secret).encode(), sig.encode())


def _self_check():
    secret, path, now = "s" * 43, r"D:\MOvies\a.mp4", 1_000_000
    exp = now + TTL
    sig = sign(path, exp, secret)
    assert verify(path, exp, sig, secret, now), "valid"
    assert not verify(path, now - 1, sign(path, now - 1, secret), secret, now), "expired"
    assert not verify(r"D:\MOvies\b.mp4", exp, sig, secret, now), "tampered path"
    assert not verify(path, exp + 1, sig, secret, now), "tampered exp"
    assert not verify(path, exp, ("0" if sig[0] != "0" else "1") + sig[1:], secret, now), "tampered sig"
    assert not verify(path, exp, sig, "x" * 43, now), "other secret"
    assert not verify(path, exp, chr(233), secret, now), "non-ascii sig"
    print("signing self-check OK")


if __name__ == "__main__":
    _self_check()
