## 2025-02-14 - [HIGH] Prevent Cross-Site WebSocket Hijacking (CSWSH)
**Vulnerability:** The WebSocket upgrade path in `process_request` in `src/livelink/serve.py` did not validate the `Origin` header against the `Host` header.
**Learning:** This missing validation allowed malicious websites to establish authenticated WebSocket sessions on behalf of the user when CORS was not enabled. Non-browser clients are typically not affected because they don't send `Origin` headers, but browsers do.
**Prevention:** Always validate the `Origin` header against the `Host` header for incoming WebSocket connection requests unless cross-origin requests are explicitly enabled.
