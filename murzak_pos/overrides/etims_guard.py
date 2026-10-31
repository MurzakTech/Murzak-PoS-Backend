"""
Keep the Kenya tax app (eTIMS) out of supplier bills for a business that does not use eTIMS.

The Kenya tax app attaches four steps to every Purchase Invoice: one when it is saved, one when it is checked, one
when it is submitted and one when it is cancelled. All four assume the business is registered with KRA eTIMS: the check
reads the bill's "branch" and refuses a bill without one, and the submit step sends the bill to KRA. But "branch" is an
accounting dimension the tax app adds only when a business's eTIMS settings are first activated. A business with no
active eTIMS settings has no such field, so every supplier bill failed with
"AttributeError: 'PurchaseInvoice' object has no attribute 'branch'", and goods could not be received.

For a business with no active eTIMS settings these steps do nothing, because there is nothing to send to KRA. A business
that does use eTIMS is not changed at all: its bills go through the tax app's steps as before. Other documents (sales
invoices, items) are never touched here: the same shared steps still run for them as before.

install() is called when the Purchase Invoice class is loaded (overrides/purchase_invoice_class.py), which is before any
step can run, and again harmlessly in every process. The tax app is found by name when it is installed; when it is not,
nothing happens.
"""

from __future__ import annotations

import functools
import importlib
from collections.abc import Callable

# The tax app's steps for a Purchase Invoice, as its hooks name them. Two are shared with Sales Invoice; the guard acts only on a
# Purchase Invoice and passes every other document straight to the tax app.
GUARDED_STEPS = (
	("kenya_compliance.kenya_compliance.utils", "before_save_"),
	("kenya_compliance.kenya_compliance.overrides.server.purchase_invoice", "validate"),
	("kenya_compliance.kenya_compliance.overrides.server.purchase_invoice", "on_submit"),
	("kenya_compliance.kenya_compliance.overrides.server.sales_invoice", "before_cancel"),
)
GUARDED_DOCTYPES = ("Purchase Invoice",)
MARK = "_murzak_etims_guard"


def guard(step: Callable, uses_etims: Callable[[str | None], bool]) -> Callable:
	"""`step` wrapped so a supplier bill of a business without eTIMS skips it. A wrapped step is not wrapped twice."""
	if getattr(step, MARK, False):
		return step

	@functools.wraps(step)
	def guarded(doc, *args, **kwargs):
		if getattr(doc, "doctype", None) in GUARDED_DOCTYPES and not uses_etims(doc.get("company")):
			return None
		return step(doc, *args, **kwargs)

	setattr(guarded, MARK, True)
	return guarded


def install(import_module: Callable = importlib.import_module, uses_etims: Callable | None = None) -> int:
	"""Wrap the tax app's steps. Returns how many were wrapped now (0 when the app is absent or they were already wrapped)."""
	if uses_etims is None:
		from murzak_pos.api.etims_optional import company_uses_etims as uses_etims

	wrapped = 0
	for module_name, step_name in GUARDED_STEPS:
		try:
			module = import_module(module_name)
		except ImportError:
			continue
		step = getattr(module, step_name, None)
		if not callable(step) or getattr(step, MARK, False):
			continue
		setattr(module, step_name, guard(step, uses_etims))
		wrapped += 1
	return wrapped
