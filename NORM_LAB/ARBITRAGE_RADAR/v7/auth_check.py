"""Finam read-only authorization smoke; never print credentials/JWT/account IDs."""
import json
import os
import sys
from tls_setup import enable_native_tls
enable_native_tls()

from finam_ws_collector import authorize_readonly, ControlledFailure


def main():
    key = os.getenv("FINAM_READONLY_API_KEY", "")
    if not key:
        print(json.dumps({"auth": "MISSING_READONLY_KEY", "orders": 0}))
        return 10
    try:
        jwt = authorize_readonly(key)
        if not jwt:
            raise ControlledFailure("AUTH_TOKEN_NOT_RETURNED")
        print(json.dumps({"auth": "FINAM_READONLY_VERIFIED", "orders": 0}))
        return 0
    except ControlledFailure as exc:
        print(json.dumps({"auth": str(exc), "orders": 0}))
        return 11
    except Exception:
        print(json.dumps({"auth": "AUTH_UNKNOWN_FAILURE", "orders": 0}))
        return 12


if __name__ == "__main__":
    sys.exit(main())