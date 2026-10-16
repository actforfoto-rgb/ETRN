"""Check https://api.finam.ru verified TLS BEFORE requesting a Finam key.

Never prints any secrets or raw certificates. No broker API calls.
"""
import json
import socket
import ssl
import sys

from tls_setup import enable_native_tls
enable_native_tls()

HOST = "api.finam.ru"

def probe_tls(host=HOST):
    try:
        with socket.create_connection((host, 443), timeout=12) as raw:
            raw.settimeout(12)
            with ssl.create_default_context().wrap_socket(raw, server_hostname=host) as sec:
                cert = sec.getpeercert(binary_form=True)
                if not cert or not sec.cipher():
                    return 12, "TLS_NO_VERIFIED_PEER"
        return 0, "TLS_CERTIFICATE_VERIFIED_NATIVE_WINDOWS" if sys.platform == "win32" else "TLS_CERTIFICATE_VERIFIED"
    except ssl.SSLCertVerificationError:
        return 11, "TLS_CERTIFICATE_UNTRUSTED_BY_WINDOWS"
    except (ssl.SSLError, socket.timeout, TimeoutError, OSError):
        return 12, "TLS_CONNECTION_FAILED"

def main():
    code, status=probe_tls()
    print(json.dumps({"tls": status, "orders": 0}, ensure_ascii=False),flush=True)
    return code

if __name__ == "__main__":
    raise SystemExit(main())