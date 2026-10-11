# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for the security headers the backend adds to its responses, and for the checker that the person
looking after the server runs to confirm the setup. No database or real site is needed: responses are
built by hand and the checker talks to a tiny web server started just for the test.
"""

import contextlib
import importlib.util
import io
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock, patch

import frappe
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Response

from murzak_pos import security_headers

_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "check_security_headers.py")
_spec = importlib.util.spec_from_file_location("check_security_headers", _SCRIPT)
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)


def request(**headers):
	return EnvironBuilder(path="/api/method/ping", headers=headers).get_request()


class TestBackendAddsSecurityHeaders(unittest.TestCase):
	def apply(self, response=None, req=None, conf=None):
		response = response or Response("ok")
		with patch.object(frappe, "conf", frappe._dict(conf or {})):
			security_headers.add_security_headers(response=response, request=req or request())
		return response.headers

	def test_the_basic_protections_are_added(self):
		headers = self.apply()
		self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
		self.assertEqual(headers["Referrer-Policy"], "strict-origin-when-cross-origin")
		self.assertEqual(headers["X-Frame-Options"], "SAMEORIGIN")
		self.assertEqual(headers["Content-Security-Policy"], "frame-ancestors 'self'")

	def test_the_secure_connection_rule_is_only_sent_over_https(self):
		self.assertNotIn("Strict-Transport-Security", self.apply(req=request()))
		for proto in ("https", "HTTPS", "https, http"):
			headers = self.apply(req=request(**{"X-Forwarded-Proto": proto}))
			self.assertEqual(headers["Strict-Transport-Security"], "max-age=31536000", proto)
		self.assertNotIn(
			"Strict-Transport-Security", self.apply(req=request(**{"X-Forwarded-Proto": "http"}))
		)

	def test_a_request_that_arrived_securely_gets_it_even_without_the_forwarded_header(self):
		secure = MagicMock(is_secure=True, headers={})
		self.assertIn("Strict-Transport-Security", self.apply(req=secure))

	def test_an_existing_policy_is_left_alone_and_no_framing_header_is_added(self):
		# Frappe's embeddable web forms set their own policy; adding a second would contradict it
		response = Response("form")
		response.headers["Content-Security-Policy"] = "frame-ancestors 'self' https://partner.example"
		headers = self.apply(response)
		self.assertEqual(headers["Content-Security-Policy"], "frame-ancestors 'self' https://partner.example")
		self.assertNotIn("X-Frame-Options", headers)

	def test_values_already_on_the_response_are_never_changed(self):
		response = Response("ok")
		response.headers["X-Frame-Options"] = "DENY"
		response.headers["Referrer-Policy"] = "no-referrer"
		response.headers["Strict-Transport-Security"] = "max-age=300"
		headers = self.apply(response, req=request(**{"X-Forwarded-Proto": "https"}))
		self.assertEqual(headers["X-Frame-Options"], "DENY")
		self.assertEqual(headers["Referrer-Policy"], "no-referrer")
		self.assertEqual(headers["Strict-Transport-Security"], "max-age=300")

	def test_it_can_be_switched_off_for_a_site(self):
		headers = self.apply(conf={"murzak_pos_security_headers": 0})
		for name in (
			"X-Content-Type-Options",
			"X-Frame-Options",
			"Content-Security-Policy",
			"Referrer-Policy",
		):
			self.assertNotIn(name, headers)

	def test_the_switch_set_to_one_keeps_it_on(self):
		self.assertIn("X-Frame-Options", self.apply(conf={"murzak_pos_security_headers": 1}))

	def test_a_problem_never_breaks_the_response(self):
		broken = MagicMock()
		broken.headers.__contains__.side_effect = RuntimeError("boom")
		with patch.object(frappe, "conf", frappe._dict()), patch.object(frappe, "logger", create=True):
			security_headers.add_security_headers(response=broken, request=request())  # must not raise

	def test_no_response_is_fine(self):
		with patch.object(frappe, "conf", frappe._dict()):
			security_headers.add_security_headers(response=None, request=request())

	def test_the_hook_is_registered(self):
		from murzak_pos import hooks

		self.assertIn("murzak_pos.security_headers.add_security_headers", hooks.after_request)
		self.assertTrue(callable(security_headers.add_security_headers))


GOOD = {
	"x-frame-options": "DENY",
	"content-security-policy": "frame-ancestors 'none'",
	"x-content-type-options": "nosniff",
	"referrer-policy": "strict-origin-when-cross-origin",
	"strict-transport-security": "max-age=31536000",
}


class TestTheChecker(unittest.TestCase):
	def outcome(self, headers, https=True):
		return {what: passed for what, passed, _found in checker.evaluate(headers, https)}

	def test_a_fully_protected_site_passes_everything(self):
		self.assertTrue(all(self.outcome(GOOD).values()))
		self.assertEqual(len(self.outcome(GOOD)), 4)

	def test_a_site_with_no_headers_fails_everything(self):
		self.assertFalse(any(self.outcome({}).values()))

	def test_either_framing_header_is_enough(self):
		only_old = {**GOOD}
		del only_old["content-security-policy"]
		only_new = {**GOOD}
		del only_new["x-frame-options"]
		for headers in (only_old, only_new):
			self.assertTrue(self.outcome(headers)["Other websites cannot show this page inside their own"])

	def test_a_policy_with_no_frame_rule_does_not_count(self):
		headers = {**GOOD, "content-security-policy": "default-src 'self'"}
		del headers["x-frame-options"]
		self.assertFalse(self.outcome(headers)["Other websites cannot show this page inside their own"])

	def test_a_short_secure_connection_rule_fails_and_says_what_to_do(self):
		headers = {**GOOD, "strict-transport-security": "max-age=300"}
		results = {what: (passed, found) for what, passed, found in checker.evaluate(headers, True)}
		passed, found = results["Browsers always use a secure connection"]
		self.assertFalse(passed)
		self.assertIn("raise it", found)

	def test_the_secure_connection_rule_is_not_expected_on_plain_http(self):
		self.assertNotIn("Browsers always use a secure connection", self.outcome({}, https=False))

	def test_a_longer_value_with_extra_parts_passes(self):
		headers = {**GOOD, "strict-transport-security": "max-age=63072000; includeSubDomains"}
		self.assertTrue(self.outcome(headers)["Browsers always use a secure connection"])


class _Handler(BaseHTTPRequestHandler):
	def do_GET(self):
		if self.path == "/moved":
			self.send_response(301)
			self.send_header("Location", "https://example.test/")
		elif self.path == "/open":
			self.send_response(200)
		else:
			self.send_response(200)
			for name, value in GOOD.items():
				self.send_header(name, value)
		self.end_headers()

	def log_message(self, *args):
		pass


class TestTheCheckerAgainstARealServer(unittest.TestCase):
	def setUp(self):
		self.server = HTTPServer(("127.0.0.1", 0), _Handler)
		threading.Thread(target=self.server.serve_forever, daemon=True).start()
		self.addCleanup(self.server.server_close)
		self.addCleanup(self.server.shutdown)
		self.base = f"http://127.0.0.1:{self.server.server_port}"

	def run_check(self, *addresses):
		out = io.StringIO()
		with contextlib.redirect_stdout(out):
			code = checker.main(list(addresses))
		return code, out.getvalue()

	def test_a_protected_address_passes(self):
		code, text = self.run_check(self.base + "/")
		self.assertEqual(code, 0, text)
		self.assertNotIn("FAIL", text)

	def test_an_unprotected_address_fails(self):
		code, text = self.run_check(self.base + "/open")
		self.assertEqual(code, 1)
		self.assertIn("FAIL", text)

	def test_one_bad_address_among_good_ones_fails_the_run(self):
		code, _text = self.run_check(self.base + "/", self.base + "/open")
		self.assertEqual(code, 1)

	def test_an_address_that_cannot_be_reached_fails(self):
		code, text = self.run_check("http://127.0.0.1:1/")
		self.assertEqual(code, 1)
		self.assertIn("could not reach", text)

	def test_a_redirect_is_reported_as_it_is_not_followed(self):
		status, headers = checker.fetch(self.base + "/moved")
		self.assertEqual(status, 301)
		self.assertEqual(headers["location"], "https://example.test/")

	def test_plain_http_that_sends_visitors_to_https_passes(self):
		ok, _found = checker.check_redirect(f"https://127.0.0.1:{self.server.server_port}/moved")
		self.assertTrue(ok)

	def test_plain_http_that_just_answers_fails(self):
		ok, found = checker.check_redirect(f"https://127.0.0.1:{self.server.server_port}/open")
		self.assertFalse(ok)
		self.assertIn("instead of sending visitors to https", found)

	def test_no_addresses_prints_how_to_use_it(self):
		code, text = self.run_check()
		self.assertEqual(code, 2)
		self.assertIn("check_security_headers.py", text)


if __name__ == "__main__":
	unittest.main()
