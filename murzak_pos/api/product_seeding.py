
"""
POS Industry Products Seeding API
Handles POS industry Product seeding for selected industry
"""

import frappe
import json
import os
from frappe import _
from typing import Dict, List, Optional
from frappe.utils import flt, nowdate, getdate, get_first_day, get_last_day

from murzak_pos.api.access_control import (
	ensure_record_access,
	require_catalogue_role,
	require_stock_role,
	require_system_manager,
)
from murzak_pos.api.etims_optional import relax_etims_mandatory
from murzak_pos.api.payment_gateway_common import resolve_company
from murzak_pos.api.vat_setup import item_tax_template_for
from murzak_pos.api.seed_results import readable_seed_error, seed_result_status
from murzak_pos.api.seed_rules import resolve_uom, starter_product, stock_flag
from murzak_pos.api.error_handler import safe_error_message
from murzak_pos.api.industry_profile import PHARMACY, key_for_industry
from murzak_pos.api.pharmacy_rules import looks_like_antibiotic


def _antibiotics_need_a_prescription(item_doc, industry) -> None:
    """A pharmacy's starter product named after an antibiotic is loaded as prescription-only (pharmacy_rules)."""
    try:
        code = frappe.db.get_value("POS Industry", industry, "industry_code") if industry else None
        if key_for_industry(code or industry) != PHARMACY:
            return
        if not looks_like_antibiotic(item_doc.item_name, item_doc.item_code):
            return
        if item_doc.meta.has_field("custom_prescription_only"):
            item_doc.set("custom_prescription_only", 1)
    except Exception:
        # The flag is a safety default, never a reason for a starter product to fail
        frappe.log_error("Starter products: prescription flag", frappe.get_traceback())


def ensure_fiscal_year_exists(company: str, posting_date: str = None) -> str:
    """
    Ensure a Fiscal Year exists for the given company and date.
    Creates one automatically if it doesn't exist.
    
    Args:
        company: Company name
        posting_date: Date to check (defaults to today)
    
    Returns:
        Fiscal Year name
    """
    from frappe.utils import getdate, get_first_day, get_last_day
    
    if not posting_date:
        posting_date = nowdate()
    
    date = getdate(posting_date)
    year = date.year
    
    # Check if a fiscal year exists for this date and company
    fiscal_year = frappe.db.sql("""
        SELECT fy.name 
        FROM `tabFiscal Year` fy
        LEFT JOIN `tabFiscal Year Company` fyc ON fyc.parent = fy.name
        WHERE fy.disabled = 0
        AND %s BETWEEN fy.year_start_date AND fy.year_end_date
        AND (fyc.company = %s OR fyc.company IS NULL OR NOT EXISTS (
            SELECT 1 FROM `tabFiscal Year Company` WHERE parent = fy.name
        ))
        LIMIT 1
    """, (posting_date, company), as_dict=True)
    
    if fiscal_year:
        return fiscal_year[0].name
    
    # No fiscal year found, create one
    # Determine fiscal year dates (calendar year by default)
    year_start = f"{year}-01-01"
    year_end = f"{year}-12-31"
    fy_name = str(year)
    
    # Check if fiscal year with this name already exists but doesn't cover this company
    existing_fy = frappe.db.exists("Fiscal Year", fy_name)
    
    if existing_fy:
        # Add company to existing fiscal year
        fy_doc = frappe.get_doc("Fiscal Year", fy_name)
        company_exists = any(c.company == company for c in fy_doc.companies)
        if not company_exists:
            fy_doc.append("companies", {"company": company})
            fy_doc.save(ignore_permissions=True)
            frappe.db.commit()
        return fy_name
    
    # Create new fiscal year
    try:
        fy_doc = frappe.new_doc("Fiscal Year")
        fy_doc.year = fy_name
        fy_doc.year_start_date = year_start
        fy_doc.year_end_date = year_end
        fy_doc.disabled = 0
        fy_doc.append("companies", {"company": company})
        fy_doc.insert(ignore_permissions=True)
        frappe.db.commit()
        
        frappe.logger().info(
            f"Auto-created Fiscal Year {fy_name} for company {company}"
        )
        
        return fy_doc.name
    except frappe.DuplicateEntryError:
        # Race condition - fiscal year was created by another process
        return fy_name
    except Exception as e:
        frappe.log_error(
            "Auto Create Fiscal Year Error",
            f"Error creating Fiscal Year for {company}: {str(e)}"
        )
        raise



@frappe.whitelist(allow_guest=True)
def get_pos_industries(is_active: bool = True) -> Dict:
    """The list of business types, active ones by default. An older address of industry_api.get_pos_industries."""
    from murzak_pos.api.industry_api import get_pos_industries as industries

    return industries(is_active=is_active)


@frappe.whitelist(methods=["GET", "POST"])
def seed_products(industry):
    """
    Return all items for the given industry, the total count.
    """

    # Validate industry parameter
    if not industry:
        return {"status": "error", "message": _("Industry is required"), "total_products": 0}

    if not frappe.db.exists("POS Industry", industry):
        return {
            "status": "error",
            "message": _("Industry '{0}' does not exist").format(industry),
            "total_products": 0
        }

    templates = frappe.get_all(
        "Industry Product Template",
        filters={"industry": industry},
        fields=["item_code", "item_name", "item_group", "uom"]
    )

    total_products = len(templates)

    if total_products == 0:
        return {
            "status": "error",
            "message": _("No products found for industry '{0}'").format(industry),
            "total_products": 0
        }

    # Format each template
    products = []
    for tpl in templates:
        # sku, name and status as before, plus the unit and whether it is a service,
        # so the till can price and stock each one properly
        products.append(starter_product(tpl))

    return {
        "status": "success",
        "industry": industry,
        "total_products": total_products,
        "products": products
    }






@frappe.whitelist(methods=["POST"])
def bulk_upload_products():
    """
    Load industry products from JSON file and insert into Industry Product Template.
    Supports:
      - Normal Run : POST /api/method/murzak_pos.api.product_seeding.bulk_upload_products
      - Dry Run (Preview Only)  :: POST /api/method/murzak_pos.api.product_seeding.bulk_upload_products?dry_run=true
      - strict=true   → fail if any industry is missing : 
                    POST /api/method/murzak_pos.api.product_seeding.bulk_upload_products?strict=true

      - dry_run=true  → preview only, no DB writes
      - strict + Dry-run : POST /api/method/murzak_pos.api.product_seeding.bulk_upload_products?strict=true&dry_run=true

    The templates it writes are shared by every business on the site, so only a System Manager may run it.
    """
    require_system_manager()

    strict = frappe.form_dict.get("strict") in ("1", "true", "True")
    dry_run = frappe.form_dict.get("dry_run") in ("1", "true", "True")

    try:
        file_path = frappe.get_app_path(
            "murzak_pos",
            "products_seed_data",
            "industry_products.json"
        )

        if not os.path.exists(file_path):
            return {
                "status": "error",
                "message": _("Seed data file not found"),
                "path": file_path
            }

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not data:
            return {
                "status": "error",
                "message": _("Seed data file is empty")
            }

        created = 0
        skipped = 0
        failed = 0

        ignored_industries = []
        skipped_items = []
        failed_items = []

        # ----------------------------------
        # Resolve industries first (STRICT)
        # ----------------------------------
        resolved_industries = {}

        for industry_key in data.keys():
            industry = (
                frappe.db.get_value("POS Industry", {"name": industry_key}, "name")
                or frappe.db.get_value("POS Industry", {"industry_code": industry_key}, "name")
                or frappe.db.get_value("POS Industry", {"industry_name": industry_key}, "name")
            )

            if not industry:
                ignored_industries.append(industry_key)
            else:
                resolved_industries[industry_key] = industry

        if strict and ignored_industries:
            return {
                "status": "failed",
                "message": _("Strict mode enabled. Missing industries detected."),
                "summary": {
                    "industries_in_file": len(data),
                    "industries_missing": len(ignored_industries)
                },
                "details": {
                    "missing_industries": ignored_industries
                }
            }

        # ----------------------------------
        # Process items
        # ----------------------------------
        for industry_key, items in data.items():
            industry = resolved_industries.get(industry_key)

            if not industry:
                continue

            for item in items:
                item_code = item.get("item_code")
                item_name = item.get("item_name")

                if not item_code or not item_name:
                    skipped += 1
                    skipped_items.append({
                        "industry": industry_key,
                        "item_code": item_code,
                        "reason": "Missing item_code or item_name"
                    })
                    continue

                exists = frappe.db.exists(
                    "Industry Product Template",
                    {
                        "industry": industry,
                        "item_code": item_code
                    }
                )

                if exists:
                    skipped += 1
                    skipped_items.append({
                        "industry": industry_key,
                        "item_code": item_code,
                        "reason": "Already exists"
                    })
                    continue

                if dry_run:
                    created += 1
                    continue

                try:
                    doc = frappe.get_doc({
                        "doctype": "Industry Product Template",
                        "industry": industry,
                        "item_code": item_code,
                        "item_name": item_name,
                        "item_group": item.get("item_group"),
                        "uom": item.get("uom")
                    })
                    doc.insert(ignore_permissions=True)
                    created += 1

                except frappe.DuplicateEntryError:
                    skipped += 1
                    skipped_items.append({
                        "industry": industry_key,
                        "item_code": item_code,
                        "reason": "Duplicate entry"
                    })

                except Exception as e:
                    failed += 1
                    failed_items.append({
                        "industry": industry_key,
                        "item_code": item_code,
                        "error": safe_error_message(e)
                    })

        if not dry_run:
            frappe.db.commit()

        # ----------------------------------
        # Final response
        # ----------------------------------
        status = (
            "success"
            if created and not failed
            else "partial_success"
            if created or skipped
            else "failed"
        )

        return {
            "status": status,
            "mode": {
                "strict": strict,
                "dry_run": dry_run
            },
            "summary": {
                "industries_in_file": len(data),
                "industries_ignored": len(ignored_industries),
                "items_created": created,
                "items_skipped": skipped,
                "items_failed": failed,
                "total_items_processed": created + skipped + failed
            },
            "details": {
                "ignored_industries": ignored_industries,
                "skipped_items": skipped_items,
                "failed_items": failed_items
            },
            "message": (
                _("Dry run completed. No data was written.")
                if dry_run
                else _("Products imported successfully.")
            )
        }

    except Exception as e:
        return {
            "status": "error",
            "message": _("Unexpected error: {0}").format(safe_error_message(e))
        }








def _make_uom(name: str, whole_numbers_only: int) -> str:
    uom = frappe.new_doc("UOM")
    uom.uom_name = name
    uom.must_be_whole_number = whole_numbers_only
    uom.insert(ignore_permissions=True)
    return uom.name


def ensure_uom(requested) -> str:
    """
    The name of a unit of measure that exists on this site for what a starter list asks for.

    A list may say "L" or "ml", and a site may already hold that unit as "Litre" or "Millilitre": use the
    one that is there. A unit nobody has made yet is made, so the product is sold in the unit it should be.
    """
    return resolve_uom(requested, lambda name: frappe.db.exists("UOM", name), _make_uom)


@frappe.whitelist(methods=["POST"])
def create_seed_item(company: str = None):
    """
    Create Items, Item Prices, and optionally update inventory from seed data.
    Handles PermissionError and AuthenticationError gracefully.
    Response separates items created/skipped/failed and stock entry status.
    Failed and skipped items include short readable messages.
    """
    # Adding products is the catalogue's job. Say who may ask before anything is read.
    require_catalogue_role()
    # Refuse a company the caller does not belong to
    if company:
        company = resolve_company(company)
    try:
        # ----------------------
        # Parse payload safely
        # ----------------------
        payload = {}
        try:
            raw_data = frappe.request.data
            if isinstance(raw_data, bytes):
                raw_data = raw_data.decode("utf-8")
            if raw_data:
                payload = json.loads(raw_data)
        except Exception:
            payload = {}

        if not payload:
            payload = frappe.form_dict or {}

        if not isinstance(payload, dict):
            frappe.throw(_("Invalid payload"))

        # ----------------------
        # Authentication check
        # ----------------------
        if frappe.session.user == "Guest":
            frappe.throw(_("Authentication required"), frappe.AuthenticationError)

        # ----------------------
        # Extract parameters
        # ----------------------
        price_list = payload.get("price_list")
        buying_price_list = payload.get("buying_price_list")
        items = payload.get("items")
        industry_code = payload.get("industry")
        default_warehouse = payload.get("warehouse")
        company = payload.get("company") or company or frappe.defaults.get_user_default("Company")

        if not price_list:
            frappe.throw(_("Price List is required"))

        if not items or not isinstance(items, list):
            frappe.throw(_("Items must be a non-empty list"))

        if not company:
            frappe.throw(_("Company is required."))

        # ----------------------
        # Company & industry setup
        # ----------------------
        # The company used from here on is the one in the request body, which is not the same value as the
        # `company` argument checked at the top: Frappe fills that argument from the query string when the
        # request is not sent as JSON, while this function reads the raw body itself. So the company in use
        # is checked here, after it is chosen, and every record it will write into must belong to it.
        company = resolve_company(company)
        ensure_record_access("Price List", price_list)
        if buying_price_list:
            ensure_record_access("Price List", buying_price_list)
        warehouses = {default_warehouse} | {
            row.get("warehouse") for row in items if isinstance(row, dict) and row.get("warehouse")
        }
        for warehouse in sorted(w for w in warehouses if w):
            ensure_record_access("Warehouse", warehouse)
        # Receiving opening stock is a stock job, on top of adding the products
        if any(isinstance(row, dict) and flt(row.get("qty")) > 0 for row in items):
            require_stock_role()

        company_abbr = frappe.db.get_value("Company", company, "abbr")
        if not company_abbr:
            frappe.throw(_("Company '{0}' does not have an abbreviation set").format(company))
        company_abbr = company_abbr.strip().upper()

        product_industry = None
        if industry_code:
            industry_doc = frappe.db.get_value(
                "POS Industry",
                {"name": industry_code, "is_active": 1},
                "name"
            ) or frappe.db.get_value(
                "POS Industry",
                {"industry_code": industry_code, "is_active": 1},
                "name"
            )
            if industry_doc:
                product_industry = industry_doc
            else:
                frappe.throw(_("The industry '{0}' does not exist or is not active").format(industry_code))
        else:
            try:
                user_industry = frappe.db.get_value("User", frappe.session.user, "custom_pos_industry")
                if user_industry:
                    product_industry = user_industry
            except Exception as e:
                # Handle case where custom_pos_industry column doesn't exist in database
                # Log short error message to avoid CharacterLengthExceededError
                error_msg = str(e)[:100] if len(str(e)) > 100 else str(e)
                frappe.log_error(
                    "Seed Products - POS Industry",
                    f"POS industry field missing: {error_msg}"
                )
                user_industry = None

        # ----------------------
        # Initialize counters
        # ----------------------
        items_created = []
        items_skipped = []
        items_failed = []
        stock_entry_status = {"created": False, "name": None, "error": None}
        stock_entry_items = []
        stock_skipped = []  # items given a quantity that keep no stock, so none was recorded

        # ----------------------
        # Process each item
        # ----------------------
        for row in items:
            # A row that fails part way (say the item saved but its price did not)
            # is undone completely, so no half-made products are left behind
            frappe.db.savepoint("seed_item")
            try:
                if not isinstance(row, dict):
                    raise ValueError(_("Each item must be an object"))

                original_item_code = row.get("item_code")
                item_name = row.get("item_name")
                item_price = flt(row.get("item_price"))
                buying_price = flt(row.get("buying_price")) if row.get("buying_price") else None
                item_group = row.get("item_group") or "All Item Groups"
                item_uom = ensure_uom(row.get("uom"))
                qty = flt(row.get("qty")) if row.get("qty") is not None else None
                item_warehouse = row.get("warehouse") or default_warehouse
                basic_rate = flt(row.get("basic_rate")) if row.get("basic_rate") is not None else None

                if not original_item_code or not item_name:
                    raise ValueError(_("Item Code and Item Name are required"))

                # Services (a haircut, a car wash) keep no stock, so they can be sold at any time.
                # The till says which are services; for a till that does not, the starter list decides.
                template_group = None
                if row.get("is_stock_item") in (None, "") and product_industry:
                    template_group = frappe.db.get_value(
                        "Industry Product Template",
                        {"industry": product_industry, "item_code": original_item_code},
                        "item_group",
                    )
                keeps_stock = stock_flag(row.get("is_stock_item"), template_group)

                # Prefix item code with company abbreviation
                if original_item_code.upper().startswith(f"{company_abbr}-"):
                    item_code = original_item_code
                else:
                    item_code = f"{company_abbr}-{original_item_code}"

                # Check for global uniqueness
                if frappe.db.exists("Item", item_code):
                    items_skipped.append({
                        "item_code": original_item_code,
                        "prefixed_item_code": item_code,
                        "reason": _("Item already exists")
                    })
                    continue

                # Create Item
                item_doc = frappe.get_doc({
                    "doctype": "Item",
                    "item_code": item_code,
                    "item_name": item_name,
                    "item_group": item_group,
                    "stock_uom": item_uom,
                    "is_stock_item": keeps_stock,
                    "custom_company": company,
                    "custom_pos_industry": product_industry,
                    "custom_prevent_etims_registration": 1
                })
                _antibiotics_need_a_prescription(item_doc, product_industry)

                # How the product is taxed, when the till says (standard, zero_rated or exempt)
                vat_template = item_tax_template_for(company, row.get("vat_class"))
                if vat_template:
                    item_doc.append("taxes", {"item_tax_template": vat_template})
                
                # Explicitly set custom_item_classification to None if field exists
                # This prevents AttributeError when fetch_from tries to access it during validation
                # The field might be referenced by other fields with fetch_from (e.g., custom_taxation_type)
                try:
                    # Check if field exists in doctype meta
                    field_exists = any(f.fieldname == 'custom_item_classification' for f in item_doc.meta.fields)
                    if field_exists:
                        item_doc.set('custom_item_classification', None)
                except (AttributeError, Exception):
                    # If field doesn't exist in meta or setting fails, try using setattr
                    # This will fail silently if the field truly doesn't exist
                    try:
                        setattr(item_doc, 'custom_item_classification', None)
                    except (AttributeError, Exception):
                        # Field doesn't exist in the doctype - this is okay, continue
                        pass
                
                # Try to insert the item
                # If it fails due to custom_item_classification AttributeError, retry with ignore_validate
                # Starter products are not sent to eTIMS, so they have no KRA classification
                relax_etims_mandatory(item_doc)
                try:
                    item_doc.insert(ignore_permissions=True)
                except AttributeError as e:
                    if 'custom_item_classification' in str(e):
                        # Field doesn't exist but is referenced by fetch_from - skip validation
                        # Since custom_prevent_etims_registration = 1, we don't need eTIMS validation anyway
                        item_doc.insert(ignore_permissions=True, ignore_validate=True)
                    else:
                        # Different AttributeError - re-raise
                        raise

                # Create Selling Price
                item_price_doc = frappe.get_doc({
                    "doctype": "Item Price",
                    "item_code": item_code,
                    "uom": item_uom,
                    "price_list": price_list,
                    "price_list_rate": item_price
                })
                if hasattr(item_price_doc, "company"):
                    item_price_doc.company = company
                item_price_doc.insert(ignore_permissions=True)

                # Create Buying Price
                if buying_price_list and buying_price is not None and buying_price > 0:
                    buying_item_price_doc = frappe.get_doc({
                        "doctype": "Item Price",
                        "item_code": item_code,
                        "uom": item_uom,
                        "price_list": buying_price_list,
                        "price_list_rate": buying_price
                    })
                    if hasattr(buying_item_price_doc, "company"):
                        buying_item_price_doc.company = company
                    buying_item_price_doc.insert(ignore_permissions=True)

                # Collect stock entry items (a service has no stock to record)
                if qty is not None and qty > 0 and not keeps_stock:
                    stock_skipped.append(item_code)
                elif qty is not None and qty > 0 and item_warehouse:
                    valuation_rate = basic_rate if basic_rate and basic_rate > 0 else (buying_price if buying_price else 0)
                    stock_entry_items.append({
                        "item_code": item_code,
                        "qty": qty,
                        "t_warehouse": item_warehouse,
                        "basic_rate": valuation_rate,
                        "uom": item_uom
                    })

                items_created.append(item_code)

            except Exception as e:
                frappe.db.rollback(save_point="seed_item")
                items_failed.append({
                    "item_code": row.get("item_code"),
                    "item_name": row.get("item_name") if isinstance(row, dict) else None,
                    "prefixed_item_code": item_code if 'item_code' in locals() else None,
                    "error_message": readable_seed_error(e)
                })

        # ----------------------
        # Create Stock Entry
        # ----------------------
        if stock_entry_items:
            try:
                # Validate warehouses exist and are not group warehouses
                for wh in {i["t_warehouse"] for i in stock_entry_items}:
                    if not frappe.db.exists("Warehouse", wh):
                        raise ValueError(_("Warehouse '{0}' does not exist").format(wh))
                    
                    # Check if warehouse is a group warehouse (group warehouses cannot be used in transactions)
                    is_group = frappe.db.get_value("Warehouse", wh, "is_group")
                    if is_group:
                        raise ValueError(_("Warehouse '{0}' is a group warehouse and cannot be used for transactions. Please select a non-group warehouse.").format(wh))

                # Ensure fiscal year exists for the company (auto-create if needed)
                try:
                    fiscal_year = ensure_fiscal_year_exists(company)
                except Exception as fy_error:
                    frappe.log_error(
                        "Fiscal Year Auto-Creation Error",
                        f"Could not create fiscal year for {company}: {str(fy_error)}"
                    )
                    # Continue anyway - ERPNext might still work if there's a global fiscal year

                stock_entry = frappe.new_doc("Stock Entry")
                stock_entry.stock_entry_type = "Material Receipt"
                stock_entry.company = company
                stock_entry.purpose = "Material Receipt"

                for i in stock_entry_items:
                    stock_entry.append("items", {
                        "item_code": i["item_code"],
                        "qty": i["qty"],
                        "t_warehouse": i["t_warehouse"],
                        "basic_rate": i["basic_rate"],
                        "uom": i["uom"],
                        "conversion_factor": 1.0
                    })

                stock_entry.validate()
                stock_entry.insert(ignore_permissions=True)
                stock_entry.submit()

                stock_entry_status = {"created": True, "name": stock_entry.name, "error": None}

            except Exception as e:
                stock_entry_status = {"created": False, "name": None, "error": safe_error_message(e)}

        # ----------------------
        # Final response
        # ----------------------
        return {
            "status": seed_result_status(
                created=len(items_created),
                failed=len(items_failed),
                skipped=len(items_skipped),
                stock_needed=bool(stock_entry_items),
                stock_created=stock_entry_status["created"],
            ),
            "company": company,
            "company_abbr": company_abbr,
            "industry": product_industry,
            "items_created": items_created,
            "items_skipped": items_skipped,  # now includes reason
            "items_failed": items_failed,
            "total_received": len(items),
            "stock_entry": stock_entry_status,
            "stock_skipped": stock_skipped,
            "note": _("Item codes are automatically prefixed with company abbreviation '{0}' to ensure uniqueness across companies. Format: {0}-{{original_code}}").format(company_abbr)
        }

    except frappe.PermissionError:
        return {
            "status": "error",
            "error_type": "PermissionError",
            "message": _("You are not permitted to access this resource. Login or whitelist this function."),
            "hint": _("Add @frappe.whitelist or log in with a valid user to call this API.")
        }
    except frappe.AuthenticationError:
        return {
            "status": "error",
            "error_type": "AuthenticationError",
            "message": _("Authentication failed. Please log in or provide valid API credentials."),
            "hint": _("Check session or API key authentication.")
        }
    except Exception as e:
        return {
            "status": "error",
            "error_type": "Exception",
            "message": safe_error_message(e)
        }





@frappe.whitelist(allow_guest=False)
def delete_industry_items(industries=None):
    """
    Delete Items if possible, else disable them.
    Returns per-industry summary with deleted, disabled, and failure reasons.
    """

    if not frappe.has_permission("Item", "delete"):
        frappe.throw(_("You do not have permission to delete Items"))

    if industries is None:
        industries = []

    if "" not in industries:
        industries.append("")  # Always include blank industry

    summary = []

    for industry in industries:
        if industry:
            filters = {"custom_pos_industry": industry}
        else:
            filters = [["custom_pos_industry", "in", ("", None)]]

        items = frappe.get_all("Item", filters=filters, fields=["name"])
        deleted = []
        disabled = []
        failed = []

        for item in items:
            try:
                frappe.delete_doc("Item", item.name, force=True)
                deleted.append(item.name)
            except Exception as e:
                try:
                    # If deletion fails, disable the item
                    frappe.db.set_value("Item", item.name, "disabled", 1)
                    disabled.append(item.name)
                except Exception as e2:
                    failed.append({
                        "item": item.name,
                        "reason": str(e2)
                    })

        summary.append({
            "industry": industry or "BLANK",
            "total_found": len(items),
            "deleted_count": len(deleted),
            "deleted_items": deleted,
            "disabled_count": len(disabled),
            "disabled_items": disabled,
            "failed_count": len(failed),
            "failed_items": failed
        })

    return {"summary": summary}
