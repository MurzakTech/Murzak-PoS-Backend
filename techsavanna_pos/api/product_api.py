"""
Product Management API
Handles complete product/item management including CRUD operations, search, pricing, and stock management
"""

import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.model.rename_doc import rename_doc as rename_document
from typing import Dict, List, Optional
from frappe.utils import flt, cint, cstr, nowdate, getdate
import json
from datetime import datetime
import erpnext
import re

from techsavanna_pos.api.etims_optional import relax_etims_mandatory


@frappe.whitelist()
def create_product(
    item_code: str,
    item_name: str,
    item_group: str = "All Item Groups",
    stock_uom: str = "Nos",
    standard_rate: float = 0.0,
    description: str = None,
    is_stock_item: bool = True,
    is_sales_item: bool = True,
    is_purchase_item: bool = False,
    brand: str = None,
    barcode: str = None,
    image: str = None,
    weight_per_unit: float = None,
    weight_uom: str = None,
    item_defaults: List[Dict] = None,
    taxes: List[Dict] = None,
    company: str = None,
    # eTIMS fields (optional - will skip eTIMS validation if not provided)
    prevent_etims_registration: bool = True,
    etims_country_of_origin_code: str = None,
    product_type: str = None,
    packaging_unit_code: str = None,
    unit_of_quantity_code: str = None,
    item_classification: str = None,
    taxation_type: str = None
) -> Dict:
    """Create a new product/item
    
    Args:
        item_code: Unique item code
        item_name: Item name
        item_group: Item group (default: "All Item Groups")
        stock_uom: Stock unit of measure (default: "Nos")
        standard_rate: Standard selling rate
        description: Item description
        is_stock_item: Whether item is a stock item
        is_sales_item: Whether item can be sold
        is_purchase_item: Whether item can be purchased
        brand: Brand name
        barcode: Barcode for the item
        image: Image URL or file path
        weight_per_unit: Weight per unit
        weight_uom: Weight unit of measure
        item_defaults: List of item defaults (warehouse, company, etc.)
        taxes: List of tax templates
        company: Company for item defaults
        prevent_etims_registration: Skip eTIMS validation (default: True). 
            Set to False to enable eTIMS registration, but then all eTIMS fields are required.
        etims_country_of_origin_code: Country of origin code (required if prevent_etims_registration is False)
        product_type: Product type name from "Navari eTims Product Type" doctype (required if prevent_etims_registration is False)
        packaging_unit_code: Packaging unit code (required if prevent_etims_registration is False)
        unit_of_quantity_code: Unit of quantity code (required if prevent_etims_registration is False)
        item_classification: Item classification name (required if prevent_etims_registration is False)
        taxation_type: Taxation type name (required if prevent_etims_registration is False)
        
    Returns:
        Created product details
    """
    try:
        # Validate user permissions
        if frappe.session.user == "Guest":
            frappe.throw(_("Please log in to create a product. Your session has expired or you are not authenticated."), frappe.AuthenticationError)
        
        # Validate required fields
        if not item_code or not item_code.strip():
            frappe.throw(_("Product code is required. Please provide a unique code for this product."), frappe.ValidationError)
        
        if not item_name or not item_name.strip():
            frappe.throw(_("Product name is required. Please provide a name for this product."), frappe.ValidationError)
        
        # Get company if not provided
        if not company:
            company = frappe.defaults.get_user_default("Company")
            if not company:
                frappe.throw(_("Company is required. Please set a default company in your profile settings or provide the company parameter when creating the product."), frappe.ValidationError)
        
        # Validate company exists
        if not frappe.db.exists("Company", company):
            frappe.throw(_("The company '{0}' does not exist. Please check the company name and try again, or contact your administrator if you believe this is an error.").format(company), frappe.ValidationError)
        
        # Check if item code already exists
        if frappe.db.exists("Item", item_code):
            frappe.throw(_("A product with the code '{0}' already exists. Please use a different product code.").format(item_code), frappe.ValidationError)
        
        # Validate item group
        if not frappe.db.exists("Item Group", item_group):
            frappe.throw(_("The product category '{0}' does not exist. Please select a valid category from the list.").format(item_group), frappe.ValidationError)
        
        # Validate UOM
        if not frappe.db.exists("UOM", stock_uom):
            frappe.throw(_("The unit of measure '{0}' does not exist. Please select a valid unit (e.g., 'Nos', 'Kg', 'Ltr') from the list.").format(stock_uom), frappe.ValidationError)
        
        # Validate brand if provided
        if brand and not frappe.db.exists("Brand", brand):
            frappe.throw(_("The brand '{0}' does not exist. Please select a valid brand from the list or leave this field empty.").format(brand), frappe.ValidationError)
        
        # Validate weight UOM if provided
        if weight_uom and not frappe.db.exists("UOM", weight_uom):
            frappe.throw(_("The weight unit '{0}' does not exist. Please select a valid unit from the list.").format(weight_uom), frappe.ValidationError)
        
        # Validate eTIMS fields if registration is enabled
        if not prevent_etims_registration:
            missing_fields = []
            if not etims_country_of_origin_code:
                missing_fields.append("Country of Origin Code")
            if not product_type:
                missing_fields.append("Product Type")
            if not packaging_unit_code:
                missing_fields.append("Packaging Unit Code")
            if not unit_of_quantity_code:
                missing_fields.append("Unit of Quantity Code")
            if not item_classification:
                missing_fields.append("Item Classification")
            if not taxation_type:
                missing_fields.append("Taxation Type")
            
            if missing_fields:
                frappe.throw(_("To enable eTIMS registration, the following fields are required: {0}. Please provide all required eTIMS information or set prevent_etims_registration to True.").format(", ".join(missing_fields)), frappe.ValidationError)
        
        # Create item
        item = frappe.new_doc("Item")
        item.item_code = item_code
        item.item_name = item_name
        item.item_group = item_group
        item.stock_uom = stock_uom
        item.standard_rate = flt(standard_rate)
        item.description = description
        item.is_stock_item = 1 if is_stock_item else 0
        item.is_sales_item = 1 if is_sales_item else 0
        item.is_purchase_item = 1 if is_purchase_item else 0
        
        if brand:
            item.brand = brand
        if image:
            item.image = image
        if weight_per_unit:
            item.weight_per_unit = flt(weight_per_unit)
        if weight_uom:
            item.weight_uom = weight_uom
        
        # Add item defaults
        # Note: We don't set default_price_list here to prevent automatic price creation
        # which would fail due to permissions. We'll create the price manually after insert.
        if item_defaults:
            for default in item_defaults:
                # Remove default_price_list to prevent automatic price creation
                default_copy = default.copy()
                default_copy.pop("default_price_list", None)
                item.append("item_defaults", default_copy)
        else:
            # Add default company warehouse
            default_warehouse = frappe.db.get_value(
                "Warehouse",
                {"company": company, "is_group": 0},
                "name",
                order_by="creation desc"
            )
            if default_warehouse:
                item.append("item_defaults", {
                    "company": company,
                    "default_warehouse": default_warehouse
                })
        
        # Add taxes
        if taxes:
            for tax in taxes:
                item.append("taxes", tax)
        
        # Add barcode
        if barcode:
            item.append("barcodes", {
                "barcode": barcode
            })
        
        # Set company for product isolation
        item.custom_company = company
        
        # Set eTIMS fields or prevent eTIMS registration
        item.custom_prevent_etims_registration = 1 if prevent_etims_registration else 0
        
        if not prevent_etims_registration:
            # Set eTIMS fields if provided
            if etims_country_of_origin_code:
                item.custom_etims_country_of_origin_code = etims_country_of_origin_code
            if product_type:
                item.custom_product_type = product_type
            if packaging_unit_code:
                item.custom_packaging_unit_code = packaging_unit_code
            if unit_of_quantity_code:
                item.custom_unit_of_quantity_code = unit_of_quantity_code
            if item_classification:
                item.custom_item_classification = item_classification
            if taxation_type:
                item.custom_taxation_type = taxation_type
        
        # Temporarily set standard_rate to 0 to prevent automatic price creation
        # We'll restore it and create the price manually after insert
        temp_standard_rate = item.standard_rate
        item.standard_rate = 0
        
        # Products not sent to eTIMS have no KRA classification to give
        relax_etims_mandatory(item)
        item.insert(ignore_permissions=True)
        
        # Restore standard_rate
        if temp_standard_rate:
            item.standard_rate = temp_standard_rate
            item.db_set("standard_rate", temp_standard_rate)
            
            # Manually create Item Price with ignore_permissions if standard_rate is set
            try:
                # Get default price list
                default_price_list = None
                if item_defaults:
                    for default in item_defaults:
                        if default.get("default_price_list"):
                            default_price_list = default.get("default_price_list")
                            break
                
                if not default_price_list:
                    default_price_list = frappe.get_single_value(
                        "Selling Settings", "selling_price_list"
                    ) or frappe.db.get_value("Price List", _("Standard Selling"))
                
                if default_price_list and frappe.db.exists("Price List", default_price_list):
                    # Check if price already exists
                    existing_price = frappe.db.exists(
                        "Item Price",
                        {"item_code": item_code, "price_list": default_price_list}
                    )
                    
                    if not existing_price:
                        item_price = frappe.new_doc("Item Price")
                        item_price.price_list = default_price_list
                        item_price.item_code = item_code
                        item_price.uom = stock_uom
                        item_price.brand = brand
                        item_price.currency = erpnext.get_default_currency()
                        item_price.price_list_rate = flt(temp_standard_rate)
                        item_price.insert(ignore_permissions=True)
            except Exception:
                # If price creation fails, log but don't fail the item creation
                frappe.log_error(
                    "Item Price Creation Error",
                    f"Failed to create Item Price for {item_code}: {frappe.get_traceback()}"
                )
        
        frappe.db.commit()
        
        # Set HTTP status code
        frappe.local.response["http_status_code"] = 201
        
        return {
            "product": {
                "item_code": item.item_code,
                "item_name": item.item_name,
                "item_group": item.item_group,
                "stock_uom": item.stock_uom,
                "standard_rate": item.standard_rate,
                "is_stock_item": item.is_stock_item,
                "is_sales_item": item.is_sales_item,
                "is_purchase_item": item.is_purchase_item,
                "disabled": item.disabled
            },
            "message": _("Product created successfully")
        }
    
    except frappe.AuthenticationError:
        # Re-raise authentication errors as-is
        raise
    except frappe.ValidationError:
        # Re-raise validation errors as-is (they already have user-friendly messages)
        raise
    except frappe.DuplicateEntryError as e:
        # Handle duplicate entry errors
        frappe.throw(
            _("A product with this information already exists. Please check the product code '{0}' and try again with a unique code.").format(item_code),
            frappe.ValidationError
        )
    except frappe.MandatoryError as e:
        # Handle missing mandatory fields
        error_msg = str(e)
        if "item_code" in error_msg.lower():
            frappe.throw(_("Product code is required. Please provide a unique code for this product."), frappe.ValidationError)
        elif "item_name" in error_msg.lower():
            frappe.throw(_("Product name is required. Please provide a name for this product."), frappe.ValidationError)
        else:
            frappe.throw(_("Some required information is missing: {0}. Please fill in all required fields and try again.").format(error_msg), frappe.ValidationError)
    except frappe.PermissionError as e:
        # Handle permission errors
        frappe.throw(
            _("You don't have permission to create products. Please contact your administrator to grant you the necessary permissions."),
            frappe.PermissionError
        )
    except Exception as e:
        # Log the full error for debugging
        frappe.log_error(
            "Product Creation Error",
            f"Error creating product '{item_code}': {frappe.get_traceback()}"
        )
        # Return user-friendly error message
        frappe.throw(
            _("An error occurred while creating the product. Please check that all information is correct and try again. If the problem persists, contact support."),
            frappe.ValidationError
        )


@frappe.whitelist()
def get_products(
    company: str = None,
    item_group: str = None,
    brand: str = None,
    is_stock_item: bool = None,
    is_sales_item: bool = None,
    disabled: bool = False,
    search_term: str = None,
    page: int = 1,
    page_size: int = 20,
    price_list: str = None,
    warehouse: str = None
) -> Dict:
    """Get list of products with filtering and pagination
    
    Args:
        company: Filter by company
        item_group: Filter by item group
        brand: Filter by brand
        is_stock_item: Filter by stock item status
        is_sales_item: Filter by sales item status
        disabled: Include disabled items (default: false)
        search_term: Search in item_code, item_name, description
        page: Page number (default: 1)
        page_size: Items per page (default: 20)
        price_list: Price list to get prices from (optional, will use default if not provided)
        warehouse: Warehouse to get buying_price and selling_price from Inventory Item Details (optional)
        
    Returns:
        List of products with pagination info
    """
    # Validate user permissions
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    # Get default price list if not provided
    if not price_list:
        if company:
            # Get default selling price list from company
            price_list = frappe.db.get_value(
                "Price List",
                {"enabled": 1, "selling": 1},
                "name",
                order_by="creation desc"
            )
        if not price_list:
            # Get from Selling Settings
            price_list = frappe.get_single_value("Selling Settings", "selling_price_list")
        if not price_list:
            # Try to get "Standard Selling" price list
            price_list = frappe.db.get_value("Price List", _("Standard Selling"), "name")
    
    # Get company for filtering (required for product isolation)
    if not company:
        company = frappe.defaults.get_user_default("Company")
        if not company:
            return {
                "success": False,
                "message": "Company is required for product listing. Please set a default company or provide company parameter.",
                "data": {
                    "products": [],
                    "pagination": {
                        "page": page,
                        "page_size": page_size,
                        "total": 0,
                        "total_pages": 0
                    }
                }
            }
    
    # Get user's POS industry for filtering
    try:
        user_industry = frappe.db.get_value("User", frappe.session.user, "custom_pos_industry")
    except Exception as e:
        # Handle case where custom_pos_industry column doesn't exist in database
        # Log short error message to avoid CharacterLengthExceededError
        error_msg = str(e)[:100] if len(str(e)) > 100 else str(e)
        frappe.log_error(
            "Get Products - POS Industry",
            f"POS industry field missing: {error_msg}"
        )
        user_industry = None
    
    # Build filters
    filters = {}
    # Company isolation - only show products for the user's company
    filters["custom_company"] = company
    if not disabled:
        filters["disabled"] = 0
    if item_group:
        filters["item_group"] = item_group
    if brand:
        filters["brand"] = brand
    if is_stock_item is not None:
        filters["is_stock_item"] = 1 if is_stock_item else 0
    if is_sales_item is not None:
        filters["is_sales_item"] = 1 if is_sales_item else 0
    
    # Build industry filter - show products that are either:
    # 1. Not linked to any industry (custom_pos_industry is NULL) - available to all
    # 2. Linked to the user's industry
    industry_filters = None
    if user_industry:
        industry_filters = [
            ["custom_pos_industry", "is", "not set"],
            ["custom_pos_industry", "=", user_industry]
        ]
    
    # Build search conditions
    search_conditions = None
    if search_term:
        search_conditions = [
            ["item_code", "like", f"%{search_term}%"],
            ["item_name", "like", f"%{search_term}%"],
            ["description", "like", f"%{search_term}%"]
        ]
    
    # Get total count - need to handle industry filter separately since it's an OR condition
    if industry_filters:
        # Use SQL for complex filtering with OR condition
        where_clauses_count = ["custom_company = %(company)s"]
        if not disabled:
            where_clauses_count.append("disabled = 0")
        where_clauses_count.append("(custom_pos_industry IS NULL OR custom_pos_industry = %(industry)s)")
        where_sql_count = " AND ".join(where_clauses_count)
        
        total = frappe.db.sql(f"""
            SELECT COUNT(*) 
            FROM `tabItem`
            WHERE {where_sql_count}
        """, {
            "company": company,
            "industry": user_industry
        })[0][0]
    else:
        total = frappe.db.count("Item", filters=filters)
    
    # Get paginated results
    start = (page - 1) * page_size
    
    # Handle industry filtering with SQL for complex OR condition
    if industry_filters:
        # Build WHERE clause for industry filter
        industry_where = " AND (custom_pos_industry IS NULL OR custom_pos_industry = %(industry)s)"
        
        # Build WHERE clause for other filters
        where_clauses = ["custom_company = %(company)s"]
        params = {"company": company, "industry": user_industry}
        
        if not disabled:
            where_clauses.append("disabled = 0")
        # If disabled=True, don't add any disabled filter to show all products
        
        if item_group:
            where_clauses.append("item_group = %(item_group)s")
            params["item_group"] = item_group
        
        if brand:
            where_clauses.append("brand = %(brand)s")
            params["brand"] = brand
        
        if is_stock_item is not None:
            where_clauses.append(f"is_stock_item = {1 if is_stock_item else 0}")
        
        if is_sales_item is not None:
            where_clauses.append(f"is_sales_item = {1 if is_sales_item else 0}")
        
        # Build search conditions
        search_where = ""
        if search_term:
            search_where = " AND (item_code LIKE %(search)s OR item_name LIKE %(search)s OR description LIKE %(search)s)"
            params["search"] = f"%{search_term}%"
        
        where_sql = " AND ".join(where_clauses) + industry_where + search_where
        
        # Check if custom fields exist before including them in query
        custom_fields_exist = False
        try:
            meta = frappe.get_meta("Item")
            custom_fields_exist = (
                meta.get_field("custom_warranty_period_unit") is not None and
                meta.get_field("custom_warranty_period_value") is not None
            )
        except Exception:
            custom_fields_exist = False
        
        # Build SELECT query with or without custom fields
        if custom_fields_exist:
            products = frappe.db.sql(f"""
                SELECT name, item_code, item_name, item_group, stock_uom,
                       standard_rate, is_stock_item, is_sales_item, is_purchase_item,
                       disabled, brand, image, warranty_period,
                       custom_warranty_period_unit, custom_warranty_period_value
                FROM `tabItem`
                WHERE {where_sql}
                ORDER BY creation DESC
                LIMIT %(limit)s OFFSET %(offset)s
            """, {
                **params,
                "limit": page_size,
                "offset": start
            }, as_dict=True)
        else:
            # Custom fields don't exist, query without them
            products = frappe.db.sql(f"""
                SELECT name, item_code, item_name, item_group, stock_uom,
                       standard_rate, is_stock_item, is_sales_item, is_purchase_item,
                       disabled, brand, image, warranty_period
                FROM `tabItem`
                WHERE {where_sql}
                ORDER BY creation DESC
                LIMIT %(limit)s OFFSET %(offset)s
            """, {
                **params,
                "limit": page_size,
                "offset": start
            }, as_dict=True)
    else:
        # Check if custom fields exist before including them
        custom_fields = ["warranty_period"]
        try:
            meta = frappe.get_meta("Item")
            if meta.get_field("custom_warranty_period_unit") is not None:
                custom_fields.append("custom_warranty_period_unit")
            if meta.get_field("custom_warranty_period_value") is not None:
                custom_fields.append("custom_warranty_period_value")
        except Exception:
            pass  # If meta check fails, just use basic fields
        
        products = frappe.get_all(
            "Item",
            fields=[
                "name", "item_code", "item_name", "item_group", "stock_uom",
                "standard_rate", "is_stock_item", "is_sales_item", "is_purchase_item",
                "disabled", "brand", "image"
            ] + custom_fields,
            filters=filters,
            or_filters=search_conditions,
            limit=page_size,
            start=start,
            order_by="creation desc"
        )
    
    # Get prices from Item Price for each product if price_list is available
    if price_list and frappe.db.exists("Price List", price_list):
        item_codes = [p["item_code"] for p in products]
        if item_codes:
            # Fetch all prices for these items in one query
            # Item Price uniqueness is based on item_code, price_list, uom, and optional fields
            # We'll fetch all matching prices and then match by UOM
            item_prices = frappe.db.get_all(
                "Item Price",
                filters={
                    "item_code": ["in", item_codes],
                    "price_list": price_list
                },
                fields=["item_code", "price_list_rate", "currency", "uom"]
            )
            
            # Create a dictionary for quick lookup: item_code -> list of prices
            price_map = {}
            for ip in item_prices:
                item_code = ip["item_code"]
                if item_code not in price_map:
                    price_map[item_code] = []
                price_map[item_code].append(ip)
            
            # Get price list currency for fallback
            price_list_currency = frappe.db.get_value("Price List", price_list, "currency")
            
            # Map prices to products
            for product in products:
                item_code = product["item_code"]
                stock_uom = product.get("stock_uom")
                
                # Try to find price with matching UOM first
                item_price = None
                if item_code in price_map:
                    prices = price_map[item_code]
                    # Prefer exact UOM match
                    for ip in prices:
                        if ip.get("uom") == stock_uom:
                            item_price = ip
                            break
                    # If no exact match, use first available price
                    if not item_price and prices:
                        item_price = prices[0]
                
                if item_price:
                    product["price"] = item_price["price_list_rate"]
                    product["price_currency"] = item_price.get("currency") or price_list_currency
                    product["price_list"] = price_list
                    product["price_source"] = "price_list"
                else:
                    # Fallback to standard_rate
                    product["price"] = product.get("standard_rate") or 0
                    product["price_source"] = "standard_rate"
                    product["price_currency"] = price_list_currency
    else:
        # No price list, use standard_rate as fallback
        for product in products:
            product["price"] = product.get("standard_rate") or 0
            product["price_source"] = "standard_rate"
    
    # Get stock quantities if company provided
    if company:
        from erpnext.stock.utils import get_stock_balance
        for product in products:
            try:
                product["stock_qty"] = get_stock_balance(
                    product["item_code"],
                    None,
                    nowdate()
                )
            except Exception:
                product["stock_qty"] = 0
    
    # Get buying_price and selling_price from Inventory Item Details if warehouse is provided
    if warehouse and company:
        item_codes = [p["item_code"] for p in products]
        if item_codes:
            # Check if Inventory Item Details doctype exists
            if frappe.db.exists("DocType", "Inventory Item Details"):
                # Verify warehouse exists and get its company if company doesn't match
                warehouse_company = frappe.db.get_value("Warehouse", warehouse, "company")
                if warehouse_company and warehouse_company != company:
                    # Use warehouse's company instead of provided company
                    company = warehouse_company
                
                # Fetch all inventory item details for these items in one query
                inventory_details = frappe.db.get_all(
                    "Inventory Item Details",
                    filters={
                        "item_code": ["in", item_codes],
                        "warehouse": warehouse,
                        "company": company
                    },
                    fields=["item_code", "buying_price", "selling_price"]
                )
                
                # Create a dictionary for quick lookup: item_code -> inventory details
                inventory_map = {}
                for inv in inventory_details:
                    inventory_map[inv["item_code"]] = inv
                
                # Map buying_price and selling_price to products
                for product in products:
                    item_code = product["item_code"]
                    if item_code in inventory_map:
                        inv_details = inventory_map[item_code]
                        # Only set if value is not 0 or None (0.00 is the default, treat as not set)
                        buying_price = inv_details.get("buying_price")
                        selling_price = inv_details.get("selling_price")
                        product["buying_price"] = flt(buying_price) if buying_price and flt(buying_price) > 0 else None
                        product["selling_price"] = flt(selling_price) if selling_price and flt(selling_price) > 0 else None
                    else:
                        product["buying_price"] = None
                        product["selling_price"] = None
            else:
                # Doctype doesn't exist, set to None
                for product in products:
                    product["buying_price"] = None
                    product["selling_price"] = None
        else:
            # No products, set to None
            for product in products:
                product["buying_price"] = None
                product["selling_price"] = None
    else:
        # No warehouse provided, set to None
        for product in products:
            product["buying_price"] = None
            product["selling_price"] = None
    
    # Convert warranty_period to integer and use original unit if available
    # Check for custom fields that store the original unit and value
    for product in products:
        warranty_period = product.get("warranty_period")
        custom_unit = product.get("custom_warranty_period_unit")
        custom_value = product.get("custom_warranty_period_value")
        
        # If custom fields exist, use the original unit and value
        if custom_unit and custom_value:
            try:
                product["warranty_period"] = cint(custom_value)
                product["warranty_period_unit"] = custom_unit
            except (ValueError, TypeError):
                # Fallback to warranty_period in days
                if warranty_period:
                    try:
                        warranty_days = cint(warranty_period)
                        product["warranty_period"] = warranty_days
                        product["warranty_period_unit"] = "Days"
                    except (ValueError, TypeError):
                        product["warranty_period"] = 0
                        product["warranty_period_unit"] = None
                else:
                    product["warranty_period"] = 0
                    product["warranty_period_unit"] = None
        elif warranty_period:
            # No custom fields, use warranty_period in days (default ERPNext behavior)
            try:
                # Try to convert to integer
                warranty_days = cint(warranty_period)
                product["warranty_period"] = warranty_days
                product["warranty_period_unit"] = "Days"
            except (ValueError, TypeError):
                # If conversion fails, try to extract number from string
                try:
                    numbers = re.findall(r'\d+', str(warranty_period))
                    if numbers:
                        warranty_days = cint(numbers[0])
                        product["warranty_period"] = warranty_days
                        product["warranty_period_unit"] = "Days"
                    else:
                        product["warranty_period"] = 0
                        product["warranty_period_unit"] = None
                except (ValueError, TypeError):
                    product["warranty_period"] = 0
                    product["warranty_period_unit"] = None
        else:
            product["warranty_period"] = 0
            product["warranty_period_unit"] = None
        
        # Remove custom fields from response (they're only used internally)
        product.pop("custom_warranty_period_unit", None)
        product.pop("custom_warranty_period_value", None)
    
    # Set HTTP status code
    frappe.local.response["http_status_code"] = 200
    
    return {
        "products": products,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size
        },
        "price_list": price_list if price_list else None,
        "warehouse": warehouse if warehouse else None
    }


@frappe.whitelist()
def get_product_details(item_code: str, company: str = None) -> Dict:
    """Get detailed information about a specific product
    
    Args:
        item_code: Product item code
        company: Company for stock and pricing info
        
    Returns:
        Detailed product information
    """
    # Validate user permissions
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    # Validate item exists
    if not frappe.db.exists("Item", item_code):
        frappe.throw(_("Item {0} does not exist").format(item_code), frappe.DoesNotExistError)
    
    # Get item document
    item = frappe.get_doc("Item", item_code)
    
    # Build response
    product_data = {
        "item_code": item.item_code,
        "item_name": item.item_name,
        "item_group": item.item_group,
        "stock_uom": item.stock_uom,
        "standard_rate": item.standard_rate,
        "description": item.description,
        "is_stock_item": item.is_stock_item,
        "is_sales_item": item.is_sales_item,
        "is_purchase_item": item.is_purchase_item,
        "disabled": item.disabled,
        "brand": item.brand,
        "image": item.image,
        "weight_per_unit": item.weight_per_unit,
        "weight_uom": item.weight_uom,
        "variant_of": item.variant_of,
        "has_variants": item.has_variants,
    }
    
    # Convert warranty_period to integer and use original unit if available
    # Check for custom fields that store the original unit and value
    warranty_period = item.warranty_period
    custom_unit = getattr(item, 'custom_warranty_period_unit', None)
    custom_value = getattr(item, 'custom_warranty_period_value', None)
    
    # If custom fields exist, use the original unit and value
    if custom_unit and custom_value:
        try:
            product_data["warranty_period"] = cint(custom_value)
            product_data["warranty_period_unit"] = custom_unit
        except (ValueError, TypeError):
            # Fallback to warranty_period in days
            if warranty_period:
                try:
                    warranty_days = cint(warranty_period)
                    product_data["warranty_period"] = warranty_days
                    product_data["warranty_period_unit"] = "Days"
                except (ValueError, TypeError):
                    product_data["warranty_period"] = 0
                    product_data["warranty_period_unit"] = None
            else:
                product_data["warranty_period"] = 0
                product_data["warranty_period_unit"] = None
    elif warranty_period:
        # No custom fields, use warranty_period in days (default ERPNext behavior)
        try:
            warranty_days = cint(warranty_period)
            product_data["warranty_period"] = warranty_days
            product_data["warranty_period_unit"] = "Days"
        except (ValueError, TypeError):
            # If conversion fails, try to extract number from string
            try:
                numbers = re.findall(r'\d+', str(warranty_period))
                if numbers:
                    warranty_days = cint(numbers[0])
                    product_data["warranty_period"] = warranty_days
                    product_data["warranty_period_unit"] = "Days"
                else:
                    product_data["warranty_period"] = 0
                    product_data["warranty_period_unit"] = None
            except (ValueError, TypeError):
                product_data["warranty_period"] = 0
                product_data["warranty_period_unit"] = None
    else:
        product_data["warranty_period"] = 0
        product_data["warranty_period_unit"] = None
    
    # Get stock quantity if company provided
    if company and item.is_stock_item:
        from erpnext.stock.utils import get_stock_balance
        try:
            product_data["stock_qty"] = get_stock_balance(
                item_code,
                None,
                nowdate()
            )
        except Exception:
            product_data["stock_qty"] = 0
    
    # Get prices if company provided
    if company:
        # Resolve a default selling price list (same logic as get_products)
        default_price_list = frappe.db.get_value(
            "Price List",
            {"enabled": 1, "selling": 1},
            "name",
            order_by="creation desc"
        ) or frappe.get_single_value("Selling Settings", "selling_price_list") \
          or frappe.db.get_value("Price List", _("Standard Selling"), "name")

        bin_data = frappe.db.sql("""
            SELECT b.warehouse, b.actual_qty, b.valuation_rate
            FROM `tabBin` b
            INNER JOIN `tabWarehouse` w ON w.name = b.warehouse
            WHERE b.item_code = %(item_code)s AND w.company = %(company)s AND b.actual_qty > 0
            ORDER BY b.actual_qty DESC
            LIMIT 1
        """, {"item_code": item_code, "company": company}, as_dict=True)
        product_data["valuation_rate"] = bin_data[0]["valuation_rate"] if bin_data else 0

        price_lists = frappe.get_all(
            "Item Price",
            filters={"item_code": item_code},
            fields=["price_list", "price_list_rate", "currency"],
            limit=5
        )
        product_data["prices"] = price_lists

        # Compute a single "price" field matching what get_products shows,
        # so the details view and the list view never disagree
        matched_price = None
        if default_price_list:
            for p in price_lists:
                if p.get("price_list") == default_price_list and (not p.get("uom") or p.get("uom") == item.stock_uom):
                    matched_price = p
                    break
            if not matched_price:
                matched_price = next((p for p in price_lists if p.get("price_list") == default_price_list), None)

        if matched_price:
            product_data["price"] = matched_price["price_list_rate"]
            product_data["price_currency"] = matched_price.get("currency")
            product_data["price_source"] = "price_list"
        else:
            product_data["price"] = item.standard_rate or 0
            product_data["price_source"] = "standard_rate"
    
    # Get barcodes
    barcodes = [b.barcode for b in item.barcodes]
    if barcodes:
        product_data["barcodes"] = barcodes
    
    # Set HTTP status code
    frappe.local.response["http_status_code"] = 200
    
    return {
        "product": product_data
    }


@frappe.whitelist()
def update_product(
    item_code: str,
    item_name: str = None,
    item_group: str = None,
    stock_uom: str = None,
    standard_rate: float = None,
    description: str = None,
    is_stock_item: bool = None,
    is_sales_item: bool = None,
    is_purchase_item: bool = None,
    brand: str = None,
    image: str = None,
    weight_per_unit: float = None,
    weight_uom: str = None,
    disabled: bool = None,
    buying_price: float = None,
    warehouse: str = None,
    company: str = None
) -> Dict:
    """Update an existing product/item
    
    Args:
        item_code: Item code to update
        item_name: New item name
        item_group: New item group
        stock_uom: New stock UOM
        standard_rate: New standard rate (selling price)
        description: New description
        is_stock_item: New stock item status
        is_sales_item: New sales item status
        is_purchase_item: New purchase item status
        brand: New brand
        image: New image
        weight_per_unit: New weight per unit
        weight_uom: New weight UOM
        disabled: Disabled status
        buying_price: Buying price (cost price) - requires warehouse
        warehouse: Warehouse for buying_price (required if buying_price is provided)
        company: Company for buying_price (optional, auto-detected from warehouse if not provided)
        
    Returns:
        Updated product details
    """
    # Validate user permissions
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    # Validate item exists
    if not frappe.db.exists("Item", item_code):
        frappe.throw(_("Item {0} does not exist").format(item_code), frappe.DoesNotExistError)
    
    # Get item document
    item = frappe.get_doc("Item", item_code)
    
    # Update fields
    if item_name is not None:
        item.item_name = item_name
    if item_group is not None:
        if not frappe.db.exists("Item Group", item_group):
            frappe.throw(_("Item Group {0} does not exist").format(item_group))
        item.item_group = item_group
    if stock_uom is not None:
        if not frappe.db.exists("UOM", stock_uom):
            frappe.throw(_("Unit of Measure {0} does not exist").format(stock_uom))
        item.stock_uom = stock_uom
    if standard_rate is not None:
        item.standard_rate = flt(standard_rate)
    if description is not None:
        item.description = description
    if is_stock_item is not None:
        item.is_stock_item = 1 if is_stock_item else 0
    if is_sales_item is not None:
        item.is_sales_item = 1 if is_sales_item else 0
    if is_purchase_item is not None:
        item.is_purchase_item = 1 if is_purchase_item else 0
    if brand is not None:
        item.brand = brand
    if image is not None:
        item.image = image
    if weight_per_unit is not None:
        item.weight_per_unit = flt(weight_per_unit)
    if weight_uom is not None:
        item.weight_uom = weight_uom
    if disabled is not None:
        item.disabled = 1 if disabled else 0
    
    item.save(ignore_permissions=True)
    
    # Update buying_price in Inventory Item Details if provided
    buying_price_updated = False
    if buying_price is not None:
        if not warehouse:
            frappe.throw(_("Warehouse is required when updating buying_price"))
        
        # Validate warehouse exists
        if not frappe.db.exists("Warehouse", warehouse):
            frappe.throw(_("Warehouse '{0}' does not exist").format(warehouse))
        
        # Import the helper function
        from techsavanna_pos.api.inventory_api import _create_or_update_inventory_item_details
        
        # Update buying_price in Inventory Item Details
        _create_or_update_inventory_item_details(
            item_code=item_code,
            warehouse=warehouse,
            company=company,
            buying_price=flt(buying_price)
        )
        buying_price_updated = True
    
    frappe.db.commit()
    
    # Set HTTP status code
    frappe.local.response["http_status_code"] = 200
    
    response_data = {
        "item_code": item.item_code,
        "item_name": item.item_name,
        "item_group": item.item_group,
        "stock_uom": item.stock_uom,
        "standard_rate": item.standard_rate,
        "is_stock_item": item.is_stock_item,
        "is_sales_item": item.is_sales_item,
        "is_purchase_item": item.is_purchase_item,
        "disabled": item.disabled
    }
    
    # Add buying_price info if it was updated
    if buying_price_updated:
        response_data["buying_price"] = flt(buying_price)
        response_data["buying_price_warehouse"] = warehouse
    
    return {
        "product": response_data,
        "message": _("Product updated successfully")
    }


@frappe.whitelist()
def delete_product(item_code: str) -> Dict:
    """Delete/disable a product/item
    
    Args:
        item_code: Item code to delete/disable
        
    Returns:
        Success message
    """
    # Validate user permissions
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    # Validate item exists
    if not frappe.db.exists("Item", item_code):
        frappe.throw(_("Item {0} does not exist").format(item_code), frappe.DoesNotExistError)
    
    # Disable item instead of deleting (soft delete)
    frappe.db.set_value("Item", item_code, "disabled", 1)
    frappe.db.commit()
    
    # Set HTTP status code
    frappe.local.response["http_status_code"] = 200
    
    return {
        "message": _("Product disabled successfully")
    }


@frappe.whitelist()
def enable_product(item_code: str) -> Dict:
    """Enable a disabled product/item
    
    Args:
        item_code: Item code to enable
        
    Returns:
        Success message
    """
    # Validate user permissions
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    # Validate item exists
    if not frappe.db.exists("Item", item_code):
        frappe.throw(_("Item {0} does not exist").format(item_code), frappe.DoesNotExistError)
    
    # Enable item
    frappe.db.set_value("Item", item_code, "disabled", 0)
    frappe.db.commit()
    
    # Set HTTP status code
    frappe.local.response["http_status_code"] = 200
    
    return {
        "message": _("Product enabled successfully")
    }


@frappe.whitelist()
def add_barcode(item_code: str, barcode: str) -> Dict:
    """Add a barcode to an item
    
    Args:
        item_code: Item code
        barcode: Barcode to add
        
    Returns:
        Success message
    """
    # Validate user permissions
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    # Validate item exists
    if not frappe.db.exists("Item", item_code):
        frappe.throw(_("Item {0} does not exist").format(item_code), frappe.DoesNotExistError)
    
    # Get item document
    item = frappe.get_doc("Item", item_code)
    
    # Check if barcode already exists
    existing_barcodes = [b.barcode for b in item.barcodes]
    if barcode in existing_barcodes:
        frappe.throw(_("Barcode {0} already exists for this item").format(barcode))
    
    # Add barcode
    item.append("barcodes", {"barcode": barcode})
    item.save(ignore_permissions=True)
    frappe.db.commit()
    
    # Set HTTP status code
    frappe.local.response["http_status_code"] = 200
    
    return {
        "message": _("Barcode added successfully")
    }


@frappe.whitelist()
def remove_barcode(item_code: str, barcode: str) -> Dict:
    """Remove a barcode from an item
    
    Args:
        item_code: Item code
        barcode: Barcode to remove
        
    Returns:
        Success message
    """
    # Validate user permissions
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    # Validate item exists
    if not frappe.db.exists("Item", item_code):
        frappe.throw(_("Item {0} does not exist").format(item_code), frappe.DoesNotExistError)
    
    # Get item document
    item = frappe.get_doc("Item", item_code)
    
    # Remove barcode
    item.barcodes = [b for b in item.barcodes if b.barcode != barcode]
    item.save(ignore_permissions=True)
    frappe.db.commit()
    
    # Set HTTP status code
    frappe.local.response["http_status_code"] = 200
    
    return {
        "message": _("Barcode removed successfully")
    }


@frappe.whitelist()
def get_product_price(item_code: str, price_list: str = None, company: str = None) -> Dict:
    """Get product price from price list
    
    Args:
        item_code: Item code
        price_list: Price list name (optional)
        company: Company for default price list
        
    Returns:
        Product price information
    """
    # Validate user permissions
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    # Validate item exists
    if not frappe.db.exists("Item", item_code):
        frappe.throw(_("Item {0} does not exist").format(item_code), frappe.DoesNotExistError)
    
    # Get price list if not provided
    if not price_list:
        if company:
            # Get default selling price list
            price_list = frappe.db.get_value(
                "Price List",
                {"enabled": 1, "selling": 1},
                "name",
                order_by="creation desc"
            )
        if not price_list:
            frappe.throw(_("Price list is required"))
    
    # Get price
    price = frappe.db.get_value(
        "Item Price",
        {"item_code": item_code, "price_list": price_list},
        ["price_list_rate", "currency"],
        as_dict=True
    )
    
    if not price:
        # Get standard rate as fallback
        standard_rate = frappe.db.get_value("Item", item_code, "standard_rate")
        currency = frappe.db.get_value("Price List", price_list, "currency")
        return {
            "item_code": item_code,
            "price_list": price_list,
            "price": standard_rate or 0,
            "currency": currency,
            "source": "standard_rate"
        }
    
    # Set HTTP status code
    frappe.local.response["http_status_code"] = 200
    
    return {
        "item_code": item_code,
        "price_list": price_list,
        "price": price.price_list_rate,
        "currency": price.currency,
        "source": "price_list"
    }


@frappe.whitelist()
def set_product_price(
    item_code: str,
    price: float,
    price_list: str,
    currency: str = None,
    company: str = None
) -> Dict:
    """Set product price in a price list
    
    Args:
        item_code: Item code
        price: Price to set
        price_list: Price list name
        currency: Currency (optional, defaults to company currency)
        company: Company for currency default
        
    Returns:
        Success message
    """
    # Validate user permissions
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    # Validate item exists
    if not frappe.db.exists("Item", item_code):
        frappe.throw(_("Item {0} does not exist").format(item_code), frappe.DoesNotExistError)
    
    # Validate price list exists
    if not frappe.db.exists("Price List", price_list):
        frappe.throw(_("Price List {0} does not exist").format(price_list))
    
    # Get currency if not provided
    if not currency:
        if company:
            currency = frappe.db.get_value("Company", company, "default_currency")
        if not currency:
            currency = frappe.db.get_value("Price List", price_list, "currency")
    
    # Check if price already exists
    existing_price = frappe.db.exists(
        "Item Price",
        {"item_code": item_code, "price_list": price_list}
    )
    
    if existing_price:
        # Update existing price
        frappe.db.set_value("Item Price", existing_price, {
            "price_list_rate": flt(price),
            "currency": currency
        })
    else:
        # Create new price
        item_price = frappe.new_doc("Item Price")
        item_price.item_code = item_code
        item_price.price_list = price_list
        item_price.price_list_rate = flt(price)
        item_price.currency = currency
        item_price.selling = 1
        item_price.insert(ignore_permissions=True)
    
    frappe.db.commit()
    
    # Set HTTP status code
    frappe.local.response["http_status_code"] = 200
    
    return {
        "message": _("Product price set successfully")
    }


@frappe.whitelist()
def get_stock_quantity(item_code: str, company: str = None, warehouse: str = None) -> Dict:
    """Get stock quantity for a product
    
    Args:
        item_code: Item code
        company: Company (optional)
        warehouse: Warehouse (optional)
        
    Returns:
        Stock quantity information
    """
    # Validate user permissions
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    # Validate item exists
    if not frappe.db.exists("Item", item_code):
        frappe.throw(_("Item {0} does not exist").format(item_code), frappe.DoesNotExistError)
    
    # Get stock balance
    from erpnext.stock.utils import get_stock_balance
    try:
        qty = get_stock_balance(item_code, warehouse, nowdate())
    except Exception:
        qty = 0
    
    # Set HTTP status code
    frappe.local.response["http_status_code"] = 200
    
    return {
        "item_code": item_code,
        "warehouse": warehouse,
        "quantity": qty
    }


@frappe.whitelist()
def bulk_create_products(products: List[Dict], company: str = None) -> Dict:
    """Create multiple products in bulk
    
    Args:
        products: List of product dictionaries
        company: Company for item defaults
        
    Returns:
        Summary of created products
    """
    # Validate user permissions
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    if isinstance(products, str):
        products = json.loads(products)
    
    if not isinstance(products, list):
        frappe.throw(_("Products must be a list"))
    
    created = []
    failed = []
    
    for product_data in products:
        try:
            # Extract required fields
            item_code = product_data.get("item_code")
            item_name = product_data.get("item_name")
            
            if not item_code or not item_name:
                failed.append({
                    "item_code": item_code,
                    "error": "item_code and item_name are required"
                })
                continue
            
            # Create product using existing function
            result = create_product(
                item_code=item_code,
                item_name=item_name,
                item_group=product_data.get("item_group", "All Item Groups"),
                stock_uom=product_data.get("stock_uom", "Nos"),
                standard_rate=product_data.get("standard_rate", 0.0),
                description=product_data.get("description"),
                is_stock_item=product_data.get("is_stock_item", True),
                is_sales_item=product_data.get("is_sales_item", True),
                is_purchase_item=product_data.get("is_purchase_item", False),
                brand=product_data.get("brand"),
                barcode=product_data.get("barcode"),
                image=product_data.get("image"),
                company=company or product_data.get("company"),
                prevent_etims_registration=product_data.get("prevent_etims_registration", True)
            )
            created.append(result["product"])
        except Exception as e:
            failed.append({
                "item_code": product_data.get("item_code"),
                "error": str(e)
            })
    
    # Set HTTP status code
    frappe.local.response["http_status_code"] = 201
    
    return {
        "created": created,
        "failed": failed,
        "total": len(products),
        "success_count": len(created),
        "failure_count": len(failed)
    }


@frappe.whitelist()
def get_item_groups() -> Dict:
    """Get all item groups/categories
    
    Returns:
        List of item groups
    """
    # Validate user permissions
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    item_groups = frappe.get_all(
        "Item Group",
        fields=["name", "item_group_name", "parent_item_group", "is_group"],
        order_by="name"
    )
    
    # Set HTTP status code
    frappe.local.response["http_status_code"] = 200
    
    return {
        "item_groups": item_groups,
        "count": len(item_groups)
    }


@frappe.whitelist()
def get_brands() -> Dict:
    """Get all brands
    
    Returns:
        List of brands
    """
    # Validate user permissions
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    brands = frappe.get_all(
        "Brand",
        fields=["name", "brand"],
        order_by="name"
    )
    
    # Set HTTP status code
    frappe.local.response["http_status_code"] = 200
    
    return {
        "brands": brands,
        "count": len(brands)
    }


@frappe.whitelist()
def get_uoms() -> Dict:
    """Get all units of measure
    
    Returns:
        List of UOMs
    """
    # Validate user permissions
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    uoms = frappe.get_all(
        "UOM",
        fields=["name", "uom_name"],
        order_by="name"
    )
    
    # Set HTTP status code
    frappe.local.response["http_status_code"] = 200
    
    return {
        "uoms": uoms,
        "count": len(uoms)
    }


# ==================== NEW ENDPOINTS ====================

@frappe.whitelist()
def bulk_update_prices(price_updates: List[Dict], price_list: str, currency: str = None, company: str = None) -> Dict:
    """Bulk update prices for multiple products
    
    Args:
        price_updates: List of dicts with item_code and price
        price_list: Price list name
        currency: Currency (optional)
        company: Company for currency default
        
    Returns:
        Summary of updated prices
    """
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    if isinstance(price_updates, str):
        price_updates = json.loads(price_updates)
    
    if not frappe.db.exists("Price List", price_list):
        frappe.throw(_("Price List {0} does not exist").format(price_list))
    
    if not currency:
        if company:
            currency = frappe.db.get_value("Company", company, "default_currency")
        if not currency:
            currency = frappe.db.get_value("Price List", price_list, "currency")
    
    updated = []
    failed = []
    
    for update in price_updates:
        try:
            item_code = update.get("item_code")
            price = update.get("price")
            
            if not item_code or price is None:
                failed.append({"item_code": item_code, "error": "item_code and price are required"})
                continue
            
            if not frappe.db.exists("Item", item_code):
                failed.append({"item_code": item_code, "error": "Item does not exist"})
                continue
            
            # Get the item to fetch stock_uom for proper Item Price matching
            stock_uom = frappe.db.get_value("Item", item_code, "stock_uom")
            if not stock_uom:
                failed.append({"item_code": item_code, "error": "Item does not have stock_uom"})
                continue
            
            # Check if price exists - use get_value to get the actual document name
            # Item Price uniqueness is based on item_code, price_list, uom, and other optional fields
            existing_price_name = frappe.db.get_value(
                "Item Price",
                {
                    "item_code": item_code,
                    "price_list": price_list,
                    "uom": stock_uom
                },
                "name"
            )
            
            if existing_price_name:
                # Update existing price using get_doc and save to ensure validations run
                item_price = frappe.get_doc("Item Price", existing_price_name)
                item_price.price_list_rate = flt(price)
                # Currency is automatically set from Price List during validation, but update if explicitly provided
                if currency and currency != item_price.currency:
                    item_price.currency = currency
                item_price.save(ignore_permissions=True)
            else:
                # Create new price
                item_price = frappe.new_doc("Item Price")
                item_price.item_code = item_code
                item_price.price_list = price_list
                item_price.price_list_rate = flt(price)
                item_price.uom = stock_uom
                item_price.selling = 1
                # Currency will be automatically set from Price List during validation
                # but set it explicitly if provided to ensure consistency
                if currency:
                    item_price.currency = currency
                item_price.insert(ignore_permissions=True)
            
            updated.append({"item_code": item_code, "price": price})
        except Exception as e:
            failed.append({"item_code": update.get("item_code"), "error": str(e)})
    
    frappe.db.commit()
    frappe.local.response["http_status_code"] = 200
    
    return {
        "updated": updated,
        "failed": failed,
        "total": len(price_updates),
        "success_count": len(updated),
        "failure_count": len(failed)
    }


@frappe.whitelist()
def create_product_variant(
    template_item_code: str,
    variant_attributes: List[Dict],
    item_code: str = None,
    item_name: str = None,
    standard_rate: float = None
) -> Dict:
    """Create a product variant from a template
    
    Args:
        template_item_code: Template item code (must have has_variants=1)
        variant_attributes: List of dicts with attribute and attribute_value
        item_code: Optional custom item code
        item_name: Optional custom item name
        standard_rate: Optional custom price
        
    Returns:
        Created variant details
    """
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    if isinstance(variant_attributes, str):
        variant_attributes = json.loads(variant_attributes)
    
    if not frappe.db.exists("Item", template_item_code):
        frappe.throw(_("Template item {0} does not exist").format(template_item_code))
    
    template = frappe.get_doc("Item", template_item_code)
    if not template.has_variants:
        frappe.throw(_("Item {0} does not have variants enabled").format(template_item_code))
    
    # Build attributes dict for variant creation
    args = {}
    for attr in variant_attributes:
        args[attr.get("attribute")] = attr.get("attribute_value")
    
    # Import variant creation function
    from erpnext.controllers.item_variant import create_variant
    
    try:
        variant = create_variant(template_item_code, args)
        
        if item_code:
            variant.item_code = item_code
        if item_name:
            variant.item_name = item_name
        if standard_rate is not None:
            variant.standard_rate = flt(standard_rate)
        
        variant.insert(ignore_permissions=True)
        frappe.db.commit()
        
        frappe.local.response["http_status_code"] = 201
        return {
            "variant": {
                "item_code": variant.item_code,
                "item_name": variant.item_name,
                "variant_of": variant.variant_of,
                "standard_rate": variant.standard_rate
            },
            "message": _("Product variant created successfully")
        }
    except Exception as e:
        frappe.throw(_("Error creating variant: {0}").format(str(e)))


@frappe.whitelist()
def get_product_variants(template_item_code: str) -> Dict:
    """Get all variants of a template product
    
    Args:
        template_item_code: Template item code
        
    Returns:
        List of variants
    """
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    if not frappe.db.exists("Item", template_item_code):
        frappe.throw(_("Template item {0} does not exist").format(template_item_code))
    
    variants = frappe.get_all(
        "Item",
        filters={"variant_of": template_item_code, "disabled": 0},
        fields=["name", "item_code", "item_name", "standard_rate", "disabled"],
        order_by="item_code"
    )
    
    # Get attributes for each variant
    for variant in variants:
        attributes = frappe.get_all(
            "Item Variant Attribute",
            filters={"parent": variant.item_code},
            fields=["attribute", "attribute_value"],
            order_by="idx"
        )
        variant["attributes"] = attributes
    
    frappe.local.response["http_status_code"] = 200
    return {
        "template_item_code": template_item_code,
        "variants": variants,
        "count": len(variants)
    }


@frappe.whitelist()
def bulk_import_products(products_data: str, company: str = None) -> Dict:
    """Bulk import products from JSON string
    
    Args:
        products_data: JSON string or list of product dictionaries
        company: Company for item defaults
        
    Returns:
        Import summary
    """
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    if isinstance(products_data, str):
        try:
            products = json.loads(products_data)
        except json.JSONDecodeError:
            frappe.throw(_("Invalid JSON format"))
    else:
        products = products_data
    
    if not isinstance(products, list):
        frappe.throw(_("Products data must be a list"))
    
    return bulk_create_products(products, company)


@frappe.whitelist()
def bulk_import_opening_stock(
    stock_data: List[Dict],
    company: str,
    posting_date: str = None,
    warehouse: str = None
) -> Dict:
    """Bulk import opening stock for multiple items
    
    Args:
        stock_data: List of dicts with item_code, qty, and valuation_rate
        company: Company name
        posting_date: Posting date (default: today)
        warehouse: Default warehouse (optional)
        
    Returns:
        Stock reconciliation document details
    """
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    if isinstance(stock_data, str):
        stock_data = json.loads(stock_data)
    
    if not frappe.db.exists("Company", company):
        frappe.throw(_("Company {0} does not exist").format(company))
    
    if not posting_date:
        posting_date = nowdate()
    
    # Create Stock Reconciliation
    stock_reco = frappe.new_doc("Stock Reconciliation")
    stock_reco.company = company
    stock_reco.purpose = "Opening Stock"
    stock_reco.posting_date = posting_date
    stock_reco.posting_time = "00:00:00"
    
    # Get default warehouse if not provided
    if not warehouse:
        warehouse = frappe.db.get_value(
            "Warehouse",
            {"company": company, "is_group": 0},
            "name",
            order_by="creation desc"
        )
    
    added_items = []
    failed_items = []
    
    for stock_item in stock_data:
        try:
            item_code = stock_item.get("item_code")
            qty = stock_item.get("qty", 0)
            valuation_rate = stock_item.get("valuation_rate", 0)
            item_warehouse = stock_item.get("warehouse", warehouse)
            
            if not item_code:
                failed_items.append({"item_code": item_code, "error": "item_code is required"})
                continue
            
            if not frappe.db.exists("Item", item_code):
                failed_items.append({"item_code": item_code, "error": "Item does not exist"})
                continue
            
            stock_reco.append("items", {
                "item_code": item_code,
                "warehouse": item_warehouse,
                "qty": flt(qty),
                "valuation_rate": flt(valuation_rate)
            })
            added_items.append(item_code)
        except Exception as e:
            failed_items.append({"item_code": stock_item.get("item_code"), "error": str(e)})
    
    if not added_items:
        frappe.throw(_("No valid items to import"))
    
    stock_reco.insert(ignore_permissions=True)
    stock_reco.submit()
    frappe.db.commit()
    
    frappe.local.response["http_status_code"] = 201
    return {
        "stock_reconciliation": stock_reco.name,
        "company": company,
        "posting_date": posting_date,
        "added_items": added_items,
        "failed_items": failed_items,
        "total_items": len(stock_data),
        "success_count": len(added_items),
        "failure_count": len(failed_items)
    }


@frappe.whitelist()
def create_price_list(
    price_list_name: str,
    currency: str,
    selling: bool = True,
    buying: bool = False,
    enabled: bool = True
) -> Dict:
    """Create a new price list
    
    Args:
        price_list_name: Name of the price list
        currency: Currency code
        selling: Is selling price list (default: True)
        buying: Is buying price list (default: False)
        enabled: Is enabled (default: True)
        
    Returns:
        Created price list details
    """
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    if frappe.db.exists("Price List", price_list_name):
        frappe.throw(_("Price List {0} already exists").format(price_list_name))
    
    if not frappe.db.exists("Currency", currency):
        frappe.throw(_("Currency {0} does not exist").format(currency))
    
    price_list = frappe.new_doc("Price List")
    price_list.price_list_name = price_list_name
    price_list.currency = currency
    price_list.selling = 1 if selling else 0
    price_list.buying = 1 if buying else 0
    price_list.enabled = 1 if enabled else 0
    price_list.insert(ignore_permissions=True)
    frappe.db.commit()
    
    frappe.local.response["http_status_code"] = 201
    return {
        "price_list": {
            "name": price_list.name,
            "price_list_name": price_list.price_list_name,
            "currency": price_list.currency,
            "selling": price_list.selling,
            "buying": price_list.buying,
            "enabled": price_list.enabled
        },
        "message": _("Price list created successfully")
    }


@frappe.whitelist()
def get_price_lists(
    selling: bool = None,
    buying: bool = None,
    enabled: str = "all",
    page: int = 1,
    page_size: int = 20
) -> Dict:
    """Get price lists with optional filters and pagination
    
    Args:
        selling: Filter by selling price lists (True/False/None for all)
        buying: Filter by buying price lists (True/False/None for all)
        enabled: Filter by enabled status - "all" (default), "enabled", or "disabled"
        page: Page number (default: 1)
        page_size: Items per page (default: 20)
        
    Returns:
        Paginated list of price lists with pagination info
    """
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    # Validate pagination parameters
    page = max(1, cint(page))  # Ensure page is at least 1
    page_size = max(1, min(cint(page_size), 100))  # Ensure page_size is between 1 and 100
    
    # Build filters
    filters = {}
    
    # Handle enabled filter - accept "all", "enabled", "disabled", or boolean for backward compatibility
    if enabled is not None:
        if isinstance(enabled, str):
            enabled_lower = enabled.lower().strip()
            if enabled_lower == "enabled":
                filters["enabled"] = 1
            elif enabled_lower == "disabled":
                filters["enabled"] = 0
            # "all" or any other value means no filter
        elif isinstance(enabled, bool):
            # Backward compatibility: boolean values
            filters["enabled"] = 1 if enabled else 0
    
    if selling is not None:
        filters["selling"] = 1 if selling else 0
    if buying is not None:
        filters["buying"] = 1 if buying else 0
    
    # Get total count for pagination
    total = frappe.db.count("Price List", filters=filters)
    
    # Calculate pagination
    start = (page - 1) * page_size
    
    # Get paginated results
    price_lists = frappe.get_all(
        "Price List",
        fields=["name", "price_list_name", "currency", "selling", "buying", "enabled"],
        filters=filters,
        order_by="price_list_name",
        limit=page_size,
        start=start
    )
    
    frappe.local.response["http_status_code"] = 200
    return {
        "price_lists": price_lists,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size if total > 0 else 0
        },
        "count": len(price_lists),
        "filters": {
            "selling": selling,
            "buying": buying,
            "enabled": enabled
        }
    }


@frappe.whitelist()
def update_price_list(
    name: str = None,
    price_list_name: str = None,
    currency: str = None,
    selling: bool = None,
    buying: bool = None,
    enabled: bool = None,
    new_price_list_name: str = None
) -> Dict:
    """Update a price list
    
    Args:
        name: Current price list document name (for finding the price list)
              If provided, used to find the price list by document name
        price_list_name: Current price list name - used in two ways:
                       1. If 'name' is not provided, used to find the price list (by document name or price_list_name field)
                       2. If 'name' is provided and this is different, treated as new name for renaming
        currency: New currency
        selling: New selling status
        buying: New buying status
        enabled: New enabled status
        new_price_list_name: New price list name (for renaming, optional)
                           If both name and new_price_list_name are provided, rename will occur
        
    Returns:
        Updated price list details
    """
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    # Determine current price list identifier and potential new name for renaming
    # Price List uses autoname="field:price_list_name", so name should match price_list_name
    # Support both 'name' and 'price_list_name' as identifier (for finding the price list)
    current_identifier = None
    new_name = None
    
    # Priority: 'name' parameter takes precedence over 'price_list_name' for finding
    if name and name.strip():
        current_identifier = name.strip()
        # If new_price_list_name is provided, use it for renaming
        if new_price_list_name and new_price_list_name.strip():
            new_name = new_price_list_name.strip()
    elif price_list_name and price_list_name.strip():
        # Use price_list_name as identifier (backward compatibility)
        current_identifier = price_list_name.strip()
        # If new_price_list_name is provided, use it for renaming
        if new_price_list_name and new_price_list_name.strip():
            new_name = new_price_list_name.strip()
    
    if not current_identifier:
        frappe.throw(_("Price list name is required. Please provide the current name of the price list you want to update (use 'name' or 'price_list_name' parameter)."), frappe.ValidationError)
    
    # Try to find price list by document name first (since Price List uses autoname="field:price_list_name")
    price_list_doc = None
    if frappe.db.exists("Price List", current_identifier):
        # Found by document name (which should match price_list_name due to autoname)
        price_list_doc = frappe.get_doc("Price List", current_identifier)
    else:
        # Try to find by price_list_name field as fallback (in case document name is different)
        price_list_match = frappe.db.get_value(
            "Price List",
            {"price_list_name": current_identifier},
            "name"
        )
        if price_list_match:
            price_list_doc = frappe.get_doc("Price List", price_list_match)
        else:
            frappe.throw(_("Price List '{0}' does not exist. Please check the price list name and try again.").format(current_identifier), frappe.ValidationError)
    
    # Handle renaming if new name is provided and different from current
    if new_name and new_name != price_list_doc.price_list_name:
        # Validate new name doesn't already exist
        if frappe.db.exists("Price List", new_name):
            frappe.throw(_("A price list with the name '{0}' already exists. Please choose a different name.").format(new_name), frappe.ValidationError)
        
        # Use rename_document to properly rename the price list
        # Price List has autoname="field:price_list_name", similar to Brand and Item Group
        try:
            renamed_name = rename_document(
                doctype="Price List",
                old=price_list_doc.name,
                new=new_name,
                force=False,
                merge=False,
                ignore_permissions=True,
                show_alert=False
            )
            
            # Reload the price list with new name
            price_list_doc = frappe.get_doc("Price List", renamed_name)
            
            # Update the price_list_name field to match the new name (in case it doesn't auto-update)
            if price_list_doc.price_list_name != new_name:
                price_list_doc.price_list_name = new_name
                price_list_doc.save(ignore_permissions=True)
        except frappe.DuplicateEntryError:
            frappe.throw(_("A price list with the name '{0}' already exists. Please choose a different name.").format(new_name), frappe.ValidationError)
        except frappe.ValidationError as e:
            error_msg = str(e)
            if "already exists" in error_msg.lower():
                frappe.throw(_("A price list with the name '{0}' already exists. Please choose a different name.").format(new_name), frappe.ValidationError)
            else:
                frappe.throw(_("Unable to rename price list: {0}. Please check that all information is correct and try again.").format(error_msg), frappe.ValidationError)
        except Exception as e:
            frappe.log_error(
                "Rename Price List Error",
                f"Error renaming price list '{current_identifier}' to '{new_name}': {frappe.get_traceback()}"
            )
            frappe.throw(_("An error occurred while renaming the price list. Please check that all information is correct and try again. If the problem persists, contact support."), frappe.ValidationError)
    
    # Update other fields
    if currency:
        if not frappe.db.exists("Currency", currency):
            frappe.throw(_("Currency '{0}' does not exist. Please check the currency code and try again.").format(currency), frappe.ValidationError)
        price_list_doc.currency = currency
    
    if selling is not None:
        price_list_doc.selling = 1 if selling else 0
    
    if buying is not None:
        price_list_doc.buying = 1 if buying else 0
    
    if enabled is not None:
        price_list_doc.enabled = 1 if enabled else 0
    
    # Save changes
    price_list_doc.save(ignore_permissions=True)
    frappe.db.commit()
    
    # Reload to get latest values
    price_list_doc.reload()
    
    frappe.local.response["http_status_code"] = 200
    message = _("Price list '{0}' has been successfully updated").format(price_list_doc.price_list_name)
    if new_name and new_name != current_identifier and new_name != price_list_doc.price_list_name:
        message = _("Price list '{0}' has been successfully renamed to '{1}' and updated").format(current_identifier, new_name)
    
    return {
        "price_list": {
            "name": price_list_doc.name,
            "price_list_name": price_list_doc.price_list_name,
            "currency": price_list_doc.currency,
            "selling": price_list_doc.selling,
            "buying": price_list_doc.buying,
            "enabled": price_list_doc.enabled
        },
        "message": message
    }


@frappe.whitelist()
def delete_price_list(price_list_name: str) -> Dict:
    """Delete a price list
    
    Args:
        price_list_name: Name of the price list to delete
        
    Returns:
        Success message
    """
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    if not frappe.db.exists("Price List", price_list_name):
        frappe.throw(_("Price List {0} does not exist").format(price_list_name))
    
    frappe.delete_doc("Price List", price_list_name, ignore_permissions=True)
    frappe.db.commit()
    
    frappe.local.response["http_status_code"] = 200
    return {
        "message": _("Price list deleted successfully")
    }


@frappe.whitelist()
def create_uom(uom_name: str, must_be_whole_number: bool = False) -> Dict:
    """Create a new unit of measure
    
    Args:
        uom_name: Name of the UOM (e.g., "Nos", "Kg", "Ltr")
        must_be_whole_number: Must be whole number (default: False)
        
    Returns:
        Created UOM details
    """
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    if not uom_name or not uom_name.strip():
        frappe.local.response["http_status_code"] = 400
        return {
            "success": False,
            "error": "UOM name required",
            "message": _("UOM name is required. Please provide a name for the unit of measure.")
        }
    
    uom_name = uom_name.strip()
    
    # Check if UOM already exists by document name (since UOM uses autoname="field:uom_name")
    if frappe.db.exists("UOM", uom_name):
        existing_uom = frappe.get_doc("UOM", uom_name)
        frappe.local.response["http_status_code"] = 409
        return {
            "success": False,
            "error": "Duplicate UOM",
            "message": _("A unit of measure with the name '{0}' already exists. Please choose a different name or update the existing UOM instead.").format(uom_name),
            "existing_uom": {
                "name": existing_uom.name,
                "uom_name": existing_uom.uom_name,
                "must_be_whole_number": existing_uom.must_be_whole_number
            }
        }
    
    # Also check by uom_name field as a fallback
    existing_uom_name = frappe.db.get_value(
        "UOM",
        {"uom_name": uom_name},
        "name"
    )
    if existing_uom_name:
        existing_uom = frappe.get_doc("UOM", existing_uom_name)
        frappe.local.response["http_status_code"] = 409
        return {
            "success": False,
            "error": "Duplicate UOM",
            "message": _("A unit of measure with the name '{0}' already exists. Please choose a different name or update the existing UOM instead.").format(uom_name),
            "existing_uom": {
                "name": existing_uom.name,
                "uom_name": existing_uom.uom_name,
                "must_be_whole_number": existing_uom.must_be_whole_number
            }
        }
    
    try:
        uom = frappe.new_doc("UOM")
        uom.uom_name = uom_name
        uom.must_be_whole_number = 1 if must_be_whole_number else 0
        uom.insert(ignore_permissions=True)
        frappe.db.commit()
        
        frappe.local.response["http_status_code"] = 201
        return {
            "success": True,
            "uom": {
                "name": uom.name,
                "uom_name": uom.uom_name,
                "must_be_whole_number": uom.must_be_whole_number
            },
            "message": _("Unit of measure '{0}' has been successfully created").format(uom_name)
        }
    except frappe.DuplicateEntryError:
        existing_uom = None
        if frappe.db.exists("UOM", uom_name):
            existing_uom = frappe.get_doc("UOM", uom_name)
        
        frappe.local.response["http_status_code"] = 409
        response = {
            "success": False,
            "error": "Duplicate UOM",
            "message": _("A unit of measure with the name '{0}' already exists. Please choose a different name or update the existing UOM instead.").format(uom_name)
        }
        if existing_uom:
            response["existing_uom"] = {
                "name": existing_uom.name,
                "uom_name": existing_uom.uom_name,
                "must_be_whole_number": existing_uom.must_be_whole_number
            }
        return response
    except Exception as e:
        frappe.log_error(
            "Create UOM Error",
            f"Error creating UOM '{uom_name}': {frappe.get_traceback()}"
        )
        error_msg = str(e).lower()
        if "duplicate" in error_msg or "already exists" in error_msg:
            existing_uom = None
            try:
                if frappe.db.exists("UOM", uom_name):
                    existing_uom = frappe.get_doc("UOM", uom_name)
            except:
                pass
            
            frappe.local.response["http_status_code"] = 409
            response = {
                "success": False,
                "error": "Duplicate UOM",
                "message": _("A unit of measure with the name '{0}' already exists. Please choose a different name or update the existing UOM instead.").format(uom_name)
            }
            if existing_uom:
                response["existing_uom"] = {
                    "name": existing_uom.name,
                    "uom_name": existing_uom.uom_name,
                    "must_be_whole_number": existing_uom.must_be_whole_number
                }
            return response
        else:
            frappe.local.response["http_status_code"] = 500
            return {
                "success": False,
                "error": "Creation failed",
                "message": _("An error occurred while creating the unit of measure. Please check that all information is correct and try again. If the problem persists, contact support.")
            }


@frappe.whitelist()
def update_uom(
    name: str = None,
    uom_name: str = None,
    new_uom_name: str = None,
    must_be_whole_number: bool = None
) -> Dict:
    """Update a unit of measure
    
    Args:
        name: Current UOM document name (for finding the UOM)
              If provided, used to find the UOM by document name
        uom_name: Current UOM name - used in two ways:
                 1. If 'name' is not provided, used to find the UOM (by document name or uom_name field)
                 2. If 'name' is provided and this is different, treated as new name for renaming
        new_uom_name: New UOM name (for renaming, optional)
                     If both name and new_uom_name are provided, rename will occur
        must_be_whole_number: New must_be_whole_number status
        
    Returns:
        Updated UOM details
    """
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    # Determine current UOM identifier and potential new name
    # UOM uses autoname="field:uom_name", so name should match uom_name
    # Support both 'name' and 'uom_name' as identifier (for finding the UOM)
    current_identifier = None
    new_name = None
    
    # Priority: 'name' parameter takes precedence over 'uom_name' for finding
    if name and name.strip():
        current_identifier = name.strip()
        # If new_uom_name is provided, use it for renaming
        if new_uom_name and new_uom_name.strip():
            new_name = new_uom_name.strip()
    elif uom_name and uom_name.strip():
        # Use uom_name as identifier (backward compatibility)
        current_identifier = uom_name.strip()
        # If new_uom_name is provided, use it for renaming
        if new_uom_name and new_uom_name.strip():
            new_name = new_uom_name.strip()
    
    if not current_identifier:
        frappe.local.response["http_status_code"] = 400
        return {
            "success": False,
            "error": "UOM name required",
            "message": _("UOM name is required. Please provide the current name of the unit of measure you want to update (use 'name' or 'uom_name' parameter).")
        }
    
    # Try to find UOM by document name first (since UOM uses autoname="field:uom_name")
    uom_doc = None
    if frappe.db.exists("UOM", current_identifier):
        # Found by document name (which should match uom_name due to autoname)
        uom_doc = frappe.get_doc("UOM", current_identifier)
    else:
        # Try to find by uom_name field as fallback (in case document name is different)
        uom_match = frappe.db.get_value(
            "UOM",
            {"uom_name": current_identifier},
            "name"
        )
        if uom_match:
            uom_doc = frappe.get_doc("UOM", uom_match)
        else:
            frappe.local.response["http_status_code"] = 404
            return {
                "success": False,
                "error": "UOM not found",
                "message": _("Unit of measure '{0}' does not exist. Please check the UOM name and try again.").format(current_identifier)
            }
    
    # Handle renaming if new name is provided and different from current
    if new_name and new_name != uom_doc.uom_name:
        # Validate new name doesn't already exist
        if frappe.db.exists("UOM", new_name):
            existing_uom = frappe.get_doc("UOM", new_name)
            frappe.local.response["http_status_code"] = 409
            return {
                "success": False,
                "error": "Duplicate UOM",
                "message": _("A unit of measure with the name '{0}' already exists. Please choose a different name.").format(new_name),
                "existing_uom": {
                    "name": existing_uom.name,
                    "uom_name": existing_uom.uom_name,
                    "must_be_whole_number": existing_uom.must_be_whole_number
                }
            }
        
        # Also check by uom_name field as fallback
        existing_uom_name = frappe.db.get_value(
            "UOM",
            {"uom_name": new_name},
            "name"
        )
        if existing_uom_name and existing_uom_name != uom_doc.name:
            existing_uom = frappe.get_doc("UOM", existing_uom_name)
            frappe.local.response["http_status_code"] = 409
            return {
                "success": False,
                "error": "Duplicate UOM",
                "message": _("A unit of measure with the name '{0}' already exists. Please choose a different name.").format(new_name),
                "existing_uom": {
                    "name": existing_uom.name,
                    "uom_name": existing_uom.uom_name,
                    "must_be_whole_number": existing_uom.must_be_whole_number
                }
            }
        
        # Use rename_document to properly rename the UOM
        # UOM has autoname="field:uom_name", similar to Brand, Item Group, and Price List
        try:
            renamed_name = rename_document(
                doctype="UOM",
                old=uom_doc.name,
                new=new_name,
                force=False,
                merge=False,
                ignore_permissions=True,
                show_alert=False
            )
            
            # Reload the UOM with new name
            uom_doc = frappe.get_doc("UOM", renamed_name)
            
            # Update the uom_name field to match the new name (in case it doesn't auto-update)
            if uom_doc.uom_name != new_name:
                uom_doc.uom_name = new_name
                uom_doc.save(ignore_permissions=True)
        except frappe.DuplicateEntryError:
            existing_uom = None
            try:
                if frappe.db.exists("UOM", new_name):
                    existing_uom = frappe.get_doc("UOM", new_name)
            except:
                pass
            
            frappe.local.response["http_status_code"] = 409
            response = {
                "success": False,
                "error": "Duplicate UOM",
                "message": _("A unit of measure with the name '{0}' already exists. Please choose a different name.").format(new_name)
            }
            if existing_uom:
                response["existing_uom"] = {
                    "name": existing_uom.name,
                    "uom_name": existing_uom.uom_name,
                    "must_be_whole_number": existing_uom.must_be_whole_number
                }
            return response
        except frappe.ValidationError as e:
            error_msg = str(e)
            if "already exists" in error_msg.lower():
                existing_uom = None
                try:
                    if frappe.db.exists("UOM", new_name):
                        existing_uom = frappe.get_doc("UOM", new_name)
                except:
                    pass
                
                frappe.local.response["http_status_code"] = 409
                response = {
                    "success": False,
                    "error": "Duplicate UOM",
                    "message": _("A unit of measure with the name '{0}' already exists. Please choose a different name.").format(new_name)
                }
                if existing_uom:
                    response["existing_uom"] = {
                        "name": existing_uom.name,
                        "uom_name": existing_uom.uom_name,
                        "must_be_whole_number": existing_uom.must_be_whole_number
                    }
                return response
            else:
                frappe.local.response["http_status_code"] = 400
                return {
                    "success": False,
                    "error": "Rename failed",
                    "message": _("Unable to rename unit of measure: {0}. Please check that all information is correct and try again.").format(error_msg)
                }
        except Exception as e:
            frappe.log_error(
                "Rename UOM Error",
                f"Error renaming UOM '{current_identifier}' to '{new_name}': {frappe.get_traceback()}"
            )
            error_msg = str(e).lower()
            if "duplicate" in error_msg or "already exists" in error_msg:
                existing_uom = None
                try:
                    if frappe.db.exists("UOM", new_name):
                        existing_uom = frappe.get_doc("UOM", new_name)
                except:
                    pass
                
                frappe.local.response["http_status_code"] = 409
                response = {
                    "success": False,
                    "error": "Duplicate UOM",
                    "message": _("A unit of measure with the name '{0}' already exists. Please choose a different name.").format(new_name)
                }
                if existing_uom:
                    response["existing_uom"] = {
                        "name": existing_uom.name,
                        "uom_name": existing_uom.uom_name,
                        "must_be_whole_number": existing_uom.must_be_whole_number
                    }
                return response
            else:
                frappe.local.response["http_status_code"] = 500
                return {
                    "success": False,
                    "error": "Rename failed",
                    "message": _("An error occurred while renaming the unit of measure. Please check that all information is correct and try again. If the problem persists, contact support.")
                }
    
    # Update other fields
    if must_be_whole_number is not None:
        uom_doc.must_be_whole_number = 1 if must_be_whole_number else 0
    
    # Save changes
    uom_doc.save(ignore_permissions=True)
    frappe.db.commit()
    
    # Reload to get latest values
    uom_doc.reload()
    
    frappe.local.response["http_status_code"] = 200
    message = _("Unit of measure '{0}' has been successfully updated").format(uom_doc.uom_name)
    if new_name and new_name != current_identifier and new_name != uom_doc.uom_name:
        message = _("Unit of measure '{0}' has been successfully renamed to '{1}' and updated").format(current_identifier, new_name)
    
    return {
        "success": True,
        "uom": {
            "name": uom_doc.name,
            "uom_name": uom_doc.uom_name,
            "must_be_whole_number": uom_doc.must_be_whole_number
        },
        "message": message
    }


@frappe.whitelist()
def delete_uom(uom_name: str) -> Dict:
    """Delete a unit of measure
    
    Args:
        uom_name: Name of the UOM to delete
        
    Returns:
        Success message
    """
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    if not uom_name or not uom_name.strip():
        frappe.local.response["http_status_code"] = 400
        return {
            "success": False,
            "error": "UOM name is required",
            "message": _("UOM name is required. Please provide the name of the unit of measure you want to delete.")
        }
    
    uom_name = uom_name.strip()
    
    # Try to find UOM by document name first
    uom_doc_name = None
    if frappe.db.exists("UOM", uom_name):
        uom_doc_name = uom_name
    else:
        # Try to find by uom_name field
        uom_match = frappe.db.get_value(
            "UOM",
            {"uom_name": uom_name},
            "name"
        )
        if uom_match:
            uom_doc_name = uom_match
        else:
            frappe.local.response["http_status_code"] = 404
            return {
                "success": False,
                "error": "UOM not found",
                "message": _("Unit of measure '{0}' does not exist. Please check the UOM name and try again.").format(uom_name)
            }
    
    # Check if UOM is used in any items
    items_using_uom = frappe.get_all(
        "Item",
        filters={"stock_uom": uom_doc_name},
        fields=["name", "item_code", "item_name"],
        limit=10
    )
    
    if items_using_uom:
        item_count = frappe.db.count("Item", {"stock_uom": uom_doc_name})
        item_codes = [item.get("item_code") or item.get("name") for item in items_using_uom]
        
        frappe.local.response["http_status_code"] = 400
        return {
            "success": False,
            "error": "UOM in use",
            "message": _("Cannot delete unit of measure '{0}' because it is currently assigned to {1} item(s). Please remove the UOM from all items before deleting it.").format(uom_name, item_count),
            "items_using_uom": item_codes,
            "item_count": item_count
        }
    
    try:
        frappe.delete_doc("UOM", uom_doc_name, ignore_permissions=True)
        frappe.db.commit()
        
        frappe.local.response["http_status_code"] = 200
        return {
            "success": True,
            "message": _("Unit of measure '{0}' has been successfully deleted").format(uom_name)
        }
    except frappe.LinkExistsError as e:
        frappe.local.response["http_status_code"] = 400
        return {
            "success": False,
            "error": "UOM in use",
            "message": _("Cannot delete unit of measure '{0}' because it is currently in use. Please remove all references to this UOM before deleting it.").format(uom_name)
        }
    except Exception as e:
        frappe.log_error(
            "Delete UOM Error",
            f"Error deleting UOM '{uom_name}': {frappe.get_traceback()}"
        )
        frappe.local.response["http_status_code"] = 500
        return {
            "success": False,
            "error": "Delete failed",
            "message": _("An error occurred while deleting the unit of measure. Please try again or contact support if the problem persists.")
        }


@frappe.whitelist()
def create_item_group(
    item_group_name: str,
    parent_item_group: str = "All Item Groups",
    is_group: bool = False
) -> Dict:
    """Create a new item group/category
    
    Args:
        item_group_name: Name of the item group
        parent_item_group: Parent item group (default: "All Item Groups")
        is_group: Is a group (default: False)
        
    Returns:
        Created item group details
    """
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    if frappe.db.exists("Item Group", item_group_name):
        frappe.throw(_("Item Group {0} already exists").format(item_group_name))
    
    if not frappe.db.exists("Item Group", parent_item_group):
        frappe.throw(_("Parent Item Group {0} does not exist").format(parent_item_group))
    
    item_group = frappe.new_doc("Item Group")
    item_group.item_group_name = item_group_name
    item_group.parent_item_group = parent_item_group
    item_group.is_group = 1 if is_group else 0
    item_group.insert(ignore_permissions=True)
    frappe.db.commit()
    
    frappe.local.response["http_status_code"] = 201
    return {
        "item_group": {
            "name": item_group.name,
            "item_group_name": item_group.item_group_name,
            "parent_item_group": item_group.parent_item_group,
            "is_group": item_group.is_group
        },
        "message": _("Item group created successfully")
    }


@frappe.whitelist()
def update_item_group(
    name: str = None,
    item_group_name: str = None,
    new_item_group_name: str = None,
    parent_item_group: str = None,
    is_group: bool = None,
    company: str = None
) -> Dict:
    """Update/rename an item group/category
    
    Args:
        name: Current item group name (if using name parameter)
        item_group_name: Current item group name OR new item group name (for backward compatibility)
                        If both name and item_group_name are provided, name takes precedence as current name
                        If only item_group_name is provided, it's treated as current name
        new_item_group_name: New item group name (optional, for backward compatibility)
        parent_item_group: New parent item group
        is_group: New is_group status
        company: Company (optional, for validation purposes)
        
    Returns:
        Updated item group details
    """
    if frappe.session.user == "Guest":
        frappe.throw(_("Please log in to update an item group. Your session has expired or you are not authenticated."), frappe.AuthenticationError)
    
    # Determine current and new names from parameters
    # Support both naming conventions:
    # 1. name (current) + item_group_name (new) - matching user's payload
    # 2. item_group_name (current) + new_item_group_name (new) - backward compatibility
    current_name = None
    new_name = None
    
    if name:
        # User is using: name (current) + item_group_name (new)
        current_name = name.strip()
        if item_group_name and item_group_name.strip():
            new_name = item_group_name.strip()
    elif item_group_name:
        # Backward compatibility: item_group_name (current)
        current_name = item_group_name.strip()
        if new_item_group_name and new_item_group_name.strip():
            new_name = new_item_group_name.strip()
    
    if not current_name:
        frappe.throw(_("Item group name is required. Please provide the current name of the item group you want to update."), frappe.ValidationError)
    
    # Validate current item group exists
    if not frappe.db.exists("Item Group", current_name):
        frappe.throw(_("Item group '{0}' does not exist. Please check the item group name and try again.").format(current_name), frappe.ValidationError)
    
    # Get the item group document
    item_group = frappe.get_doc("Item Group", current_name)
    
    # Handle renaming if new name is provided
    if new_name and new_name != current_name:
        # Validate new name doesn't already exist
        if frappe.db.exists("Item Group", new_name):
            frappe.throw(_("An item group with the name '{0}' already exists. Please choose a different name.").format(new_name), frappe.ValidationError)
        
        # Use rename_document to properly rename the item group
        # Item Group has autoname="field:item_group_name", similar to Brand
        try:
            renamed_name = rename_document(
                doctype="Item Group",
                old=current_name,
                new=new_name,
                force=False,
                merge=False,
                ignore_permissions=True,
                show_alert=False
            )
            
            # Reload the item group with new name
            item_group = frappe.get_doc("Item Group", renamed_name)
            
            # Update the item_group_name field to match the new name (in case it doesn't auto-update)
            if item_group.item_group_name != new_name:
                item_group.item_group_name = new_name
                item_group.save(ignore_permissions=True)
        except frappe.DuplicateEntryError:
            frappe.throw(_("An item group with the name '{0}' already exists. Please choose a different name.").format(new_name), frappe.ValidationError)
        except frappe.ValidationError as e:
            error_msg = str(e)
            if "already exists" in error_msg.lower():
                frappe.throw(_("An item group with the name '{0}' already exists. Please choose a different name.").format(new_name), frappe.ValidationError)
            else:
                frappe.throw(_("Unable to rename item group: {0}. Please check that all information is correct and try again.").format(error_msg), frappe.ValidationError)
        except Exception as e:
            frappe.log_error(
                "Rename Item Group Error",
                f"Error renaming item group '{current_name}' to '{new_name}': {frappe.get_traceback()}"
            )
            frappe.throw(_("An error occurred while renaming the item group. Please check that all information is correct and try again. If the problem persists, contact support."), frappe.ValidationError)
    
    # Update parent item group if provided
    if parent_item_group:
        parent_item_group = parent_item_group.strip()
        if not frappe.db.exists("Item Group", parent_item_group):
            frappe.throw(_("Parent item group '{0}' does not exist. Please check the parent item group name and try again.").format(parent_item_group), frappe.ValidationError)
        item_group.parent_item_group = parent_item_group
    
    # Update is_group status if provided
    if is_group is not None:
        item_group.is_group = 1 if is_group else 0
    
    # Save changes (if any fields were updated)
    item_group.save(ignore_permissions=True)
    frappe.db.commit()
    
    # Reload to get latest values
    item_group.reload()
    
    frappe.local.response["http_status_code"] = 200
    return {
        "item_group": {
            "name": item_group.name,
            "item_group_name": item_group.item_group_name,
            "parent_item_group": item_group.parent_item_group,
            "is_group": bool(item_group.is_group)
        },
        "message": _("Item group '{0}' has been successfully updated").format(item_group.item_group_name) if not new_name else _("Item group '{0}' has been successfully renamed to '{1}'").format(current_name, new_name)
    }


@frappe.whitelist()
def delete_item_group(name: str = None, item_group_name: str = None) -> Dict:
    """Delete an item group/category
    
    Args:
        name: Name of the item group to delete (preferred parameter, matches update_item_group)
        item_group_name: Name of the item group to delete (for backward compatibility)
                        If both are provided, 'name' takes precedence
        
    Returns:
        Success message or error details
    """
    if frappe.session.user == "Guest":
        frappe.local.response["http_status_code"] = 401
        return {
            "success": False,
            "error": _("Authentication required"),
            "message": _("Please log in to delete an item group. Your session has expired or you are not authenticated."),
            "error_type": "authentication_error"
        }
    
    # Determine the item group name to delete
    # Support both 'name' and 'item_group_name' parameters for consistency
    group_name_to_delete = None
    if name and name.strip():
        group_name_to_delete = name.strip()
    elif item_group_name and item_group_name.strip():
        group_name_to_delete = item_group_name.strip()
    
    if not group_name_to_delete:
        frappe.local.response["http_status_code"] = 400
        return {
            "success": False,
            "error": _("Item group name required"),
            "message": _("Item group name is required. Please provide the name of the item group you want to delete."),
            "error_type": "validation_error"
        }
    
    # Validate item group exists
    if not frappe.db.exists("Item Group", group_name_to_delete):
        frappe.local.response["http_status_code"] = 404
        return {
            "success": False,
            "error": _("Item group not found"),
            "message": _("Item group '{0}' does not exist. Please check the item group name and try again.").format(group_name_to_delete),
            "error_type": "not_found"
        }
    
    # Check if item group is used by any items - get item details for user-friendly response
    try:
        items_using_group = frappe.get_all(
            "Item",
            filters={"item_group": group_name_to_delete},
            fields=["name", "item_code", "item_name"],
            limit=10  # Limit to first 10 for display
        )
        items_count = frappe.db.count("Item", {"item_group": group_name_to_delete})
        
        if items_count > 0:
            # Build list of item names for the response
            item_names = [item.get("item_code") or item.get("name") for item in items_using_group[:5]]  # Show first 5
            item_list = ", ".join(item_names)
            if items_count > 5:
                item_list += f", and {items_count - 5} more"
            
            # Return a clean error response instead of throwing
            frappe.local.response["http_status_code"] = 400
            if items_count == 1:
                return {
                    "success": False,
                    "error": _("Cannot delete item group - item group is in use"),
                    "message": _("Cannot delete item group '{0}' because it is currently assigned to 1 item ('{1}'). Please remove the item group from this item before deleting it.").format(group_name_to_delete, item_list),
                    "error_type": "validation_error",
                    "items_using_group": [
                        {
                            "item_code": item.get("item_code"),
                            "item_name": item.get("item_name"),
                            "name": item.get("name")
                        }
                        for item in items_using_group
                    ],
                    "items_count": items_count
                }
            else:
                return {
                    "success": False,
                    "error": _("Cannot delete item group - item group is in use"),
                    "message": _("Cannot delete item group '{0}' because it is currently assigned to {1} items (e.g., {2}). Please remove the item group from all items before deleting it.").format(group_name_to_delete, items_count, item_list),
                    "error_type": "validation_error",
                    "items_using_group": [
                        {
                            "item_code": item.get("item_code"),
                            "item_name": item.get("item_name"),
                            "name": item.get("name")
                        }
                        for item in items_using_group
                    ],
                    "items_count": items_count
                }
    except Exception as e:
        frappe.log_error("Delete Item Group - Check Items Error", f"Error checking items using item group '{group_name_to_delete}': {str(e)}")
        # Continue with deletion attempt if check fails (shouldn't happen, but be safe)
    
    # Check if item group has child item groups (since Item Group is a tree structure)
    try:
        child_groups = frappe.get_all(
            "Item Group",
            filters={"parent_item_group": group_name_to_delete},
            fields=["name", "item_group_name"],
            limit=10
        )
        child_count = frappe.db.count("Item Group", {"parent_item_group": group_name_to_delete})
        
        if child_count > 0:
            # Build list of child group names
            child_names = [child.get("item_group_name") or child.get("name") for child in child_groups[:5]]
            child_list = ", ".join(child_names)
            if child_count > 5:
                child_list += f", and {child_count - 5} more"
            
            frappe.local.response["http_status_code"] = 400
            if child_count == 1:
                return {
                    "success": False,
                    "error": _("Cannot delete item group - has child groups"),
                    "message": _("Cannot delete item group '{0}' because it has 1 child item group ('{1}'). Please delete or move the child item group first.").format(group_name_to_delete, child_list),
                    "error_type": "validation_error",
                    "child_groups": [
                        {
                            "name": child.get("name"),
                            "item_group_name": child.get("item_group_name")
                        }
                        for child in child_groups
                    ],
                    "child_count": child_count
                }
            else:
                return {
                    "success": False,
                    "error": _("Cannot delete item group - has child groups"),
                    "message": _("Cannot delete item group '{0}' because it has {1} child item groups (e.g., {2}). Please delete or move all child item groups first.").format(group_name_to_delete, child_count, child_list),
                    "error_type": "validation_error",
                    "child_groups": [
                        {
                            "name": child.get("name"),
                            "item_group_name": child.get("item_group_name")
                        }
                        for child in child_groups
                    ],
                    "child_count": child_count
                }
    except Exception as e:
        frappe.log_error("Delete Item Group - Check Children Error", f"Error checking child item groups for '{group_name_to_delete}': {str(e)}")
        # Continue with deletion attempt
    
    # Delete the item group
    try:
        frappe.delete_doc("Item Group", group_name_to_delete, ignore_permissions=True)
        frappe.db.commit()
        
        frappe.local.response["http_status_code"] = 200
        return {
            "success": True,
            "message": _("Item group '{0}' has been successfully deleted").format(group_name_to_delete)
        }
    except frappe.DoesNotExistError:
        frappe.local.response["http_status_code"] = 404
        return {
            "success": False,
            "error": _("Item group not found"),
            "message": _("Item group '{0}' does not exist. Please check the item group name and try again.").format(group_name_to_delete),
            "error_type": "not_found"
        }
    except frappe.LinkExistsError as e:
        # This error occurs when item group is linked to other documents
        error_msg = str(e)
        items_count = frappe.db.count("Item", {"item_group": group_name_to_delete})
        
        # Try to get item details
        items_using_group = []
        try:
            items_using_group = frappe.get_all(
                "Item",
                filters={"item_group": group_name_to_delete},
                fields=["name", "item_code", "item_name"],
                limit=10
            )
        except Exception:
            pass
        
        frappe.local.response["http_status_code"] = 400
        if items_count > 0:
            item_names = [item.get("item_code") or item.get("name") for item in items_using_group[:5]]
            item_list = ", ".join(item_names) if item_names else "items"
            
            if items_count == 1:
                return {
                    "success": False,
                    "error": _("Cannot delete item group - item group is in use"),
                    "message": _("Cannot delete item group '{0}' because it is currently assigned to 1 item ('{1}'). Please remove the item group from this item before deleting it.").format(group_name_to_delete, item_list),
                    "error_type": "validation_error",
                    "items_using_group": [
                        {
                            "item_code": item.get("item_code"),
                            "item_name": item.get("item_name"),
                            "name": item.get("name")
                        }
                        for item in items_using_group
                    ] if items_using_group else None,
                    "items_count": items_count
                }
            else:
                return {
                    "success": False,
                    "error": _("Cannot delete item group - item group is in use"),
                    "message": _("Cannot delete item group '{0}' because it is currently assigned to {1} items (e.g., {2}). Please remove the item group from all items before deleting it.").format(group_name_to_delete, items_count, item_list),
                    "error_type": "validation_error",
                    "items_using_group": [
                        {
                            "item_code": item.get("item_code"),
                            "item_name": item.get("item_name"),
                            "name": item.get("name")
                        }
                        for item in items_using_group
                    ] if items_using_group else None,
                    "items_count": items_count
                }
        else:
            return {
                "success": False,
                "error": _("Cannot delete item group - item group is linked"),
                "message": _("Cannot delete item group '{0}' because it is linked to other records in the system. Please remove all links to this item group before deleting it.").format(group_name_to_delete),
                "error_type": "validation_error"
            }
    except frappe.ValidationError as e:
        # Handle validation errors that might occur during deletion
        error_msg = str(e)
        frappe.local.response["http_status_code"] = 400
        
        if "linked" in error_msg.lower() or "used" in error_msg.lower() or "item" in error_msg.lower():
            items_count = frappe.db.count("Item", {"item_group": group_name_to_delete})
            return {
                "success": False,
                "error": _("Cannot delete item group - item group is in use"),
                "message": _("Cannot delete item group '{0}' because it is currently in use. Please remove the item group from all items and other records before deleting it.").format(group_name_to_delete),
                "error_type": "validation_error",
                "items_count": items_count if items_count > 0 else None
            }
        else:
            return {
                "success": False,
                "error": _("Validation error"),
                "message": _("Unable to delete item group: {0}. Please check that the item group exists and is not in use, then try again.").format(error_msg),
                "error_type": "validation_error"
            }
    except frappe.PermissionError:
        frappe.local.response["http_status_code"] = 403
        return {
            "success": False,
            "error": _("Permission denied"),
            "message": _("You do not have permission to delete item groups. Please contact your administrator to grant you the necessary permissions."),
            "error_type": "permission_error"
        }
    except Exception as e:
        # Log the full error for debugging
        frappe.log_error(
            "Delete Item Group Error",
            f"Error deleting item group '{group_name_to_delete}': {frappe.get_traceback()}"
        )
        
        # Return user-friendly error message
        error_msg = str(e).lower()
        frappe.local.response["http_status_code"] = 500
        
        if "linked" in error_msg or "used" in error_msg or "item" in error_msg:
            return {
                "success": False,
                "error": _("Cannot delete item group - item group is in use"),
                "message": _("Cannot delete item group '{0}' because it is currently in use. Please remove the item group from all items and other records before deleting it.").format(group_name_to_delete),
                "error_type": "validation_error"
            }
        elif "child" in error_msg or "parent" in error_msg:
            return {
                "success": False,
                "error": _("Cannot delete item group - has child groups"),
                "message": _("Cannot delete item group '{0}' because it has child item groups. Please delete or move all child item groups first.").format(group_name_to_delete),
                "error_type": "validation_error"
            }
        elif "permission" in error_msg:
            frappe.local.response["http_status_code"] = 403
            return {
                "success": False,
                "error": _("Permission denied"),
                "message": _("You do not have permission to delete item groups. Please contact your administrator to grant you the necessary permissions."),
                "error_type": "permission_error"
            }
        elif "does not exist" in error_msg:
            frappe.local.response["http_status_code"] = 404
            return {
                "success": False,
                "error": _("Item group not found"),
                "message": _("Item group '{0}' does not exist. Please check the item group name and try again.").format(group_name_to_delete),
                "error_type": "not_found"
            }
        else:
            return {
                "success": False,
                "error": _("Error deleting item group"),
                "message": _("An error occurred while deleting the item group. Please check that the item group exists and is not in use, then try again. If the problem persists, contact support."),
                "error_type": "general_error"
            }


@frappe.whitelist()
def create_brand(brand_name: str) -> Dict:
    """Create a new brand
    
    Args:
        brand_name: Name of the brand
        
    Returns:
        Created brand details
    """
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    if frappe.db.exists("Brand", brand_name):
        frappe.throw(_("Brand {0} already exists").format(brand_name))
    
    brand = frappe.new_doc("Brand")
    brand.brand = brand_name
    brand.insert(ignore_permissions=True)
    frappe.db.commit()
    
    frappe.local.response["http_status_code"] = 201
    return {
        "brand": {
            "name": brand.name,
            "brand": brand.brand
        },
        "message": _("Brand created successfully")
    }


@frappe.whitelist()
def update_brand(brand_name: str, new_brand_name: str, company: str = None) -> Dict:
    """Update/rename a brand
    
    Args:
        brand_name: Current brand name
        new_brand_name: New brand name
        company: Company (optional, for validation purposes)
        
    Returns:
        Updated brand details
    """
    if frappe.session.user == "Guest":
        frappe.throw(_("Please log in to update a brand. Your session has expired or you are not authenticated."), frappe.AuthenticationError)
    
    if not brand_name or not brand_name.strip():
        frappe.throw(_("Brand name is required. Please provide the current name of the brand you want to update."), frappe.ValidationError)
    
    if not new_brand_name or not new_brand_name.strip():
        frappe.throw(_("New brand name is required. Please provide the new name for the brand."), frappe.ValidationError)
    
    brand_name = brand_name.strip()
    new_brand_name = new_brand_name.strip()
    
    if brand_name == new_brand_name:
        # No change needed - return current brand details
        try:
            brand = frappe.get_doc("Brand", brand_name)
            frappe.local.response["http_status_code"] = 200
            return {
                "brand": {
                    "name": brand.name,
                    "brand": brand.brand
                },
                "message": _("Brand name unchanged - the new name is the same as the current name")
            }
        except frappe.DoesNotExistError:
            frappe.throw(_("Brand '{0}' does not exist. Please check the brand name and try again.").format(brand_name), frappe.ValidationError)
    
    # Validate brand exists
    if not frappe.db.exists("Brand", brand_name):
        frappe.throw(_("Brand '{0}' does not exist. Please check the brand name and try again.").format(brand_name), frappe.ValidationError)
    
    # Validate new brand name doesn't already exist
    if frappe.db.exists("Brand", new_brand_name):
        frappe.throw(_("A brand with the name '{0}' already exists. Please choose a different name for the brand.").format(new_brand_name), frappe.ValidationError)
    
    # Validate company if provided (check if brand belongs to company)
    # Note: Brand doctype doesn't have a company field by default, so this might not apply
    # But we'll keep it for custom implementations
    if company:
        try:
            brand_company = frappe.db.get_value("Brand", brand_name, "company")
            if brand_company and brand_company != company:
                frappe.throw(_("Brand '{0}' does not belong to company '{1}'. Please select a brand from the correct company.").format(brand_name, company), frappe.ValidationError)
        except Exception:
            # Company field might not exist on Brand doctype - ignore this check
            pass
    
    # Use rename_document to properly rename the brand
    # This updates all references and handles the renaming correctly
    try:
        # Get the brand document first
        brand = frappe.get_doc("Brand", brand_name)
        
        # Rename the document from old name to new name
        # This updates the name field and all references to the brand
        new_name = rename_document(
            doctype="Brand",
            old=brand_name,
            new=new_brand_name,
            force=False,
            merge=False,
            ignore_permissions=True,
            show_alert=False
        )
        
        # After renaming, get the renamed brand document
        # Since Brand has autoname="field:brand", the brand field should match the name
        updated_brand = frappe.get_doc("Brand", new_name)
        
        # Update the brand field to match the new name (in case it doesn't auto-update)
        if updated_brand.brand != new_brand_name:
            updated_brand.brand = new_brand_name
            updated_brand.save(ignore_permissions=True)
        
        frappe.db.commit()
        
        frappe.local.response["http_status_code"] = 200
        return {
            "brand": {
                "name": updated_brand.name,
                "brand": updated_brand.brand
            },
            "message": _("Brand '{0}' has been successfully renamed to '{1}'").format(brand_name, new_brand_name)
        }
    except frappe.DoesNotExistError:
        frappe.throw(_("Brand '{0}' does not exist. Please check the brand name and try again.").format(brand_name), frappe.ValidationError)
    except frappe.DuplicateEntryError:
        frappe.throw(_("A brand with the name '{0}' already exists. Please choose a different name for the brand.").format(new_brand_name), frappe.ValidationError)
    except frappe.ValidationError as e:
        # Re-raise validation errors with user-friendly message
        error_msg = str(e)
        if "already exists" in error_msg.lower():
            frappe.throw(_("A brand with the name '{0}' already exists. Please choose a different name for the brand.").format(new_brand_name), frappe.ValidationError)
        elif "does not exist" in error_msg.lower():
            frappe.throw(_("Brand '{0}' does not exist. Please check the brand name and try again.").format(brand_name), frappe.ValidationError)
        else:
            frappe.throw(_("Unable to update brand: {0}. Please check that all information is correct and try again.").format(error_msg), frappe.ValidationError)
    except frappe.PermissionError:
        frappe.throw(_("You do not have permission to rename brands. Please contact your administrator to grant you the necessary permissions."), frappe.PermissionError)
    except Exception as e:
        # Log the full error for debugging
        frappe.log_error(
            "Rename Brand Error",
            f"Error renaming brand '{brand_name}' to '{new_brand_name}': {frappe.get_traceback()}"
        )
        
        # Return user-friendly error message
        error_msg = str(e).lower()
        if "duplicate" in error_msg or "already exists" in error_msg:
            frappe.throw(_("A brand with the name '{0}' already exists. Please choose a different name for the brand.").format(new_brand_name), frappe.ValidationError)
        elif "does not exist" in error_msg:
            frappe.throw(_("Brand '{0}' does not exist. Please check the brand name and try again.").format(brand_name), frappe.ValidationError)
        elif "permission" in error_msg:
            frappe.throw(_("You do not have permission to rename brands. Please contact your administrator to grant you the necessary permissions."), frappe.PermissionError)
        else:
            frappe.throw(_("An error occurred while updating the brand. Please check that all information is correct (brand name, new brand name) and try again. If the problem persists, contact support."), frappe.ValidationError)


@frappe.whitelist()
def delete_brand(brand_name: str) -> Dict:
    """Delete a brand
    
    Args:
        brand_name: Name of the brand to delete
        
    Returns:
        Success message
    """
    if frappe.session.user == "Guest":
        frappe.local.response["http_status_code"] = 401
        return {
            "success": False,
            "error": _("Authentication required"),
            "message": _("Please log in to delete a brand. Your session has expired or you are not authenticated."),
            "error_type": "authentication_error"
        }
    
    if not brand_name or not brand_name.strip():
        frappe.local.response["http_status_code"] = 400
        return {
            "success": False,
            "error": _("Brand name required"),
            "message": _("Brand name is required. Please provide the name of the brand you want to delete."),
            "error_type": "validation_error"
        }
    
    brand_name = brand_name.strip()
    
    # Validate brand exists
    if not frappe.db.exists("Brand", brand_name):
        frappe.local.response["http_status_code"] = 404
        return {
            "success": False,
            "error": _("Brand not found"),
            "message": _("Brand '{0}' does not exist. Please check the brand name and try again.").format(brand_name),
            "error_type": "not_found"
        }
    
    # Check if brand is used by any items - get item details for user-friendly response
    try:
        items_using_brand = frappe.get_all(
            "Item",
            filters={"brand": brand_name},
            fields=["name", "item_code", "item_name"],
            limit=10  # Limit to first 10 for display
        )
        items_count = frappe.db.count("Item", {"brand": brand_name})
        
        if items_count > 0:
            # Build list of item names for the response
            item_names = [item.get("item_code") or item.get("name") for item in items_using_brand[:5]]  # Show first 5
            item_list = ", ".join(item_names)
            if items_count > 5:
                item_list += f", and {items_count - 5} more"
            
            # Return a clean error response instead of throwing
            frappe.local.response["http_status_code"] = 400
            if items_count == 1:
                return {
                    "success": False,
                    "error": _("Cannot delete brand '{0}' because it is currently assigned to 1 item").format(brand_name),
                    "message": _("Cannot delete brand '{0}' because it is currently assigned to item '{1}'. Please remove the brand from this item before deleting it.").format(brand_name, item_list),
                    "error_type": "validation_error",
                    "items_using_brand": [
                        {
                            "item_code": item.get("item_code"),
                            "item_name": item.get("item_name"),
                            "name": item.get("name")
                        }
                        for item in items_using_brand
                    ],
                    "items_count": items_count
                }
            else:
                return {
                    "success": False,
                    "error": _("Cannot delete brand '{0}' because it is currently assigned to {1} items").format(brand_name, items_count),
                    "message": _("Cannot delete brand '{0}' because it is currently assigned to {1} items (e.g., {2}). Please remove the brand from all items before deleting it.").format(brand_name, items_count, item_list),
                    "error_type": "validation_error",
                    "items_using_brand": [
                        {
                            "item_code": item.get("item_code"),
                            "item_name": item.get("item_name"),
                            "name": item.get("name")
                        }
                        for item in items_using_brand
                    ],
                    "items_count": items_count
                }
    except Exception as e:
        frappe.log_error("Delete Brand - Check Items Error", f"Error checking items using brand '{brand_name}': {str(e)}")
        # Continue with deletion attempt if check fails (shouldn't happen, but be safe)
    
    # Delete the brand
    try:
        frappe.delete_doc("Brand", brand_name, ignore_permissions=True)
        frappe.db.commit()
        
        frappe.local.response["http_status_code"] = 200
        return {
            "success": True,
            "message": _("Brand '{0}' has been successfully deleted").format(brand_name)
        }
    except frappe.DoesNotExistError:
        frappe.local.response["http_status_code"] = 404
        return {
            "success": False,
            "error": _("Brand not found"),
            "message": _("Brand '{0}' does not exist. Please check the brand name and try again.").format(brand_name),
            "error_type": "not_found"
        }
    except frappe.LinkExistsError as e:
        # This error occurs when brand is linked to other documents
        # Extract item information from error message if possible
        error_msg = str(e)
        items_count = frappe.db.count("Item", {"brand": brand_name})
        
        # Try to get item details
        items_using_brand = []
        try:
            items_using_brand = frappe.get_all(
                "Item",
                filters={"brand": brand_name},
                fields=["name", "item_code", "item_name"],
                limit=10
            )
        except Exception:
            pass
        
        frappe.local.response["http_status_code"] = 400
        if items_count > 0:
            item_names = [item.get("item_code") or item.get("name") for item in items_using_brand[:5]]
            item_list = ", ".join(item_names) if item_names else "items"
            
            if items_count == 1:
                return {
                    "success": False,
                    "error": _("Cannot delete brand - brand is in use"),
                    "message": _("Cannot delete brand '{0}' because it is currently assigned to 1 item ('{1}'). Please remove the brand from this item before deleting it.").format(brand_name, item_list),
                    "error_type": "validation_error",
                    "items_using_brand": [
                        {
                            "item_code": item.get("item_code"),
                            "item_name": item.get("item_name"),
                            "name": item.get("name")
                        }
                        for item in items_using_brand
                    ] if items_using_brand else None,
                    "items_count": items_count
                }
            else:
                return {
                    "success": False,
                    "error": _("Cannot delete brand - brand is in use"),
                    "message": _("Cannot delete brand '{0}' because it is currently assigned to {1} items (e.g., {2}). Please remove the brand from all items before deleting it.").format(brand_name, items_count, item_list),
                    "error_type": "validation_error",
                    "items_using_brand": [
                        {
                            "item_code": item.get("item_code"),
                            "item_name": item.get("item_name"),
                            "name": item.get("name")
                        }
                        for item in items_using_brand
                    ] if items_using_brand else None,
                    "items_count": items_count
                }
        else:
            return {
                "success": False,
                "error": _("Cannot delete brand - brand is linked"),
                "message": _("Cannot delete brand '{0}' because it is linked to other records in the system. Please remove all links to this brand before deleting it.").format(brand_name),
                "error_type": "validation_error"
            }
    except frappe.ValidationError as e:
        # Handle validation errors that might occur during deletion
        error_msg = str(e)
        frappe.local.response["http_status_code"] = 400
        
        if "linked" in error_msg.lower() or "used" in error_msg.lower() or "item" in error_msg.lower():
            items_count = frappe.db.count("Item", {"brand": brand_name})
            return {
                "success": False,
                "error": _("Cannot delete brand - brand is in use"),
                "message": _("Cannot delete brand '{0}' because it is currently in use. Please remove the brand from all items and other records before deleting it.").format(brand_name),
                "error_type": "validation_error",
                "items_count": items_count if items_count > 0 else None
            }
        else:
            return {
                "success": False,
                "error": _("Validation error"),
                "message": _("Unable to delete brand: {0}. Please check that the brand exists and is not in use, then try again.").format(error_msg),
                "error_type": "validation_error"
            }
    except frappe.PermissionError:
        frappe.local.response["http_status_code"] = 403
        return {
            "success": False,
            "error": _("Permission denied"),
            "message": _("You do not have permission to delete brands. Please contact your administrator to grant you the necessary permissions."),
            "error_type": "permission_error"
        }
    except Exception as e:
        # Log the full error for debugging
        frappe.log_error(
            "Delete Brand Error",
            f"Error deleting brand '{brand_name}': {frappe.get_traceback()}"
        )
        
        # Return user-friendly error message
        error_msg = str(e).lower()
        frappe.local.response["http_status_code"] = 500
        
        if "linked" in error_msg or "used" in error_msg or "item" in error_msg:
            return {
                "success": False,
                "error": _("Cannot delete brand - brand is in use"),
                "message": _("Cannot delete brand '{0}' because it is currently in use. Please remove the brand from all items and other records before deleting it.").format(brand_name),
                "error_type": "validation_error"
            }
        elif "permission" in error_msg:
            frappe.local.response["http_status_code"] = 403
            return {
                "success": False,
                "error": _("Permission denied"),
                "message": _("You do not have permission to delete brands. Please contact your administrator to grant you the necessary permissions."),
                "error_type": "permission_error"
            }
        elif "does not exist" in error_msg:
            frappe.local.response["http_status_code"] = 404
            return {
                "success": False,
                "error": _("Brand not found"),
                "message": _("Brand '{0}' does not exist. Please check the brand name and try again.").format(brand_name),
                "error_type": "not_found"
            }
        else:
            return {
                "success": False,
                "error": _("Error deleting brand"),
                "message": _("An error occurred while deleting the brand. Please check that the brand exists and is not in use, then try again. If the problem persists, contact support."),
                "error_type": "general_error"
            }


@frappe.whitelist()
def seed_global_products(company: str = None, products_data: list = None) -> Dict:
    """Seed global products (available to all industries) for a company
    
    Args:
        company: Company name. If not provided, seeds for all companies
        products_data: Optional list of product dictionaries. If not provided, uses default products
        
    Returns:
        Summary of seeding operation
    """
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    # Only System Managers can seed products
    if "System Manager" not in frappe.get_roles():
        frappe.throw(_("Only System Managers can seed products"), frappe.PermissionError)
    
    # Try to use global products seeding if available
    # This feature is optional and can work without savanna_pos
    try:
        try:
            # Try savanna_pos first (for backward compatibility)
            from savanna_pos.savanna_pos.setup.seed_global_products import (
                seed_global_products as _seed_global_products
            )
        except ImportError:
            # If savanna_pos not available, check for techsavanna_pos implementation
            try:
                from techsavanna_pos.setup.seed_global_products import (
                    seed_global_products as _seed_global_products
                )
            except ImportError:
                # If neither available, return helpful error
                frappe.throw(
                    _("Global product seeding module is not available. This is an optional feature that requires additional setup. Please use the standard product creation APIs instead."),
                    frappe.ValidationError
                )
        
        result = _seed_global_products(company=company, products_data=products_data)
        
        frappe.local.response["http_status_code"] = 200
        return {
            "success": True,
            "message": _("Seeded {0} products, skipped {1} products").format(
                result["created"], result["skipped"]
            ),
            "created": result["created"],
            "skipped": result["skipped"],
            "created_products": result["created_products"],
            "skipped_products": result["skipped_products"]
        }
    except frappe.ValidationError:
        raise
    except Exception as e:
        frappe.log_error("Seed Global Products", f"Error seeding global products: {str(e)}")
        frappe.throw(_("Error seeding products: {0}").format(str(e)), frappe.ValidationError)


@frappe.whitelist()
def set_product_warranty(item_code: str, warranty_period: int, warranty_period_unit: str = "Days") -> Dict:
    """Set warranty period for a product
    
    Args:
        item_code: Item code
        warranty_period: Warranty period (number)
        warranty_period_unit: Unit (Days, Months, Years) - default: Days
                             Note: Value is converted to days before storing (1 Month = 30 days, 1 Year = 365 days)
        
    Returns:
        Success message with warranty information
    """
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    if not frappe.db.exists("Item", item_code):
        frappe.throw(_("Item {0} does not exist").format(item_code), frappe.DoesNotExistError)
    
    valid_units = ["Days", "Months", "Years"]
    if warranty_period_unit not in valid_units:
        frappe.throw(_("Warranty period unit must be one of: {0}").format(", ".join(valid_units)))
    
    # Convert warranty period to days based on unit
    # ERPNext stores warranty_period in days
    warranty_period_in_days = warranty_period
    if warranty_period_unit == "Months":
        warranty_period_in_days = warranty_period * 30  # Approximate: 1 month = 30 days
    elif warranty_period_unit == "Years":
        warranty_period_in_days = warranty_period * 365  # 1 year = 365 days
    
    item = frappe.get_doc("Item", item_code)
    # Store as string (ERPNext's warranty_period field is Data type, not Int)
    item.warranty_period = str(warranty_period_in_days)
    
    # Store the original value and unit in custom fields for display purposes
    # This allows us to show the warranty period in the original unit format
    # Use update() method which works with custom fields even if they don't exist as attributes
    try:
        item.update({
            "custom_warranty_period_unit": warranty_period_unit,
            "custom_warranty_period_value": warranty_period
        })
    except Exception:
        # If custom fields don't exist, try setting via db.set_value (will work if fields exist but not loaded)
        try:
            frappe.db.set_value("Item", item_code, {
                "custom_warranty_period_unit": warranty_period_unit,
                "custom_warranty_period_value": warranty_period
            })
        except Exception as e:
            # Custom fields might not exist yet - log but continue (main warranty_period will still be saved)
            frappe.log_error(
                "Set Warranty Custom Fields Warning",
                f"Could not save custom warranty fields for Item '{item_code}'. Error: {str(e)}. Please ensure custom fields 'custom_warranty_period_unit' and 'custom_warranty_period_value' exist on Item doctype."
            )
    
    item.save(ignore_permissions=True)
    frappe.db.commit()
    
    frappe.local.response["http_status_code"] = 200
    return {
        "item_code": item_code,
        "warranty_period": warranty_period_in_days,
        "warranty_period_display": f"{warranty_period} {warranty_period_unit}",
        "warranty_period_unit": warranty_period_unit,  # Return original unit
        "warranty_period_value": warranty_period,  # Return original value
        "warranty_period_in_days": warranty_period_in_days,  # Also return in days for reference
        "message": _("Warranty period set successfully: {0} ({1} days)").format(f"{warranty_period} {warranty_period_unit}", warranty_period_in_days)
    }


@frappe.whitelist()
def get_product_warranty(item_code: str) -> Dict:
    """Get warranty information for a product
    
    Args:
        item_code: Item code
        
    Returns:
        Warranty information (warranty_period is always returned in days)
    """
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    if not frappe.db.exists("Item", item_code):
        frappe.throw(_("Item {0} does not exist").format(item_code), frappe.DoesNotExistError)
    
    warranty_period_str = frappe.db.get_value("Item", item_code, "warranty_period")
    
    # Convert to integer if possible (warranty_period is stored as Data/varchar in ERPNext)
    warranty_period = 0
    if warranty_period_str:
        try:
            warranty_period = cint(warranty_period_str)
        except (ValueError, TypeError):
            # If conversion fails, try to extract number from string
            try:
                import re
                numbers = re.findall(r'\d+', str(warranty_period_str))
                if numbers:
                    warranty_period = cint(numbers[0])
            except (ValueError, TypeError):
                warranty_period = 0
    
    frappe.local.response["http_status_code"] = 200
    return {
        "item_code": item_code,
        "warranty_period": warranty_period,
        "warranty_period_unit": "Days",  # ERPNext stores warranty_period in days
        "warranty_period_display": f"{warranty_period} Days" if warranty_period > 0 else "No warranty"
    }
