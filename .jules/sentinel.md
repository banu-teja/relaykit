## 2024-05-18 - [Missing CSWSH Protection on serve endpoint]
**Vulnerability:** Missing Origin validation in `agent.serve()` WebSocket handler enabled Cross-Site WebSocket Hijacking (CSWSH) when `cors=False`.
**Learning:** By default, the `websockets` library does not validate the `Origin` header. An attacker on a malicious website could open a WebSocket to `ws://localhost:8000/ws` and fully control a user's local agent.
**Prevention:** Implement an `Origin` check in `process_request` comparing `Origin`'s `netloc` to the `Host` header if `cors=False`.
