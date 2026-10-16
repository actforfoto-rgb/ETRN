"""Windows certificate-chain verification using the native Windows CryptoAPI.

This is imported ONLY by the application's entry points. TLS verification
remains mandatory, with hostname and chain verification enforced by Windows.
"""
import sys

def enable_native_tls():
    if sys.platform == "win32":
        import truststore
        truststore.inject_into_ssl()
        return "WINDOWS_CRYPTOAPI_VERIFIED"
    return "DEFAULT_PLATFORM_TLS"