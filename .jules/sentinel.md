## 2024-09-12 - Wildcard CORS enabled for UI endpoints
**Vulnerability:** The application's UI endpoint (`/`) sets `Access-Control-Allow-Origin: *` when CORS is enabled, allowing any domain to access it. Note that `cors=False` is default, but if someone enables it, it uses `*`. Another issue is that security headers like CSP and X-Frame-Options are missing on the UI endpoints. Since this UI can connect back to the WebSocket endpoint for execution, lack of CSP makes it easier to perform XSS attacks if there is any injection (although current UI seems hardcoded, it's defense-in-depth). The `/health` endpoint lacks CORS entirely when enabled, and all endpoints lack basic security headers.

**Learning:** Missing basic HTTP security headers is a common gap in ASGI/WebSocket wrappers serving basic HTTP UI. The `serve` method implements a lightweight HTTP responder with missing CSP, X-Frame-Options, X-Content-Type-Options headers.

**Prevention:** We need to set defensive headers explicitly for all HTTP responses in `websockets.http11.Response` when we serve static or API content over HTTP.
