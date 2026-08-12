## 2023-10-27 - Security Headers

**Vulnerability:** Missing Security Headers
**Learning:** By default, Python websocket/HTTP servers don't include security headers for returned HTML content. This can lead to clickjacking.
**Prevention:** Explicitly define X-Frame-Options, X-Content-Type-Options, and Content-Security-Policy headers whenever returning HTML content.
