"""
Who may do what: shared checks for the calls that manage staff, roles and permissions.

These calls change what people on the site are allowed to do, so being signed in is not enough.
The rules, in plain words:

- only a business owner (a System Manager) or the Administrator may manage staff and roles
- nobody can hand out a role they do not hold themselves, so a manager cannot make a bigger manager
- a standard role is shared by every business on the site, so only the Administrator may change it
- a role a business created can only be changed from inside that business

The frontend already shows the Staff and Roles screens only to these two roles. These checks make
the server enforce the same rule, because anything the browser decides can be bypassed.
"""

from __future__ import annotations

import frappe
from frappe import _

from murzak_pos.api.payment_gateway_common import get_user_companies
from murzak_pos.api.role_tiers import OWNER_ROLES

# The field that says which company owns a record. Most records have `company`; customers, items, price lists and
# suppliers carry their owner in `custom_company` (added by this app).
OWNER_FIELD = {
	"Customer": "custom_company",
	"Item": "custom_company",
	"Supplier": "custom_company",
	"Price List": "custom_company",
	"Sales Person": "custom_company",
}

# Who may do the sensitive everyday jobs. These are copied from the frontend's role table
# (src/config/roleAccessConfig.js in the Murzak-PoS-Frontend repository) so the server and the screens
# agree. "All" is left out on purpose: Frappe gives that role to every user, including cashiers.
CATALOGUE_ROLES = frozenset(
	{"Business Owner", "System Manager", "Sales Manager", "Stock Manager", "Item Manager", "Agriculture Manager"}
)
STOCK_ROLES = frozenset(
	{
		"Business Owner",
		"System Manager",
		"Purchase Manager",
		"Stock Manager",
		"Stock User",
		"Item Manager",
		"Agriculture Manager",
	}
)
STOCK_COUNT_ROLES = frozenset(
	{"Business Owner", "System Manager", "Purchase Manager", "Stock Manager", "Item Manager", "Agriculture Manager"}
)
CREDIT_ROLES = frozenset(
	{
		"Business Owner",
		"System Manager",
		"Sales Manager",
		"Accounts Manager",
		"Accounts User",
		"Delivery Manager",
		"Agent Manager",
	}
)
# The frontend's full Suppliers screens (supplier groups among them); Purchase User only views suppliers
SUPPLIER_ROLES = frozenset({"Business Owner", "System Manager", "Purchase Manager"})
LOYALTY_ROLES = frozenset({"Business Owner", "System Manager", "Accounts Manager"})
TRANSFER_ROLES = frozenset({"Business Owner", "System Manager", "Stock Manager", "Stock User"})

# The online store is one module with four permissions (src/config/roleAccessConfig.js, onlineStoreAccess, in the
# frontend). The owner roles hold all four. A Sales User (the till) fulfils orders; a Stock Manager may only look at
# them, to follow the stock they hold and move.
ONLINE_CLERK_ROLE = "Online Orders Clerk"
ONLINE_ORDERS_ROLES = OWNER_ROLES | {"Sales Manager", "Sales User", ONLINE_CLERK_ROLE}
ONLINE_ORDERS_VIEW_ROLES = ONLINE_ORDERS_ROLES | {"Stock Manager"}
ONLINE_CATALOGUE_ROLES = OWNER_ROLES | {"Sales Manager", "Item Manager"}
ONLINE_SETTINGS_ROLES = OWNER_ROLES | {"Sales Manager"}
ONLINE_INTEGRATION_ROLES = frozenset(OWNER_ROLES)


def require_manager() -> None:
	"""Refuse anyone who is not signed in, or is not a business owner (System Manager) or the Administrator."""
	user = frappe.session.user
	if user == "Guest":
		frappe.throw(_("Please sign in first."), frappe.AuthenticationError)
	if user == "Administrator":
		return
	if not OWNER_ROLES & set(frappe.get_roles(user)):
		frappe.throw(
			_("Only a business owner or System Manager can do this."),
			frappe.PermissionError,
		)


def require_roles_held(roles) -> None:
	"""Refuse handing out a role the caller does not hold. The Administrator may hand out any role."""
	if frappe.session.user == "Administrator":
		return
	held = set(frappe.get_roles(frappe.session.user))
	missing = sorted(set(roles or []) - held)
	if missing:
		frappe.throw(
			_("You cannot give roles you do not hold yourself: {0}.").format(", ".join(missing)),
			frappe.PermissionError,
		)


def require_role_manageable(role_name: str) -> None:
	"""
	Refuse changing a role that is not the caller's to change.

	A standard role is shared by every business, so only the Administrator may change it. A custom
	role may be changed by the person who made it, or by someone from the same business.
	"""
	user = frappe.session.user
	if user == "Administrator":
		return

	found = frappe.db.get_value("Role", role_name, ["is_custom", "owner"])
	if not found:
		return  # no such role: let the call say so in its own words
	is_custom, creator = found

	if not is_custom:
		frappe.throw(
			_(
				"{0} is a standard role shared by every business. Only the system administrator can change it."
			).format(role_name),
			frappe.PermissionError,
		)

	if creator == user:
		return

	mine = frappe.db.get_value("User", user, "custom_company")
	theirs = frappe.db.get_value("User", creator, "custom_company") if creator else None
	if mine and mine == theirs:
		return

	frappe.throw(_("This role belongs to another business."), frappe.PermissionError)


def require_any_role(roles, action: str) -> None:
	"""Refuse anyone who holds none of `roles`. The Administrator always passes."""
	user = frappe.session.user
	if user == "Guest":
		frappe.throw(_("Please sign in first."), frappe.AuthenticationError)
	if user == "Administrator":
		return
	if not set(roles) & set(frappe.get_roles(user)):
		frappe.throw(
			_("Your role cannot {0}. It takes one of these roles: {1}.").format(
				action, ", ".join(sorted(roles))
			),
			frappe.PermissionError,
		)


def require_catalogue_role() -> None:
	"""Changing prices, the product list, or importing products in bulk."""
	require_any_role(CATALOGUE_ROLES, _("change prices or the product catalogue"))


def can_manage_online_orders(user: str | None = None) -> bool:
	"""Does the user hold the online store's `orders` permission? The Administrator does."""
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	return bool(ONLINE_ORDERS_ROLES & set(frappe.get_roles(user)))


def require_online_orders_view() -> None:
	"""Online store `orders`, view only: the list of online orders and one order's lines."""
	require_any_role(ONLINE_ORDERS_VIEW_ROLES, _("see online orders"))


def require_online_orders() -> None:
	"""Online store `orders`: see and move online orders, mark them paid, invoice them, open them in the till."""
	require_any_role(ONLINE_ORDERS_ROLES, _("work with online orders"))


def require_online_catalogue() -> None:
	"""Online store `catalogue`: show or hide products, edit a product's online description and photos."""
	require_any_role(ONLINE_CATALOGUE_ROLES, _("change what is shown in the online store"))


def require_online_settings() -> None:
	"""Online store `settings`: the shop's address, delivery, payment, WhatsApp number, logo, open or close."""
	require_any_role(ONLINE_SETTINGS_ROLES, _("change the online store settings"))


def require_online_integrations() -> None:
	"""Online store `integrations`: WooCommerce keys, the webhook secret and syncing. Owner roles only."""
	require_any_role(ONLINE_INTEGRATION_ROLES, _("manage the website connection"))


def require_supplier_role() -> None:
	"""Adding or changing supplier groups."""
	require_any_role(SUPPLIER_ROLES, _("change supplier groups"))


def require_shared_record_manageable(doctype: str, name) -> None:
	"""
	Refuse renaming or deleting a record every business on the site shares: a unit, a product category, a brand or
	a supplier group. Changing one changes it for all of them, so only the Administrator may, or someone from the
	business that added it. On a shop's own site (one business) its people may, as nobody else uses the record.
	"""
	user = frappe.session.user
	if user == "Guest":
		frappe.throw(_("Please sign in first."), frappe.AuthenticationError)
	if user == "Administrator" or not name:
		return
	creator = frappe.db.get_value(doctype, name, "owner")
	if not creator or creator == user:
		return  # no such record: the call says so in its own words
	mine = get_user_companies(user)
	if mine and frappe.db.count("Company") == 1:
		return
	if creator != "Administrator" and mine & get_user_companies(creator):
		return
	frappe.throw(
		_("{0} {1} is shared by every business. Only the business that added it can change or remove it.").format(
			_(doctype), name
		),
		frappe.PermissionError,
	)


def require_stock_role() -> None:
	"""Stock entries, material receipts, issues and transfers, and stock reconciliation."""
	require_any_role(STOCK_ROLES, _("change stock"))


def require_stock_count_role() -> None:
	"""Starting a multi-level stock reconciliation."""
	require_any_role(STOCK_COUNT_ROLES, _("start a stock count"))


def require_credit_role() -> None:
	"""Setting or removing a customer's credit limit."""
	require_any_role(CREDIT_ROLES, _("change customer credit limits"))


def require_loyalty_role() -> None:
	"""Creating a loyalty program or assigning one to customers."""
	require_any_role(LOYALTY_ROLES, _("manage loyalty programs"))


def require_transfer_role() -> None:
	"""Moving stock between stores: requesting, approving, dispatching and receiving a transfer."""
	require_any_role(TRANSFER_ROLES, _("move stock between stores"))


def ensure_record_access(doctype: str, name) -> None:
	"""
	Refuse a record that belongs to another business, such as an invoice, stock entry or warehouse opened by its number.

	Invoice and stock entry numbers run in sequence, so without this anyone signed in could read or cancel
	another business's records by counting. The Administrator may open any record.

	Left to the call itself: a record that does not exist (the call says so), and a record with no owner
	recorded, such as an item made before items belonged to a business.
	"""
	user = frappe.session.user
	if user == "Administrator" or not name or not isinstance(name, str | int):
		return
	if user == "Guest":
		frappe.throw(_("Please sign in first."), frappe.AuthenticationError)

	field = OWNER_FIELD.get(doctype, "company")
	if not frappe.db.has_column(doctype, field):
		return
	owner = frappe.db.get_value(doctype, name, field)
	if owner and owner not in get_user_companies(user):
		frappe.throw(_("You do not have access to this {0}.").format(_(doctype)), frappe.PermissionError)
