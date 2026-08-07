## 2024-08-08 - Add security headers to custom websocket HTTP handler
**Vulnerability:** The HTTP handshake handler in `livelink.serve` lacked standard security headers (CSP, X-Frame-Options, X-Content-Type-Options), which exposed endpoints serving HTML (`/`) to framing attacks and content-sniffing.
**Learning:** In a custom WebSocket server utilizing the `websockets` library (like `serve.py`), standard web framework security middlewares do not apply. Any HTML or JSON served prior to the WebSocket upgrade must have security headers added manually.
**Prevention:** Always manually configure `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, and an appropriate `Content-Security-Policy` when returning `websockets.http11.Response` objects for raw HTTP paths in a WebSocket server.
