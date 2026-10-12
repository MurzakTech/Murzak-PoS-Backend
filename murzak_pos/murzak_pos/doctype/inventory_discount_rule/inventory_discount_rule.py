from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, nowdate


class InventoryDiscountRule(Document):
	"""
	A discount on one batch of stock.

	Discounts on an item or an item group are ERPNext Pricing Rules (see murzak_pos.api.discount_rules); ERPNext
	has no Pricing Rule for a batch, so this record keeps that one kind.
	"""

	def validate(self):
		self._validate_rule_target()
		self._validate_discount_value()
		self._validate_dates()

	def _validate_rule_target(self):
		if self.rule_type != "Batch":
			frappe.throw(
				_("A discount on an item or an item group is a Pricing Rule. Make a Pricing Rule instead.")
			)
		if not self.batch_no:
			frappe.throw(_("Batch is required for rule type Batch"))

	def _validate_discount_value(self):
		if not self.discount_value or self.discount_value <= 0:
			frappe.throw(_("Discount Value must be greater than 0"))

		if self.discount_type == "Percentage" and self.discount_value > 100:
			frappe.throw(_("Percentage discount cannot exceed 100"))

	def _validate_dates(self):
		if self.valid_from and self.valid_upto:
			if getdate(self.valid_from) > getdate(self.valid_upto):
				frappe.throw(_("Valid Upto must be after Valid From"))


def get_applicable_inventory_discount(
	item_code: str,
	company: str,
	warehouse: str | None = None,
	batch_no: str | None = None,
	item_group: str | None = None,
	posting_date: str | None = None,
) -> dict | None:
	"""
	Return the batch discount for the given batch, if there is one.

	Only batch discounts are kept in this record. A discount on the item or its group is a Pricing Rule, which
	ERPNext applies itself on the sale. `item_code` and `item_group` are accepted so callers need not change.
	Within the batch, a warehouse rule applies only in its warehouse, and a lower priority number wins (default 10).

	If the DocType isn't installed, returns None.
	"""

	if not batch_no or not company:
		return None

	# Allow murzak_pos to run without this DocType if an admin removed it
	if not frappe.db.exists("DocType", "Inventory Discount Rule"):
		return None

	date = getdate(posting_date) if posting_date else getdate(nowdate())
	filters = {"company": company, "rule_type": "Batch", "is_active": 1, "batch_no": batch_no}

	# Only apply warehouse-specific rules when they match
	if warehouse:
		filters["warehouse"] = ["in", ["", warehouse]]

	results = frappe.get_all(
		"Inventory Discount Rule",
		fields=[
			"name",
			"rule_type",
			"item_code",
			"batch_no",
			"item_group",
			"warehouse",
			"company",
			"discount_type",
			"discount_value",
			"priority",
			"valid_from",
			"valid_upto",
		],
		filters=filters,
		order_by="priority asc, modified desc",
	)

	for rule in results:
		if rule.valid_from and getdate(rule.valid_from) > date:
			continue
		if rule.valid_upto and getdate(rule.valid_upto) < date:
			continue
		if rule.warehouse and warehouse and rule.warehouse != warehouse:
			continue
		return rule

	return None
