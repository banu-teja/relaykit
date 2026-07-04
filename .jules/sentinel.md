## 2024-07-04 - [Missing Baseline Security Headers on HTTP Endpoints]
**Vulnerability:** Missing `X-Content-Type-Options` and `X-Frame-Options` on HTTP endpoints `/` and `/health` served by `websockets.http11.Response`.
**Learning:** WebSocket servers that also serve HTTP fallbacks/UIs often overlook standard HTTP security headers since their primary purpose is WebSocket connections.
**Prevention:** Always apply baseline HTTP security headers (anti-clickjacking and MIME-sniffing protection) even on secondary/health check HTTP endpoints in WebSocket applications.
