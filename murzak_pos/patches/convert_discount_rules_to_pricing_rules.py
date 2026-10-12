"""Move item and item group discounts into ERPNext Pricing Rules.

Discounts on an item or an item group used to be kept in this app's own Inventory Discount Rule records, so only
the app's till knew about them. They are now Pricing Rules, which ERPNext applies on every sale (see
murzak_pos/api/discount_rules.py). This copies each such rule across, keeping its percentage or amount, warehouse,
dates, priority and whether it was switched on. Where two rules for the same item would tie, the one changed more
recently wins, as it did before: it is given the next stronger free priority. The old record is then switched off and marked with the Pricing
Rule that replaced it, so nothing is applied twice and nothing is lost. Batch discounts stay where they are.

A rule that cannot be copied (for example its item has since been deleted) is logged in the Error Log and left
as it was; the rest carry on.
"""

import frappe

MOVED_NOTE = "[Moved to Pricing Rule "


def execute():
	if not frappe.db.exists("DocType", "Inventory Discount Rule"):
		return

	from murzak_pos.api import discount_rules

	rules = frappe.get_all(
		"Inventory Discount Rule",
		filters={"rule_type": ["in", list(discount_rules.KINDS)]},
		fields=[
			"name",
			"rule_type",
			"item_code",
			"item_group",
			"company",
			"warehouse",
			"discount_type",
			"discount_value",
			"priority",
			"is_active",
			"valid_from",
			"valid_upto",
			"description",
		],
		order_by="modified asc",
	)
	moved, failed = 0, []
	for rule in rules:
		if MOVED_NOTE in (rule.description or ""):
			continue
		try:
			# Two old rules could share a priority and the newest won; ERPNext refuses a tie, so the one changed later moves up
			priority = rule.priority
			if rule.is_active:
				priority = discount_rules.free_priority(
					rule.company,
					rule.rule_type,
					rule.item_code,
					rule.item_group,
					rule.warehouse,
					rule.valid_from,
					rule.valid_upto,
					rule.priority,
				)
				if priority is None:
					frappe.throw("No free priority is left for this rule")
			created = discount_rules.create_rule(
				rule_type=rule.rule_type,
				company=rule.company,
				discount_type=rule.discount_type,
				discount_value=rule.discount_value,
				item_code=rule.item_code,
				item_group=rule.item_group,
				warehouse=rule.warehouse,
				priority=priority,
				is_active=rule.is_active,
				valid_from=rule.valid_from,
				valid_upto=rule.valid_upto,
				description=rule.description,
			)
		except Exception:
			failed.append(rule.name)
			frappe.log_error(
				title=f"Discount rule {rule.name} could not move to a Pricing Rule",
				message=frappe.get_traceback(),
			)
			continue
		note = f"{(rule.description or '').strip()} {MOVED_NOTE}{created['name']}]".strip()
		frappe.db.set_value(
			"Inventory Discount Rule",
			rule.name,
			{"is_active": 0, "description": note},
			update_modified=False,
		)
		moved += 1

	print(f"Discount rules moved to Pricing Rules: {moved}; left as they were: {len(failed)} {failed or ''}")
