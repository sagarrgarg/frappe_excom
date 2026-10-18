"""Remove credentials from text before it is stored or shown (errors, alerts, job logs).

Platforms take tokens in query strings, and HTTP libraries put the full URL in their exception
messages, so any str(exception) can carry one. Everything that persists error text runs it
through redact() first.
"""

import re

_PATTERNS = [
	# key=value in URLs and form bodies
	(re.compile(r"(?i)\b(access_token|input_token|client_secret|refresh_token|appsecret_proof|api_key|key|token|password|secret)=([^&\s'\"<>]+)"), r"\1=***"),
	# Authorization headers
	(re.compile(r"(?i)\b(bearer|oauth)\s+[A-Za-z0-9._\-~+/=]{12,}"), r"\1 ***"),
	# Meta long-lived / system-user tokens and app tokens (APPID|SECRET)
	(re.compile(r"\bEA[A-Za-z0-9]{30,}"), "***"),
	(re.compile(r"\b\d{6,}\|[A-Za-z0-9_\-]{16,}"), "***"),
	# Google access / refresh tokens and PEM keys
	(re.compile(r"\bya29\.[\w\-.]+"), "***"),
	(re.compile(r"\b1//[\w\-]{20,}"), "***"),
	(re.compile(r"-----BEGIN [A-Z ]+-----.*?(-----END [A-Z ]+-----|$)", re.S), "***"),
]


def redact(text, secrets=()) -> str:
	out = str(text or "")
	for s in sorted({s for s in secrets if s and len(str(s)) >= 6}, key=len, reverse=True):
		out = out.replace(str(s), "***")
	for pattern, repl in _PATTERNS:
		out = pattern.sub(repl, out)
	return out
