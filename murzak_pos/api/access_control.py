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

from murzak_pos.api import role_tiers
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


# Who may add or change customers: the people who work with customers (the frontend's Customers screens) and the
# people who ring up sales, who add a customer at the till. A stock clerk, an auditor or a viewer may not.
CUSTOMER_ROLES = frozenset(
	{
		"Business Owner",
		"System Manager",
		"Sales Manager",
		"Sales User",
		"Accounts Manager",
		"Accounts User",
		"Delivery Manager",
		"Agent Manager",
		"Agent",
		"Desk User",
		"Employee",
		"Academics User",
	}
)
# Who may change someone else's draft invoice: the roles that reach the invoice edit page. Anyone may still change
# a draft they made themselves.
SALES_EDIT_ROLES = frozenset(
	{"Business Owner", "System Manager", "Sales Manager", "Delivery Manager", "Agent Manager"}
)
# Who may set up how the books are kept (provisional accounts, the Credit way of paying): the owner and the accountant
ACCOUNTS_SETUP_ROLES = frozenset({"Business Owner", "System Manager", "Accounts Manager"})
# The steps of a multi-level stock count. The first is the counter on the shop floor, the second the quality check, the
# third the stock manager (who also submits it). The owner and the stock manager may do any step.
STOCK_TAKE_FIRST_STEP_ROLES = frozenset({"Business Owner", "System Manager", "Stock Manager", "Sales User"})
STOCK_TAKE_SECOND_STEP_ROLES = frozenset({"Business Owner", "System Manager", "Stock Manager", "Quality Manager"})


# Who may handle supplier bills and pay suppliers: the owner, the accountants and the purchase manager (the same people who
# may cancel a supplier invoice)
BILLING_ROLES = frozenset({"Business Owner", "System Manager", "Accounts Manager", "Purchase Manager"})

# Who may add or change a store: the owner and the stock managers
STORE_ROLES = frozenset({"Business Owner", "System Manager", "Stock Manager"})


def require_store_role() -> None:
	"""Adding a store and changing its name or address."""
	require_any_role(STORE_ROLES, _("change a store"))


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


# An owner may hand out any of the business roles to their staff even if their own account lacks one (see
# role_tiers.BUSINESS_ROLES); System Manager and every role outside that list can be given only by someone who holds it.
BUSINESS_ROLES = frozenset(role_tiers.BUSINESS_ROLES)


def require_roles_held(roles) -> None:
	"""
	Refuse handing out a role the caller does not hold. The Administrator may hand out any role. An owner (Business
	Owner or System Manager) may also hand out any of the business roles even if their own account lacks one, since
	giving staff those roles is what an owner is for; a role outside that list still has to be one they hold.
	"""
	if frappe.session.user == "Administrator":
		return
	held = set(frappe.get_roles(frappe.session.user))
	if held & OWNER_ROLES:
		held |= BUSINESS_ROLES
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


def require_supplier_role() -> None:
	"""Adding or changing supplier groups."""
	require_any_role(SUPPLIER_ROLES, _("change supplier groups"))


def require_supplier_editor() -> None:
	"""Adding, changing or switching off a supplier (the same people who may change supplier groups)."""
	require_any_role(SUPPLIER_ROLES, _("change suppliers"))


def require_system_manager() -> None:
	"""Work that changes records every business on the site shares, such as loading the starter products."""
	require_any_role({"System Manager"}, _("load the starter products"))


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


def require_billing_role() -> None:
	"""Supplier bills and payments to suppliers."""
	require_any_role(BILLING_ROLES, _("handle supplier bills and payments"))


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


def require_customer_role() -> None:
	"""Adding a customer or changing a customer's details."""
	require_any_role(CUSTOMER_ROLES, _("change customers"))


def require_accounts_setup_role() -> None:
	"""Setting up the accounts the books use: provisional accounts and the Credit way of paying."""
	require_any_role(ACCOUNTS_SETUP_ROLES, _("set up the accounts"))


def require_stock_take_first_step_role() -> None:
	"""Adding the first count of a multi-level stock count."""
	require_any_role(STOCK_TAKE_FIRST_STEP_ROLES, _("add the first count of a stock count"))


def require_stock_take_second_step_role() -> None:
	"""Adding the quality check count of a multi-level stock count."""
	require_any_role(STOCK_TAKE_SECOND_STEP_ROLES, _("add the quality check count of a stock count"))


def require_invoice_editor(doctype: str, name) -> None:
	"""
	Changing a draft invoice: a sales manager or the owner may change any, and anyone may change the drafts they made.

	Without this a cashier could rewrite a draft that a colleague started, such as its prices and discounts.
	"""
	user = frappe.session.user
	if user == "Guest":
		frappe.throw(_("Please sign in first."), frappe.AuthenticationError)
	if user == "Administrator" or SALES_EDIT_ROLES & set(frappe.get_roles(user)):
		return
	if name and frappe.db.get_value(doctype, name, "owner") == user:
		return
	frappe.throw(
		_("Only the person who made this invoice, or a sales manager, can change it."),
		frappe.PermissionError,
	)


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
