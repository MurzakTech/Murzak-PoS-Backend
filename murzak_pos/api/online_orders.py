"""
Online orders: the one place an order from the internet becomes a Sales Order in the POS.

Orders arrive from three channels: the hosted store ("Website"), a WooCommerce shop ("WooCommerce") and a
WhatsApp order ("WhatsApp"). Each channel calls `place_order`, which gives the same guarantees:

- the same order is never recorded twice (the website's own number is kept and checked);
- prices come from the POS, never from the customer's browser (only a WooCommerce order, already paid for at
  the shop's own price, keeps the price the website charged);
- an order for more than is in stock is refused before it is taken (a WooCommerce order is the exception: the
  customer has already paid, so it is recorded and flagged instead of being lost);
- the order reserves its stock, so the till and the website do not sell the same last item twice.

The calls at the bottom are for the shop's staff: see the orders, move them through fulfilment, and raise the
invoice.
"""

from __future__ import annotations

import json
import secrets

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import add_days, cint, cstr, flt, nowdate

from murzak_pos.api import online_catalogue
from murzak_pos.api.access_control import require_online_orders, require_online_orders_view
from murzak_pos.api.party_names import name_for_new_record
from murzak_pos.api.payment_gateway_common import resolve_company

CHANNELS = ("Website", "WooCommerce", "WhatsApp")
FULFILMENT = ("New", "Confirmed", "Ready", "Delivered", "Cancelled")
DELIVERY_METHODS = ("Delivery", "Pickup")
MAX_LINES = 50
MAX_QTY = 10000

# Each status may move to these
NEXT_STATUS = {
	"New": ("Confirmed", "Ready", "Cancelled"),
	"Confirmed": ("Ready", "Delivered", "Cancelled"),
	"Ready": ("Delivered", "Cancelled"),
	"Delivered": (),
	"Cancelled": (),
}


def clean_lines(lines) -> list[dict]:
	"""The order lines, checked and with repeated products added together."""
	if isinstance(lines, str):
		try:
			lines = json.loads(lines)
		except ValueError:
			lines = None
	if not isinstance(lines, list) or not lines:
		frappe.throw(_("Add at least one product to the order."), frappe.ValidationError)
	if len(lines) > MAX_LINES:
		frappe.throw(
			_("An order can have at most {0} different products.").format(MAX_LINES), frappe.ValidationError
		)

	merged: dict[str, dict] = {}
	for line in lines:
		if not isinstance(line, dict):
			frappe.throw(_("Each order line must be a product and a quantity."), frappe.ValidationError)
		code = cstr(line.get("item_code")).strip()
		qty = flt(line.get("qty"))
		# "not (0 < qty <= max)" also refuses a quantity that is not a number
		if not code or not (0 < qty <= MAX_QTY):
			frappe.throw(_("Each product needs a code and a quantity above zero."), frappe.ValidationError)
		entry = merged.setdefault(code, {"item_code": code, "qty": 0.0, "rate": None})
		entry["qty"] += qty
		if line.get("rate") is not None:
			entry["rate"] = flt(line.get("rate"))
	return list(merged.values())


def _item_for_order(company: str, code: str, shown_online_only: bool):
	filters = {"name": code, "custom_company": company, "disabled": 0}
	if shown_online_only:
		filters.update(online_catalogue.online_filters(company))
		filters["name"] = code
	return frappe.db.get_value(
		"Item", filters, ["name", "item_name", "standard_rate", "stock_uom", "is_stock_item"], as_dict=True
	)


DELIVERY_ITEM_NAME = "Delivery"


def delivery_item(company: str) -> str:
	"""
	The business's "Delivery" service item, which carries the delivery fee on an order. Made the first time it is needed.
	It is not a stock item and is kept off the online store's product list.
	"""
	found = frappe.db.get_value(
		"Item", {"custom_company": company, "item_name": DELIVERY_ITEM_NAME, "is_stock_item": 0}, "name"
	)
	if found:
		return found
	abbr = frappe.db.get_value("Company", company, "abbr") or company[:6]
	group = (
		frappe.db.get_value("Item Group", {"name": "Services"}, "name")
		or frappe.db.get_value("Item Group", {"is_group": 0}, "name")
		or "All Item Groups"
	)
	item = frappe.new_doc("Item")
	item.item_code = f"DELIVERY-{abbr}"
	item.item_name = DELIVERY_ITEM_NAME
	item.item_group = group
	item.stock_uom = "Nos"
	item.is_stock_item = 0
	item.is_sales_item = 1
	item.custom_company = company
	item.custom_show_online = 0
	item.insert(ignore_permissions=True)
	return item.name


def subtotal_for(company: str, lines) -> float:
	"""What these lines come to at the shop's prices (for "free delivery above..." and delivery quotes)."""
	total = 0.0
	for line in clean_lines(lines):
		item = _item_for_order(company, line["item_code"], shown_online_only=True)
		if not item:
			frappe.throw(_("Product {0} is not available.").format(line["item_code"]), frappe.ValidationError)
		total += flt(item.standard_rate) * line["qty"]
	return total


def customer_for(company: str, name: str | None, phone: str | None, email: str | None = None) -> str:
	"""The business's customer with this phone number, made on the first order."""
	phone = cstr(phone).strip()
	if phone:
		found = frappe.db.get_value(
			"Customer", {"custom_company": company, "mobile_no": phone, "disabled": 0}, "name"
		)
		if found:
			return found

	display = (cstr(name).strip() or phone or _("Online customer"))[:120]
	group = frappe.db.get_value("Customer Group", {"is_group": 0}, "name") or "All Customer Groups"
	territory = frappe.db.get_value("Territory", {"is_group": 0}, "name") or "All Territories"

	# The same name can belong to two different people, or to another business: try the plain name, then with the
	# last digits of the phone, then with a random tail
	suffixes = [None, phone[-4:] if phone else None, secrets.token_hex(2)]
	for suffix in suffixes:
		wanted = f"{display} {suffix}" if suffix else display
		record_name = name_for_new_record("Customer", wanted, company)
		if frappe.db.exists("Customer", record_name):
			continue
		customer = frappe.new_doc("Customer")
		customer.customer_name = record_name
		customer.customer_type = "Individual"
		customer.customer_group = group
		customer.territory = territory
		customer.custom_company = company
		if phone:
			customer.mobile_no = phone
		if email:
			customer.email_id = email
		customer.insert(ignore_permissions=True)
		return customer.name
	frappe.throw(_("Could not record the customer. Please try again."), frappe.ValidationError)


def place_order(
	company: str,
	channel: str,
	lines,
	customer_name: str | None = None,
	phone: str | None = None,
	email: str | None = None,
	delivery_method: str | None = None,
	address: str | None = None,
	payment: str | None = None,
	paid: bool = False,
	notes: str | None = None,
	external_ref: str | None = None,
	trust_prices: bool = False,
	warehouse: str | None = None,
	minimum_total: float = 0,
	delivery_fee: float = 0,
) -> dict:
	"""
	Record an online order as a Sales Order and return {"created", "order", "token", "total", "lines"}.

	`created` is False when this channel's `external_ref` was recorded before: the earlier order is returned.
	`trust_prices` is for a WooCommerce order only: the price the website charged is kept, and a shortage of stock
	is flagged instead of refusing the order.
	"""
	if channel not in CHANNELS:
		frappe.throw(_("Unknown order channel."), frappe.ValidationError)
	if delivery_method and delivery_method not in DELIVERY_METHODS:
		frappe.throw(_("Choose delivery or pickup."), frappe.ValidationError)

	external_ref = cstr(external_ref).strip()[:140] or None
	if external_ref:
		earlier = frappe.db.get_value(
			"Sales Order",
			{"company": company, "custom_online_channel": channel, "custom_online_ref": external_ref},
			["name", "custom_online_token", "grand_total"],
			as_dict=True,
		)
		if earlier:
			return {
				"created": False,
				"order": earlier.name,
				"token": earlier.custom_online_token,
				"total": flt(earlier.grand_total),
				"lines": [],
			}

	wanted = clean_lines(lines)
	warehouse = online_catalogue.default_warehouse(company, warehouse)
	if not warehouse:
		frappe.throw(_("This shop has no warehouse to take the order from."), frappe.ValidationError)

	found = []
	for line in wanted:
		item = _item_for_order(company, line["item_code"], shown_online_only=not trust_prices)
		if not item:
			frappe.throw(_("Product {0} is not available.").format(line["item_code"]), frappe.ValidationError)
		found.append((line, item))

	stock = online_catalogue.available_stock([i.name for _line, i in found if i.is_stock_item], warehouse)
	short = []
	for line, item in found:
		if item.is_stock_item and stock.get(item.name, 0.0) < line["qty"]:
			short.append(item.item_name)
	if short and not trust_prices:
		frappe.throw(_("Sorry, not enough stock for: {0}.").format(", ".join(short)), frappe.ValidationError)

	if minimum_total:
		subtotal = sum(
			(line["rate"] if (trust_prices and line["rate"] is not None) else flt(item.standard_rate))
			* line["qty"]
			for line, item in found
		)
		if subtotal < flt(minimum_total):
			frappe.throw(
				_("The smallest order this shop accepts is {0}.").format(flt(minimum_total)),
				frappe.ValidationError,
			)

	customer = customer_for(company, customer_name, phone, email)

	order = frappe.new_doc("Sales Order")
	order.company = company
	order.customer = customer
	order.order_type = "Sales"
	order.transaction_date = nowdate()
	order.delivery_date = add_days(nowdate(), 2)
	# The customer was shown a price: a discount rule must not change it afterwards
	order.ignore_pricing_rule = 1
	order.custom_online_channel = channel
	order.custom_online_ref = external_ref
	order.custom_online_token = secrets.token_urlsafe(12)
	order.custom_online_fulfilment = "New"
	order.custom_delivery_method = delivery_method
	order.custom_delivery_address = cstr(address).strip()[:500] or None
	order.custom_online_payment = cstr(payment).strip()[:140] or None
	order.custom_online_paid = 1 if paid else 0
	order.custom_online_contact = cstr(phone).strip()[:40] or None
	notes = cstr(notes).strip()[:500]
	if short:
		notes = (notes + " " if notes else "") + _("Stock was short for: {0}.").format(", ".join(short))
	for line, item in found:
		rate = line["rate"] if (trust_prices and line["rate"] is not None) else flt(item.standard_rate)
		order.append(
			"items",
			{
				"item_code": item.name,
				"qty": line["qty"],
				"rate": rate,
				"warehouse": warehouse,
				"delivery_date": order.delivery_date,
			},
		)
	if flt(delivery_fee) > 0:
		order.append(
			"items",
			{
				"item_code": delivery_item(company),
				"qty": 1,
				"rate": flt(delivery_fee),
				"delivery_date": order.delivery_date,
			},
		)
	order.flags.ignore_permissions = True
	order.insert()
	order.submit()

	if notes:
		order.add_comment("Comment", notes)

	from murzak_pos.api.realtime_events import notify_online_order

	notify_online_order(company, order.name, channel)

	return {
		"created": True,
		"order": order.name,
		"token": order.custom_online_token,
		"total": flt(order.grand_total),
		"delivery_fee": flt(delivery_fee),
		"lines": [
			{"item_code": i.item_code, "item_name": i.item_name, "qty": flt(i.qty), "rate": flt(i.rate)}
			for i in order.items
		],
	}


# ---------------------------------------------------------------------------
# Settling an online order at the till
# ---------------------------------------------------------------------------

# On POS Invoice and Sales Invoice: the order a sale settled, and the channel for reports
SALE_ORDER_FIELD = "custom_online_order"
SALE_CHANNEL_FIELD = "custom_sales_channel"
# On the Sales Order: the sale that settled it
ORDER_SALE_TYPE_FIELD = "custom_till_sale_type"
ORDER_SALE_FIELD = "custom_till_sale"


def check_order_for_till_sale(order: str, company: str) -> None:
	"""
	Refuse a till sale that names an online order it may not settle: one that is missing, belongs to another
	business, is cancelled, or was already settled by another sale. The order row is locked until the sale is saved, so
	two tills selling the same order at once cannot both pass.
	"""
	require_online_orders()
	row = frappe.db.get_value(
		"Sales Order",
		order,
		[
			"company",
			"docstatus",
			"status",
			"custom_online_channel",
			"custom_online_fulfilment",
			ORDER_SALE_FIELD,
		],
		as_dict=True,
		for_update=True,
	)
	if not row or row.company != company or not row.custom_online_channel:
		frappe.throw(_("Online order {0} was not found.").format(order), frappe.DoesNotExistError)
	if row.docstatus != 1 or row.custom_online_fulfilment == "Cancelled" or row.status == "Closed":
		frappe.throw(
			_("Online order {0} is cancelled or closed, so it cannot be sold.").format(order),
			frappe.ValidationError,
		)
	if row.get(ORDER_SALE_FIELD):
		frappe.throw(
			_("Online order {0} was already sold in {1}.").format(order, row.get(ORDER_SALE_FIELD)),
			frappe.ValidationError,
		)


def mark_sale_as_online(doc, order: str | None) -> None:
	"""Put the order and the Online channel on a sale document that is about to be saved."""
	if not order:
		return
	if doc.meta.has_field(SALE_ORDER_FIELD):
		doc.set(SALE_ORDER_FIELD, order)
	if doc.meta.has_field(SALE_CHANNEL_FIELD):
		doc.set(SALE_CHANNEL_FIELD, "Online")


def settle_order_with_sale(order: str, sale) -> None:
	"""
	The till sale for an online order went through: link the two, mark the order paid unless the sale left money
	owing, and close the order. Closing it frees the stock the order was holding, and the sale has already taken the
	stock out, so it is counted once.
	"""
	paid = flt(sale.get("outstanding_amount")) <= 0
	values = {ORDER_SALE_TYPE_FIELD: sale.doctype, ORDER_SALE_FIELD: sale.name}
	if paid:
		values["custom_online_paid"] = 1
	frappe.db.set_value("Sales Order", order, values)

	doc = frappe.get_doc("Sales Order", order)
	if doc.status != "Closed":
		doc.flags.ignore_permissions = True
		doc.update_status("Closed")


def settle_recorded_sale(order: str | None, company: str, doctype: str, name: str) -> None:
	"""
	A till retried a sale that was already recorded. If the first try was cut off before the order was settled, finish
	the job now, so the order never keeps holding stock the sale already took. Does nothing for a settled order.
	"""
	if not order:
		return
	require_online_orders()
	row = frappe.db.get_value(
		"Sales Order",
		order,
		["company", "custom_online_channel", ORDER_SALE_FIELD],
		as_dict=True,
		for_update=True,
	)
	if not row or row.company != company or not row.custom_online_channel or row.get(ORDER_SALE_FIELD):
		return
	settle_order_with_sale(order, frappe.get_doc(doctype, name))


# ---------------------------------------------------------------------------
# For the shop's staff
# ---------------------------------------------------------------------------


def _owned_order(order: str, company: str):
	row = frappe.db.get_value(
		"Sales Order",
		order,
		[
			"name",
			"company",
			"custom_online_channel",
			"custom_online_fulfilment",
			"docstatus",
			ORDER_SALE_FIELD,
		],
		as_dict=True,
	)
	if not row or row.company != company or not row.custom_online_channel:
		frappe.throw(_("Online order {0} was not found.").format(order), frappe.DoesNotExistError)
	return row


def cancel_order_document(order: str) -> None:
	"""Cancel the Sales Order, which frees the stock it reserved. Refused once it has been invoiced or delivered."""
	doc = frappe.get_doc("Sales Order", order)
	if flt(doc.per_billed) > 0 or flt(doc.per_delivered) > 0:
		frappe.throw(
			_("This order is already invoiced or delivered, so it cannot be cancelled."),
			frappe.ValidationError,
		)
	doc.flags.ignore_permissions = True
	doc.cancel()


@frappe.whitelist()
def list_online_orders(
	company: str | None = None,
	status: str | None = None,
	channel: str | None = None,
	page: int = 1,
	page_size: int = 20,
) -> dict:
	"""The shop's online orders, newest first. `status` and `channel` narrow the list."""
	require_online_orders_view()
	company = resolve_company(company)
	page = max(1, cint(page) or 1)
	page_size = min(100, max(1, cint(page_size) or 20))

	filters: dict = {"company": company, "docstatus": ["in", [1, 2]], "custom_online_channel": ["!=", ""]}
	if status:
		if status not in FULFILMENT:
			frappe.throw(_("Unknown order status."), frappe.ValidationError)
		filters["custom_online_fulfilment"] = status
	if channel:
		if channel not in CHANNELS:
			frappe.throw(_("Unknown order channel."), frappe.ValidationError)
		filters["custom_online_channel"] = channel

	total = frappe.db.count("Sales Order", filters)
	rows = frappe.get_all(
		"Sales Order",
		filters=filters,
		fields=[
			"name",
			"creation",
			"customer",
			"customer_name",
			"grand_total",
			"custom_online_channel as channel",
			"custom_online_ref as reference",
			"custom_online_fulfilment as status",
			"custom_delivery_method as delivery_method",
			"custom_delivery_address as address",
			"custom_online_payment as payment",
			"custom_online_paid as paid",
			"custom_online_contact as phone",
		],
		order_by="creation desc",
		limit_start=(page - 1) * page_size,
		limit_page_length=page_size,
	)
	return {"success": True, "data": {"rows": rows, "total": total, "page": page, "page_size": page_size}}


@frappe.whitelist()
def get_online_order(order: str, company: str | None = None) -> dict:
	"""One online order with its lines."""
	require_online_orders_view()
	company = resolve_company(company)
	_owned_order(order, company)
	doc = frappe.get_doc("Sales Order", order)
	return {
		"success": True,
		"data": {
			"name": doc.name,
			"customer": doc.customer,
			"customer_name": doc.customer_name,
			"phone": doc.get("custom_online_contact"),
			"channel": doc.get("custom_online_channel"),
			"reference": doc.get("custom_online_ref"),
			"status": doc.get("custom_online_fulfilment"),
			"delivery_method": doc.get("custom_delivery_method"),
			"address": doc.get("custom_delivery_address"),
			"payment": doc.get("custom_online_payment"),
			"paid": bool(doc.get("custom_online_paid")),
			"sold_in": doc.get(ORDER_SALE_FIELD) or None,
			"grand_total": flt(doc.grand_total),
			"lines": [
				{
					"item_code": i.item_code,
					"item_name": i.item_name,
					"qty": flt(i.qty),
					"rate": flt(i.rate),
					"amount": flt(i.amount),
				}
				for i in doc.items
			],
		},
	}


@frappe.whitelist()
def set_online_order_status(order: str, status: str, company: str | None = None) -> dict:
	"""Move an online order along: New, Confirmed, Ready, Delivered or Cancelled. Cancelling frees its stock."""
	require_online_orders()
	company = resolve_company(company)
	row = _owned_order(order, company)
	if status not in FULFILMENT:
		frappe.throw(_("Unknown order status."), frappe.ValidationError)
	current = row.custom_online_fulfilment or "New"
	if status not in NEXT_STATUS.get(current, ()):
		frappe.throw(
			_("An order that is {0} cannot be changed to {1}.").format(current, status),
			frappe.ValidationError,
		)

	if status == "Cancelled" and row.get(ORDER_SALE_FIELD):
		frappe.throw(
			_("This order was already sold in {0}, so it cannot be cancelled. Return the sale instead.").format(
				row.get(ORDER_SALE_FIELD)
			),
			frappe.ValidationError,
		)
	if status == "Cancelled" and row.docstatus == 1:
		cancel_order_document(order)
	frappe.db.set_value("Sales Order", order, "custom_online_fulfilment", status)
	frappe.db.commit()
	return {
		"success": True,
		"message": _("Order is now {0}.").format(status),
		"data": {"order": order, "status": status},
	}


@frappe.whitelist()
def mark_online_order_paid(order: str, company: str | None = None) -> dict:
	"""Record that the customer has paid (cash on delivery received, or an M-Pesa payment checked by the owner)."""
	require_online_orders()
	company = resolve_company(company)
	_owned_order(order, company)
	# Marking an order that is already paid (the till does this after the sale has) changes nothing and does not fail
	if not frappe.db.get_value("Sales Order", order, "custom_online_paid"):
		frappe.db.set_value("Sales Order", order, "custom_online_paid", 1)
		frappe.db.commit()
	return {"success": True, "message": _("Order marked as paid."), "data": {"order": order}}


@frappe.whitelist()
def create_invoice_for_online_order(order: str, company: str | None = None) -> dict:
	"""Make a draft Sales Invoice from an online order, taking the stock out. The owner reviews and submits it."""
	require_online_orders()
	company = resolve_company(company)
	row = _owned_order(order, company)
	if row.get(ORDER_SALE_FIELD):
		frappe.throw(
			_("This order was already sold in {0}, so it needs no second invoice.").format(
				row.get(ORDER_SALE_FIELD)
			),
			frappe.ValidationError,
		)
	if row.docstatus != 1:
		frappe.throw(_("Only an open order can be invoiced."), frappe.ValidationError)

	from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

	invoice = make_sales_invoice(order)
	invoice.update_stock = 1
	invoice.insert()
	return {
		"success": True,
		"message": _("Draft invoice {0} created. Review it and submit it to complete the sale.").format(
			invoice.name
		),
		"data": {"invoice": invoice.name},
	}


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=60, seconds=10 * 60)
def track_online_order(token: str) -> dict:
	"""What a customer sees when they open their tracking code: where the order is, never anyone's details."""
	token = cstr(token).strip()
	if len(token) < 8:
		frappe.throw(_("Order not found."), frappe.DoesNotExistError)
	row = frappe.db.get_value(
		"Sales Order",
		{"custom_online_token": token},
		[
			"name",
			"company",
			"custom_online_fulfilment",
			"custom_delivery_method",
			"custom_online_paid",
			"grand_total",
			"transaction_date",
		],
		as_dict=True,
	)
	if not row:
		frappe.throw(_("Order not found."), frappe.DoesNotExistError)
	return {
		"success": True,
		"data": {
			"order": row.name,
			"status": row.custom_online_fulfilment or "New",
			"delivery_method": row.custom_delivery_method,
			"paid": bool(row.custom_online_paid),
			"total": flt(row.grand_total),
			"date": str(row.transaction_date),
			"shop": frappe.db.get_value("Company", row.company, "company_name") or row.company,
		},
	}


def on_payment_update(doc, method=None) -> None:
	"""An M-Pesa payment for an online order succeeded: mark the order paid. (Hooked on the M-Pesa transaction log.)"""
	try:
		if (
			doc.get("status") != "Success"
			or doc.get("invoice_type") != "Sales Order"
			or not doc.get("invoice")
		):
			return
		if frappe.db.get_value("Sales Order", doc.invoice, "company") != doc.get("company"):
			return
		frappe.db.set_value("Sales Order", doc.invoice, "custom_online_paid", 1)
	except Exception:
		# A fault here must never undo the payment record itself
		frappe.log_error("Online order payment mark", frappe.get_traceback())
