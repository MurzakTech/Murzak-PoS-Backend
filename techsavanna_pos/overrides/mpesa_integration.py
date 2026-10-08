"""
MPESA Integration for POS Invoice and Sales Invoice
Adds MPESA payment support to invoices
"""

import frappe
from frappe import _
from frappe.model.document import Document


def get_mpesa_payment_method(company: str) -> str:
    """
    Get MPESA Mode of Payment for a company
    
    Args:
        company: Company name
        
    Returns:
        Mode of Payment name or None
    """
    try:
        settings_name = frappe.db.get_value(
            "MPESA Settings",
            {"company": company, "is_active": 1},
            "name"
        )
        
        if settings_name:
            settings = frappe.get_doc("MPESA Settings", settings_name)
            return settings.mode_of_payment
        
        return None
    except Exception:
        return None


def add_mpesa_to_pos_profile(pos_profile: str, company: str) -> None:
    """
    Add MPESA to POS Profile payment methods if not already present
    
    Args:
        pos_profile: POS Profile name
        company: Company name
    """
    try:
        mpesa_mop = get_mpesa_payment_method(company)
        if not mpesa_mop:
            return
        
        pos_profile_doc = frappe.get_doc("POS Profile", pos_profile)
        
        # Check if MPESA already in payment methods
        mpesa_exists = False
        for payment in pos_profile_doc.payments:
            if payment.mode_of_payment == mpesa_mop:
                mpesa_exists = True
                break
        
        # Add MPESA if not exists
        if not mpesa_exists:
            pos_profile_doc.append("payments", {
                "mode_of_payment": mpesa_mop,
                "default": 0,
                "allow_in_returns": 1
            })
            pos_profile_doc.flags.ignore_gateway_hook = True
            pos_profile_doc.save(ignore_permissions=True)
            frappe.db.commit()
            
    except Exception as e:
        frappe.log_error(
            f"Error adding MPESA to POS Profile {pos_profile}: {str(e)}",
            "MPESA Integration Error"
        )


@frappe.whitelist()
def initiate_mpesa_payment_from_invoice(
    invoice_type: str,
    invoice_name: str,
    phone_number: str = None
) -> dict:
    """
    Initiate MPESA payment from an invoice
    
    Args:
        invoice_type: Sales Invoice or POS Invoice
        invoice_name: Invoice name
        phone_number: Customer phone number (optional, will use invoice customer's phone if not provided)
        
    Returns:
        Dictionary with transaction details
    """
    # Validate authentication
    if frappe.session.user == "Guest":
        frappe.throw(
            _("Authentication required. Please log in to initiate MPESA payment."),
            frappe.AuthenticationError
        )
    
    # Validate invoice type
    if invoice_type not in ["Sales Invoice", "POS Invoice"]:
        frappe.throw(_("Invalid invoice type. Must be Sales Invoice or POS Invoice"), frappe.ValidationError)
    
    # Get invoice
    try:
        invoice = frappe.get_doc(invoice_type, invoice_name)
    except frappe.DoesNotExistError:
        frappe.throw(_("Invoice {0} not found").format(invoice_name), frappe.ValidationError)
    
    # Get company, refusing invoices of other businesses
    company = invoice.company
    from techsavanna_pos.api.payment_gateway_common import ensure_record_company, normalize_kenyan_phone

    ensure_record_company(company)
    
    # Get phone number
    if not phone_number:
        # Try to get from customer contact
        customer = invoice.customer
        contact = frappe.db.get_value(
            "Contact",
            {"link_doctype": "Customer", "link_name": customer},
            ["mobile_no", "phone"],
            as_dict=True
        )
        
        if contact:
            phone_number = contact.mobile_no or contact.phone
        
        if not phone_number:
            frappe.throw(
                _("Phone number is required. Please provide phone_number parameter or set customer contact phone number."),
                frappe.ValidationError
            )
    
    phone_number = normalize_kenyan_phone(phone_number)

    if invoice.docstatus != 1:
        frappe.throw(_("Submit the invoice before collecting payment for it"), frappe.ValidationError)

    # Get amount (outstanding amount or grand total)
    amount = invoice.outstanding_amount

    if not amount or amount <= 0:
        frappe.throw(_("Invoice has no outstanding amount"), frappe.ValidationError)
    
    # Generate reference
    reference = invoice.name
    
    # Generate description
    description = f"Payment for {invoice_type} {invoice.name}"
    
    # Import here to avoid circular imports
    from techsavanna_pos.api.mpesa_api import initiate_stk_push_payment
    
    # Initiate STK Push
    result = initiate_stk_push_payment(
        company=company,
        phone_number=phone_number,
        amount=amount,
        reference=reference,
        description=description,
        invoice_type=invoice_type,
        invoice_name=invoice_name,
        # Record the money against the invoice when the customer pays
        settle_invoice=1 if invoice_type == "Sales Invoice" else 0,
    )
    
    return result


def on_pos_profile_update(doc: Document, method: str = None) -> None:
    """
    Hook: When POS Profile is updated, ensure MPESA is in payment methods
    """
    if doc.flags.ignore_gateway_hook:
        return
    try:
        if doc.company:
            add_mpesa_to_pos_profile(doc.name, doc.company)
            from techsavanna_pos.api.payment_gateway_api import add_gateway_modes_to_pos_profile

            add_gateway_modes_to_pos_profile(doc.name, doc.company)
    except Exception as e:
        frappe.log_error(
            f"Error in on_pos_profile_update hook: {str(e)}",
            "MPESA Integration Error"
        )


def on_pos_invoice_update(doc: Document, method: str = None) -> None:
    """
    Hook: When POS Invoice is updated after submit, check for MPESA payments
    This can be used to trigger payment status checks if needed
    """
    try:
        # This hook can be extended to check payment status
        # For now, it's a placeholder for future enhancements
        pass
    except Exception as e:
        frappe.log_error(
            f"Error in on_pos_invoice_update hook: {str(e)}",
            "MPESA Integration Error"
        )


def on_sales_invoice_update(doc: Document, method: str = None) -> None:
    """
    Hook: When Sales Invoice is updated after submit, check for MPESA payments
    This can be used to trigger payment status checks if needed
    """
    try:
        # This hook can be extended to check payment status
        # For now, it's a placeholder for future enhancements
        pass
    except Exception as e:
        frappe.log_error(
            f"Error in on_sales_invoice_update hook: {str(e)}",
            "MPESA Integration Error"
        )

