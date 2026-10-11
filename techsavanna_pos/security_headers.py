"""
Security headers on every response the backend sends.

Two protections matter most, and a browser only honours them when the server sends them:

- stopping other websites from showing the till inside their own page, to trick staff into
  clicking (called "clickjacking"): `X-Frame-Options` and `Content-Security-Policy: frame-ancestors`
- making browsers always use a secure connection once they have seen the site over HTTPS:
  `Strict-Transport-Security`

The website that shows the till (the frontend) is served by Nginx, so it gets the same headers from
there; see docs/SERVER_SECURITY_SETUP.md. This hook covers the backend's own addresses (the API and
the Frappe desk), so they are protected whatever Nginx is set up to do.

Rules, so that nothing that works today breaks:

- A header the response already carries is never changed. Frappe's own embeddable web forms set
  their own `Content-Security-Policy`; when that is present, no framing header is added at all, since
  a second one would contradict it.
- Framing is limited to the site's own pages (`SAMEORIGIN` / `'self'`), not blocked outright,
  because the Frappe desk shows some of its own pages inside frames.
- `Strict-Transport-Security` is sent only on HTTPS responses, as browsers ignore it elsewhere.
- Nothing here can break a request: any problem is logged and the response goes out unchanged.

To switch it off for a site, set `"techsavanna_pos_security_headers": 0` in its site_config.json.
"""

from __future__ import annotations

import frappe

HSTS_SECONDS = 365 * 24 * 60 * 60


def add_security_headers(response=None, request=None, **kwargs) -> None:
	"""Frappe `after_request` hook: add the headers a response does not already have."""
	try:
		if response is None or not _enabled():
			return

		headers = response.headers

		_add(headers, "X-Content-Type-Options", "nosniff")
		_add(headers, "Referrer-Policy", "strict-origin-when-cross-origin")

		if "Content-Security-Policy" not in headers:
			_add(headers, "Content-Security-Policy", "frame-ancestors 'self'")
			_add(headers, "X-Frame-Options", "SAMEORIGIN")

		if _is_https(request):
			_add(headers, "Strict-Transport-Security", f"max-age={HSTS_SECONDS}")
	except Exception:
		# A header problem must never turn a good response into an error
		try:
			frappe.logger().error("techsavanna_pos security headers could not be added", exc_info=True)
		except Exception:
			pass


def _enabled() -> bool:
	setting = frappe.conf.get("techsavanna_pos_security_headers")
	return setting is None or bool(int(setting))


def _is_https(request) -> bool:
	"""Nginx terminates HTTPS and tells the backend so in X-Forwarded-Proto."""
	if request is None:
		return False
	forwarded = (request.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
	return forwarded == "https" or bool(getattr(request, "is_secure", False))


def _add(headers, name: str, value: str) -> None:
	if name not in headers:
		headers[name] = value
