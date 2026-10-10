"""
Sales Management API
Handles creation and querying of Sales Invoices and POS Invoices for SavvyPOS
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Dict, List, Optional, Union

import frappe
from frappe import _
from frappe.utils import flt, nowdate, getdate, cint
from techsavanna_pos.techsavanna_pos.doctype.inventory_discount_rule.inventory_discount_rule import (
    get_applicable_inventory_discount,
)


def _get_default_company() -> Optional[str]:
    """Get the default company for the current user.
    
    Uses custom_company field as fallback to prevent silent fallback
    to global default company, which can cause multi-company issues.
    """
    company = frappe.defaults.get_user_default("Company")
    if not company:
        # Fallback to custom_company field instead of global defaults
        # This prevents silent fallback to global default company
        company = frappe.db.get_value("User", frappe.session.user, "custom_company")
    return company


def _pos_settings_has_invoice_type() -> bool:
    """POS Settings only has an invoice_type field in newer ERPNext releases.

    Reading a field that does not exist makes Frappe queue "Field invoice_type does
    not exist on POS Settings" for the user before it raises, so even when the
    exception is caught the message still reaches the screen. Checking first avoids it.
    """
    return bool(frappe.get_meta("POS Settings").has_field("invoice_type"))


def _get_pos_settings_invoice_type() -> Optional[str]:
    """The global POS Settings invoice type, or None when this ERPNext has no such setting."""
    if not _pos_settings_has_invoice_type():
        return None
    return frappe.db.get_single_value("POS Settings", "invoice_type")


def _set_pos_settings_invoice_type(invoice_type: str) -> bool:
    """Set the global POS Settings invoice type. Returns False when this ERPNext has no such setting."""
    if not _pos_settings_has_invoice_type():
        return False
    frappe.db.set_single_value("POS Settings", "invoice_type", invoice_type)
    return True


def _get_invoice_type(company: Optional[str] = None) -> str:
    """
    Resolve invoice type with a company override stored in defaults.
    Falls back to POS Settings invoice_type, then defaults to 'POS Invoice'.
    """
    # Company override via Default key; does not require schema changes
    if company:
        override = frappe.db.get_default(f"pos_invoice_type::{company}")
        if override:
            return override

    # Then the global POS Settings value, on ERPNext versions that have it
    return _get_pos_settings_invoice_type() or "POS Invoice"


def _check_pos_opening_entry_has_invoices(start_date, end_date, pos_profile, user) -> bool:
    """
    Check if there are any invoices for a given POS opening entry period.
    This is a simplified version that avoids importing get_invoices from ERPNext.
    
    Args:
        start_date: Period start date (datetime)
        end_date: Period end date (datetime)
        pos_profile: POS Profile name
        user: User name
        
    Returns:
        True if invoices exist, False otherwise
    """
    from frappe.query_builder import DocType, functions as fn
    
    # Invoice type from POS Settings (older ERPNext has no such setting: POS Invoice)
    invoice_doctype = _get_pos_settings_invoice_type() or "POS Invoice"
    
    # Check Sales Invoice. "is_created_using_pos" and "pos_closing_entry" exist on Sales
    # Invoice only in newer ERPNext; querying them on ERPNext 15 fails with
    # (1054, "Unknown column ..."), which made cancelling a shift fail and made an old
    # open shift look as if it had sales. Only use the columns this database has.
    SalesInvoice = DocType("Sales Invoice")
    conditions = (
        (SalesInvoice.owner == user)
        & (SalesInvoice.docstatus == 1)
        & (SalesInvoice.is_pos == 1)
        & (SalesInvoice.pos_profile == pos_profile)
        & (fn.Timestamp(SalesInvoice.posting_date, SalesInvoice.posting_time) >= start_date)
        & (fn.Timestamp(SalesInvoice.posting_date, SalesInvoice.posting_time) <= end_date)
    )
    if frappe.db.has_column("Sales Invoice", "is_created_using_pos"):
        conditions = conditions & (SalesInvoice.is_created_using_pos == 1)
    if frappe.db.has_column("Sales Invoice", "pos_closing_entry"):
        conditions = conditions & (fn.IfNull(SalesInvoice.pos_closing_entry, "").eq(""))
    if frappe.db.has_column("Sales Invoice", "is_consolidated"):
        # Invoices made when an earlier shift was closed are not this shift's sales
        conditions = conditions & (SalesInvoice.is_consolidated == 0)
    sales_inv_query = frappe.qb.from_(SalesInvoice).select(fn.Count(SalesInvoice.name).as_("count")).where(conditions)
    
    sales_count = sales_inv_query.run(as_dict=True)
    if sales_count and sales_count[0].get("count", 0) > 0:
        return True
    
    # Check POS Invoice if invoice type is POS Invoice
    if invoice_doctype == "POS Invoice":
        POSInvoice = DocType("POS Invoice")
        pos_inv_query = (
            frappe.qb.from_(POSInvoice)
            .select(fn.Count(POSInvoice.name).as_("count"))
            .where(
                (POSInvoice.owner == user)
                & (POSInvoice.docstatus == 1)
                & (POSInvoice.is_pos == 1)
                & (POSInvoice.pos_profile == pos_profile)
                & (
                    (fn.Timestamp(POSInvoice.posting_date, POSInvoice.posting_time) >= start_date)
                    & (fn.Timestamp(POSInvoice.posting_date, POSInvoice.posting_time) <= end_date)
                )
                & (fn.IfNull(POSInvoice.consolidated_invoice, "").eq(""))
            )
        )
        
        pos_count = pos_inv_query.run(as_dict=True)
        if pos_count and pos_count[0].get("count", 0) > 0:
            return True
    
    return False


def _parse_opening_balances(balance_details) -> List[Dict]:
    """Opening amounts as sent by the till (a list, or the same list as JSON text).

    Previously create_pos_opening_entry accepted these but never used them, so every
    shift started at 0 whatever cash the cashier counted into the drawer.
    """
    import json

    if not balance_details:
        return []
    if isinstance(balance_details, str):
        try:
            balance_details = json.loads(balance_details)
        except ValueError:
            frappe.throw(_("Opening amounts could not be read. Please try again."), frappe.ValidationError)
    if not isinstance(balance_details, list):
        return []

    rows = []
    for row in balance_details:
        if not isinstance(row, dict) or not row.get("mode_of_payment"):
            continue
        if not frappe.db.exists("Mode of Payment", row["mode_of_payment"]):
            frappe.throw(
                _("Payment method {0} does not exist.").format(frappe.bold(row["mode_of_payment"])),
                frappe.ValidationError,
            )
        amount = flt(row.get("opening_amount"))
        if amount < 0:
            frappe.throw(_("Opening amounts cannot be negative."), frappe.ValidationError)
        rows.append({"mode_of_payment": row["mode_of_payment"], "opening_amount": amount})
    return rows


def _get_or_create_pos_opening_entry(
    pos_profile: str, company: str, user: str = None, balance_details: Optional[List[Dict]] = None
) -> str:
    """
    Get an existing open POS Opening Entry for the POS Profile, or create one if none exists.
    Handles outdated opening entries by canceling them if they have no invoices.
    
    Args:
        pos_profile: POS Profile name
        company: Company name
        user: User name (defaults to current user)
        balance_details: Opening amounts counted by the cashier, as
            [{"mode_of_payment": "Cash", "opening_amount": 5000}, ...]. Used for a new
            entry; when omitted every method of the POS Profile starts at 0.
        
    Returns:
        POS Opening Entry name
    """
    if not user:
        user = frappe.session.user
    
    # Check if there's an open POS Opening Entry
    from frappe.utils import today, get_datetime, get_date_str
    
    # Get all open POS Opening Entries for this POS Profile
    open_entries = frappe.get_all(
        "POS Opening Entry",
        filters={
            "pos_profile": pos_profile,
            "status": "Open"
        },
        fields=["name", "period_start_date", "user"],
        order_by="period_start_date desc"
    )
    
    # Check if there's a valid (today's) opening entry
    today_str = today()
    valid_entry = None
    outdated_entries = []
    
    for entry in open_entries:
        entry_date_str = get_date_str(entry.period_start_date)
        if entry_date_str == today_str:
            # Found a valid entry for today
            valid_entry = entry
            break
        else:
            # This entry is outdated
            outdated_entries.append(entry)
    
    # If we found a valid entry, return it
    if valid_entry:
        return valid_entry.name
    
    # If there are outdated entries, check them all first, then cancel if possible
    if outdated_entries:
        # Check all outdated entries to see if any have invoices
        entries_with_invoices = []
        for outdated_entry in outdated_entries:
            try:
                # Directly query for invoices instead of importing get_invoices
                # This avoids import errors and is more reliable
                has_invoices = _check_pos_opening_entry_has_invoices(
                    outdated_entry.period_start_date,
                    get_datetime(),
                    pos_profile,
                    outdated_entry.user
                )
                
                if has_invoices:
                    entries_with_invoices.append(outdated_entry)
            except Exception as e:
                # If we can't check, assume it has invoices to be safe
                error_msg = f"Error checking invoices for POS Opening Entry {outdated_entry.name}"
                frappe.log_error(
                    "POS Opening Entry Check Error",
                    f"{error_msg}: {str(e)[:100]}"
                )
                entries_with_invoices.append(outdated_entry)
        
        # If any outdated entry has invoices, throw an error
        if entries_with_invoices:
            entry_names = ", ".join([frappe.bold(e.name) for e in entries_with_invoices])
            frappe.throw(
                _(
                    "Found outdated POS Opening Entries with invoices: {0}. "
                    "Please close the POS manually by creating a POS Closing Entry for each before creating a new POS Opening Entry."
                ).format(entry_names),
                title=_("Outdated POS Opening Entries with Invoices")
            )
        
        # All outdated entries have no invoices, so we can cancel them
        for outdated_entry in outdated_entries:
            try:
                opening_entry_doc = frappe.get_doc("POS Opening Entry", outdated_entry.name)
                opening_entry_doc.flags.ignore_permissions = True
                opening_entry_doc.cancel()
                frappe.db.commit()
                frappe.log_error(
                    "POS Opening Entry Auto-Cancel",
                    f"Cancelled outdated POS Opening Entry {outdated_entry.name} (period_start_date: {outdated_entry.period_start_date})"
                )
            except Exception as e:
                # If we can't cancel, throw an error
                frappe.log_error(
                    "POS Opening Entry Auto-Cancel Error",
                    f"Error cancelling outdated POS Opening Entry {outdated_entry.name}: {str(e)}"
                )
                frappe.throw(
                    _(
                        "Found outdated POS Opening Entry - {0} (period_start_date: {1}). "
                        "Please close or cancel it manually before creating a new POS Opening Entry. "
                        "Error: {2}"
                    ).format(
                        frappe.bold(outdated_entry.name),
                        frappe.bold(str(outdated_entry.period_start_date)),
                        str(e)
                    ),
                    title=_("Cannot Handle Outdated POS Opening Entry")
                )
    
    # No opening entry exists: create one with the amounts the cashier counted,
    # or zero balances for the POS Profile's payment methods
    pos_profile_doc = frappe.get_doc("POS Profile", pos_profile)

    balance_details = _parse_opening_balances(balance_details)
    if not balance_details:
        for payment in pos_profile_doc.payments:
            balance_details.append({
                "mode_of_payment": payment.mode_of_payment,
                "opening_amount": 0.0
            })
    
    # If no payment methods, add a default Cash entry or get from POS Profile payments
    if not balance_details:
        # Try to get Cash mode of payment
        cash_mode = frappe.db.get_value("Mode of Payment", {"name": "Cash"}, "name")
        if not cash_mode:
            # Try to get any mode of payment
            cash_mode = frappe.db.get_value("Mode of Payment", {"enabled": 1}, "name", order_by="creation desc")
        
        if cash_mode:
            balance_details.append({
                "mode_of_payment": cash_mode,
                "opening_amount": 0.0
            })
        else:
            frappe.throw(
                _("No payment methods found. Please configure at least one Mode of Payment in POS Profile {0} or create a default 'Cash' mode of payment.").format(pos_profile),
                frappe.ValidationError
            )
    
    # Create POS Opening Entry
    try:
        opening_entry_doc = frappe.new_doc("POS Opening Entry")
        opening_entry_doc.period_start_date = get_datetime()
        opening_entry_doc.posting_date = today()
        opening_entry_doc.user = user
        opening_entry_doc.pos_profile = pos_profile
        opening_entry_doc.company = company
        
        # Add balance details
        for balance in balance_details:
            opening_entry_doc.append("balance_details", balance)
        
        opening_entry_doc.flags.ignore_permissions = True
        opening_entry_doc.insert(ignore_permissions=True)
        
        # Commit to ensure it's saved before invoice validation
        frappe.db.commit()
        
        opening_entry_doc.submit()
        
        # Commit again after submission
        frappe.db.commit()
        
        return opening_entry_doc.name
    except Exception as e:
        frappe.log_error("POS Opening Entry Creation", f"Error creating POS Opening Entry for {pos_profile}: {str(e)}")
        frappe.throw(
            _("Failed to create POS Opening Entry: {0}").format(str(e)),
            frappe.ValidationError
        )


def _get_or_create_pos_profile(company: str) -> str:
    """
    Get an existing POS Profile for the company, or create a default one if none exists.
    
    Args:
        company: Company name
        
    Returns:
        POS Profile name
        
    Raises:
        frappe.ValidationError: If required setup is missing
    """
    # Try to get an existing POS Profile for the company
    pos_profile = frappe.db.get_value(
        "POS Profile",
        {"company": company, "disabled": 0},
        "name",
        order_by="creation desc"
    )
    
    if pos_profile:
        return pos_profile
    
    # No POS Profile exists, create a default one
    if not frappe.db.exists("Company", company):
        frappe.throw(_("Company {0} does not exist").format(company), frappe.ValidationError)
    
    company_doc = frappe.get_doc("Company", company)
    
    # Get default warehouse (required)
    warehouse = frappe.db.get_value(
        "Warehouse",
        {"company": company, "is_group": 0},
        "name",
        order_by="creation desc"
    )
    
    if not warehouse:
        # Get any warehouse for the company
        warehouse = frappe.db.get_value(
            "Warehouse",
            {"company": company},
            "name",
            order_by="creation desc"
        )
    
    if not warehouse:
        frappe.throw(
            _("No warehouse found for company {0}. Please create a warehouse first.").format(company),
            frappe.ValidationError
        )
    
    # Get default customer (Walk-in Customer)
    customer = frappe.db.get_value(
        "Customer",
        {"customer_name": "Walk-in Customer"},
        "name"
    )
    
    if not customer:
        # Create default customer if it doesn't exist
        try:
            customer_doc = frappe.new_doc("Customer")
            customer_doc.customer_name = "Walk-in Customer"
            customer_doc.customer_type = "Company"
            customer_doc.insert(ignore_permissions=True)
            customer = customer_doc.name
        except Exception as e:
            frappe.log_error("POS Profile Creation", f"Error creating Walk-in Customer: {str(e)}")
            # Try to get any customer
            customer = frappe.db.get_value("Customer", {"company": company}, "name")
            if not customer:
                frappe.throw(
                    _("Could not create or find a default customer. Please create a customer first."),
                    frappe.ValidationError
                )
    
    # Get default selling price list
    selling_price_list = frappe.db.get_value(
        "Price List",
        {"selling": 1, "currency": company_doc.default_currency},
        "name"
    )
    
    if not selling_price_list:
        # Get any selling price list
        selling_price_list = frappe.db.get_value(
            "Price List",
            {"selling": 1},
            "name"
        )
    
    if not selling_price_list:
        frappe.throw(
            _("No selling price list found. Please create a selling price list first."),
            frappe.ValidationError
        )
    
    # Get default cost center
    cost_center = frappe.db.get_value(
        "Cost Center",
        {"company": company, "is_group": 0},
        "name",
        order_by="creation desc"
    )
    
    # Get write off account (usually Round Off Account)
    write_off_account = frappe.db.get_value(
        "Account",
        {"company": company, "account_type": "Round Off"},
        "name"
    )
    
    if not write_off_account:
        # Try to get any expense account
        write_off_account = frappe.db.get_value(
            "Account",
            {"company": company, "root_type": "Expense"},
            "name"
        )
    
    # Get or create Cash mode of payment
    cash_mode = frappe.db.get_value("Mode of Payment", {"name": "Cash"}, "name")
    if not cash_mode:
        # Create Cash mode of payment if it doesn't exist
        try:
            cash_mode_doc = frappe.new_doc("Mode of Payment")
            cash_mode_doc.mode_of_payment = "Cash"
            cash_mode_doc.type = "Cash"
            
            # Add account for Cash mode of payment
            cash_account = frappe.db.get_value(
                "Account",
                {"company": company, "account_type": "Cash"},
                "name"
            )
            if cash_account:
                cash_mode_doc.append("accounts", {
                    "company": company,
                    "default_account": cash_account
                })
            
            cash_mode_doc.insert(ignore_permissions=True)
            cash_mode = cash_mode_doc.name
        except Exception as e:
            frappe.log_error("POS Profile Creation", f"Error creating Cash mode of payment: {str(e)}")
            # Try to get any mode of payment
            cash_mode = frappe.db.get_value("Mode of Payment", {}, "name")
            if not cash_mode:
                frappe.throw(
                    _("Could not create or find a mode of payment. Please create a mode of payment first."),
                    frappe.ValidationError
                )
    
    # Create POS Profile
    profile_name = f"{company} POS Profile"
    try:
        pos_profile_doc = frappe.new_doc("POS Profile")
        pos_profile_doc.company = company
        pos_profile_doc.name = profile_name
        pos_profile_doc.warehouse = warehouse
        pos_profile_doc.currency = company_doc.default_currency
        pos_profile_doc.customer = customer
        pos_profile_doc.selling_price_list = selling_price_list
        if cost_center:
            pos_profile_doc.cost_center = cost_center
            pos_profile_doc.write_off_cost_center = cost_center
        if write_off_account:
            pos_profile_doc.write_off_account = write_off_account
        pos_profile_doc.update_stock = 1
        pos_profile_doc.allow_discount_change = 1
        pos_profile_doc.allow_rate_change = 1
        pos_profile_doc.allow_partial_payment = 1
        
        # Add payment method (required)
        if cash_mode:
            pos_profile_doc.append("payments", {
                "mode_of_payment": cash_mode,
                "default": 1
            })
        else:
            frappe.throw(
                _("No mode of payment available. Please create a mode of payment first."),
                frappe.ValidationError
            )
        
        # Add current user to applicable users
        pos_profile_doc.append("applicable_for_users", {
            "user": frappe.session.user
        })
        
        pos_profile_doc.insert(ignore_permissions=True)
        pos_profile_doc.save(ignore_permissions=True)
        
        # Set POS Settings to use POS Invoice if not already set (only where the setting exists)
        if _pos_settings_has_invoice_type() and not _get_pos_settings_invoice_type():
            _set_pos_settings_invoice_type("POS Invoice")
        
        return pos_profile_doc.name
    except Exception as e:
        frappe.log_error("POS Profile Creation", f"Error creating POS Profile: {str(e)}")
        frappe.throw(
            _("Error creating POS Profile: {0}").format(str(e)),
            frappe.ValidationError
        )


def _parse_items(items: Union[str, List[Dict]]) -> List[Dict]:
    """Parse items that can be passed as JSON string or list."""
    import json

    if isinstance(items, str):
        items = json.loads(items)

    if not isinstance(items, list) or not items:
        frappe.throw(
            _("Items must be a non-empty list"),
            frappe.ValidationError,
        )
    return items


def _validate_customer(customer: str) -> None:
    if not customer:
        frappe.throw(_("Customer is required"), frappe.ValidationError)

    if not frappe.db.exists("Customer", customer):
        frappe.throw(
            _("Customer {0} does not exist").format(customer),
            frappe.ValidationError,
        )


def _validate_items_exist(items: List[Dict]) -> None:
    for row in items:
        item_code = row.get("item_code")
        if not item_code:
            frappe.throw(
                _("item_code is required for all items"),
                frappe.ValidationError,
            )
        if not frappe.db.exists("Item", item_code):
            frappe.throw(
                _("Item {0} does not exist").format(item_code),
                frappe.ValidationError,
            )


def _build_invoice_items(items: List[Dict], company: str) -> List[Dict]:
    built_items: List[Dict] = []
    for row in items:
        qty = flt(row.get("qty"))
        if qty <= 0:
            frappe.throw(
                _("Quantity must be greater than 0 for item {0}").format(
                    row.get("item_code")
                ),
                frappe.ValidationError,
            )

        discount_percentage = flt(row.get("discount_percentage", 0))
        discount_amount = flt(row.get("discount_amount", 0))
        batch_no = row.get("batch_no")
        warehouse = row.get("warehouse")
        item_code = row.get("item_code")

        if not discount_percentage and not discount_amount:
            item_group = frappe.db.get_value("Item", item_code, "item_group")
            rule = get_applicable_inventory_discount(
                item_code=item_code,
                company=company,
                warehouse=warehouse,
                batch_no=batch_no,
                item_group=item_group,
            )
            if rule:
                if rule.discount_type == "Percentage":
                    discount_percentage = flt(rule.discount_value)
                else:
                    discount_amount = flt(rule.discount_value)

        built_items.append(
            {
                "item_code": item_code,
                "qty": qty,
                "rate": flt(row.get("rate")) if row.get("rate") is not None else None,
                "uom": row.get("uom"),
                "warehouse": warehouse,
                "discount_percentage": discount_percentage,
                "discount_amount": discount_amount,
                "batch_no": batch_no,
                "serial_no": row.get("serial_no"),
            }
        )
    return built_items


def _recorded_sale_response(doctype: str, name: str) -> Dict:
    """Reply for a retried sale: the invoice that was already created, as if just created."""
    doc = frappe.get_doc(doctype, name)
    return {
        "success": True,
        "duplicate": True,
        "message": _("This sale was already recorded as {0}").format(doc.name),
        "data": {
            "name": doc.name,
            "customer": doc.customer,
            "company": doc.company,
            "posting_date": str(doc.posting_date),
            "grand_total": flt(doc.get("grand_total")),
            "rounded_total": flt(doc.get("rounded_total")),
            "outstanding_amount": flt(doc.get("outstanding_amount")),
            "docstatus": doc.docstatus,
            "is_pos": bool(doc.get("is_pos")) if doctype == "Sales Invoice" else None,
        },
    }


def _lock_gateway_payments_or_recorded(
    gateway_claims: List, company: Optional[str], client_reference: Optional[str]
) -> Optional[Dict]:
    """
    Lock the sale's gateway payments right before the invoice is saved (see
    payment_gateway_api.lock_gateway_payments). When another copy of the same sale got there
    first, return that sale instead of an error.
    """
    from techsavanna_pos.api.payment_gateway_api import find_recorded_sale, lock_gateway_payments

    try:
        lock_gateway_payments(gateway_claims)
    except frappe.ValidationError:
        recorded = find_recorded_sale(company, client_reference)
        if recorded:
            return _recorded_sale_response(*recorded)
        raise
    return None


def _payment_row_has_reference(doctype: str) -> bool:
    """Sales Invoice Payment has reference_no on current ERPNext; older versions may not."""
    child = frappe.get_meta(doctype).get_field("payments")
    return bool(child and frappe.get_meta(child.options).has_field("reference_no"))


def _create_invoice_document(
    doctype: str,
    customer: str,
    items: List[Dict],
    posting_date: Optional[str] = None,
    due_date: Optional[str] = None,
    company: Optional[str] = None,
    is_pos: Optional[bool] = None,
    pos_profile: Optional[str] = None,
    payments: Optional[List[Dict]] = None,
    apply_discount_on: Optional[str] = None,
    additional_discount_percentage: Optional[float] = None,
    discount_amount: Optional[float] = None,
) -> "frappe.model.document.Document":
    if not company:
        company = _get_default_company()
        if not company:
            frappe.throw(
                _(
                    "Company is required. Please set a default company or provide company parameter."
                ),
                frappe.ValidationError,
            )

    _validate_customer(customer)
    _validate_items_exist(items)

    doc = frappe.new_doc(doctype)
    doc.customer = customer
    doc.company = company

    # Posting & due dates
    doc.posting_date = posting_date or nowdate()
    if due_date:
        doc.due_date = due_date

    # POS flags
    if is_pos is not None and doctype == "Sales Invoice":
        doc.is_pos = 1 if is_pos else 0
    if pos_profile and hasattr(doc, "pos_profile"):
        doc.pos_profile = pos_profile

    # Build items
    for row in _build_invoice_items(items, company):
        doc.append("items", row)

    # Discounts
    if apply_discount_on:
        # "Net Total" / "Grand Total" are common options, but we accept any valid value
        doc.apply_discount_on = apply_discount_on
    if additional_discount_percentage is not None:
        doc.additional_discount_percentage = flt(additional_discount_percentage)
    if discount_amount is not None:
        doc.discount_amount = flt(discount_amount)

    # Payments (mainly for POS)
    if payments:
        for p in payments:
            mode_of_payment = p.get("mode_of_payment")
            if not mode_of_payment:
                frappe.throw(
                    _("mode_of_payment is required for all payment rows"),
                    frappe.ValidationError,
                )
            payment_row = {
                "mode_of_payment": mode_of_payment,
                "amount": flt(p.get("amount", 0)),
                "base_amount": flt(p.get("base_amount", p.get("amount", 0))),
                "account": p.get("account"),
            }
            # M-Pesa code, gateway confirmation or bank transfer reference, when the row has one
            if p.get("reference_no") and _payment_row_has_reference(doctype):
                payment_row["reference_no"] = str(p["reference_no"])[:140]
            doc.append("payments", payment_row)

    return doc


@frappe.whitelist()
def create_sales_invoice(
    customer: str,
    items: Union[str, List[Dict]],
    posting_date: Optional[str] = None,
    due_date: Optional[str] = None,
    company: Optional[str] = None,
    is_pos: Optional[bool] = False,
    pos_profile: Optional[str] = None,
    warehouse: Optional[str] = None,
    update_stock: bool = True,
    payments: Optional[Union[str, List[Dict]]] = None,
    apply_discount_on: Optional[str] = None,
    additional_discount_percentage: Optional[float] = None,
    discount_amount: Optional[float] = None,
    do_not_submit: bool = False,
    client_reference: Optional[str] = None,
) -> Dict:
    """
    Create a Sales Invoice for standard or POS sales.

    Args:
        customer: Customer name
        items: JSON string or list of items with item_code, qty, rate, etc.
        posting_date: Posting date (optional, defaults to today)
        due_date: Due date (optional)
        company: Company name (optional, uses default if not provided)
        is_pos: Whether this is a POS invoice (only for Sales Invoice doctype)
        pos_profile: POS Profile to use (optional, mainly for POS flow)
        warehouse: Default warehouse for all items (optional, uses POS Profile warehouse if not provided)
        update_stock: Whether to update stock/inventory (default: True)
        payments: JSON string or list of payment rows (for POS / immediate payment)
        apply_discount_on: Field to apply discount on (Net Total / Grand Total)
        additional_discount_percentage: Additional discount percentage on net total
        discount_amount: Flat discount amount
        do_not_submit: If True, don't submit the document (draft only)
        client_reference: The till's id for this sale. Sending the same id again (a retry after a
            lost reply) returns the invoice already created instead of making a second one.

    Returns:
        dict: Created Sales Invoice details
    """
    import json

    try:
        parsed_items = _parse_items(items)

        parsed_payments: Optional[List[Dict]] = None
        if payments:
            if isinstance(payments, str):
                parsed_payments = json.loads(payments)
            else:
                parsed_payments = payments  # type: ignore[assignment]

        # Gateway payments (M-Pesa, Pesapal, PayPal) must be real, successful and unused
        from techsavanna_pos.api.payment_gateway_api import (
            find_recorded_sale,
            mark_gateway_payments_used,
            remember_sale,
            validate_gateway_payments,
        )

        company = company or _get_default_company()
        recorded = find_recorded_sale(company, client_reference)
        if recorded:
            return _recorded_sale_response(*recorded)
        gateway_claims = validate_gateway_payments(parsed_payments, company) if company else []

        si = _create_invoice_document(
            doctype="Sales Invoice",
            customer=customer,
            items=parsed_items,
            posting_date=posting_date,
            due_date=due_date,
            company=company,
            is_pos=is_pos,
            pos_profile=pos_profile,
            payments=parsed_payments,
            apply_discount_on=apply_discount_on,
            additional_discount_percentage=additional_discount_percentage,
            discount_amount=discount_amount,
        )

        # Set update_stock flag - CRITICAL for inventory reduction
        si.update_stock = 1 if update_stock else 0
        
        # Set warehouse if provided or get from POS Profile
        if warehouse:
            si.set_warehouse = warehouse
            # Set warehouse for items that don't have it
            for item in si.items:
                if not item.warehouse:
                    item.warehouse = warehouse
        elif pos_profile:
            # Try to get warehouse from POS Profile
            try:
                pos_profile_doc = frappe.get_doc("POS Profile", pos_profile)
                if pos_profile_doc.warehouse:
                    si.set_warehouse = pos_profile_doc.warehouse
                    # Set warehouse for items that don't have it
                    for item in si.items:
                        if not item.warehouse:
                            item.warehouse = pos_profile_doc.warehouse
            except Exception:
                pass  # Continue if POS Profile doesn't exist or has no warehouse

        recorded = _lock_gateway_payments_or_recorded(gateway_claims, company, client_reference)
        if recorded:
            return recorded

        si.insert(ignore_permissions=True)

        if not do_not_submit:
            si.submit()

        mark_gateway_payments_used(gateway_claims, "Sales Invoice", si.name)
        remember_sale(company, client_reference, "Sales Invoice", si.name)

        return {
            "success": True,
            "message": _("Sales Invoice created successfully"),
            "data": {
                "name": si.name,
                "customer": si.customer,
                "company": si.company,
                "posting_date": str(si.posting_date),
                "due_date": str(getdate(si.due_date)) if si.get("due_date") else None,
                "is_pos": bool(si.get("is_pos")),
                "grand_total": flt(si.get("grand_total")),
                "rounded_total": flt(si.get("rounded_total")),
                "outstanding_amount": flt(si.get("outstanding_amount")),
                "docstatus": si.docstatus,
            },
        }
    except frappe.ValidationError as e:
        frappe.log_error(
            "Create Sales Invoice Validation Error",
            f"Validation error creating Sales Invoice: {str(e)}",
        )
        return {
            "success": False,
            "message": f"Validation error: {str(e)}",
            "error_type": "validation_error",
        }
    except Exception as e:
        frappe.log_error(
            "Create Sales Invoice Error",
            frappe.get_traceback(),
        )
        return {
            "success": False,
            "message": f"Error creating Sales Invoice: {str(e)}",
        }


@frappe.whitelist()
def create_pos_invoice(
    customer: str,
    items: Union[str, List[Dict]],
    posting_date: Optional[str] = None,
    company: Optional[str] = None,
    pos_profile: Optional[str] = None,
    warehouse: Optional[str] = None,
    update_stock: bool = True,
    payments: Optional[Union[str, List[Dict]]] = None,
    apply_discount_on: Optional[str] = None,
    additional_discount_percentage: Optional[float] = None,
    discount_amount: Optional[float] = None,
    redeem_loyalty_points: Optional[bool] = False,
    loyalty_points: Optional[int] = None,
    do_not_submit: bool = False,
    client_reference: Optional[str] = None,
) -> Dict:
    """
    Create a POS Invoice (used for walk-in POS sales).

    Args:
        customer: Customer name
        items: JSON string or list of items with item_code, qty, rate, etc.
            Each item can have a 'warehouse' field. If not provided, uses document-level warehouse.
        posting_date: Posting date (optional, defaults to today)
        company: Company name (optional, uses default if not provided)
        pos_profile: POS Profile to use (optional, auto-created if not provided)
        warehouse: Default warehouse for all items (optional, uses POS Profile warehouse if not provided)
        update_stock: Whether to update stock/inventory (default: True)
        payments: JSON string or list of payment rows
        apply_discount_on: Field to apply discount on (Net Total / Grand Total)
        additional_discount_percentage: Additional discount percentage on net total
        discount_amount: Flat discount amount
        redeem_loyalty_points: Whether to redeem loyalty points (default: False)
        loyalty_points: Number of loyalty points to redeem (required if redeem_loyalty_points is True)
        do_not_submit: If True, don't submit the document (draft only)
        client_reference: The till's id for this sale. Sending the same id again (a retry after a
            lost reply) returns the invoice already created instead of making a second one.

    Returns:
        dict: Created POS Invoice details
    
    Note:
        - Loyalty points redemption is NOT a payment method - it reduces the invoice total amount
        - After loyalty points are applied, you still need to pay the remaining amount using regular payment methods
        - Customer must be enrolled in a loyalty program and have sufficient points
        - The loyalty_amount is automatically calculated based on the redemption conversion factor
    """
    try:
        parsed_items = _parse_items(items)
        parsed_payments = _parse_payments(payments)

        # Get company (required for POS Profile lookup)
        if not company:
            company = _get_default_company()
            if not company:
                frappe.throw(
                    _(
                        "Company is required. Please set a default company or provide company parameter."
                    ),
                    frappe.ValidationError,
                )

        receivable_account = _resolve_receivable_account(customer, company, throw_if_missing=True)

        # Gateway payments (M-Pesa, Pesapal, PayPal) must be real, successful and unused
        from techsavanna_pos.api.payment_gateway_api import (
            find_recorded_sale,
            mark_gateway_payments_used,
            remember_sale,
            validate_gateway_payments,
        )

        recorded = find_recorded_sale(company, client_reference)
        if recorded:
            return _recorded_sale_response(*recorded)
        gateway_claims = validate_gateway_payments(parsed_payments, company)

        # Ensure every payment row has an account; allow opt-in mapping to receivables
        # Determine invoice type with company override; default to POS Invoice when not set
        invoice_type = _get_invoice_type(company)
        # Align POS Settings to the resolved invoice type so ERPNext validation passes
        # (only on ERPNext versions that have the setting)
        if _pos_settings_has_invoice_type() and _get_pos_settings_invoice_type() != invoice_type:
            _set_pos_settings_invoice_type(invoice_type)

        # Ensure every payment row has an account; allow receivable when POS behavior is intended
        parsed_payments = _apply_payment_accounts(
            parsed_payments,
            company,
            receivable_account,
            allow_receivable_in_payments=invoice_type == "POS Invoice",
        )
        
        # Auto-get or create POS Profile if not provided
        if not pos_profile:
            pos_profile = _get_or_create_pos_profile(company)
        
        # Get POS Profile to get default warehouse if not provided
        pos_profile_doc = frappe.get_doc("POS Profile", pos_profile)
        if not warehouse:
            warehouse = pos_profile_doc.warehouse

        # Auto-create POS Opening Entry if it doesn't exist (only needed for POS Invoice)
        # Must be created BEFORE invoice document creation to pass validation
        if invoice_type == "POS Invoice":
            try:
                opening_entry_name = _get_or_create_pos_opening_entry(pos_profile, company, frappe.session.user)
                if not opening_entry_name:
                    frappe.throw(
                        _("Failed to create POS Opening Entry for POS Profile {0}. Please create it manually or check POS Profile configuration.").format(pos_profile),
                        frappe.ValidationError
                    )
            except Exception as e:
                frappe.log_error("POS Opening Entry Creation Error", f"Error creating POS Opening Entry: {str(e)}")
                frappe.throw(
                    _("Error creating POS Opening Entry for POS Profile {0}: {1}. Please create it manually first.").format(pos_profile, str(e)),
                    frappe.ValidationError
                )
        
        # If POS Settings is set to "Sales Invoice", create Sales Invoice with is_pos=1 instead
        if invoice_type == "Sales Invoice":
            # Use Sales Invoice with is_pos=1
            si = _create_invoice_document(
                doctype="Sales Invoice",
                customer=customer,
                items=parsed_items,
                posting_date=posting_date,
                company=company,
                is_pos=True,
                pos_profile=pos_profile,
                payments=parsed_payments,
                apply_discount_on=apply_discount_on,
                additional_discount_percentage=additional_discount_percentage,
                discount_amount=discount_amount,
            )

            if receivable_account and hasattr(si, "debit_to"):
                si.debit_to = receivable_account
            
            # Set additional fields for POS Sales Invoice BEFORE insert
            si.is_created_using_pos = 1
            si.update_stock = 1 if update_stock else 0
            
            # Set warehouse - MUST be done before insert for stock updates to work
            if warehouse:
                si.set_warehouse = warehouse
            elif pos_profile_doc and pos_profile_doc.warehouse:
                si.set_warehouse = pos_profile_doc.warehouse
                warehouse = pos_profile_doc.warehouse
            
            # Ensure all stock items have warehouses - REQUIRED for update_stock_ledger to work
            if si.update_stock and warehouse:
                for item in si.items:
                    if not item.warehouse:
                        item.warehouse = warehouse
            
            # Handle loyalty points redemption if requested
            # Note: Loyalty points redemption reduces the invoice total amount (not a payment method)
            # After loyalty points are applied, you still need to pay the remaining amount
            if redeem_loyalty_points and loyalty_points:
                si.redeem_loyalty_points = 1
                si.loyalty_points = int(loyalty_points)
                # The loyalty_amount will be calculated automatically during document validation
                # based on the loyalty program's conversion_factor
            
            recorded = _lock_gateway_payments_or_recorded(gateway_claims, company, client_reference)
            if recorded:
                return recorded

            si.insert(ignore_permissions=True)
            
            _finalize_credit_and_outstanding(si, pos_profile_doc, receivable_account)

            if not do_not_submit:
                # Reload document from database to ensure we have the latest state
                si = frappe.get_doc("Sales Invoice", si.name)
                si.submit()

            mark_gateway_payments_used(gateway_claims, "Sales Invoice", si.name)
            remember_sale(company, client_reference, "Sales Invoice", si.name)

            return {
                "success": True,
                "message": _("POS Invoice created successfully (as Sales Invoice)"),
                "data": {
                    "name": si.name,
                    "customer": si.customer,
                    "company": si.company,
                    "posting_date": str(si.posting_date),
                    "grand_total": flt(si.get("grand_total")),
                    "rounded_total": flt(si.get("rounded_total")),
                    "outstanding_amount": flt(si.get("outstanding_amount")),
                    "docstatus": si.docstatus,
                    "is_pos": True,
                },
            }
        
        # Create POS Invoice (default behavior)
        pi = _create_invoice_document(
            doctype="POS Invoice",
            customer=customer,
            items=parsed_items,
            posting_date=posting_date,
            company=company,
            pos_profile=pos_profile,
            payments=parsed_payments,
            apply_discount_on=apply_discount_on,
            additional_discount_percentage=additional_discount_percentage,
            discount_amount=discount_amount,
        )

        if receivable_account and hasattr(pi, "debit_to"):
            pi.debit_to = receivable_account
        
        # Set stock update and warehouse BEFORE insert
        pi.update_stock = 1 if update_stock else 0
        
        # Set warehouse - MUST be done before insert for stock updates to work
        if warehouse:
            pi.set_warehouse = warehouse
        elif pos_profile_doc and pos_profile_doc.warehouse:
            pi.set_warehouse = pos_profile_doc.warehouse
            warehouse = pos_profile_doc.warehouse
        
        # Ensure all stock items have warehouses - REQUIRED for update_stock_ledger to work
        if pi.update_stock and warehouse:
            for item in pi.items:
                if not item.warehouse:
                    item.warehouse = warehouse
        
        # Handle loyalty points redemption if requested
        # Note: Loyalty points redemption reduces the invoice total amount (not a payment method)
        # After loyalty points are applied, you still need to pay the remaining amount
        if redeem_loyalty_points and loyalty_points:
            pi.redeem_loyalty_points = 1
            pi.loyalty_points = int(loyalty_points)
            # The loyalty_amount will be calculated automatically during document validation
            # based on the loyalty program's conversion_factor

        recorded = _lock_gateway_payments_or_recorded(gateway_claims, company, client_reference)
        if recorded:
            return recorded

        pi.insert(ignore_permissions=True)

        _finalize_credit_and_outstanding(pi, pos_profile_doc, receivable_account)

        if not do_not_submit:
            # Reload document from database to ensure we have the latest state
            # This ensures the overridden POS Invoice class is properly used
            pi = frappe.get_doc("POS Invoice", pi.name)
            pi.submit()

        mark_gateway_payments_used(gateway_claims, "POS Invoice", pi.name)
        remember_sale(company, client_reference, "POS Invoice", pi.name)

        return {
            "success": True,
            "message": _("POS Invoice created successfully"),
            "data": {
                "name": pi.name,
                "customer": pi.customer,
                "company": pi.company,
                "posting_date": str(pi.posting_date),
                "grand_total": flt(pi.get("grand_total")),
                "rounded_total": flt(pi.get("rounded_total")),
                "outstanding_amount": flt(pi.get("outstanding_amount")),
                "docstatus": pi.docstatus,
            },
        }
    except frappe.ValidationError as e:
        frappe.log_error(
            "Create POS Invoice Validation Error",
            f"Validation error creating POS Invoice: {str(e)}",
        )
        return {
            "success": False,
            "message": f"Validation error: {str(e)}",
            "error_type": "validation_error",
        }
    except Exception as e:
        frappe.log_error(
            "Create POS Invoice Error",
            f"Error creating POS Invoice: {str(e)}",
        )
        return {
            "success": False,
            "message": f"Error creating POS Invoice: {str(e)}",
        }


@frappe.whitelist()
def get_sales_invoice(name: str) -> Dict:
    """
    Get a single Sales Invoice by name.
    """
    try:
        if not frappe.db.exists("Sales Invoice", name):
            return {
                "success": False,
                "message": _("Sales Invoice {0} does not exist").format(name),
            }

        si = frappe.get_doc("Sales Invoice", name)

        return {
            "success": True,
            "data": si.as_dict(),
        }
    except Exception as e:
        frappe.log_error(
            "Get Sales Invoice Error",
            f"Error fetching Sales Invoice {name}: {str(e)}",
        )
        return {
            "success": False,
            "message": f"Error fetching Sales Invoice: {str(e)}",
        }


@frappe.whitelist()
def list_sales_invoices(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    customer: Optional[str] = None,
    company: Optional[str] = None,
    status: Optional[str] = None,
    is_pos: Optional[bool] = None,
    limit_start: int = 0,
    limit_page_length: int = 20,
) -> Dict:
    """
    List Sales Invoices with optional filters.
    """
    try:
        if not company:
            company = _get_default_company()

        filters: Dict = {}
        if company:
            filters["company"] = company
        if from_date:
            filters["posting_date"] = (">=", from_date)
        if to_date:
            filters.setdefault("posting_date", (">=", from_date or "1900-01-01"))
            filters["posting_date"] = ["between", [from_date or "1900-01-01", to_date]]
        if customer:
            filters["customer"] = customer
        if status:
            filters["status"] = status
        if is_pos is not None:
            filters["is_pos"] = 1 if is_pos else 0

        data = frappe.db.get_all(
            "Sales Invoice",
            filters=filters,
            fields=[
                "name",
                "customer",
                "posting_date",
                "company",
                "grand_total",
                "rounded_total",
                "outstanding_amount",
                "status",
                "is_pos",
                "docstatus",
            ],
            order_by="posting_date desc, name desc",
            limit_start=limit_start,
            limit_page_length=limit_page_length,
        )

        return {
            "success": True,
            "data": data,
            "count": len(data),
        }
    except Exception as e:
        frappe.log_error(
            "List Sales Invoices Error",
            f"Error listing Sales Invoices: {str(e)}",
        )
        return {
            "success": False,
            "message": f"Error listing Sales Invoices: {str(e)}",
        }


@frappe.whitelist()
def cancel_sales_invoice(name: str, reason: Optional[str] = None) -> Dict:
    """
    Cancel a submitted Sales Invoice.

    Args:
        name: Sales Invoice name
        reason: Optional cancellation reason (stored in remarks)
    """
    try:
        if not frappe.db.exists("Sales Invoice", name):
            return {
                "success": False,
                "message": _("Sales Invoice {0} does not exist").format(name),
            }

        si = frappe.get_doc("Sales Invoice", name)

        if si.docstatus != 1:
            return {
                "success": False,
                "message": _(
                    "Only submitted Sales Invoices (docstatus 1) can be cancelled"
                ),
            }

        if reason:
            # Append reason to existing remarks
            remarks = (si.get("remarks") or "") + f"\nCancellation Reason: {reason}"
            si.remarks = remarks.strip()

        si.cancel()

        return {
            "success": True,
            "message": _("Sales Invoice cancelled successfully"),
            "data": {
                "name": si.name,
                "docstatus": si.docstatus,
                "status": si.status,
            },
        }
    except Exception as e:
        frappe.log_error(
            "Cancel Sales Invoice Error",
            f"Error cancelling Sales Invoice {name}: {str(e)}",
        )
        return {
            "success": False,
            "message": f"Error cancelling Sales Invoice: {str(e)}",
        }


def _parse_payments(payments: Optional[Union[str, List[Dict]]]) -> Optional[List[Dict]]:
    """Parse payments that can be passed as JSON string or list."""
    import json

    if not payments:
        return None

    if isinstance(payments, str):
        payments = json.loads(payments)

    if not isinstance(payments, list):
        frappe.throw(
            _("Payments must be a list"),
            frappe.ValidationError,
        )
    return payments


def _resolve_receivable_account(customer: str, company: str, throw_if_missing: bool = False) -> Optional[str]:
    """Return the best receivable account for a customer within a company."""
    party_account = frappe.db.get_value(
        "Party Account",
        {"parent": customer, "parenttype": "Customer", "company": company},
        "account",
    )
    default_receivable = frappe.db.get_value("Company", company, "default_receivable_account")
    account = party_account or default_receivable

    if throw_if_missing and not account:
        frappe.throw(
            _(
                "No receivable account configured for customer {0} in company {1}. "
                "Set a Party Account on the customer or configure the company's Default Receivable Account."
            ).format(customer, company),
            frappe.ValidationError,
        )

    return account


def _apply_payment_accounts(
    payments: Optional[List[Dict]],
    company: str,
    receivable_account: Optional[str] = None,
    allow_receivable_in_payments: bool = False,
) -> Optional[List[Dict]]:
    """
    Ensure every payment row has an account.
    If `use_receivable_account` is truthy, map to the receivable account only when allowed.
    """
    if not payments:
        return payments

    resolved: List[Dict] = []
    for row in payments:
        payment = dict(row)
        mode_of_payment = payment.get("mode_of_payment")
        if not mode_of_payment:
            frappe.throw(_("mode_of_payment is required for all payment rows"), frappe.ValidationError)

        wants_receivable = bool(payment.get("use_receivable_account"))

        if not payment.get("account"):
            account = None
            if wants_receivable:
                if not allow_receivable_in_payments:
                    frappe.throw(
                        _(
                            "Receivable accounts are not allowed in payments for this invoice type. "
                            "Omit the payment row or switch invoice type to POS Invoice."
                        ),
                        frappe.ValidationError,
                    )
                account = receivable_account
            if not account:
                account = frappe.db.get_value(
                    "Mode of Payment Account",
                    {"parent": mode_of_payment, "company": company},
                    "default_account",
                )
            if not account and wants_receivable:
                account = receivable_account

            if not account:
                frappe.throw(
                    _(
                        "No account configured for Mode of Payment {0} in company {1}. "
                        "Add a Mode of Payment Account or provide `account` / set `use_receivable_account`."
                    ).format(mode_of_payment, company),
                    frappe.ValidationError,
                )
            payment["account"] = account
        elif wants_receivable:
            if not allow_receivable_in_payments:
                frappe.throw(
                    _(
                        "Receivable accounts are not allowed in payments for this invoice type. "
                        "Omit the payment row or switch invoice type to POS Invoice."
                    ),
                    frappe.ValidationError,
                )
            payment["account"] = receivable_account

        resolved.append(payment)

    return resolved


def _finalize_credit_and_outstanding(
    doc: "frappe.model.document.Document",
    pos_profile_doc,
    receivable_account: Optional[str],
) -> None:
    """Set receivable account, recompute outstanding, check credit limit, and enable partial payments on the POS Profile when needed."""
    if receivable_account and hasattr(doc, "debit_to"):
        doc.debit_to = receivable_account

    # Ensure paid_amount is calculated from payments table
    if hasattr(doc, "set_paid_amount"):
        doc.set_paid_amount()
    
    # Recalculate outstanding amount
    invoice_total = flt(doc.get("rounded_total") or doc.get("grand_total") or 0)
    paid_amount = flt(doc.get("paid_amount") or 0)
    if invoice_total:
        doc.outstanding_amount = invoice_total - paid_amount if invoice_total > paid_amount else 0

    # Save before submit so validations use the updated values
    doc.save(ignore_permissions=True)
    
    # Check credit limit if there's an outstanding amount (credit payment)
    # This needs to be done before submit to validate against credit limit
    if doc.outstanding_amount > 0 and hasattr(doc, "check_credit_limit"):
        # Check if credit limit validation should be performed
        from erpnext.selling.doctype.customer.customer import check_credit_limit, get_credit_limit
        
        # Only check if customer has a credit limit set
        credit_limit = get_credit_limit(doc.customer, doc.company)
        if credit_limit:
            # Check if items are linked to sales orders/delivery notes
            validate_against_credit_limit = False
            bypass_credit_limit_check_at_sales_order = frappe.db.get_value(
                "Customer Credit Limit",
                filters={"parent": doc.customer, "parenttype": "Customer", "company": doc.company},
                fieldname=["bypass_credit_limit_check"],
            )
            
            if bypass_credit_limit_check_at_sales_order:
                validate_against_credit_limit = True
            
            # If any item is not linked to SO/DN, validate against credit limit
            if not validate_against_credit_limit:
                for item in doc.get("items", []):
                    if not (item.get("sales_order") or item.get("delivery_note")):
                        validate_against_credit_limit = True
                        break
            
            if validate_against_credit_limit:
                # Pass the outstanding amount as extra_amount to include it in credit limit check
                check_credit_limit(
                    doc.customer, 
                    doc.company, 
                    ignore_outstanding_sales_order=bypass_credit_limit_check_at_sales_order,
                    extra_amount=doc.outstanding_amount
                )

    # Allow partial payments on the POS Profile if this invoice has an outstanding balance
    if (
        pos_profile_doc
        and invoice_total > paid_amount
        and hasattr(pos_profile_doc, "allow_partial_payment")
        and not pos_profile_doc.allow_partial_payment
    ):
        pos_profile_doc.allow_partial_payment = 1
        pos_profile_doc.save(ignore_permissions=True)


def _update_invoice_items(doc, items: List[Dict]) -> None:
    """Update invoice items by replacing all existing items."""
    # Clear existing items
    doc.items = []
    # Add new items
    for row in _build_invoice_items(items, doc.company):
        doc.append("items", row)


def _update_invoice_payments(doc, payments: List[Dict]) -> None:
    """Update invoice payments by replacing all existing payments."""
    # Clear existing payments
    doc.payments = []
    # Add new payments
    for p in payments:
        mode_of_payment = p.get("mode_of_payment")
        if not mode_of_payment:
            frappe.throw(
                _("mode_of_payment is required for all payment rows"),
                frappe.ValidationError,
            )
        doc.append(
            "payments",
            {
                "mode_of_payment": mode_of_payment,
                "amount": flt(p.get("amount", 0)),
                "base_amount": flt(p.get("base_amount", p.get("amount", 0))),
                "account": p.get("account"),
            },
        )


@frappe.whitelist()
def list_payment_methods(company: Optional[str] = None, only_enabled: bool = True) -> Dict:
    """List modes of payment with optional company-specific account mapping."""
    filters = {}
    if only_enabled:
        filters["enabled"] = 1

    methods = frappe.get_all(
        "Mode of Payment",
        filters=filters,
        fields=["name", "type", "enabled"],
        order_by="name asc",
    )

    results = []
    for mop in methods:
        account_filters = {"parent": mop.name}
        if company:
            account_filters["company"] = company

        account_fields = ["company", "default_account"]
        # Older schemas may not have currency; include it only when present.
        if frappe.db.has_column("Mode of Payment Account", "currency"):
            account_fields.append("currency")
        elif frappe.db.has_column("Mode of Payment Account", "default_currency"):
            account_fields.append("default_currency")

        accounts = frappe.get_all(
            "Mode of Payment Account",
            filters=account_filters,
            fields=account_fields,
        )

        results.append(
            {
                "name": mop.name,
                "type": mop.type,
                "enabled": mop.enabled,
                "accounts": accounts,
            }
        )

    return {"success": True, "data": results}


@frappe.whitelist()
def create_credit_mode_of_payment(
    company: str,
    default_account: str,
    mop_type: str = "Bank",
    currency: Optional[str] = None,
    enabled: int = 1,
) -> Dict:
    """
    Create or enable a 'Credit' Mode of Payment and add its company account row.
    Safe to call multiple times (idempotent).
    """
    if not company:
        frappe.throw(_("company is required"), frappe.ValidationError)
    if not default_account:
        # Fallback to company's default receivable if caller omits account
        default_account = frappe.db.get_value("Company", company, "default_receivable_account")
        if not default_account:
            frappe.throw(
                _("default_account is required (or set the Company's Default Receivable Account)"),
                frappe.ValidationError,
            )

    # Validate account exists
    if not frappe.db.exists("Account", default_account):
        frappe.throw(
            _("Default Account {0} not found. Use a valid Account (e.g., company receivable).").format(
                frappe.bold(default_account)
            ),
            frappe.LinkValidationError,
        )

    mop_name = "Credit"
    mop_doc = frappe.get_doc("Mode of Payment", mop_name) if frappe.db.exists("Mode of Payment", mop_name) else frappe.new_doc("Mode of Payment")

    mop_doc.mode_of_payment = mop_name
    mop_doc.name = mop_name  # ensure consistent naming
    if mop_type:
        mop_doc.type = mop_type
    mop_doc.enabled = cint(enabled)

    # Ensure account row for the company exists/updated
    account_row = None
    for row in mop_doc.get("accounts", []):
        if row.company == company:
            account_row = row
            break

    has_currency = frappe.db.has_column("Mode of Payment Account", "currency")
    has_default_currency = frappe.db.has_column("Mode of Payment Account", "default_currency")

    if not account_row:
        account_row = mop_doc.append("accounts", {"company": company})

    account_row.default_account = default_account
    if currency:
        if has_currency:
            account_row.currency = currency
        elif has_default_currency:
            account_row.default_currency = currency

    mop_doc.flags.ignore_permissions = True
    mop_doc.save()

    return {
        "success": True,
        "message": _("Credit mode of payment configured"),
        "data": {
            "mode_of_payment": mop_name,
            "type": mop_doc.type,
            "enabled": mop_doc.enabled,
            "company": company,
            "default_account": account_row.default_account,
            "currency": getattr(account_row, "currency", None) or getattr(account_row, "default_currency", None),
        },
    }


@frappe.whitelist()
def get_pos_invoice_type(company: Optional[str] = None) -> Dict:
    """
    Return the POS invoice_type.
    - If company is provided, returns the company override when set.
    - Otherwise returns the global POS Settings value (defaults to 'POS Invoice').
    """
    return {"success": True, "data": {"invoice_type": _get_invoice_type(company)}}


@frappe.whitelist()
def set_pos_invoice_type(invoice_type: str, company: Optional[str] = None) -> Dict:
    """
    Set the POS invoice_type.
    - Allowed values: 'POS Invoice', 'Sales Invoice'
    - If company is provided, stores a per-company override via defaults (pos_invoice_type::<company>).
    - Otherwise sets the global POS Settings value.
    """
    allowed = {"POS Invoice", "Sales Invoice"}
    if invoice_type not in allowed:
        frappe.throw(_("invoice_type must be one of: {0}").format(", ".join(allowed)), frappe.ValidationError)

    if company:
        frappe.db.set_default(f"pos_invoice_type::{company}", invoice_type)
    else:
        if not _set_pos_settings_invoice_type(invoice_type):
            frappe.throw(
                _("Cannot set invoice_type: Field 'invoice_type' does not exist on POS Settings doctype. Please use company-specific invoice type instead or add the field to POS Settings."),
                frappe.ValidationError
            )

    return {
        "success": True,
        "message": _("POS invoice type updated"),
        "data": {"invoice_type": invoice_type, "scope": "company" if company else "global"},
    }


@frappe.whitelist()
def get_receivable_account(customer: str, company: str) -> Dict:
    """Return the receivable account to use for a customer in a company."""
    if not customer:
        frappe.throw(_("customer is required"), frappe.ValidationError)
    if not company:
        frappe.throw(_("company is required"), frappe.ValidationError)

    account = _resolve_receivable_account(customer, company)
    return {
        "success": True,
        "data": {
            "account": account,
            "source": (
                "party_account"
                if account and frappe.db.exists(
                    "Party Account",
                    {"parent": customer, "parenttype": "Customer", "company": company, "account": account},
                )
                else "company_default" if account else None
            ),
        },
        "message": "Receivable account found" if account else "No receivable account configured",
    }


@frappe.whitelist()
def update_sales_invoice(
    name: str,
    customer: Optional[str] = None,
    items: Optional[Union[str, List[Dict]]] = None,
    posting_date: Optional[str] = None,
    due_date: Optional[str] = None,
    company: Optional[str] = None,
    is_pos: Optional[bool] = None,
    pos_profile: Optional[str] = None,
    payments: Optional[Union[str, List[Dict]]] = None,
    apply_discount_on: Optional[str] = None,
    additional_discount_percentage: Optional[float] = None,
    discount_amount: Optional[float] = None,
    do_not_submit: bool = False,
) -> Dict:
    """
    Update an existing Sales Invoice. Can only update draft invoices (docstatus 0).

    Args:
        name: Sales Invoice name to update
        customer: Update customer name
        items: Update items list (replaces all existing items if provided)
        posting_date: Update posting date
        due_date: Update due date
        company: Update company
        is_pos: Update POS flag
        pos_profile: Update POS profile
        payments: Update payment rows (replaces all existing payments if provided)
        apply_discount_on: Update discount application field
        additional_discount_percentage: Update additional discount percentage
        discount_amount: Update discount amount
        do_not_submit: If True, invoice remains as draft after update

    Returns:
        dict: Updated Sales Invoice details
    """
    try:
        if not frappe.db.exists("Sales Invoice", name):
            return {
                "success": False,
                "message": _("Sales Invoice {0} not found").format(name),
                "error_type": "not_found",
            }

        si = frappe.get_doc("Sales Invoice", name)

        # Only allow updating draft invoices
        if si.docstatus != 0:
            return {
                "success": False,
                "message": _("Cannot update submitted invoice. Only draft invoices can be updated."),
                "error_type": "validation_error",
                "docstatus": si.docstatus,
            }

        # Update fields
        if customer:
            _validate_customer(customer)
            si.customer = customer
        if company:
            si.company = company
        if posting_date:
            si.posting_date = posting_date
        if due_date:
            si.due_date = due_date
        if is_pos is not None:
            si.is_pos = 1 if is_pos else 0
        if pos_profile:
            si.pos_profile = pos_profile

        # Update items (replaces all existing items)
        if items:
            parsed_items = _parse_items(items)
            _update_invoice_items(si, parsed_items)

        # Update payments (replaces all existing payments)
        if payments:
            parsed_payments = _parse_payments(payments)
            if parsed_payments:
                _update_invoice_payments(si, parsed_payments)

        # Update discounts
        if apply_discount_on:
            si.apply_discount_on = apply_discount_on
        if additional_discount_percentage is not None:
            si.additional_discount_percentage = flt(additional_discount_percentage)
        if discount_amount is not None:
            si.discount_amount = flt(discount_amount)

        # Save changes
        si.save(ignore_permissions=True)

        # Submit if requested
        if not do_not_submit:
            si.submit()

        return {
            "success": True,
            "message": _("Sales Invoice updated successfully"),
            "data": {
                "name": si.name,
                "customer": si.customer,
                "company": si.company,
                "posting_date": str(si.posting_date),
                "due_date": str(getdate(si.due_date)) if si.get("due_date") else None,
                "is_pos": bool(si.get("is_pos")),
                "grand_total": flt(si.get("grand_total")),
                "rounded_total": flt(si.get("rounded_total")),
                "outstanding_amount": flt(si.get("outstanding_amount")),
                "docstatus": si.docstatus,
            },
        }
    except frappe.ValidationError as e:
        frappe.log_error(
            "Update Sales Invoice Validation Error",
            f"Validation error updating Sales Invoice {name}: {str(e)}",
        )
        return {
            "success": False,
            "message": f"Validation error: {str(e)}",
            "error_type": "validation_error",
        }
    except Exception as e:
        frappe.log_error(
            "Update Sales Invoice Error",
            f"Error updating Sales Invoice {name}: {str(e)}",
        )
        return {
            "success": False,
            "message": f"Error updating Sales Invoice: {str(e)}",
        }


@frappe.whitelist()
def update_pos_invoice(
    name: str,
    customer: Optional[str] = None,
    items: Optional[Union[str, List[Dict]]] = None,
    posting_date: Optional[str] = None,
    company: Optional[str] = None,
    pos_profile: Optional[str] = None,
    payments: Optional[Union[str, List[Dict]]] = None,
    apply_discount_on: Optional[str] = None,
    additional_discount_percentage: Optional[float] = None,
    discount_amount: Optional[float] = None,
    do_not_submit: bool = False,
) -> Dict:
    """
    Update an existing POS Invoice. Can only update draft invoices (docstatus 0).

    Args:
        name: POS Invoice name to update
        customer: Update customer name
        items: Update items list (replaces all existing items if provided)
        posting_date: Update posting date
        company: Update company
        pos_profile: Update POS profile
        payments: Update payment rows (replaces all existing payments if provided)
        apply_discount_on: Update discount application field
        additional_discount_percentage: Update additional discount percentage
        discount_amount: Update discount amount
        do_not_submit: If True, invoice remains as draft after update

    Returns:
        dict: Updated POS Invoice details
    """
    try:
        if not frappe.db.exists("POS Invoice", name):
            return {
                "success": False,
                "message": _("POS Invoice {0} not found").format(name),
                "error_type": "not_found",
            }

        pi = frappe.get_doc("POS Invoice", name)

        # Only allow updating draft invoices
        if pi.docstatus != 0:
            return {
                "success": False,
                "message": _("Cannot update submitted invoice. Only draft invoices can be updated."),
                "error_type": "validation_error",
                "docstatus": pi.docstatus,
            }

        # Update fields
        if customer:
            _validate_customer(customer)
            pi.customer = customer
        if company:
            pi.company = company
        if posting_date:
            pi.posting_date = posting_date
        if pos_profile:
            pi.pos_profile = pos_profile

        # Update items (replaces all existing items)
        if items:
            parsed_items = _parse_items(items)
            _update_invoice_items(pi, parsed_items)

        # Update payments (replaces all existing payments)
        if payments:
            parsed_payments = _parse_payments(payments)
            if parsed_payments:
                _update_invoice_payments(pi, parsed_payments)

        # Update discounts
        if apply_discount_on:
            pi.apply_discount_on = apply_discount_on
        if additional_discount_percentage is not None:
            pi.additional_discount_percentage = flt(additional_discount_percentage)
        if discount_amount is not None:
            pi.discount_amount = flt(discount_amount)

        # Save changes
        pi.save(ignore_permissions=True)

        # Submit if requested
        if not do_not_submit:
            pi.submit()

        return {
            "success": True,
            "message": _("POS Invoice updated successfully"),
            "data": {
                "name": pi.name,
                "customer": pi.customer,
                "company": pi.company,
                "posting_date": str(pi.posting_date),
                "grand_total": flt(pi.get("grand_total")),
                "rounded_total": flt(pi.get("rounded_total")),
                "outstanding_amount": flt(pi.get("outstanding_amount")),
                "docstatus": pi.docstatus,
            },
        }
    except frappe.ValidationError as e:
        frappe.log_error(
            "Update POS Invoice Validation Error",
            f"Validation error updating POS Invoice {name}: {str(e)}",
        )
        return {
            "success": False,
            "message": f"Validation error: {str(e)}",
            "error_type": "validation_error",
        }
    except Exception as e:
        frappe.log_error(
            "Update POS Invoice Error",
            f"Error updating POS Invoice {name}: {str(e)}",
        )
        return {
            "success": False,
            "message": f"Error updating POS Invoice: {str(e)}",
        }


@frappe.whitelist()
def get_pos_invoice(name: str) -> Dict:
    """
    Get a single POS Invoice by name.
    """
    try:
        if not frappe.db.exists("POS Invoice", name):
            return {
                "success": False,
                "message": _("POS Invoice {0} not found").format(name),
                "error_type": "not_found",
            }

        pi = frappe.get_doc("POS Invoice", name)

        return {
            "success": True,
            "data": pi.as_dict(),
        }
    except Exception as e:
        frappe.log_error(
            "Get POS Invoice Error",
            f"Error fetching POS Invoice {name}: {str(e)}",
        )
        return {
            "success": False,
            "message": f"Error fetching POS Invoice: {str(e)}",
        }


@frappe.whitelist()
def list_pos_invoices(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    customer: Optional[str] = None,
    company: Optional[str] = None,
    status: Optional[str] = None,
    pos_profile: Optional[str] = None,
    limit_start: int = 0,
    limit_page_length: int = 20,
) -> Dict:
    """
    List POS Invoices with optional filters.
    """
    try:
        if not company:
            company = _get_default_company()

        filters: Dict = {}
        if company:
            filters["company"] = company
        if from_date:
            filters["posting_date"] = (">=", from_date)
        if to_date:
            filters.setdefault("posting_date", (">=", from_date or "1900-01-01"))
            filters["posting_date"] = ["between", [from_date or "1900-01-01", to_date]]
        if customer:
            filters["customer"] = customer
        if status:
            filters["status"] = status
        if pos_profile:
            filters["pos_profile"] = pos_profile

        data = frappe.db.get_all(
            "POS Invoice",
            filters=filters,
            fields=[
                "name",
                "customer",
                "posting_date",
                "company",
                "grand_total",
                "rounded_total",
                "outstanding_amount",
                "paid_amount",
                "status",
                "pos_profile",
                "docstatus",
            ],
            order_by="posting_date desc, name desc",
            limit_start=limit_start,
            limit_page_length=limit_page_length,
        )

        names = [row.name for row in data] if data else []
        credit_accounts: set[str] = set()
        payments_by_parent: Dict[str, List[Dict]] = {}

        if names:
            payments = frappe.get_all(
                "Sales Invoice Payment",
                filters={"parent": ["in", names], "parenttype": "POS Invoice"},
                fields=["parent", "mode_of_payment", "account", "amount"],
            )
            accounts = {p.account for p in payments if p.account}
            if accounts:
                account_info = frappe.get_all(
                    "Account",
                    filters={"name": ["in", list(accounts)]},
                    fields=["name", "account_type"],
                )
                credit_accounts = {
                    a.name
                    for a in account_info
                    if (a.account_type or "").lower() == "receivable"
                }

            for p in payments:
                payments_by_parent.setdefault(p.parent, []).append(p)

        enriched: List[Dict] = []
        for row in data:
            total = flt(row.grand_total or row.rounded_total or 0)
            paid_amount = flt(row.paid_amount or 0)
            outstanding = flt(row.outstanding_amount or 0)

            payment_rows = payments_by_parent.get(row.name, [])
            credit_amount = 0.0
            for p in payment_rows:
                mode = (p.mode_of_payment or "").lower()
                if (p.account and p.account in credit_accounts) or mode == "credit":
                    credit_amount += flt(p.amount or 0)

            # Treat credit components as not settled for display purposes
            credit_outstanding = max(total - (paid_amount - credit_amount), 0)
            is_partial = (
                row.docstatus == 1
                and total > 0
                and (credit_outstanding > 0)
                and (paid_amount - credit_amount) > 0
            )

            row.is_partially_paid = is_partial
            row.credit_amount = credit_amount
            row.credit_outstanding = credit_outstanding
            if is_partial:
                row.status = "Partly Paid"

            enriched.append(row)

        return {
            "success": True,
            "data": enriched,
            "count": len(enriched),
        }
    except Exception as e:
        frappe.log_error(
            "List POS Invoices Error",
            f"Error listing POS Invoices: {str(e)}",
        )
        return {
            "success": False,
            "message": f"Error listing POS Invoices: {str(e)}",
        }


@frappe.whitelist()
def cancel_pos_invoice(name: str, reason: Optional[str] = None) -> Dict:
    """
    Cancel a submitted POS Invoice.

    Args:
        name: POS Invoice name
        reason: Optional cancellation reason (stored in remarks)
    """
    try:
        if not frappe.db.exists("POS Invoice", name):
            return {
                "success": False,
                "message": _("POS Invoice {0} not found").format(name),
                "error_type": "not_found",
            }

        pi = frappe.get_doc("POS Invoice", name)

        if pi.docstatus != 1:
            return {
                "success": False,
                "message": _(
                    "Only submitted POS Invoices (docstatus 1) can be cancelled"
                ),
                "error_type": "validation_error",
            }

        if reason:
            # Append reason to existing remarks
            remarks = (pi.get("remarks") or "") + f"\nCancellation Reason: {reason}"
            pi.remarks = remarks.strip()

        pi.cancel()

        return {
            "success": True,
            "message": _("POS Invoice cancelled successfully"),
            "data": {
                "name": pi.name,
                "docstatus": pi.docstatus,
                "status": pi.status,
            },
        }
    except Exception as e:
        frappe.log_error(
            "Cancel POS Invoice Error",
            f"Error cancelling POS Invoice {name}: {str(e)}",
        )
        return {
            "success": False,
            "message": f"Error cancelling POS Invoice: {str(e)}",
        }


def _build_credit_note_items(items: List[Dict], return_against: Optional[str] = None) -> List[Dict]:
    """Build items for credit note/sales return."""
    built_items: List[Dict] = []
    
    # If return_against is provided, try to get original invoice for rate matching
    original_invoice = None
    if return_against:
        if frappe.db.exists("Sales Invoice", return_against):
            original_invoice = frappe.get_doc("Sales Invoice", return_against)
    
    for row in items:
        qty = flt(row.get("qty"))
        if qty <= 0:
            frappe.throw(
                _("Quantity must be greater than 0 for item {0}").format(
                    row.get("item_code")
                ),
                frappe.ValidationError,
            )

        # Callers send how many units came back as a positive number; ERPNext
        # rejects a return unless its lines carry negative quantities.
        item_data = {
            "item_code": row.get("item_code"),
            "qty": -qty,
            "rate": flt(row.get("rate")) if row.get("rate") is not None else None,
            "uom": row.get("uom"),
            "warehouse": row.get("warehouse"),
            "discount_percentage": flt(row.get("discount_percentage", 0)),
            "discount_amount": flt(row.get("discount_amount", 0)),
            "batch_no": row.get("batch_no"),
            "serial_no": row.get("serial_no"),
        }

        # Link each line to the original invoice row so ERPNext can check it
        # against what was sold and what has already been returned.
        if return_against:
            original_row = row.get("sales_invoice_item") or row.get("against_sales_invoice_item")
            if original_row:
                item_data["sales_invoice_item"] = original_row

            # If rate not provided, try to get from original invoice
            if item_data["rate"] is None and original_invoice:
                for orig_item in original_invoice.items:
                    if (original_row and orig_item.name == original_row) or (
                        not original_row and orig_item.item_code == row.get("item_code")
                    ):
                        item_data["rate"] = orig_item.rate
                        break

        built_items.append(item_data)
    
    return built_items


# Returns hand money back, so only managers and owners may record or cancel them.
RETURN_ROLES = {"System Manager", "Sales Manager", "Accounts Manager"}


def _check_return_access(company: Optional[str] = None, invoice: Optional[str] = None) -> str:
    """Refuse cashiers and other businesses; return the company the return belongs to."""
    from techsavanna_pos.api.payment_gateway_common import resolve_company

    if frappe.session.user != "Administrator" and not RETURN_ROLES.intersection(frappe.get_roles()):
        frappe.throw(
            _("Only a Sales Manager, Accounts Manager or the owner can record or cancel returns."),
            frappe.PermissionError,
        )
    if invoice:
        invoice_company = frappe.db.get_value("Sales Invoice", invoice, "company")
        if invoice_company:
            if company and company != invoice_company:
                frappe.throw(_("Invoice {0} belongs to another company.").format(invoice), frappe.ValidationError)
            company = invoice_company
    # resolve_company refuses a company the user does not belong to
    return resolve_company(company)


@frappe.whitelist()
def create_sales_return(
    customer: str,
    items: Union[str, List[Dict]],
    return_against: Optional[str] = None,
    posting_date: Optional[str] = None,
    company: Optional[str] = None,
    apply_discount_on: Optional[str] = None,
    additional_discount_percentage: Optional[float] = None,
    discount_amount: Optional[float] = None,
    do_not_submit: bool = False,
    reason: Optional[str] = None,
) -> Dict:
    """
    Create a Sales Return (Credit Note) against an existing Sales Invoice or as a standalone credit note.

    Args:
        customer: Customer name
        items: JSON string or list of returned items
        return_against: Original Sales Invoice name (optional, for automatic matching)
        posting_date: Posting date (optional, defaults to today)
        company: Company name (optional, uses default if not provided)
        apply_discount_on: Field to apply discount on
        additional_discount_percentage: Additional discount percentage
        discount_amount: Flat discount amount
        do_not_submit: If True, credit note will be saved as draft only
        reason: Reason for return

    Returns:
        dict: Created Sales Return details
    """
    import json

    company = _check_return_access(company, return_against)

    try:
        parsed_items = _parse_items(items)

        _validate_customer(customer)
        _validate_items_exist(parsed_items)

        # Validate return_against if provided
        if return_against:
            if not frappe.db.exists("Sales Invoice", return_against):
                frappe.throw(
                    _("Sales Invoice {0} does not exist").format(return_against),
                    frappe.ValidationError,
                )

        # Create Credit Note
        cn = frappe.new_doc("Sales Invoice")
        cn.is_return = 1
        cn.customer = customer
        cn.company = company
        cn.posting_date = posting_date or nowdate()

        if return_against:
            cn.return_against = return_against
            # A POS sale takes stock out at the till; its return must put the stock
            # back the same way, or returned goods never reappear in inventory.
            cn.update_stock = frappe.db.get_value("Sales Invoice", return_against, "update_stock") or 0

        if reason:
            cn.remarks = reason

        # Build items
        for row in _build_credit_note_items(parsed_items, return_against):
            cn.append("items", row)

        # Discounts
        if apply_discount_on:
            cn.apply_discount_on = apply_discount_on
        if additional_discount_percentage is not None:
            cn.additional_discount_percentage = flt(additional_discount_percentage)
        if discount_amount is not None:
            cn.discount_amount = flt(discount_amount)

        cn.insert(ignore_permissions=True)

        if not do_not_submit:
            cn.submit()

        return {
            "success": True,
            "message": _("Sales Return created successfully"),
            "data": {
                "name": cn.name,
                "customer": cn.customer,
                "company": cn.company,
                "return_against": cn.get("return_against"),
                "posting_date": str(cn.posting_date),
                "grand_total": flt(cn.get("grand_total")),
                "rounded_total": flt(cn.get("rounded_total")),
                "outstanding_amount": flt(cn.get("outstanding_amount")),
                "docstatus": cn.docstatus,
            },
        }
    except frappe.ValidationError as e:
        frappe.log_error(
            "Create Sales Return Validation Error",
            f"Validation error creating Sales Return: {str(e)}",
        )
        return {
            "success": False,
            "message": f"Validation error: {str(e)}",
            "error_type": "validation_error",
        }
    except Exception as e:
        frappe.log_error(
            "Create Sales Return Error",
            f"Error creating Sales Return: {str(e)}",
        )
        return {
            "success": False,
            "message": f"Error creating Sales Return: {str(e)}",
        }


@frappe.whitelist()
def get_sales_return(name: str) -> Dict:
    """
    Get a single Sales Return (Credit Note) by name.
    """
    try:
        if not frappe.db.exists("Sales Invoice", name):
            return {
                "success": False,
                "message": _("Sales Return {0} not found").format(name),
                "error_type": "not_found",
            }

        cn = frappe.get_doc("Sales Invoice", name)

        # Verify it's actually a credit note
        if not cn.is_return:
            return {
                "success": False,
                "message": _("Document {0} is not a Sales Return").format(name),
                "error_type": "validation_error",
            }

        from techsavanna_pos.api.payment_gateway_common import resolve_company

        resolve_company(cn.company)

        return {
            "success": True,
            "data": cn.as_dict(),
        }
    except Exception as e:
        frappe.log_error(
            "Get Sales Return Error",
            f"Error fetching Sales Return {name}: {str(e)}",
        )
        return {
            "success": False,
            "message": f"Error fetching Sales Return: {str(e)}",
        }


@frappe.whitelist()
def list_sales_returns(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    customer: Optional[str] = None,
    company: Optional[str] = None,
    return_against: Optional[str] = None,
    status: Optional[str] = None,
    limit_start: int = 0,
    limit_page_length: int = 20,
) -> Dict:
    """
    List Sales Returns (Credit Notes) with optional filters.
    """
    try:
        from techsavanna_pos.api.payment_gateway_common import resolve_company

        company = resolve_company(company)

        filters: Dict = {"is_return": 1}
        if company:
            filters["company"] = company
        if from_date:
            filters["posting_date"] = (">=", from_date)
        if to_date:
            filters.setdefault("posting_date", (">=", from_date or "1900-01-01"))
            filters["posting_date"] = ["between", [from_date or "1900-01-01", to_date]]
        if customer:
            filters["customer"] = customer
        if return_against:
            filters["return_against"] = return_against
        if status:
            filters["status"] = status

        data = frappe.db.get_all(
            "Sales Invoice",
            filters=filters,
            fields=[
                "name",
                "customer",
                "return_against",
                "posting_date",
                "company",
                "grand_total",
                "rounded_total",
                "outstanding_amount",
                "status",
                "docstatus",
            ],
            order_by="posting_date desc, name desc",
            limit_start=limit_start,
            limit_page_length=limit_page_length,
        )

        return {
            "success": True,
            "data": data,
            "count": len(data),
        }
    except Exception as e:
        frappe.log_error(
            "List Sales Returns Error",
            f"Error listing Sales Returns: {str(e)}",
        )
        return {
            "success": False,
            "message": f"Error listing Sales Returns: {str(e)}",
        }


@frappe.whitelist()
def cancel_sales_return(name: str, reason: Optional[str] = None) -> Dict:
    """
    Cancel a submitted Sales Return (Credit Note).

    Args:
        name: Credit Note name
        reason: Optional cancellation reason (stored in remarks)
    """
    _check_return_access(invoice=name)

    try:
        if not frappe.db.exists("Sales Invoice", name):
            return {
                "success": False,
                "message": _("Sales Return {0} not found").format(name),
                "error_type": "not_found",
            }

        cn = frappe.get_doc("Sales Invoice", name)

        # Verify it's actually a credit note
        if not cn.is_return:
            return {
                "success": False,
                "message": _("Document {0} is not a Sales Return").format(name),
                "error_type": "validation_error",
            }

        if cn.docstatus != 1:
            return {
                "success": False,
                "message": _(
                    "Only submitted Sales Returns (docstatus 1) can be cancelled"
                ),
                "error_type": "validation_error",
            }

        if reason:
            # Append reason to existing remarks
            remarks = (cn.get("remarks") or "") + f"\nCancellation Reason: {reason}"
            cn.remarks = remarks.strip()

        cn.cancel()

        return {
            "success": True,
            "message": _("Sales Return cancelled successfully"),
            "data": {
                "name": cn.name,
                "docstatus": cn.docstatus,
                "status": cn.status,
            },
        }
    except Exception as e:
        frappe.log_error(
            "Cancel Sales Return Error",
            f"Error cancelling Sales Return {name}: {str(e)}",
        )
        return {
            "success": False,
            "message": f"Error cancelling Sales Return: {str(e)}",
        }


@frappe.whitelist()
def submit_invoice(name: str, invoice_type: Optional[str] = None) -> Dict:
    """
    Submit a draft Sales Invoice or POS Invoice to change its status from draft (docstatus 0) to submitted (docstatus 1).

    Args:
        name: Invoice name to submit
        invoice_type: Either "sales_invoice" or "pos_invoice". If omitted, auto-detected from name prefix.

    Returns:
        dict: Submitted invoice details
    """
    try:
        # Auto-detect invoice type if not provided
        if not invoice_type:
            if name.startswith("SINV-") or name.startswith("SI-"):
                invoice_type = "sales_invoice"
            elif name.startswith("POS-INV-") or name.startswith("PI-"):
                invoice_type = "pos_invoice"
            else:
                # Try to determine by checking which doctype exists
                if frappe.db.exists("Sales Invoice", name):
                    invoice_type = "sales_invoice"
                elif frappe.db.exists("POS Invoice", name):
                    invoice_type = "pos_invoice"
                else:
                    return {
                        "success": False,
                        "message": _("Invoice {0} not found").format(name),
                        "error_type": "not_found",
                    }

        # Get the appropriate doctype
        if invoice_type == "sales_invoice":
            doctype = "Sales Invoice"
        elif invoice_type == "pos_invoice":
            doctype = "POS Invoice"
        else:
            return {
                "success": False,
                "message": _("Invalid invoice_type. Must be 'sales_invoice' or 'pos_invoice'"),
                "error_type": "validation_error",
            }

        if not frappe.db.exists(doctype, name):
            return {
                "success": False,
                "message": _("{0} {1} not found").format(doctype, name),
                "error_type": "not_found",
            }

        doc = frappe.get_doc(doctype, name)

        if doc.docstatus != 0:
            return {
                "success": False,
                "message": _("Invoice is already submitted"),
                "error_type": "validation_error",
                "docstatus": doc.docstatus,
            }

        doc.submit()

        return {
            "success": True,
            "message": _("Invoice submitted successfully"),
            "data": {
                "name": doc.name,
                "docstatus": doc.docstatus,
                "status": doc.status,
            },
        }
    except Exception as e:
        frappe.log_error(
            "Submit Invoice Error",
            f"Error submitting invoice {name}: {str(e)}",
        )
        return {
            "success": False,
            "message": f"Error submitting invoice: {str(e)}",
        }


@frappe.whitelist()
def create_pos_opening_entry(
    pos_profile: str,
    company: str = None,
    user: str = None,
    balance_details: List[Dict] = None
) -> Dict:
    """Manually create a POS Opening Entry for a POS Profile
    
    Args:
        pos_profile: POS Profile name
        company: Company name (optional, uses default if not provided)
        user: User/cashier name (optional, uses current user if not provided)
        balance_details: List of payment method opening balances (optional, auto-created if not provided)
            Format: [{"mode_of_payment": "Cash", "opening_amount": 0.0}, ...]
        
    Returns:
        Created POS Opening Entry details
    """
    try:
        if frappe.session.user == "Guest":
            frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
        
        # Get company if not provided
        if not company:
            company = frappe.defaults.get_user_default("Company")
            if not company:
                frappe.throw(_("Company is required. Please set a default company or provide company parameter."))
        
        # Get user if not provided
        if not user:
            user = frappe.session.user
        
        # Create POS Opening Entry using helper function
        opening_entry_name = _get_or_create_pos_opening_entry(
            pos_profile=pos_profile,
            company=company,
            user=user,
            balance_details=balance_details,
        )
        
        if not opening_entry_name:
            return {
                "success": False,
                "message": _("Failed to create POS Opening Entry. Check error logs for details."),
            }
        
        # Get the created entry
        opening_entry = frappe.get_doc("POS Opening Entry", opening_entry_name)
        
        return {
            "success": True,
            "message": _("POS Opening Entry created successfully"),
            "data": {
                "name": opening_entry.name,
                "pos_profile": opening_entry.pos_profile,
                "company": opening_entry.company,
                "user": opening_entry.user,
                "status": opening_entry.status,
                "posting_date": str(opening_entry.posting_date),
                "period_start_date": str(opening_entry.period_start_date),
                "balance_details": [
                    {
                        "mode_of_payment": detail.mode_of_payment,
                        "opening_amount": detail.opening_amount
                    }
                    for detail in opening_entry.balance_details
                ]
            }
        }
    except frappe.ValidationError as e:
        frappe.log_error("POS Opening Entry Validation Error", frappe.get_traceback())
        return {
            "success": False,
            "message": f"Validation error: {str(e)}",
            "error_type": "validation_error",
        }
    except Exception as e:
        frappe.log_error("POS Opening Entry Error", frappe.get_traceback())
        return {
            "success": False,
            "message": f"Error creating POS Opening Entry: {str(e)}",
        }


@frappe.whitelist()
def list_pos_opening_entries(
    pos_profile: Optional[str] = None,
    company: Optional[str] = None,
    user: Optional[str] = None,
    status: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    limit_start: int = 0,
    limit_page_length: int = 20,
) -> Dict:
    """
    List POS Opening Entries with optional filters.
    
    Args:
        pos_profile: Filter by POS Profile name
        company: Filter by Company name
        user: Filter by User/Cashier name
        status: Filter by status (Draft, Open, Closed, Cancelled)
        from_date: Filter by posting_date >= from_date
        to_date: Filter by posting_date <= to_date
        limit_start: Pagination start (default: 0)
        limit_page_length: Number of records per page (default: 20)
        
    Returns:
        dict: List of POS Opening Entries with details
    """
    try:
        filters: Dict = {}
        
        if pos_profile:
            filters["pos_profile"] = pos_profile
        if company:
            filters["company"] = company
        if user:
            filters["user"] = user
        if status:
            filters["status"] = status
        # Handle date range properly
        if from_date and to_date:
            filters["posting_date"] = ["between", [from_date, to_date]]
        elif from_date:
            filters["posting_date"] = [">=", from_date]
        elif to_date:
            filters["posting_date"] = ["<=", to_date]
        
        data = frappe.get_all(
            "POS Opening Entry",
            filters=filters,
            fields=[
                "name",
                "pos_profile",
                "company",
                "user",
                "status",
                "posting_date",
                "period_start_date",
                "period_end_date",
                "pos_closing_entry",
                "docstatus",
                "creation",
                "modified",
            ],
            order_by="posting_date desc, period_start_date desc",
            limit_start=limit_start,
            limit_page_length=limit_page_length,
        )
        
        # Get balance details for each entry
        for entry in data:
            opening_entry_doc = frappe.get_doc("POS Opening Entry", entry.name)
            entry["balance_details"] = [
                {
                    "mode_of_payment": detail.mode_of_payment,
                    "opening_amount": detail.opening_amount
                }
                for detail in opening_entry_doc.balance_details
            ]
        
        return {
            "success": True,
            "data": data,
            "count": len(data),
        }
    except Exception as e:
        frappe.log_error(
            "List POS Opening Entries Error",
            f"Error listing POS Opening Entries: {str(e)}",
        )
        return {
            "success": False,
            "message": f"Error listing POS Opening Entries: {str(e)}",
        }


@frappe.whitelist()
def get_pos_opening_entry(name: str) -> Dict:
    """
    Get a single POS Opening Entry by name with full details.
    
    Args:
        name: POS Opening Entry name
        
    Returns:
        dict: POS Opening Entry details
    """
    try:
        if not frappe.db.exists("POS Opening Entry", name):
            return {
                "success": False,
                "message": _("POS Opening Entry {0} not found").format(name),
                "error_type": "not_found",
            }
        
        opening_entry = frappe.get_doc("POS Opening Entry", name)
        
        return {
            "success": True,
            "data": {
                "name": opening_entry.name,
                "pos_profile": opening_entry.pos_profile,
                "company": opening_entry.company,
                "user": opening_entry.user,
                "status": opening_entry.status,
                "posting_date": str(opening_entry.posting_date),
                "period_start_date": str(opening_entry.period_start_date),
                "period_end_date": str(opening_entry.period_end_date) if opening_entry.period_end_date else None,
                "pos_closing_entry": opening_entry.pos_closing_entry,
                "docstatus": opening_entry.docstatus,
                "balance_details": [
                    {
                        "mode_of_payment": detail.mode_of_payment,
                        "opening_amount": detail.opening_amount
                    }
                    for detail in opening_entry.balance_details
                ],
                "creation": str(opening_entry.creation),
                "modified": str(opening_entry.modified),
            },
        }
    except Exception as e:
        frappe.log_error(
            "Get POS Opening Entry Error",
            f"Error fetching POS Opening Entry {name}: {str(e)}",
        )
        return {
            "success": False,
            "message": f"Error fetching POS Opening Entry: {str(e)}",
        }


@frappe.whitelist()
def close_pos_opening_entry(
    pos_opening_entry: str,
    do_not_submit: bool = False,
) -> Dict:
    """
    Close a POS Opening Entry by creating a POS Closing Entry.
    This will consolidate all invoices associated with the opening entry.
    
    Args:
        pos_opening_entry: POS Opening Entry name to close
        do_not_submit: If True, the closing entry will be saved as draft only
        
    Returns:
        dict: Created POS Closing Entry details
    """
    try:
        if not frappe.db.exists("POS Opening Entry", pos_opening_entry):
            return {
                "success": False,
                "message": _("POS Opening Entry {0} not found").format(pos_opening_entry),
                "error_type": "not_found",
            }
        
        opening_entry = frappe.get_doc("POS Opening Entry", pos_opening_entry)
        _ensure_can_manage_shift(opening_entry)
        
        # Check if opening entry is open
        if opening_entry.status != "Open":
            return {
                "success": False,
                "message": _(
                    "POS Opening Entry {0} is not open (status: {1}). Only open entries can be closed."
                ).format(pos_opening_entry, opening_entry.status),
                "error_type": "validation_error",
            }
        
        # Check if opening entry is submitted
        if opening_entry.docstatus != 1:
            return {
                "success": False,
                "message": _(
                    "POS Opening Entry {0} is not submitted (docstatus: {1}). Only submitted entries can be closed."
                ).format(pos_opening_entry, opening_entry.docstatus),
                "error_type": "validation_error",
            }
        
        # Import the function to create closing entry from opening entry
        from erpnext.accounts.doctype.pos_closing_entry.pos_closing_entry import (
            make_closing_entry_from_opening,
        )
        
        # A failed earlier attempt leaves an unsubmitted closing entry behind; remove it so
        # the shift is closed from one fresh entry with every receipt up to now
        for draft in frappe.get_all(
            "POS Closing Entry",
            filters={"pos_opening_entry": opening_entry.name, "docstatus": 0},
            pluck="name",
        ):
            frappe.delete_doc("POS Closing Entry", draft, ignore_permissions=True)

        # Create the closing entry
        closing_entry = make_closing_entry_from_opening(opening_entry)
        
        # Set flags to allow saving
        closing_entry.flags.ignore_permissions = True
        
        # Save the closing entry
        closing_entry.insert(ignore_permissions=True)
        
        # Commit to ensure it's saved
        frappe.db.commit()
        
        # Submit if requested
        if not do_not_submit:
            # Submitting merges the shift's receipts into a Sales Invoice, which ERPNext saves
            # with the current user's permissions. A cashier who may close the shift (checked
            # above) need not be allowed to create invoices, so this step runs with full rights.
            with _as_administrator():
                closing_entry.submit()
            frappe.db.commit()
        
        # Reload to get updated status
        closing_entry.reload()
        
        return {
            "success": True,
            "message": _("POS Opening Entry closed successfully"),
            "data": {
                "closing_entry": {
                    "name": closing_entry.name,
                    "pos_opening_entry": closing_entry.pos_opening_entry,
                    "pos_profile": closing_entry.pos_profile,
                    "company": closing_entry.company,
                    "user": closing_entry.user,
                    "status": closing_entry.status,
                    "posting_date": str(closing_entry.posting_date),
                    "period_start_date": str(closing_entry.period_start_date),
                    "period_end_date": str(closing_entry.period_end_date),
                    "grand_total": flt(closing_entry.grand_total),
                    "net_total": flt(closing_entry.net_total),
                    "total_quantity": flt(closing_entry.total_quantity),
                    "total_taxes_and_charges": flt(closing_entry.total_taxes_and_charges),
                    "docstatus": closing_entry.docstatus,
                },
                "opening_entry": {
                    "name": opening_entry.name,
                    "status": opening_entry.status,
                },
            },
        }
    except frappe.ValidationError as e:
        return {**_shift_error("close", pos_opening_entry, e), "error_type": "validation_error"}
    except Exception as e:
        return _shift_error("close", pos_opening_entry, e)


# Roles that may close or cancel any shift of their own business, not only their own
SHIFT_MANAGER_ROLES = {"System Manager", "Accounts Manager", "Sales Manager"}


def _ensure_can_manage_shift(opening_entry) -> None:
    """Only the cashier who opened a shift, or a manager of the same business, may close or cancel it."""
    from techsavanna_pos.api.payment_gateway_common import get_user_companies

    user = frappe.session.user
    if user == "Administrator":
        return
    if user == "Guest":
        frappe.throw(_("Please sign in first."), frappe.AuthenticationError)
    if opening_entry.company not in get_user_companies(user):
        frappe.throw(_("This shift belongs to another business."), frappe.PermissionError)
    if opening_entry.user != user and not SHIFT_MANAGER_ROLES.intersection(frappe.get_roles(user)):
        frappe.throw(
            _("Only the cashier who opened this shift ({0}) or a manager can close or cancel it.").format(
                opening_entry.user
            ),
            frappe.PermissionError,
        )


@contextmanager
def _as_administrator():
    """Run the block as Administrator, then restore the signed-in user exactly as before.

    frappe.set_user edits the session in place, so it is given a copy and the real session
    is put back afterwards; the user's login stays untouched. A background job started in
    the block (ERPNext queues the merge for 10 or more receipts) also runs as Administrator.
    """
    saved = {
        key: getattr(frappe.local, key, None)
        for key in ("session", "cache", "form_dict", "jenv", "role_permissions", "new_doc_templates", "user_perms")
    }
    frappe.local.session = frappe._dict(frappe.local.session)
    frappe.set_user("Administrator")
    try:
        yield
    finally:
        for key, value in saved.items():
            setattr(frappe.local, key, value)


def _shift_error(action: str, name: str, e: Exception) -> Dict:
    """A failed close or cancel: save the full details to the Error Log and give the
    cashier a message that is never empty (some exceptions carry no text at all)."""
    frappe.log_error(title=f"POS shift {action} failed: {name}", message=frappe.get_traceback())
    detail = str(e).strip()
    if not detail:
        detail = _(
            "The server stopped with {0} and gave no reason. The full details are in the Error Log "
            "under \"POS shift {1} failed: {2}\"."
        ).format(type(e).__name__, action, name)
    return {"success": False, "message": detail}


@frappe.whitelist()
def cancel_pos_opening_entry(name: str, reason: Optional[str] = None) -> Dict:
    """
    Cancel a POS Opening Entry (only if it has no invoices).
    
    Args:
        name: POS Opening Entry name to cancel
        reason: Optional cancellation reason
        
    Returns:
        dict: Cancellation result
    """
    try:
        if not frappe.db.exists("POS Opening Entry", name):
            return {
                "success": False,
                "message": _("POS Opening Entry {0} not found").format(name),
                "error_type": "not_found",
            }
        
        opening_entry = frappe.get_doc("POS Opening Entry", name)
        _ensure_can_manage_shift(opening_entry)
        
        # Check if it's already cancelled
        if opening_entry.status == "Cancelled":
            return {
                "success": False,
                "message": _("POS Opening Entry {0} is already cancelled").format(name),
                "error_type": "validation_error",
            }
        
        # Check if it has invoices (can't cancel if it has invoices)
        from frappe.utils import get_datetime
        
        has_invoices = _check_pos_opening_entry_has_invoices(
            opening_entry.period_start_date,
            get_datetime(),
            opening_entry.pos_profile,
            opening_entry.user
        )
        
        if has_invoices:
            return {
                "success": False,
                "message": _(
                    "Cannot cancel POS Opening Entry {0} because it has invoice(s). "
                    "Please close it by creating a POS Closing Entry instead."
                ).format(name),
                "error_type": "validation_error",
            }
        
        # Add reason if provided
        if reason:
            remarks = (opening_entry.get("remarks") or "") + f"\nCancellation Reason: {reason}"
            opening_entry.remarks = remarks.strip()
        
        # Cancel the entry
        opening_entry.flags.ignore_permissions = True
        opening_entry.cancel()
        frappe.db.commit()
        
        return {
            "success": True,
            "message": _("POS Opening Entry cancelled successfully"),
            "data": {
                "name": opening_entry.name,
                "status": opening_entry.status,
                "docstatus": opening_entry.docstatus,
            },
        }
    except frappe.ValidationError as e:
        return {**_shift_error("cancel", name, e), "error_type": "validation_error"}
    except Exception as e:
        return _shift_error("cancel", name, e)


@frappe.whitelist()
def create_payment_entry_for_invoice(
    sales_invoice: str,
    paid_amount: Optional[float] = None,
    mode_of_payment: Optional[str] = None,
    bank_account: Optional[str] = None,
    posting_date: Optional[str] = None,
    reference_no: Optional[str] = None,
    reference_date: Optional[str] = None,
    remarks: Optional[str] = None,
    submit: bool = True,
) -> Dict:
    """
    Create a Payment Entry against a Sales Invoice to record payment received.
    This moves the invoice from "Partly Paid" or "Unpaid" to "Paid" status when fully paid.
    
    Args:
        sales_invoice: Sales Invoice name/ID (e.g., "SINV-00001")
        paid_amount: Amount to pay (optional, defaults to outstanding_amount if not provided)
        mode_of_payment: Mode of payment name (e.g., "Cash", "Bank Transfer")
        bank_account: Bank account name (optional, required for bank payments)
        posting_date: Posting date (optional, defaults to today)
        reference_no: Payment reference number (optional, e.g., cheque number, transaction ID)
        reference_date: Reference date (optional)
        remarks: Additional remarks/notes (optional)
        submit: Whether to submit the payment entry (default: True)
    
    Returns:
        dict: Payment Entry details and updated invoice status
    """
    try:
        # Validate sales invoice exists
        if not frappe.db.exists("Sales Invoice", sales_invoice):
            return {
                "success": False,
                "message": _("Sales Invoice {0} not found").format(sales_invoice),
                "error_type": "not_found",
            }
        
        # Get sales invoice
        si = frappe.get_doc("Sales Invoice", sales_invoice)
        
        # Validate invoice is submitted
        if si.docstatus != 1:
            return {
                "success": False,
                "message": _("Sales Invoice {0} must be submitted before receiving payment").format(sales_invoice),
                "error_type": "validation_error",
            }
        
        # Check if already fully paid
        if flt(si.outstanding_amount) <= 0:
            return {
                "success": False,
                "message": _("Sales Invoice {0} is already fully paid").format(sales_invoice),
                "error_type": "validation_error",
            }
        
        # Use outstanding amount if paid_amount not provided
        if paid_amount is None:
            paid_amount = flt(si.outstanding_amount)
        else:
            paid_amount = flt(paid_amount)
        
        # Validate paid amount
        if paid_amount <= 0:
            return {
                "success": False,
                "message": _("Paid amount must be greater than zero"),
                "error_type": "validation_error",
            }
        
        if paid_amount > flt(si.outstanding_amount):
            return {
                "success": False,
                "message": _("Paid amount ({0}) cannot be greater than outstanding amount ({1})").format(
                    paid_amount, si.outstanding_amount
                ),
                "error_type": "validation_error",
            }
        
        # Get default mode of payment if not provided
        if not mode_of_payment:
            # Try to get from company defaults or POS profile
            mode_of_payment = frappe.db.get_value("Company", si.company, "default_mode_of_payment")
            if not mode_of_payment:
                # Get first available mode of payment
                mop = frappe.db.get_value("Mode of Payment", {"enabled": 1}, "name", order_by="name")
                if not mop:
                    return {
                        "success": False,
                        "message": _("No mode of payment found. Please configure at least one Mode of Payment."),
                        "error_type": "validation_error",
                    }
                mode_of_payment = mop
        
        # Validate mode of payment exists
        if not frappe.db.exists("Mode of Payment", mode_of_payment):
            return {
                "success": False,
                "message": _("Mode of Payment {0} not found").format(mode_of_payment),
                "error_type": "not_found",
            }
        
        # Import payment entry utilities
        from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
        from erpnext.accounts.party import get_party_account
        from erpnext.accounts.utils import get_account_currency
        from frappe.utils import nowdate, getdate
        
        # Create payment entry using ERPNext utility
        pe = get_payment_entry(
            dt="Sales Invoice",
            dn=sales_invoice,
            party_amount=paid_amount,
            bank_account=bank_account,
        )
        
        # Override mode of payment if provided
        if mode_of_payment:
            pe.mode_of_payment = mode_of_payment
        
        # Set posting date
        if posting_date:
            pe.posting_date = getdate(posting_date)
        else:
            pe.posting_date = getdate(nowdate())
        
        # Set reference details
        if reference_no:
            pe.reference_no = reference_no
        if reference_date:
            pe.reference_date = getdate(reference_date)
        if remarks:
            pe.remarks = remarks
        
        # Adjust allocated amount if partial payment
        if paid_amount < flt(si.outstanding_amount):
            # Update the reference allocated amount
            if pe.references:
                pe.references[0].allocated_amount = paid_amount
                pe.references[0].outstanding_amount = flt(si.outstanding_amount)
        
        # Save payment entry
        pe.insert(ignore_permissions=True)
        
        # Submit if requested
        if submit:
            pe.submit()
            frappe.db.commit()
        
        # Reload sales invoice to get updated status
        si.reload()
        
        return {
            "success": True,
            "message": _("Payment Entry created successfully"),
            "data": {
                "payment_entry": {
                    "name": pe.name,
                    "payment_type": pe.payment_type,
                    "party": pe.party,
                    "party_type": pe.party_type,
                    "paid_amount": flt(pe.paid_amount),
                    "received_amount": flt(pe.received_amount),
                    "posting_date": str(pe.posting_date),
                    "mode_of_payment": pe.mode_of_payment,
                    "docstatus": pe.docstatus,
                },
                "sales_invoice": {
                    "name": si.name,
                    "outstanding_amount": flt(si.outstanding_amount),
                    "status": si.status,
                    "paid_amount": flt(si.paid_amount) if hasattr(si, "paid_amount") else None,
                },
            },
        }
    
    except frappe.ValidationError as e:
        frappe.log_error(
            "Create Payment Entry Validation Error",
            f"Validation error creating Payment Entry for Sales Invoice {sales_invoice}: {str(e)}",
        )
        return {
            "success": False,
            "message": f"Validation error: {str(e)}",
            "error_type": "validation_error",
        }
    except Exception as e:
        frappe.log_error(
            "Create Payment Entry Error",
            f"Error creating Payment Entry for Sales Invoice {sales_invoice}: {str(e)}",
        )
        return {
            "success": False,
            "message": f"Error creating Payment Entry: {str(e)}",
            "error_type": "server_error",
        }


@frappe.whitelist()
def get_invoice_payment_status(sales_invoice: str) -> Dict:
    """
    Get the current payment status and outstanding amount of a Sales Invoice.
    
    Args:
        sales_invoice: Sales Invoice name/ID
    
    Returns:
        dict: Invoice payment status details
    """
    try:
        if not frappe.db.exists("Sales Invoice", sales_invoice):
            return {
                "success": False,
                "message": _("Sales Invoice {0} not found").format(sales_invoice),
                "error_type": "not_found",
            }
        
        si = frappe.get_doc("Sales Invoice", sales_invoice)
        
        # Get payment entries linked to this invoice
        payment_entries = frappe.get_all(
            "Payment Entry Reference",
            filters={
                "reference_doctype": "Sales Invoice",
                "reference_name": sales_invoice,
            },
            fields=["parent", "allocated_amount", "reference_date"],
        )
        
        payment_entry_names = [pe.parent for pe in payment_entries]
        total_paid = sum(flt(pe.allocated_amount) for pe in payment_entries)
        
        # Get payment entry details
        payment_details = []
        if payment_entry_names:
            pes = frappe.get_all(
                "Payment Entry",
                filters={"name": ["in", payment_entry_names]},
                fields=["name", "posting_date", "paid_amount", "mode_of_payment", "docstatus", "status"],
            )
            payment_details = pes
        
        return {
            "success": True,
            "data": {
                "sales_invoice": {
                    "name": si.name,
                    "customer": si.customer,
                    "grand_total": flt(si.grand_total),
                    "outstanding_amount": flt(si.outstanding_amount),
                    "paid_amount": flt(si.grand_total) - flt(si.outstanding_amount),
                    "status": si.status,
                    "posting_date": str(si.posting_date),
                    "due_date": str(si.due_date) if si.due_date else None,
                },
                "payment_summary": {
                    "total_paid": total_paid,
                    "outstanding_amount": flt(si.outstanding_amount),
                    "is_fully_paid": flt(si.outstanding_amount) <= 0,
                    "payment_count": len(payment_entry_names),
                },
                "payment_entries": payment_details,
            },
        }
    
    except Exception as e:
        frappe.log_error(
            "Get Payment Status Error",
            f"Error getting payment status for Sales Invoice {sales_invoice}: {str(e)}",
        )
        return {
            "success": False,
            "message": f"Error getting payment status: {str(e)}",
            "error_type": "server_error",
        }



