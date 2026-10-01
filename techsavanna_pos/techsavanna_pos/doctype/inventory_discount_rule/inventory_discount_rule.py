from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, nowdate


class InventoryDiscountRule(Document):
	"""Inventory-level discount rule for items, batches, or item groups."""

	def validate(self):
		self._validate_rule_target()
		self._validate_discount_value()
		self._validate_dates()

	def _validate_rule_target(self):
		if self.rule_type == "Item" and not self.item_code:
			frappe.throw(_("Item is required for rule type Item"))
		if self.rule_type == "Batch" and not self.batch_no:
			frappe.throw(_("Batch is required for rule type Batch"))
		if self.rule_type == "Item Group" and not self.item_group:
			frappe.throw(_("Item Group is required for rule type Item Group"))

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
	Return the best matching inventory discount rule for the given item.
	Priority order (more specific first):
		1. Batch
		2. Item
		3. Item Group
	Within the same specificity, lower priority number wins (default 10).

	If the DocType isn't installed, returns None.
	"""

	if not item_code or not company:
		return None

	# Allow techsavanna_pos to run without this DocType if an admin removed it
	if not frappe.db.exists("DocType", "Inventory Discount Rule"):
		return None

	date = getdate(posting_date) if posting_date else getdate(nowdate())

	specificity = [
		("Batch", batch_no),
		("Item", item_code),
		("Item Group", item_group),
	]

	for rule_type, value in specificity:
		if not value:
			continue

		filters = {
			"company": company,
			"rule_type": rule_type,
			"is_active": 1,
		}

		if rule_type == "Batch":
			filters["batch_no"] = value
		elif rule_type == "Item":
			filters["item_code"] = value
		elif rule_type == "Item Group":
			filters["item_group"] = value

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


