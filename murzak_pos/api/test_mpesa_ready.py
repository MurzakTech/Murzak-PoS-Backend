import unittest

from murzak_pos.api.mpesa_ready import missing, prompt_problem

ALL = {"consumer_key": True, "consumer_secret": True, "passkey": True}


class TestMpesaReady(unittest.TestCase):
	def test_nothing_missing(self):
		self.assertEqual(missing(ALL), [])
		self.assertIsNone(prompt_problem(ALL))

	def test_one_missing_is_named(self):
		self.assertEqual(
			prompt_problem({**ALL, "passkey": False}),
			"M-Pesa prompts are not ready: add the Lipa na M-Pesa passkey in Settings > Payment gateways.",
		)

	def test_several_missing_read_as_a_list(self):
		self.assertEqual(
			prompt_problem({}),
			"M-Pesa prompts are not ready: add the Daraja consumer key, the Daraja consumer secret and the Lipa na M-Pesa passkey in Settings > Payment gateways.",
		)
