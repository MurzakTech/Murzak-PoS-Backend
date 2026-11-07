"""
Whether M-Pesa can send a payment prompt to a customer's phone, and if not, what is missing.

The till offered "Send prompt to phone" whenever M-Pesa was switched on, even with the Daraja keys or the passkey missing.
The cashier pressed it and got "Something went wrong". The till now asks first: when the prompt cannot work it is hidden
and the cashier uses the customer's M-Pesa code instead, and the owner is told in words what to add.

Kept free of Frappe imports so it can be tested on its own.
"""

from __future__ import annotations

# What a prompt needs, in the order an owner would fix it, with the words for each
NEEDED = (
	("consumer_key", "the Daraja consumer key"),
	("consumer_secret", "the Daraja consumer secret"),
	("passkey", "the Lipa na M-Pesa passkey"),
)


def missing(present) -> list[str]:
	"""Words for each thing a prompt needs that is not set. `present` maps a field name to whether it is filled in."""
	return [words for field, words in NEEDED if not present.get(field)]


def prompt_problem(present) -> str | None:
	"""One sentence for the owner, or None when a prompt can be sent."""
	gaps = missing(present)
	if not gaps:
		return None
	return (
		"M-Pesa prompts are not ready: add "
		+ ", ".join(gaps[:-1])
		+ (" and " if len(gaps) > 1 else "")
		+ gaps[-1]
		+ " in Settings > Payment gateways."
	)
