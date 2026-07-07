## 2024-07-07 - Unauthenticated Supervise Endpoint (Critical)
**Vulnerability:** The `/supervise/{session_id}` websocket endpoint for interacting with sessions does not require any authentication, allowing any client to connect, stream, inspect, and approve/cancel operations on active agent sessions.
**Learning:** External WebSocket endpoints for internal debugging or administrative interfaces must have explicit authentication mechanisms built-in from the start, as web frameworks may not automatically protect custom websocket paths.
**Prevention:** Add a `supervisor_token` parameter and enforce token verification via headers or query parameters for all `/supervise/` connections in the connection request processor.
