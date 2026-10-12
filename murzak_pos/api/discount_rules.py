"""
Item and item group discounts, kept as ERPNext Pricing Rules.

There is one discount engine: ERPNext's own Pricing Rules. A discount on an item or on an item group is a
Pricing Rule, so it applies on every sale, whether the sale is made at the till or in the Desk, and it can be
extended with ERPNext's promotional schemes and coupon codes later. The only discount this app keeps in its own
record (Inventory Discount Rule) is the discount on a batch, which ERPNext has no rule for.

The Discounts screen still reads and writes the shape it always did (rule type Item, Item Group or Batch, a
percentage or an amount, a priority where a lower number goes first). This module translates that shape to and
from Pricing Rules. A Pricing Rule with more conditions than the screen understands (a customer group, a
minimum quantity, a price list, a free item) is left to the Desk: the screen neither lists nor changes it, but it
still applies on sales, and the till's preview shows its discount.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from murzak_pos.api.payment_gateway_common import get_user_companies

PRICING_RULE = "Pricing Rule"
KINDS = ("Item", "Item Group")
APPLY_ON = {"Item": "Item Code", "Item Group": "Item Group"}
KIND_OF = {"Item Code": "Item", "Item Group": "Item Group"}
DISCOUNT_FIELDS = {"Percentage": "Discount Percentage", "Amount": "Discount Amount"}
DEFAULT_PRIORITY = 10
HIGHEST_ERPNEXT_PRIORITY = 20
SCREEN_PRIORITIES = 10
MAX_RULES_READ = 2000
TITLE_LENGTH = 140

RULE_FIELDS = [
	"name",
	"title",
	"apply_on",
	"disable",
	"rate_or_discount",
	"discount_percentage",
	"discount_amount",
	"warehouse",
	"company",
	"valid_from",
	"valid_upto",
	"priority",
	"min_qty",
	"applicable_for",
	"for_price_list",
	"condition",
	"modified",
]


# --- priority ------------------------------------------------------------------------------------


def clamp_priority(priority) -> int:
	"""A screen priority held to 1 to 10 (1 goes first); a blank one is the default, 10."""
	number = DEFAULT_PRIORITY if priority in (None, "") else cint(priority)
	return max(1, min(SCREEN_PRIORITIES, number))


def pricing_priority(priority, rule_type: str) -> str:
	"""
	The Pricing Rule priority for a screen priority.

	The screen goes lowest number first (1 beats 10); ERPNext goes highest number first (20 beats 1) and only
	takes 1 to 20. The two are mirrored. An item discount takes ERPNext's upper half (11 to 20) and an item group
	discount the lower half (1 to 10), so an item discount always beats a group discount, as it always has, and the
	two kinds can never tie (ERPNext refuses to complete a sale when two matching rules tie).
	"""
	number = clamp_priority(priority)
	if rule_type == "Item":
		return str(HIGHEST_ERPNEXT_PRIORITY + 1 - number)
	return str(SCREEN_PRIORITIES + 1 - number)


def screen_priority(pricing_rule_priority, rule_type: str) -> int:
	"""The screen priority for a Pricing Rule priority. A rule with none set goes last."""
	number = cint(pricing_rule_priority)
	if number <= 0:
		return SCREEN_PRIORITIES
	if rule_type == "Item":
		return clamp_priority(HIGHEST_ERPNEXT_PRIORITY + 1 - number)
	return clamp_priority(SCREEN_PRIORITIES + 1 - number)


# --- checking what the screen sends --------------------------------------------------------------


def check_values(
	rule_type, item_code, item_group, discount_type, discount_value, valid_from, valid_upto
) -> None:
	"""The checks the Inventory Discount Rule has always made, for a discount that becomes a Pricing Rule."""
	if rule_type == "Item" and not item_code:
		frappe.throw(_("Item is required for rule type Item"), frappe.ValidationError)
	if rule_type == "Item Group" and not item_group:
		frappe.throw(_("Item Group is required for rule type Item Group"), frappe.ValidationError)
	if discount_type not in DISCOUNT_FIELDS:
		frappe.throw(_("Discount Type must be Percentage or Amount"), frappe.ValidationError)
	if not flt(discount_value) or flt(discount_value) <= 0:
		frappe.throw(_("Discount Value must be greater than 0"), frappe.ValidationError)
	if discount_type == "Percentage" and flt(discount_value) > 100:
		frappe.throw(_("Percentage discount cannot exceed 100"), frappe.ValidationError)
	if valid_from and valid_upto and getdate(valid_from) > getdate(valid_upto):
		frappe.throw(_("Valid Upto must be after Valid From"), frappe.ValidationError)


# --- reading Pricing Rules in the screen's shape -------------------------------------------------


def _children(names: list[str]) -> dict[str, dict[str, list[str]]]:
	"""The items and item groups each Pricing Rule names."""
	found = {name: {"items": [], "groups": []} for name in names}
	if not names:
		return found
	for row in frappe.db.get_all(
		"Pricing Rule Item Code", filters={"parent": ["in", names]}, fields=["parent", "item_code"]
	):
		found[row.parent]["items"].append(row.item_code)
	for row in frappe.db.get_all(
		"Pricing Rule Item Group", filters={"parent": ["in", names]}, fields=["parent", "item_group"]
	):
		found[row.parent]["groups"].append(row.item_group)
	return found


def _is_simple(row: dict, children: dict[str, list[str]]) -> bool:
	"""A rule the screen can show and change: one item or one group, a plain percentage or amount, no conditions."""
	if row.apply_on not in KIND_OF or row.rate_or_discount not in DISCOUNT_FIELDS.values():
		return False
	if row.applicable_for or row.for_price_list or row.condition or flt(row.min_qty) > 1:
		return False
	listed = children["items"] if row.apply_on == "Item Code" else children["groups"]
	return len(listed) == 1


def serialize(row: dict, children: dict[str, list[str]]) -> dict:
	"""A Pricing Rule in the shape the Discounts screen reads."""
	kind = KIND_OF[row.apply_on]
	percentage = row.rate_or_discount == "Discount Percentage"
	return {
		"name": row.name,
		"rule_type": kind,
		"item_code": children["items"][0] if kind == "Item" and children["items"] else None,
		"batch_no": None,
		"item_group": children["groups"][0] if kind == "Item Group" and children["groups"] else None,
		"warehouse": row.warehouse,
		"company": row.company,
		"discount_type": "Percentage" if percentage else "Amount",
		"discount_value": flt(row.discount_percentage if percentage else row.discount_amount),
		"priority": screen_priority(row.priority, kind),
		"is_active": 0 if row.disable else 1,
		"valid_from": row.valid_from,
		"valid_upto": row.valid_upto,
		"description": row.title,
		"modified": row.modified,
	}


def serialize_doc(doc) -> dict:
	"""A Pricing Rule document in the screen's shape."""
	children = {
		"items": [row.item_code for row in doc.get("items")],
		"groups": [row.item_group for row in doc.get("item_groups")],
	}
	return serialize(doc, children)


def companies_in_scope(company: str | None) -> list[str] | None:
	"""The company asked for, else the businesses the caller belongs to. None means every business (the Administrator)."""
	if company:
		return [company]
	if frappe.session.user == "Administrator":
		return None
	return sorted(get_user_companies())


def simple_rules(company: str | None = None, name: str | None = None) -> list[dict]:
	"""Every Pricing Rule the screen manages, in the screen's shape, for the company or the caller's businesses."""
	filters: dict = {
		"selling": 1,
		"price_or_product_discount": "Price",
		"mixed_conditions": 0,
		"coupon_code_based": 0,
	}
	if name:
		filters["name"] = name
	scope = companies_in_scope(company)
	if scope is not None:
		if not scope:
			return []
		filters["company"] = ["in", scope]
	rows = frappe.db.get_all(
		PRICING_RULE, filters=filters, fields=RULE_FIELDS, limit_page_length=MAX_RULES_READ
	)
	children = _children([row.name for row in rows])
	return [serialize(row, children[row.name]) for row in rows if _is_simple(row, children[row.name])]


def find_simple_rule(name: str) -> dict | None:
	"""One Pricing Rule in the screen's shape, or None when it does not exist or the screen does not manage it."""
	rules = simple_rules(name=name) if name else []
	return rules[0] if rules else None


def matches(rule: dict, item_code=None, item_group=None, warehouse=None, is_active=None) -> bool:
	"""Whether a rule fits the filters the list screen sends."""
	if item_code and rule["item_code"] != item_code:
		return False
	if item_group and rule["item_group"] != item_group:
		return False
	if warehouse and rule["warehouse"] != warehouse:
		return False
	if is_active is not None and cint(rule["is_active"]) != cint(is_active):
		return False
	return True


# --- two rules that would collide at the till ---------------------------------------------------


def _overlap(a_from, a_upto, b_from, b_upto) -> bool:
	"""Whether two date ranges share a day. A missing end means open-ended."""
	if a_upto and b_from and getdate(a_upto) < getdate(b_from):
		return False
	if b_upto and a_from and getdate(b_upto) < getdate(a_from):
		return False
	return True


def _related_groups(group: str) -> set[str]:
	"""An item group with the groups above and below it: a sale of an item in one can match a rule on any of them."""
	from frappe.utils.nestedset import get_ancestors_of, get_descendants_of

	return {group, *get_ancestors_of("Item Group", group), *get_descendants_of("Item Group", group)}


def conflicting_rules(
	company, rule_type, item_code, item_group, warehouse, valid_from, valid_upto, priority, exclude=None
) -> list[str]:
	"""
	Other switched-on rules of the same kind that could apply to the same sale at the same priority.

	ERPNext refuses to complete a sale when two rules tie like this, so a tie is stopped when the rule is saved.
	Item rules and group rules are on different priority bands, so they never tie with each other.
	"""
	related = _related_groups(item_group) if rule_type == "Item Group" and item_group else None
	clashes = []
	for other in simple_rules(company):
		if other["name"] == exclude or not other["is_active"] or other["rule_type"] != rule_type:
			continue
		if rule_type == "Item":
			if other["item_code"] != item_code:
				continue
		elif other["item_group"] not in (related or {item_group}):
			continue
		if clamp_priority(other["priority"]) != clamp_priority(priority):
			continue
		if other["warehouse"] and warehouse and other["warehouse"] != warehouse:
			continue
		if not _overlap(other["valid_from"], other["valid_upto"], valid_from, valid_upto):
			continue
		clashes.append(other["name"])
	return clashes


def free_priority(
	company, rule_type, item_code, item_group, warehouse, valid_from, valid_upto, wanted
) -> int | None:
	"""The screen priority nearest the wanted one (stronger first) that no clashing rule uses, or None."""
	wanted = clamp_priority(wanted)
	for priority in [*range(wanted, 0, -1), *range(wanted + 1, SCREEN_PRIORITIES + 1)]:
		if not conflicting_rules(
			company, rule_type, item_code, item_group, warehouse, valid_from, valid_upto, priority
		):
			return priority
	return None


def _refuse_clash(
	company, rule_type, item_code, item_group, warehouse, valid_from, valid_upto, priority, exclude=None
) -> None:
	clashes = conflicting_rules(
		company, rule_type, item_code, item_group, warehouse, valid_from, valid_upto, priority, exclude
	)
	if clashes:
		frappe.throw(
			_(
				"Another discount on {0} that can apply at the same time has the same priority ({1}). Give one of them a different priority."
			).format(item_code if rule_type == "Item" else item_group, ", ".join(clashes)),
			frappe.ValidationError,
		)


# --- writing ------------------------------------------------------------------------------------


def _company_currency(company: str) -> str:
	return frappe.get_cached_value("Company", company, "default_currency")


def _set_target(doc, rule_type: str, item_code, item_group) -> None:
	"""Point a Pricing Rule at one item or one item group."""
	doc.apply_on = APPLY_ON[rule_type]
	doc.set("items", [])
	doc.set("item_groups", [])
	if rule_type == "Item":
		doc.append("items", {"item_code": item_code})
	else:
		doc.append("item_groups", {"item_group": item_group})


def _set_discount(doc, discount_type: str, discount_value) -> None:
	doc.rate_or_discount = DISCOUNT_FIELDS[discount_type]
	doc.discount_percentage = flt(discount_value) if discount_type == "Percentage" else 0
	doc.discount_amount = flt(discount_value) if discount_type == "Amount" else 0


def default_title(rule_type, item_code, item_group, discount_type, discount_value) -> str:
	target = item_code if rule_type == "Item" else item_group
	value = f"{flt(discount_value):g}" + ("%" if discount_type == "Percentage" else "")
	return f"{target} discount {value}"[:TITLE_LENGTH]


def create_rule(
	rule_type,
	company,
	discount_type,
	discount_value,
	item_code=None,
	item_group=None,
	warehouse=None,
	priority=None,
	is_active=1,
	valid_from=None,
	valid_upto=None,
	description=None,
) -> dict:
	"""Make a Pricing Rule for an item or item group discount. Returns it in the screen's shape."""
	check_values(rule_type, item_code, item_group, discount_type, discount_value, valid_from, valid_upto)
	if cint(is_active):
		_refuse_clash(company, rule_type, item_code, item_group, warehouse, valid_from, valid_upto, priority)
	doc = frappe.new_doc(PRICING_RULE)
	doc.title = (
		description or default_title(rule_type, item_code, item_group, discount_type, discount_value)
	)[:TITLE_LENGTH]
	doc.selling = 1
	doc.buying = 0
	doc.company = company
	doc.currency = _company_currency(company)
	doc.price_or_product_discount = "Price"
	_set_target(doc, rule_type, item_code, item_group)
	_set_discount(doc, discount_type, discount_value)
	doc.warehouse = warehouse or None
	doc.valid_from = valid_from or None
	doc.valid_upto = valid_upto or None
	doc.has_priority = 1
	doc.priority = pricing_priority(priority, rule_type)
	doc.disable = 0 if cint(is_active) else 1
	doc.insert(ignore_permissions=True)
	return serialize_doc(doc)


def update_rule(
	name,
	rule_type=None,
	company=None,
	discount_type=None,
	discount_value=None,
	item_code=None,
	item_group=None,
	warehouse=None,
	priority=None,
	is_active=None,
	valid_from=None,
	valid_upto=None,
	description=None,
) -> dict:
	"""Change a Pricing Rule the screen manages. Anything left as None stays as it was."""
	current = find_simple_rule(name)
	if not current:
		frappe.throw(
			_("Pricing Rule {0} cannot be changed here. Open it in the Desk.").format(name),
			frappe.ValidationError,
		)
	if rule_type == "Batch":
		frappe.throw(
			_("An item or item group discount cannot become a batch discount. Make a new batch discount."),
			frappe.ValidationError,
		)

	wanted = {
		"rule_type": rule_type or current["rule_type"],
		"item_code": current["item_code"] if item_code is None else item_code,
		"item_group": current["item_group"] if item_group is None else item_group,
		"discount_type": discount_type or current["discount_type"],
		"discount_value": current["discount_value"] if discount_value is None else flt(discount_value),
		"valid_from": current["valid_from"] if valid_from is None else valid_from,
		"valid_upto": current["valid_upto"] if valid_upto is None else valid_upto,
	}
	check_values(**wanted)
	becomes_active = cint(is_active) if is_active is not None else current["is_active"]
	if becomes_active:
		_refuse_clash(
			company or current["company"],
			wanted["rule_type"],
			wanted["item_code"],
			wanted["item_group"],
			current["warehouse"] if warehouse is None else warehouse,
			wanted["valid_from"],
			wanted["valid_upto"],
			current["priority"] if priority is None else priority,
			exclude=name,
		)

	doc = frappe.get_doc(PRICING_RULE, name)
	if company:
		doc.company = company
		doc.currency = _company_currency(company)
	_set_target(doc, wanted["rule_type"], wanted["item_code"], wanted["item_group"])
	_set_discount(doc, wanted["discount_type"], wanted["discount_value"])
	if warehouse is not None:
		doc.warehouse = warehouse or None
	if priority is not None or wanted["rule_type"] != current["rule_type"]:
		doc.has_priority = 1
		doc.priority = pricing_priority(
			current["priority"] if priority is None else priority, wanted["rule_type"]
		)
	if is_active is not None:
		doc.disable = 0 if cint(is_active) else 1
	doc.valid_from = wanted["valid_from"] or None
	doc.valid_upto = wanted["valid_upto"] or None
	if description is not None:
		doc.title = (
			description
			or default_title(
				wanted["rule_type"],
				wanted["item_code"],
				wanted["item_group"],
				wanted["discount_type"],
				wanted["discount_value"],
			)
		)[:TITLE_LENGTH]
	doc.save(ignore_permissions=True)
	return serialize_doc(doc)


def delete_rule(name: str) -> None:
	if not find_simple_rule(name):
		frappe.throw(
			_("Pricing Rule {0} cannot be deleted here. Open it in the Desk.").format(name),
			frappe.ValidationError,
		)
	frappe.delete_doc(PRICING_RULE, name, ignore_permissions=True)


# --- what a sale will charge: the till's preview -------------------------------------------------


def _selling_price_list(company: str) -> str | None:
	profile_list = frappe.db.get_value(
		"POS Profile", {"company": company, "disabled": 0}, "selling_price_list", order_by="creation asc"
	)
	return profile_list or frappe.db.get_single_value("Selling Settings", "selling_price_list")


def pricing_rule_discounts(
	rows: list[dict], company: str, posting_date: str | None = None
) -> list[dict | None]:
	"""
	The discount ERPNext will give each row if it were sold, one answer per row (None when there is none).

	This asks ERPNext's own pricing engine with a quantity of one, so the till shows what the sale will charge.
	Rules that need more (a larger quantity, a customer group, a coupon) do not show here and still apply at the sale.
	"""
	from erpnext.accounts.doctype.pricing_rule.pricing_rule import apply_pricing_rule

	if not rows:
		return []
	currency = _company_currency(company)
	price_list = _selling_price_list(company)
	items = []
	for position, row in enumerate(rows):
		items.append(
			{
				"doctype": "Sales Invoice Item",
				"name": f"preview-{position}",
				"parent": "preview",
				"parenttype": "Sales Invoice",
				"item_code": row["item_code"],
				"item_group": row["item_group"],
				"warehouse": row.get("warehouse"),
				"qty": 1,
				"stock_qty": 1,
				"uom": frappe.db.get_value("Item", row["item_code"], "stock_uom"),
				"stock_uom": frappe.db.get_value("Item", row["item_code"], "stock_uom"),
				"conversion_factor": 1,
			}
		)
	args = {
		"items": items,
		"doctype": "Sales Invoice",
		"company": company,
		"currency": currency,
		"conversion_rate": 1,
		"price_list": price_list,
		"price_list_currency": currency,
		"plc_conversion_rate": 1,
		"transaction_date": posting_date or nowdate(),
		"ignore_pricing_rule": 0,
		"is_return": 0,
	}
	answers = apply_pricing_rule(args) or []
	by_name = {answer.get("name"): answer for answer in answers}

	found: list[dict | None] = []
	for position, row in enumerate(rows):
		answer = by_name.get(f"preview-{position}") or {}
		applied = frappe.parse_json(answer.get("pricing_rules") or "[]")
		percentage, amount = flt(answer.get("discount_percentage")), flt(answer.get("discount_amount"))
		if not applied or not (percentage or amount):
			found.append(None)
			continue
		found.append(_shape_applied(applied[0], row, percentage, amount))
	return found


def _shape_applied(name: str, row: dict, percentage: float, amount: float) -> dict:
	"""The rule ERPNext applied, in the screen's shape. It may be a rule the screen does not manage."""
	managed = find_simple_rule(name)
	if managed:
		return managed
	rule = frappe.db.get_value(
		PRICING_RULE,
		name,
		["title", "apply_on", "warehouse", "company", "valid_from", "valid_upto", "priority"],
		as_dict=True,
	)
	kind = KIND_OF.get(rule.apply_on, "Item") if rule else "Item"
	return {
		"name": name,
		"rule_type": kind,
		"item_code": row["item_code"] if kind == "Item" else None,
		"batch_no": None,
		"item_group": row["item_group"] if kind == "Item Group" else None,
		"warehouse": rule.warehouse if rule else None,
		"company": rule.company if rule else None,
		"discount_type": "Percentage" if percentage else "Amount",
		"discount_value": percentage or amount,
		"priority": screen_priority(rule.priority, kind) if rule else SCREEN_PRIORITIES,
		"is_active": 1,
		"valid_from": rule.valid_from if rule else None,
		"valid_upto": rule.valid_upto if rule else None,
		"description": rule.title if rule else None,
	}


def discounts_for(rows: list[dict], company: str, posting_date: str | None = None) -> list[dict | None]:
	"""
	The discount each row would get, as the screen shows it: a batch discount first, else ERPNext's pricing engine.

	Each row has item_code and item_group, and may have warehouse and batch_no.
	"""
	from murzak_pos.murzak_pos.doctype.inventory_discount_rule.inventory_discount_rule import (
		get_applicable_inventory_discount,
	)

	answers: list[dict | None] = [None] * len(rows)
	pending: list[int] = []
	for position, row in enumerate(rows):
		batch_rule = get_applicable_inventory_discount(
			item_code=row["item_code"],
			company=company,
			warehouse=row.get("warehouse"),
			batch_no=row.get("batch_no"),
			item_group=row.get("item_group"),
			posting_date=posting_date,
		)
		if batch_rule:
			answers[position] = batch_rule
		else:
			pending.append(position)
	for position, answer in zip(
		pending, pricing_rule_discounts([rows[i] for i in pending], company, posting_date), strict=True
	):
		answers[position] = answer
	return answers
