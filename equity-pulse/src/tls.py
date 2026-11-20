"""Windows system trust-store CA bundle helper (keeps strict TLS verification enabled)."""

from __future__ import annotations

import os
import ssl
import sys
from pathlib import Path

from src.config import DATA_DIR


def configure_system_tls(bundle_path: Path | None = None, force_refresh: bool = False) -> str | None:
    """Export the OS-trusted CA certificates from Python's default SSL context
    (merged with certifi) so `requests` and `curl_cffi`/`yfinance` trust the exact
    same Windows root store as browsers and `urllib` with full TLS verification.
    """
    existing = os.environ.get("CURL_CA_BUNDLE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if existing and not force_refresh and Path(existing).exists():
        return existing

    if sys.platform != "win32":
        return None

    target = bundle_path or (DATA_DIR / ".system_ca_bundle.pem")
    target.parent.mkdir(parents=True, exist_ok=True)

    pem_blocks: list[str] = []
    seen_der: set[bytes] = set()

    # 1. Exact OS root CAs trusted by Python's default SSL context (includes Windows ROOT store)
    try:
        ctx = ssl.create_default_context()
        for der in ctx.get_ca_certs(binary_form=True):
            if der not in seen_der:
                seen_der.add(der)
                pem_blocks.append(ssl.DER_cert_to_PEM_cert(der))
    except Exception:
        pass

    # 2. Append certifi bundle as well for standard public roots
    try:
        import certifi

        certifi_path = Path(certifi.where())
        if certifi_path.exists():
            pem_blocks.append(certifi_path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        pass

    if not pem_blocks:
        return None

    target.write_text("\n".join(pem_blocks), encoding="utf-8")
    bundle_str = str(target)
    os.environ["REQUESTS_CA_BUNDLE"] = bundle_str
    os.environ["SSL_CERT_FILE"] = bundle_str
    os.environ["CURL_CA_BUNDLE"] = bundle_str
    return bundle_str
