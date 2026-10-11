#!/usr/bin/env python3
"""
Checks that websites send the security headers described in docs/SERVER_SECURITY_SETUP.md.

It needs nothing installed beyond Python 3, and changes nothing. Run it from any computer, after the
server setup, and again whenever the server is changed:

    python3 check_security_headers.py https://pos.murzaktech.tech https://api.pos.murzaktech.tech

For each address it prints PASS or FAIL for each protection, and exits with a failure if any is
missing, so it can also be used in a script.
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.parse
import urllib.request

# One year, the value in the server snippet. A short value is fine while testing, but not as the end state.
HSTS_TARGET_SECONDS = 365 * 24 * 60 * 60


class _NoRedirect(urllib.request.HTTPRedirectHandler):
	"""Report a redirect as it is, instead of following it to the page behind it."""

	def redirect_request(self, *args, **kwargs):
		return None


def fetch(url: str, timeout: int = 15) -> tuple[int, dict[str, str]]:
	"""The status code and the response headers (names in lower case) of a page, without following redirects."""
	opener = urllib.request.build_opener(_NoRedirect)
	request = urllib.request.Request(url, headers={"User-Agent": "murzak-security-header-check"})
	try:
		with opener.open(request, timeout=timeout) as response:
			return response.status, {key.lower(): value for key, value in response.getheaders()}
	except urllib.error.HTTPError as error:  # 3xx, 4xx and 5xx still carry the headers we want to see
		return error.code, {key.lower(): value for key, value in error.headers.items()}


def evaluate(headers: dict[str, str], https: bool) -> list[tuple[str, bool, str]]:
	"""Each protection as (what it does, whether it is in place, what was found)."""
	frame_options = headers.get("x-frame-options", "")
	policy = headers.get("content-security-policy", "")
	frame_ancestors = [
		part.strip() for part in policy.split(";") if part.strip().startswith("frame-ancestors")
	]

	results = [
		(
			"Other websites cannot show this page inside their own",
			frame_options.upper() in ("DENY", "SAMEORIGIN") or bool(frame_ancestors),
			f"X-Frame-Options: {frame_options or 'missing'}; "
			f"Content-Security-Policy: {frame_ancestors[0] if frame_ancestors else 'no frame-ancestors'}",
		),
		(
			"Browsers do not guess file types",
			headers.get("x-content-type-options", "").lower() == "nosniff",
			f"X-Content-Type-Options: {headers.get('x-content-type-options') or 'missing'}",
		),
		(
			"Links to other sites reveal only the site name",
			bool(headers.get("referrer-policy")),
			f"Referrer-Policy: {headers.get('referrer-policy') or 'missing'}",
		),
	]

	if https:  # browsers ignore this header on plain http, so it is only expected on https
		seconds = _hsts_seconds(headers.get("strict-transport-security", ""))
		found = headers.get("strict-transport-security") or "missing"
		if 0 < seconds < HSTS_TARGET_SECONDS:
			found += (
				f" (only {seconds} seconds; raise it to {HSTS_TARGET_SECONDS} once HTTPS works everywhere)"
			)
		results.append(
			(
				"Browsers always use a secure connection",
				seconds >= HSTS_TARGET_SECONDS,
				f"Strict-Transport-Security: {found}",
			)
		)

	return results


def _hsts_seconds(value: str) -> int:
	for part in value.split(";"):
		name, _, number = part.strip().partition("=")
		if name.lower() == "max-age" and number.strip().isdigit():
			return int(number.strip())
	return 0


def check_redirect(url: str) -> tuple[bool | None, str]:
	"""Whether the plain-http address sends people on to https. None when it cannot be reached at all."""
	parts = urllib.parse.urlsplit(url)
	plain = urllib.parse.urlunsplit(("http", parts.netloc, parts.path or "/", "", ""))
	try:
		status, headers = fetch(plain, timeout=8)
	except OSError as error:
		return None, f"{plain} could not be reached ({error})"
	where = headers.get("location", "")
	if status in (301, 302, 307, 308) and where.lower().startswith("https://"):
		return True, f"{plain} sends visitors to {where}"
	return False, f"{plain} answered {status} instead of sending visitors to https"


def check(url: str) -> bool:
	"""Print the result for one address. True when everything is in place."""
	https = urllib.parse.urlsplit(url).scheme == "https"
	print(url)
	try:
		status, headers = fetch(url)
	except OSError as error:
		print(f"  FAIL  could not reach the address ({error})")
		return False

	print(f"  (the server answered {status})")
	ok = True
	for what, passed, found in evaluate(headers, https):
		print(f"  {'PASS' if passed else 'FAIL'}  {what}\n          {found}")
		ok = ok and passed

	if https:
		redirects, found = check_redirect(url)
		if redirects is None:
			print(f"  SKIP  Plain http redirects to https\n          {found}")
		else:
			print(f"  {'PASS' if redirects else 'FAIL'}  Plain http redirects to https\n          {found}")
			ok = ok and redirects
	return ok


def main(addresses: list[str]) -> int:
	if not addresses:
		print(__doc__)
		return 2
	results = [check(address) for address in addresses]
	print()
	print(
		"All protections are in place." if all(results) else "Some protections are missing: see FAIL above."
	)
	return 0 if all(results) else 1


if __name__ == "__main__":
	sys.exit(main(sys.argv[1:]))
