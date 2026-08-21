## 2025-01-08 - Added CSWSH Prevention for Local WebSocket Agent
**Vulnerability:** Cross-Site WebSocket Hijacking (CSWSH) could allow malicious websites to connect to the local WebSocket agent if the user opens them while the agent is running, as WebSockets are not protected by Same-Origin Policy.
**Learning:** `websockets.asyncio.server.serve` does not validate `Origin` out of the box when handling all incoming requests.
**Prevention:** Always validate `Origin` against `Host` in the initial `process_request` hook before upgrading the connection to a WebSocket when CORS is not explicitly enabled.
