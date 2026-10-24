"""
What a customer sees and does on a business's hosted online store.

Everything here is open to the public (no sign-in), so it is built to be careful:

- a shop that is not switched on, or does not exist, looks the same: "not found";
- customers see only what is meant for them: name, price, picture, whether it is in stock. Never costs, other
  businesses' products, other customers' orders or any account detail;
- prices are always the POS's. The browser can ask for products and quantities, never set a price;
- every call is limited per visitor, and an order is limited per phone number as well, so the store cannot be used to
  flood a shop with fake orders or to send M-Pesa prompts to strangers;
- a customer finds their order only with the long private tracking code they were given.

The order itself is recorded by online_orders.place_order, the same way a WooCommerce order is.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cint, cstr, flt

from murzak_pos.api import online_catalogue, online_orders, online_store_api
from murzak_pos.api.payment_gateway_common import normalize_kenyan_phone

PAYMENT_LABELS = {"cash": "Pay on delivery or pickup", "mpesa": "M-Pesa"}
ORDERS_PER_PHONE_PER_HOUR = 5
PROMPT_LOCK_SECONDS = 45


def open_store(slug: str) -> tuple[str, dict]:
	"""(company, settings) of an open store. A closed or unknown store is simply not found."""
	company = online_store_api.company_for_slug(slug)
	settings = online_store_api.read_store(company) if company else None
	if not (company and settings and settings["enabled"] and settings["slug"] == cstr(slug).strip().lower()):
		frappe.throw(_("Store not found."), frappe.DoesNotExistError)
	return company, settings


def _currency(company: str) -> str:
	return frappe.db.get_value("Company", company, "default_currency") or ""


def payment_options(settings: dict) -> list[str]:
	return [
		code for code, on in (("cash", settings["accept_cash"]), ("mpesa", settings["accept_mpesa"])) if on
	]


def public_store(company: str, settings: dict) -> dict:
	"""What a customer is told about a store."""
	return {
		"slug": settings["slug"],
		"store_name": settings["store_name"]
		or frappe.db.get_value("Company", company, "company_name")
		or company,
		"tagline": settings["tagline"],
		"whatsapp_number": settings["whatsapp_number"],
		"delivery_enabled": settings["delivery_enabled"],
		"pickup_enabled": settings["pickup_enabled"],
		"pickup_address": settings["pickup_address"] if settings["pickup_enabled"] else "",
		"delivery_note": settings["delivery_note"],
		"payment_options": payment_options(settings),
		"minimum_order": flt(settings["minimum_order"]),
		"currency": _currency(company),
	}


def _public_product(row: dict) -> dict:
	"""A product as a customer sees it: no stock count beyond what they need."""
	return {
		"item_code": row["item_code"],
		"item_name": row["item_name"],
		"category": row["category"],
		"unit": row["unit"],
		"price": row["price"],
		"description": row["description"],
		"image": row["image"],
		"in_stock": row["in_stock"],
		# Shown only when low, to encourage an order without handing out the shop's full stock position
		"low_stock": row["stock"] if (row["stock"] is not None and 0 < row["stock"] <= 5) else None,
	}


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
@rate_limit(limit=300, seconds=60)
def get_store(slug: str) -> dict[str, Any]:
	"""The store's name, how to get and pay for goods, and its product categories."""
	company, settings = open_store(slug)
	data = public_store(company, settings)
	data["categories"] = online_catalogue.categories(company)
	return {"success": True, "data": data}


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
@rate_limit(limit=300, seconds=60)
def list_products(
	slug: str, search: str | None = None, category: str | None = None, page: int = 1, page_size: int = 20
) -> dict[str, Any]:
	"""One page of the store's products."""
	company, settings = open_store(slug)
	warehouse = online_catalogue.default_warehouse(company, settings["warehouse"])
	search = cstr(search).strip()[:80] or None
	category = cstr(category).strip()[:140] or None
	rows = online_catalogue.list_items(
		company,
		warehouse,
		search=search,
		category=category,
		page=cint(page) or 1,
		page_size=cint(page_size) or 20,
	)
	return {
		"success": True,
		"data": {
			"rows": [_public_product(r) for r in rows],
			"total": online_catalogue.count_items(company, search=search, category=category),
			"page": max(1, cint(page) or 1),
		},
	}


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
@rate_limit(limit=300, seconds=60)
def get_product(slug: str, item_code: str) -> dict[str, Any]:
	"""One product, only if it belongs to this store and is shown."""
	company, settings = open_store(slug)
	warehouse = online_catalogue.default_warehouse(company, settings["warehouse"])
	items = frappe.get_all(
		"Item",
		filters={**online_catalogue.online_filters(company), "name": cstr(item_code).strip()},
		fields=[
			"name",
			"item_name",
			"item_group",
			"stock_uom",
			"standard_rate",
			"description",
			"image",
			"is_stock_item",
		],
		limit_page_length=1,
	)
	if not items:
		frappe.throw(_("Product not found."), frappe.DoesNotExistError)
	return {"success": True, "data": _public_product(online_catalogue.rows_for(items, warehouse)[0])}


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


def _within_limit(key: str, limit: int, seconds: int) -> bool:
	"""Count one more use of `key`; False once it has been used more than `limit` times in `seconds`."""
	cache = frappe.cache()
	redis_key = cache.make_key(key)
	count = cache.incr(redis_key)
	if count == 1:
		cache.expire(redis_key, seconds)
	return count <= limit


def _hold_prompt_lock(order: str) -> bool:
	"""One M-Pesa prompt at a time per order, so a double tap never sends the customer two."""
	cache = frappe.cache()
	return bool(
		cache.set(cache.make_key(f"murzak_pos:store_prompt:{order}"), 1, nx=True, ex=PROMPT_LOCK_SECONDS)
	)


def send_mpesa_prompt(company: str, order: str, phone: str, amount: float) -> dict:
	"""Ask the customer's phone to pay for the order. Never raises: the order stands even if the prompt fails."""
	from murzak_pos.api.mpesa_client import MpesaClientError, initiate_stk_push

	if not _hold_prompt_lock(order):
		return {"sent": False, "message": _("A payment prompt was just sent. Please wait a moment.")}
	try:
		result = initiate_stk_push(
			company=company,
			phone_number=phone,
			amount=flt(amount),
			reference=order,
			description=_("Online order {0}").format(order)[:13],
		)
	except MpesaClientError as e:
		return {"sent": False, "message": str(e)}
	except Exception:
		frappe.log_error("Online store M-Pesa prompt", frappe.get_traceback())
		return {"sent": False, "message": _("The payment prompt could not be sent. Please try again.")}

	# Tie the payment to the order, so a successful payment marks the order paid
	frappe.db.set_value(
		"MPESA Transaction Log", result["transaction_id"], {"invoice_type": "Sales Order", "invoice": order}
	)
	frappe.db.commit()
	return {
		"sent": True,
		"message": result.get("customer_message") or _("Check your phone and enter your M-Pesa PIN."),
	}


def whatsapp_order_link(settings: dict, order: str, token: str, lines: list[dict], total: float) -> str:
	"""A link that opens WhatsApp to the shop with the order already written, so the customer can confirm in one tap."""
	number = settings.get("whatsapp_number")
	if not number:
		return ""
	items = ", ".join(f"{flt(line['qty']):g} x {line['item_name']}" for line in lines)
	text = _("Hello, I have placed order {0}: {1}. Total {2}. Tracking code {3}").format(
		order, items, flt(total), token
	)
	return f"https://wa.me/{number}?text={quote(text)}"


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=10, seconds=60 * 60)
def checkout(
	slug: str,
	customer_name: str,
	phone: str,
	lines: str | list,
	delivery_method: str,
	payment: str,
	address: str | None = None,
	notes: str | None = None,
) -> dict[str, Any]:
	"""
	Place an order on the store. `lines` is a list of {"item_code", "qty"}; `payment` is "cash" or "mpesa".
	Returns the order number, the private tracking code, the total, and (for M-Pesa) whether a prompt was sent.
	"""
	company, settings = open_store(slug)

	name = cstr(customer_name).strip()
	if len(name) < 2:
		frappe.throw(_("Please enter your name."), frappe.ValidationError)
	phone = normalize_kenyan_phone(phone)

	if (
		delivery_method not in online_orders.DELIVERY_METHODS
		or not settings[f"{delivery_method.lower()}_enabled"]
	):
		frappe.throw(_("Please choose how you want to receive your order."), frappe.ValidationError)
	address = cstr(address).strip()
	if delivery_method == "Delivery" and len(address) < 5:
		frappe.throw(_("Please enter the address to deliver to."), frappe.ValidationError)
	if payment not in payment_options(settings):
		frappe.throw(_("Please choose how you will pay."), frappe.ValidationError)

	if not _within_limit(f"murzak_pos:store_orders:{company}:{phone}", ORDERS_PER_PHONE_PER_HOUR, 60 * 60):
		frappe.throw(
			_("Too many orders from this phone number. Please try again later."), frappe.ValidationError
		)

	placed = online_orders.place_order(
		company,
		"Website",
		lines,
		customer_name=name[:80],
		phone=phone,
		delivery_method=delivery_method,
		address=address if delivery_method == "Delivery" else settings["pickup_address"],
		payment=PAYMENT_LABELS[payment],
		notes=notes,
		warehouse=settings["warehouse"] or None,
		minimum_total=flt(settings["minimum_order"]),
	)

	prompt = None
	if payment == "mpesa":
		prompt = send_mpesa_prompt(company, placed["order"], phone, placed["total"])

	return {
		"success": True,
		"message": _("Thank you! Your order {0} has been received.").format(placed["order"]),
		"data": {
			"order": placed["order"],
			"token": placed["token"],
			"total": placed["total"],
			"currency": _currency(company),
			"lines": placed["lines"],
			"payment": payment,
			"payment_prompt": prompt,
			"whatsapp_link": whatsapp_order_link(
				settings, placed["order"], placed["token"], placed["lines"], placed["total"]
			),
		},
	}


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=10, seconds=60 * 60)
def pay_for_order(token: str) -> dict[str, Any]:
	"""Send the M-Pesa prompt again for an order that has not been paid (the customer missed or dismissed it)."""
	token = cstr(token).strip()
	if len(token) < 8:
		frappe.throw(_("Order not found."), frappe.DoesNotExistError)
	order = frappe.db.get_value(
		"Sales Order",
		{"custom_online_token": token, "custom_online_channel": "Website"},
		[
			"name",
			"company",
			"docstatus",
			"grand_total",
			"custom_online_payment",
			"custom_online_paid",
			"custom_online_contact",
			"custom_online_fulfilment",
		],
		as_dict=True,
	)
	if not order:
		frappe.throw(_("Order not found."), frappe.DoesNotExistError)
	if order.docstatus != 1 or order.custom_online_fulfilment in ("Delivered", "Cancelled"):
		frappe.throw(_("This order can no longer be paid for."), frappe.ValidationError)
	if order.custom_online_paid:
		return {"success": True, "message": _("This order is already paid."), "data": {"paid": True}}
	if order.custom_online_payment != PAYMENT_LABELS["mpesa"]:
		frappe.throw(_("This order is paid on delivery or pickup."), frappe.ValidationError)

	prompt = send_mpesa_prompt(order.company, order.name, order.custom_online_contact, order.grand_total)
	return {
		"success": prompt["sent"],
		"message": prompt["message"],
		"data": {"paid": False, "payment_prompt": prompt},
	}
