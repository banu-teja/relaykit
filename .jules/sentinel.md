## 2025-02-14 - Add security headers to WebSocket HTTP fallback
**Vulnerability:** Missing `X-Frame-Options` and `X-Content-Type-Options` security headers on HTTP responses (UI and health endpoints).
**Learning:** The built-in lightweight HTTP server serving default UI and health check was missing basic security headers. This is a common pattern when using WebSocket libraries (`websockets`) to serve HTTP requests out of convenience.
**Prevention:** Ensure any HTTP handlers bundled with WebSocket servers explicitly set basic security headers.
