## 2024-08-14 - [CSWSH in websockets agent.serve]
**Vulnerability:** The default `agent.serve()` implementation did not enforce Origin checks for WebSocket connections. When used locally (e.g. `localhost:8000`), a malicious webpage could perform Cross-Site WebSocket Hijacking (CSWSH) to interact with the local agent process.
**Learning:** `websockets` does not strictly require `Origin` unless `origins` is explicitly passed. When using custom `process_request` functions, it's critical to manually parse `Origin` and ensure it matches the `Host` or expected local networks.
**Prevention:** Always compare `Origin` vs `Host` headers or enforce explicit `origins` configuration in WebSocket server components to prevent CSWSH.
