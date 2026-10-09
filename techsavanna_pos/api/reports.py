import frappe
from frappe import _
import json
from frappe.utils import flt, getdate, nowdate, add_days, date_diff, cint, get_datetime
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from frappe.query_builder import DocType, functions as fn

from erpnext.accounts.utils import get_balance_on

from erpnext.accounts.report.profit_and_loss_statement.profit_and_loss_statement import execute



@frappe.whitelist()
def inventory_summary_report(
    company: Optional[str] = None,
    warehouse: Optional[str] = None
):
    """
    Returns an inventory summary report.
    
    Args:
        company: Company name (optional, defaults to user's default company)
        warehouse: Warehouse filter (optional)
    
    Returns:
        dict: Inventory summary data by warehouse
    """
    try:
        # Get company if not provided
        if not company:
            company = frappe.defaults.get_user_default("Company")
        
        # Build WHERE conditions
        where_conditions = ["actual_qty > 0"]
        params = []
        
        if warehouse:
            where_conditions.append("warehouse = %s")
            params.append(warehouse)
        elif company:
            # Get company warehouses
            company_warehouses = frappe.get_all(
                "Warehouse",
                filters={"company": company},
                pluck="name"
            )
            if company_warehouses:
                placeholders = ",".join(["%s"] * len(company_warehouses))
                where_conditions.append(f"warehouse IN ({placeholders})")
                params.extend(company_warehouses)
        
        where_clause = " AND " + " AND ".join(where_conditions) if where_conditions else ""
        
        # Query to get total stock in each warehouse
        query = f"""
            SELECT
                warehouse,
                SUM(actual_qty) AS total_qty,
                SUM(valuation_rate * actual_qty) AS total_value
            FROM
                `tabBin`
            WHERE
                actual_qty > 0
                {where_clause}
            GROUP BY
                warehouse
        """
        
        data = frappe.db.sql(query, tuple(params), as_dict=True) if params else frappe.db.sql(query, as_dict=True)

        return {
            "success": True,
            "data": data
        }
    
    except Exception as e:
        frappe.log_error("Inventory Summary Report Error", f"Error in inventory_summary_report: {str(e)}")
        return {
            "success": False,
            "message": f"Error generating inventory summary report: {str(e)}"
        }



@frappe.whitelist()
def inventory_movement_report(
    start_date: str,
    end_date: str,
    company: Optional[str] = None,
    warehouse: Optional[str] = None
):
    """
    Returns a movement report based on the start and end date.
    
    Args:
        start_date: Start date (YYYY-MM-DD, required)
        end_date: End date (YYYY-MM-DD, required)
        company: Company name (optional, defaults to user's default company)
        warehouse: Warehouse filter (optional)
    
    Returns:
        dict: Inventory movement data
    """
    try:
        # Get company if not provided
        if not company:
            company = frappe.defaults.get_user_default("Company")
        
        # Build WHERE conditions
        where_conditions = ["se.posting_date BETWEEN %s AND %s", "se.docstatus = 1"]
        params = [start_date, end_date]
        
        if company:
            where_conditions.append("se.company = %s")
            params.append(company)
        
        if warehouse:
            where_conditions.append("sed.warehouse = %s")
            params.append(warehouse)
        elif company:
            # Get company warehouses
            company_warehouses = frappe.get_all(
                "Warehouse",
                filters={"company": company},
                pluck="name"
            )
            if company_warehouses:
                placeholders = ",".join(["%s"] * len(company_warehouses))
                where_conditions.append(f"sed.warehouse IN ({placeholders})")
                params.extend(company_warehouses)
        
        where_clause = " AND " + " AND ".join(where_conditions)
        
        query = f"""
            SELECT
                sed.item_code,
                SUM(CASE WHEN se.stock_entry_type = 'Material Receipt' THEN sed.qty ELSE 0 END) AS received_qty,
                SUM(CASE WHEN se.stock_entry_type = 'Material Issue' THEN sed.qty ELSE 0 END) AS issued_qty,
                SUM(CASE WHEN se.stock_entry_type = 'Material Transfer' THEN sed.qty ELSE 0 END) AS transferred_qty
            FROM
                `tabStock Entry` se
            JOIN
                `tabStock Entry Detail` sed ON se.name = sed.parent
            WHERE
                {where_clause}
            GROUP BY
                sed.item_code
        """
        
        data = frappe.db.sql(query, tuple(params), as_dict=True)

        return {
            "success": True,
            "data": data
        }
    
    except Exception as e:
        frappe.log_error("Inventory Movement Report Error", f"Error in inventory_movement_report: {str(e)}")
        return {
            "success": False,
            "message": f"Error generating inventory movement report: {str(e)}"
        }



@frappe.whitelist()
def stock_aging_report(
    slow_moving_threshold: Optional[float] = None,
    company: Optional[str] = None,
    warehouse: Optional[str] = None
):
    """
    Returns a stock aging report based on the stock's age.
    Enhanced version with movement rate and slow moving threshold.
    
    Args:
        slow_moving_threshold: Threshold for slow moving items (optional)
        company: Company name (optional, defaults to user's default company)
        warehouse: Warehouse filter (optional)
    
    Returns:
        dict: Stock aging data with movement rates
    """
    try:
        # Get company if not provided
        if not company:
            company = frappe.defaults.get_user_default("Company")
        
        # Build WHERE conditions
        where_conditions = ["b.actual_qty > 0"]
        params = []
        
        if warehouse:
            where_conditions.append("b.warehouse = %s")
            params.append(warehouse)
        elif company:
            # Get company warehouses
            company_warehouses = frappe.get_all(
                "Warehouse",
                filters={"company": company},
                pluck="name"
            )
            if company_warehouses:
                placeholders = ",".join(["%s"] * len(company_warehouses))
                where_conditions.append(f"b.warehouse IN ({placeholders})")
                params.extend(company_warehouses)
        
        where_clause = " AND " + " AND ".join(where_conditions) if where_conditions else ""
        
        # Get stock data with age
        query = f"""
            SELECT
                b.item_code,
                b.warehouse,
                b.actual_qty,
                DATEDIFF(CURDATE(), b.creation) AS age_days
            FROM
                `tabBin` b
            WHERE
                b.actual_qty > 0
                {where_clause}
        """
        
        data = frappe.db.sql(query, tuple(params), as_dict=True) if params else frappe.db.sql(query, as_dict=True)
        
        if not data:
            return {
                "success": True,
                "data": {
                    "0-30": [],
                    "31-60": [],
                    "61-90": [],
                    "90+": []
                }
            }
        
        # Get item codes
        item_codes = list(set(d["item_code"] for d in data))
        
        # Calculate movement rate (simplified - based on recent sales)
        # Get sales in last 30 days
        end_date = nowdate()
        start_date = add_days(end_date, -30)
        
        # Build sales filters
        sales_filters = {
            "item_code": ["in", item_codes],
            "posting_date": ["between", [start_date, end_date]],
            "is_cancelled": 0,
            "voucher_type": ["in", ["Sales Invoice", "POS Invoice", "Delivery Note"]]
        }
        
        if company:
            sales_filters["company"] = company
        if warehouse:
            sales_filters["warehouse"] = warehouse
        
        sales_sle = frappe.get_all(
            "Stock Ledger Entry",
            filters=sales_filters,
            fields=["item_code", "warehouse", "actual_qty"]
        )
        
        # Calculate movement rate per item-warehouse
        movement_data = {}
        for sle in sales_sle:
            key = f"{sle['item_code']}_{sle['warehouse']}"
            if key not in movement_data:
                movement_data[key] = 0.0
            movement_data[key] += abs(flt(sle.get("actual_qty") or 0))
        
        # Categorize the aging of stock
        categorized_data = {
            "0-30": [],
            "31-60": [],
            "61-90": [],
            "90+": []
        }
        
        for entry in data:
            age_days = entry["age_days"]
            key = f"{entry['item_code']}_{entry['warehouse']}"
            movement_rate = movement_data.get(key, 0.0)
            
            # Determine if slow moving
            is_slow_moving = False
            if slow_moving_threshold is not None:
                is_slow_moving = movement_rate < slow_moving_threshold
            
            entry_data = {
                "item_code": entry["item_code"],
                "warehouse": entry["warehouse"],
                "actual_qty": flt(entry["actual_qty"], 2),
                "age_days": age_days,
                "movement_rate": flt(movement_rate, 2),
                "is_slow_moving": is_slow_moving
            }
            
            if age_days <= 30:
                categorized_data["0-30"].append(entry_data)
            elif age_days <= 60:
                categorized_data["31-60"].append(entry_data)
            elif age_days <= 90:
                categorized_data["61-90"].append(entry_data)
            else:
                categorized_data["90+"].append(entry_data)
        
        return {
            "success": True,
            "data": categorized_data
        }
    
    except Exception as e:
        frappe.log_error("Stock Aging Report Error", f"Error in stock_aging_report: {str(e)}")
        return {
            "success": False,
            "message": f"Error generating stock aging report: {str(e)}"
        }

@frappe.whitelist()
def inventory_value_report(
    company: Optional[str] = None,
    warehouse: Optional[str] = None
):
    """
    Returns the total inventory value by warehouse.
    
    Args:
        company: Company name (optional, defaults to user's default company)
        warehouse: Warehouse filter (optional)
    
    Returns:
        dict: Inventory value data by warehouse
    """
    try:
        # Get company if not provided
        if not company:
            company = frappe.defaults.get_user_default("Company")
        
        # Build WHERE conditions
        where_conditions = ["actual_qty > 0"]
        params = []
        
        if warehouse:
            where_conditions.append("warehouse = %s")
            params.append(warehouse)
        elif company:
            # Get company warehouses
            company_warehouses = frappe.get_all(
                "Warehouse",
                filters={"company": company},
                pluck="name"
            )
            if company_warehouses:
                placeholders = ",".join(["%s"] * len(company_warehouses))
                where_conditions.append(f"warehouse IN ({placeholders})")
                params.extend(company_warehouses)
        
        where_clause = " AND " + " AND ".join(where_conditions) if where_conditions else ""
        
        query = f"""
            SELECT
                warehouse,
                SUM(actual_qty * valuation_rate) AS total_value
            FROM
                `tabBin`
            WHERE
                actual_qty > 0
                {where_clause}
            GROUP BY
                warehouse
        """
        
        data = frappe.db.sql(query, tuple(params), as_dict=True) if params else frappe.db.sql(query, as_dict=True)

        return {
            "success": True,
            "data": data
        }
    
    except Exception as e:
        frappe.log_error("Inventory Value Report Error", f"Error in inventory_value_report: {str(e)}")
        return {
            "success": False,
            "message": f"Error generating inventory value report: {str(e)}"
        }


# ==================== SALES ANALYTICS REPORTS ====================

@frappe.whitelist()
def sales_analytics_report(
    company: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    warehouse: Optional[str] = None,
    item_group: Optional[str] = None,
    customer: Optional[str] = None,
    group_by: str = "date"
) -> Dict:
    """
    Sales Analytics Report - Aggregated sales data for analytics.
    
    Args:
        company: Company name (required)
        start_date: Start date (YYYY-MM-DD, optional)
        end_date: End date (YYYY-MM-DD, optional)
        warehouse: Warehouse filter (optional)
        item_group: Item group filter (optional)
        customer: Customer filter (optional)
        group_by: Group by date|item_group|customer (default: date)
    
    Returns:
        dict: Aggregated sales analytics data
    """
    try:
        if not company:
            company = frappe.defaults.get_user_default("Company")
            if not company:
                return {
                    "success": False,
                    "message": "Company is required"
                }
        
        # Build base filters
        filters = {"company": company, "docstatus": 1}
        if start_date:
            filters["posting_date"] = [">=", start_date]
        if end_date:
            if "posting_date" in filters:
                filters["posting_date"] = ["between", [start_date or "1900-01-01", end_date]]
            else:
                filters["posting_date"] = ["<=", end_date]
        if customer:
            filters["customer"] = customer
        
        # Get Sales Invoices
        sales_invoices = frappe.get_all(
            "Sales Invoice",
            filters=filters,
            fields=["name", "posting_date", "customer", "grand_total", "rounded_total"]
        )
        
        # Get POS Invoices
        pos_filters = filters.copy()
        if warehouse:
            pos_filters["is_pos"] = 1
        pos_invoices = frappe.get_all(
            "POS Invoice",
            filters=pos_filters,
            fields=["name", "posting_date", "customer", "grand_total", "rounded_total"]
        )
        
        # Combine invoices
        all_invoices = sales_invoices + pos_invoices
        
        # Get invoice items for aggregation
        invoice_names = [inv["name"] for inv in all_invoices]
        
        if not invoice_names:
            return {
                "success": True,
                "data": {
                    "daily_sales": [],
                    "revenue_by_item_group": [],
                    "summary": {
                        "total_revenue": 0.0,
                        "total_quantity": 0.0,
                        "total_invoices": 0,
                        "average_order_value": 0.0
                    },
                    "table_data": []
                }
            }
        
        # Get invoice items
        item_filters = {"parent": ["in", invoice_names], "docstatus": 1}
        if item_group:
            item_filters["item_group"] = item_group
        
        # Get Sales Invoice Items
        si_items = frappe.get_all(
            "Sales Invoice Item",
            filters=item_filters,
            fields=["parent", "item_group", "qty", "amount", "base_amount"]
        )
        
        # Get POS Invoice Items
        pos_item_filters = {"parent": ["in", invoice_names], "docstatus": 1}
        if item_group:
            pos_item_filters["item_group"] = item_group
        pos_items = frappe.get_all(
            "POS Invoice Item",
            filters=pos_item_filters,
            fields=["parent", "item_group", "qty", "amount", "base_amount"]
        )
        
        all_items = si_items + pos_items
        
        # Create invoice map
        invoice_map = {inv["name"]: inv for inv in all_invoices}
        
        # Aggregate data
        daily_sales_map = {}
        item_group_map = {}
        total_revenue = 0.0
        total_qty = 0.0
        table_data = []
        
        for item in all_items:
            invoice = invoice_map.get(item["parent"])
            if not invoice:
                continue
            
            posting_date = str(invoice["posting_date"])
            amount = flt(item.get("base_amount") or item.get("amount") or 0)
            qty = flt(item.get("qty") or 0)
            item_group_name = item.get("item_group") or "Unknown"
            
            # Daily sales aggregation
            if posting_date not in daily_sales_map:
                daily_sales_map[posting_date] = {
                    "date": posting_date,
                    "total_amount": 0.0,
                    "total_qty": 0.0,
                    "invoice_count": 0,
                    "invoices": set()
                }
            
            daily_sales_map[posting_date]["total_amount"] += amount
            daily_sales_map[posting_date]["total_qty"] += qty
            daily_sales_map[posting_date]["invoices"].add(item["parent"])
            
            # Item group aggregation
            if item_group_name not in item_group_map:
                item_group_map[item_group_name] = {
                    "item_group": item_group_name,
                    "total_revenue": 0.0,
                    "total_qty": 0.0,
                    "item_count": 0
                }
            
            item_group_map[item_group_name]["total_revenue"] += amount
            item_group_map[item_group_name]["total_qty"] += qty
            item_group_map[item_group_name]["item_count"] += 1
            
            # Table data
            table_data.append({
                "invoice_no": item["parent"],
                "posting_date": posting_date,
                "customer": invoice.get("customer"),
                "item_group": item_group_name,
                "total_amount": amount,
                "total_qty": qty
            })
            
            total_revenue += amount
            total_qty += qty
        
        # Format daily sales
        daily_sales = []
        for date, data in sorted(daily_sales_map.items()):
            invoice_count = len(data["invoices"])
            daily_sales.append({
                "date": date,
                "total_amount": flt(data["total_amount"], 2),
                "total_qty": flt(data["total_qty"], 2),
                "invoice_count": invoice_count,
                "average_order_value": flt(data["total_amount"] / invoice_count, 2) if invoice_count > 0 else 0.0
            })
        
        # Format item group data
        revenue_by_item_group = []
        for item_group_name, data in item_group_map.items():
            percentage = (data["total_revenue"] / total_revenue * 100) if total_revenue > 0 else 0.0
            revenue_by_item_group.append({
                "item_group": item_group_name,
                "total_revenue": flt(data["total_revenue"], 2),
                "total_qty": flt(data["total_qty"], 2),
                "item_count": data["item_count"],
                "percentage": flt(percentage, 2)
            })
        
        # Sort by revenue descending
        revenue_by_item_group.sort(key=lambda x: x["total_revenue"], reverse=True)
        
        # Summary
        total_invoices = len(set(inv["name"] for inv in all_invoices))
        summary = {
            "total_revenue": flt(total_revenue, 2),
            "total_quantity": flt(total_qty, 2),
            "total_invoices": total_invoices,
            "average_order_value": flt(total_revenue / total_invoices, 2) if total_invoices > 0 else 0.0
        }
        
        return {
            "success": True,
            "data": {
                "daily_sales": daily_sales,
                "revenue_by_item_group": revenue_by_item_group,
                "summary": summary,
                "table_data": table_data
            }
        }
    
    except Exception as e:
        frappe.log_error("Sales Analytics Report Error", f"Error in sales_analytics_report: {str(e)}")
        return {
            "success": False,
            "message": f"Error generating sales analytics report: {str(e)}"
        }


@frappe.whitelist()
def export_sales_analytics_report(
    company: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    warehouse: Optional[str] = None,
    item_group: Optional[str] = None,
    customer: Optional[str] = None,
    group_by: str = "date",
    format: str = "csv"
) -> Dict:
    """
    Export Sales Analytics Report in various formats.
    
    Args:
        company: Company name (required)
        start_date: Start date (YYYY-MM-DD, optional)
        end_date: End date (YYYY-MM-DD, optional)
        warehouse: Warehouse filter (optional)
        item_group: Item group filter (optional)
        customer: Customer filter (optional)
        group_by: Group by date|item_group|customer (default: date)
        format: Export format - csv|excel|pdf (default: csv)
    
    Returns:
        dict: Export file information or error
    """
    try:
        # Get the report data
        result = sales_analytics_report(
            company=company,
            start_date=start_date,
            end_date=end_date,
            warehouse=warehouse,
            item_group=item_group,
            customer=customer,
            group_by=group_by
        )
        
        if not result.get("success"):
            return result
        
        data = result.get("data", {})
        
        # For now, return the data with export format info
        # In a full implementation, you would generate actual files
        return {
            "success": True,
            "message": f"Export in {format} format is not yet fully implemented. Use the sales_analytics_report endpoint to get data.",
            "data": data,
            "format": format
        }
    
    except Exception as e:
        frappe.log_error("Export Sales Analytics Report Error", f"Error in export_sales_analytics_report: {str(e)}")
        return {
            "success": False,
            "message": f"Error exporting sales analytics report: {str(e)}"
        }


# ==================== INVENTORY VALUATION REPORTS ====================

@frappe.whitelist()
def inventory_value_by_category_report(
    company: str,
    warehouse: Optional[str] = None,
    item_group: Optional[str] = None
) -> Dict:
    """
    Inventory Value by Category Report - Value breakdown by item category/group.
    
    Args:
        company: Company name (required)
        warehouse: Warehouse filter (optional)
        item_group: Item group filter (optional)
    
    Returns:
        dict: Inventory value by category data
    """
    try:
        if not company:
            company = frappe.defaults.get_user_default("Company")
            if not company:
                return {
                    "success": False,
                    "message": "Company is required"
                }
        
        # Build query filters
        bin_filters = {"actual_qty": [">", 0]}
        if warehouse:
            bin_filters["warehouse"] = warehouse
        
        # Get company warehouses if warehouse not specified
        if not warehouse:
            company_warehouses = frappe.get_all(
                "Warehouse",
                filters={"company": company},
                pluck="name"
            )
            if company_warehouses:
                bin_filters["warehouse"] = ["in", company_warehouses]
        
        # Get bins with item details
        bins = frappe.get_all(
            "Bin",
            filters=bin_filters,
            fields=["item_code", "warehouse", "actual_qty", "valuation_rate", "stock_value"]
        )
        
        if not bins:
            return {
                "success": True,
                "data": []
            }
        
        # Get item codes and fetch item groups
        item_codes = list(set(b["item_code"] for b in bins))
        items = frappe.get_all(
            "Item",
            filters={"name": ["in", item_codes]},
            fields=["name", "item_group"]
        )
        
        item_group_map = {item["name"]: item.get("item_group") or "Unknown" for item in items}
        
        # Filter by item_group if specified
        if item_group:
            item_codes = [item["name"] for item in items if item.get("item_group") == item_group]
            bins = [b for b in bins if b["item_code"] in item_codes]
        
        # Aggregate by item group
        group_data = {}
        total_value = 0.0
        
        for bin in bins:
            item_group_name = item_group_map.get(bin["item_code"], "Unknown")
            value = flt(bin.get("stock_value") or (bin.get("valuation_rate") or 0) * bin.get("actual_qty") or 0)
            qty = flt(bin.get("actual_qty") or 0)
            
            if item_group_name not in group_data:
                group_data[item_group_name] = {
                    "item_group": item_group_name,
                    "total_value": 0.0,
                    "total_qty": 0.0,
                    "item_count": 0,
                    "items": set()
                }
            
            group_data[item_group_name]["total_value"] += value
            group_data[item_group_name]["total_qty"] += qty
            group_data[item_group_name]["items"].add(bin["item_code"])
            total_value += value
        
        # Format results
        result = []
        for item_group_name, data in group_data.items():
            item_count = len(data["items"])
            percentage = (data["total_value"] / total_value * 100) if total_value > 0 else 0.0
            result.append({
                "item_group": item_group_name,
                "total_value": flt(data["total_value"], 2),
                "total_qty": flt(data["total_qty"], 2),
                "item_count": item_count,
                "percentage": flt(percentage, 2)
            })
        
        # Sort by total_value descending
        result.sort(key=lambda x: x["total_value"], reverse=True)
        
        return {
            "success": True,
            "data": result
        }
    
    except Exception as e:
        frappe.log_error("Inventory Value by Category Report Error", f"Error in inventory_value_by_category_report: {str(e)}")
        return {
            "success": False,
            "message": f"Error generating inventory value by category report: {str(e)}"
        }


@frappe.whitelist()
def inventory_cost_method_comparison_report(
    company: str,
    warehouse: Optional[str] = None,
    cost_methods: Optional[List[str]] = None
) -> Dict:
    """
    Inventory Cost Method Comparison Report - Compare inventory valuation using different cost methods.
    
    Args:
        company: Company name (required)
        warehouse: Warehouse filter (optional)
        cost_methods: List of cost methods to compare (optional, default: all)
    
    Returns:
        dict: Comparison data for different cost methods
    """
    try:
        if not company:
            company = frappe.defaults.get_user_default("Company")
            if not company:
                return {
                    "success": False,
                    "message": "Company is required"
                }
        
        # Note: ERPNext typically uses one valuation method per company
        # This is a simplified implementation that shows current valuation
        # A full implementation would need to calculate FIFO, LIFO, and Weighted Average separately
        
        bin_filters = {"actual_qty": [">", 0]}
        if warehouse:
            bin_filters["warehouse"] = warehouse
        
        if not warehouse:
            company_warehouses = frappe.get_all(
                "Warehouse",
                filters={"company": company},
                pluck="name"
            )
            if company_warehouses:
                bin_filters["warehouse"] = ["in", company_warehouses]
        
        bins = frappe.get_all(
            "Bin",
            filters=bin_filters,
            fields=["item_code", "warehouse", "actual_qty", "valuation_rate", "stock_value"]
        )
        
        # Get company's valuation method
        # Try to get from Stock Settings first, then default to FIFO
        valuation_method = "FIFO"  # Default value
        try:
            # Try to get from Stock Settings (common in ERPNext)
            stock_settings_method = frappe.db.get_single_value("Stock Settings", "valuation_method")
            if stock_settings_method:
                valuation_method = stock_settings_method
        except Exception:
            # If Stock Settings doesn't have the field or doesn't exist, try Company
            try:
                company_method = frappe.db.get_value("Company", company, "default_inventory_valuation_method")
                if company_method:
                    valuation_method = company_method
            except Exception:
                # Field doesn't exist, use default
                pass
        
        # Calculate total value using current method
        total_value = sum(flt(b.get("stock_value") or (b.get("valuation_rate") or 0) * (b.get("actual_qty") or 0)) for b in bins)
        item_count = len(set(b["item_code"] for b in bins))
        
        # For now, return current method only
        # In a full implementation, you would calculate all methods
        result = {
            valuation_method: {
                "total_value": flt(total_value, 2),
                "item_count": item_count
            }
        }
        
        # If other methods requested, note that they need separate calculation
        if cost_methods:
            for method in cost_methods:
                if method != valuation_method:
                    result[method] = {
                        "total_value": 0.0,
                        "item_count": item_count,
                        "note": "Calculation not implemented - requires separate valuation logic"
                    }
        
        return {
            "success": True,
            "data": result,
            "message": f"Currently showing {valuation_method} method. Other methods require separate calculation logic."
        }
    
    except Exception as e:
        frappe.log_error("Inventory Cost Method Comparison Report Error", f"Error in inventory_cost_method_comparison_report: {str(e)}")
        return {
            "success": False,
            "message": f"Error generating cost method comparison report: {str(e)}"
        }


@frappe.whitelist()
def inventory_value_trends_report(
    company: str,
    start_date: str,
    end_date: str,
    warehouse: Optional[str] = None,
    period: str = "daily"
) -> Dict:
    """
    Inventory Value Trends Report - Historical inventory values over time.
    
    Args:
        company: Company name (required)
        start_date: Start date (YYYY-MM-DD, required)
        end_date: End date (YYYY-MM-DD, required)
        warehouse: Warehouse filter (optional)
        period: Period grouping - daily|weekly|monthly (default: daily)
    
    Returns:
        dict: Inventory value trends data
    """
    try:
        if not company:
            company = frappe.defaults.get_user_default("Company")
            if not company:
                return {
                    "success": False,
                    "message": "Company is required"
                }
        
        start_dt = getdate(start_date)
        end_dt = getdate(end_date)
        
        # Get stock ledger entries for the period
        sle_filters = {
            "company": company,
            "posting_date": ["between", [start_date, end_date]],
            "is_cancelled": 0
        }
        
        if warehouse:
            sle_filters["warehouse"] = warehouse
        
        # Get stock ledger entries grouped by date
        if period == "daily":
            date_format = "%Y-%m-%d"
            group_by = "DATE(posting_date)"
        elif period == "weekly":
            date_format = "%Y-%u"  # Year-Week
            group_by = "YEARWEEK(posting_date)"
        elif period == "monthly":
            date_format = "%Y-%m"
            group_by = "DATE_FORMAT(posting_date, '%Y-%m')"
        else:
            date_format = "%Y-%m-%d"
            group_by = "DATE(posting_date)"
        
        # Query to get inventory values by period
        # This is a simplified approach - a full implementation would track historical balances
        query = f"""
            SELECT
                {group_by} AS period,
                SUM(stock_value_difference) AS value_change,
                COUNT(DISTINCT item_code) AS item_count
            FROM
                `tabStock Ledger Entry`
            WHERE
                company = %(company)s
                AND posting_date BETWEEN %(start_date)s AND %(end_date)s
                AND is_cancelled = 0
        """
        
        if warehouse:
            query += " AND warehouse = %(warehouse)s"
        
        query += " GROUP BY period ORDER BY period"
        
        params = {
            "company": company,
            "start_date": start_date,
            "end_date": end_date
        }
        if warehouse:
            params["warehouse"] = warehouse
        
        period_data = frappe.db.sql(query, params, as_dict=True)
        
        # Get current inventory value
        bin_filters = {"actual_qty": [">", 0]}
        if warehouse:
            bin_filters["warehouse"] = warehouse
        else:
            company_warehouses = frappe.get_all(
                "Warehouse",
                filters={"company": company},
                pluck="name"
            )
            if company_warehouses:
                bin_filters["warehouse"] = ["in", company_warehouses]
        
        bins = frappe.get_all(
            "Bin",
            filters=bin_filters,
            fields=["stock_value"]
        )
        current_value = sum(flt(b.get("stock_value") or 0) for b in bins)
        
        # Build trends data
        trends = []
        previous_value = current_value
        
        # Reverse to calculate from end to start
        for entry in reversed(period_data):
            period_str = str(entry["period"])
            value_change = flt(entry.get("value_change") or 0)
            current_value -= value_change  # Work backwards
            change = value_change
            change_percentage = (change / previous_value * 100) if previous_value != 0 else 0.0
            
            trends.insert(0, {
                "period": period_str,
                "total_value": flt(current_value, 2),
                "change": flt(change, 2),
                "change_percentage": flt(change_percentage, 2)
            })
            
            previous_value = current_value
        
        return {
            "success": True,
            "data": trends
        }
    
    except Exception as e:
        frappe.log_error("Inventory Value Trends Report Error", f"Error in inventory_value_trends_report: {str(e)}")
        return {
            "success": False,
            "message": f"Error generating inventory value trends report: {str(e)}"
        }


# ==================== STOCK MOVEMENT ANALYSIS REPORTS ====================

@frappe.whitelist()
def inventory_turnover_report(
    company: str,
    start_date: str,
    end_date: str,
    warehouse: Optional[str] = None,
    item_group: Optional[str] = None
) -> Dict:
    """
    Inventory Turnover Report - Calculate turnover rates for inventory items.
    
    Args:
        company: Company name (required)
        start_date: Start date (YYYY-MM-DD, required)
        end_date: End date (YYYY-MM-DD, required)
        warehouse: Warehouse filter (optional)
        item_group: Item group filter (optional)
    
    Returns:
        dict: Inventory turnover data
    """
    try:
        if not company:
            company = frappe.defaults.get_user_default("Company")
            if not company:
                return {
                    "success": False,
                    "message": "Company is required"
                }
        
        # Get stock ledger entries for the period
        sle_filters = {
            "company": company,
            "posting_date": ["between", [start_date, end_date]],
            "is_cancelled": 0,
            "voucher_type": ["in", ["Sales Invoice", "POS Invoice", "Delivery Note"]]
        }
        
        if warehouse:
            sle_filters["warehouse"] = warehouse
        
        # Get sales transactions
        sales_sle = frappe.get_all(
            "Stock Ledger Entry",
            filters=sle_filters,
            fields=["item_code", "actual_qty", "incoming_rate", "valuation_rate"]
        )
        
        # Get item codes
        item_codes = list(set(s["item_code"] for s in sales_sle))
        
        if item_group:
            items = frappe.get_all(
                "Item",
                filters={"name": ["in", item_codes], "item_group": item_group},
                pluck="name"
            )
            item_codes = items
        
        # Calculate cost of sales and average stock
        item_data = {}
        
        for sle in sales_sle:
            if sle["item_code"] not in item_codes:
                continue
            
            item_code = sle["item_code"]
            if item_code not in item_data:
                item_data[item_code] = {
                    "cost_of_sales": 0.0,
                    "qty_sold": 0.0
                }
            
            # Use outgoing qty (negative actual_qty for sales)
            qty = abs(flt(sle.get("actual_qty") or 0))
            rate = flt(sle.get("incoming_rate") or sle.get("valuation_rate") or 0)
            
            item_data[item_code]["cost_of_sales"] += qty * rate
            item_data[item_code]["qty_sold"] += qty
        
        # Get current stock balances
        bin_filters = {"item_code": ["in", item_codes], "actual_qty": [">", 0]}
        if warehouse:
            bin_filters["warehouse"] = warehouse
        
        bins = frappe.get_all(
            "Bin",
            filters=bin_filters,
            fields=["item_code", "actual_qty", "valuation_rate"]
        )
        
        # Calculate average stock (simplified - using current stock)
        bin_data = {}
        for bin in bins:
            item_code = bin["item_code"]
            if item_code not in bin_data:
                bin_data[item_code] = {
                    "total_qty": 0.0,
                    "total_value": 0.0
                }
            qty = flt(bin.get("actual_qty") or 0)
            rate = flt(bin.get("valuation_rate") or 0)
            bin_data[item_code]["total_qty"] += qty
            bin_data[item_code]["total_value"] += qty * rate
        
        # Get item names
        items = frappe.get_all(
            "Item",
            filters={"name": ["in", item_codes]},
            fields=["name", "item_name"]
        )
        item_name_map = {item["name"]: item.get("item_name") or item["name"] for item in items}
        
        # Calculate turnover rates
        result = []
        for item_code in item_codes:
            cost_of_sales = item_data.get(item_code, {}).get("cost_of_sales", 0.0)
            avg_stock = bin_data.get(item_code, {}).get("total_value", 0.0)
            
            if avg_stock > 0:
                turnover_rate = (cost_of_sales / avg_stock) if avg_stock > 0 else 0.0
                turnover_days = (365 / turnover_rate) if turnover_rate > 0 else 0.0
            else:
                turnover_rate = 0.0
                turnover_days = 0.0
            
            result.append({
                "item_code": item_code,
                "item_name": item_name_map.get(item_code, item_code),
                "average_stock": flt(avg_stock, 2),
                "cost_of_sales": flt(cost_of_sales, 2),
                "turnover_rate": flt(turnover_rate, 2),
                "turnover_days": flt(turnover_days, 2)
            })
        
        # Sort by turnover_rate descending
        result.sort(key=lambda x: x["turnover_rate"], reverse=True)
        
        return {
            "success": True,
            "data": result
        }
    
    except Exception as e:
        frappe.log_error("Inventory Turnover Report Error", f"Error in inventory_turnover_report: {str(e)}")
        return {
            "success": False,
            "message": f"Error generating inventory turnover report: {str(e)}"
        }


@frappe.whitelist()
def inventory_days_on_hand_report(
    company: str,
    warehouse: Optional[str] = None,
    item_group: Optional[str] = None,
    period_days: int = 30
) -> Dict:
    """
    Inventory Days on Hand Report - Days of stock remaining based on current stock and average consumption.
    
    Args:
        company: Company name (required)
        warehouse: Warehouse filter (optional)
        item_group: Item group filter (optional)
        period_days: Days to calculate average consumption (default: 30)
    
    Returns:
        dict: Days on hand data
    """
    try:
        if not company:
            company = frappe.defaults.get_user_default("Company")
            if not company:
                return {
                    "success": False,
                    "message": "Company is required"
                }
        
        # Calculate date range for average consumption
        end_date = nowdate()
        start_date = add_days(end_date, -period_days)
        
        # Get current stock with age calculation
        where_conditions = []
        params = []
        
        if warehouse:
            where_conditions.append("b.warehouse = %s")
            params.append(warehouse)
        else:
            company_warehouses = frappe.get_all(
                "Warehouse",
                filters={"company": company},
                pluck="name"
            )
            if company_warehouses:
                placeholders = ",".join(["%s"] * len(company_warehouses))
                where_conditions.append(f"b.warehouse IN ({placeholders})")
                params.extend(company_warehouses)
        
        where_clause = " AND " + " AND ".join(where_conditions) if where_conditions else ""
        
        # Query to get bins with age calculation (similar to stock_aging_report)
        bin_query = f"""
            SELECT
                b.item_code,
                b.warehouse,
                b.actual_qty,
                DATEDIFF(CURDATE(), b.creation) AS age_days
            FROM
                `tabBin` b
            WHERE
                b.actual_qty > 0
                {where_clause}
        """
        
        bins = frappe.db.sql(bin_query, tuple(params), as_dict=True) if params else frappe.db.sql(bin_query, as_dict=True)
        
        if not bins:
            return {
                "success": True,
                "data": []
            }
        
        item_codes = list(set(b["item_code"] for b in bins))
        
        if item_group:
            items = frappe.get_all(
                "Item",
                filters={"name": ["in", item_codes], "item_group": item_group},
                pluck="name"
            )
            item_codes = items
            bins = [b for b in bins if b["item_code"] in item_codes]
        
        # Get sales data for the period
        sle_filters = {
            "company": company,
            "posting_date": ["between", [start_date, end_date]],
            "is_cancelled": 0,
            "voucher_type": ["in", ["Sales Invoice", "POS Invoice", "Delivery Note"]],
            "item_code": ["in", item_codes]
        }
        
        if warehouse:
            sle_filters["warehouse"] = warehouse
        
        sales_sle = frappe.get_all(
            "Stock Ledger Entry",
            filters=sle_filters,
            fields=["item_code", "warehouse", "actual_qty"]
        )
        
        # Calculate average daily sales per item-warehouse
        sales_data = {}
        for sle in sales_sle:
            key = f"{sle['item_code']}_{sle['warehouse']}"
            if key not in sales_data:
                sales_data[key] = 0.0
            sales_data[key] += abs(flt(sle.get("actual_qty") or 0))
        
        # Get item names
        items = frappe.get_all(
            "Item",
            filters={"name": ["in", item_codes]},
            fields=["name", "item_name"]
        )
        item_name_map = {item["name"]: item.get("item_name") or item["name"] for item in items}
        
        # Calculate days on hand
        result = []
        for bin in bins:
            item_code = bin["item_code"]
            warehouse_name = bin["warehouse"]
            current_stock = flt(bin.get("actual_qty") or 0)
            age_days = flt(bin.get("age_days") or 0)
            key = f"{item_code}_{warehouse_name}"
            
            total_sold = sales_data.get(key, 0.0)
            avg_daily_sales = total_sold / period_days if period_days > 0 else 0.0
            
            # Use a small threshold to handle floating point precision issues
            # If avg_daily_sales is effectively zero, use age_days (like stock_aging_report)
            if avg_daily_sales > 0.0001:
                # Calculate days on hand based on current stock and average daily sales
                days_on_hand = current_stock / avg_daily_sales
                if days_on_hand < 7:
                    status = "critical"
                elif days_on_hand < 14:
                    status = "low"
                else:
                    status = "normal"
            else:
                # No sales in the period - use age in days (similar to stock_aging_report)
                # This gives accurate age for newly added items instead of returning 999.0
                days_on_hand = age_days if age_days is not None else 0
                status = "normal"  # Items with no sales history default to normal status
            
            result.append({
                "item_code": item_code,
                "item_name": item_name_map.get(item_code, item_code),
                "warehouse": warehouse_name,
                "current_stock": flt(current_stock, 2),
                "avg_daily_sales": flt(avg_daily_sales, 2),
                "days_on_hand": flt(days_on_hand, 2),
                "status": status
            })
        
        # Sort by days_on_hand ascending
        result.sort(key=lambda x: x["days_on_hand"])
        
        return {
            "success": True,
            "data": result
        }
    
    except Exception as e:
        frappe.log_error("Inventory Days on Hand Report Error", f"Error in inventory_days_on_hand_report: {str(e)}")
        return {
            "success": False,
            "message": f"Error generating days on hand report: {str(e)}"
        }


@frappe.whitelist()
def inventory_movement_patterns_report(
    company: str,
    start_date: str,
    end_date: str,
    warehouse: Optional[str] = None,
    analysis_type: str = "trend"
) -> Dict:
    """
    Inventory Movement Patterns Report - Movement trends and patterns over time.
    
    Args:
        company: Company name (required)
        start_date: Start date (YYYY-MM-DD, required)
        end_date: End date (YYYY-MM-DD, required)
        warehouse: Warehouse filter (optional)
        analysis_type: Analysis type - seasonal|trend|forecast (default: trend)
    
    Returns:
        dict: Movement patterns data
    """
    try:
        if not company:
            company = frappe.defaults.get_user_default("Company")
            if not company:
                return {
                    "success": False,
                    "message": "Company is required"
                }
        
        # Get stock ledger entries
        sle_filters = {
            "company": company,
            "posting_date": ["between", [start_date, end_date]],
            "is_cancelled": 0
        }
        
        if warehouse:
            sle_filters["warehouse"] = warehouse
        
        sle_entries = frappe.get_all(
            "Stock Ledger Entry",
            filters=sle_filters,
            fields=["item_code", "posting_date", "actual_qty", "voucher_type"]
        )
        
        if not sle_entries:
            return {
                "success": True,
                "data": {
                    "trends": [],
                    "time_series": []
                }
            }
        
        # Get item names
        item_codes = list(set(e["item_code"] for e in sle_entries))
        items = frappe.get_all(
            "Item",
            filters={"name": ["in", item_codes]},
            fields=["name", "item_name"]
        )
        item_name_map = {item["name"]: item.get("item_name") or item["name"] for item in items}
        
        # Aggregate by date
        date_data = {}
        item_movement = {}
        
        for sle in sle_entries:
            date_str = str(sle["posting_date"])
            item_code = sle["item_code"]
            qty = flt(sle.get("actual_qty") or 0)
            voucher_type = sle.get("voucher_type") or ""
            
            if date_str not in date_data:
                date_data[date_str] = {
                    "received": 0.0,
                    "issued": 0.0,
                    "net_movement": 0.0
                }
            
            # Categorize movement
            if qty > 0 or voucher_type in ["Purchase Receipt", "Stock Entry", "Material Receipt"]:
                date_data[date_str]["received"] += abs(qty)
            else:
                date_data[date_str]["issued"] += abs(qty)
            
            date_data[date_str]["net_movement"] = date_data[date_str]["received"] - date_data[date_str]["issued"]
            
            # Track item movement
            if item_code not in item_movement:
                item_movement[item_code] = {
                    "total_received": 0.0,
                    "total_issued": 0.0,
                    "dates": []
                }
            
            if qty > 0:
                item_movement[item_code]["total_received"] += abs(qty)
            else:
                item_movement[item_code]["total_issued"] += abs(qty)
            item_movement[item_code]["dates"].append(date_str)
        
        # Build time series
        time_series = []
        for date_str in sorted(date_data.keys()):
            data = date_data[date_str]
            time_series.append({
                "date": date_str,
                "received": flt(data["received"], 2),
                "issued": flt(data["issued"], 2),
                "net_movement": flt(data["net_movement"], 2)
            })
        
        # Calculate trends for items
        trends = []
        for item_code, movement in item_movement.items():
            net_movement = movement["total_received"] - movement["total_issued"]
            
            # Simple trend calculation
            if len(movement["dates"]) > 1:
                # Compare first half vs second half
                sorted_dates = sorted(set(movement["dates"]))
                mid_point = len(sorted_dates) // 2
                first_half = sorted_dates[:mid_point]
                second_half = sorted_dates[mid_point:]
                
                # This is simplified - a full implementation would do proper trend analysis
                if net_movement > 0:
                    trend = "increasing"
                elif net_movement < 0:
                    trend = "decreasing"
                else:
                    trend = "stable"
                
                change_percentage = (net_movement / (movement["total_received"] + movement["total_issued"]) * 100) if (movement["total_received"] + movement["total_issued"]) > 0 else 0.0
            else:
                trend = "stable"
                change_percentage = 0.0
            
            trends.append({
                "item_code": item_code,
                "item_name": item_name_map.get(item_code, item_code),
                "trend": trend,
                "change_percentage": flt(change_percentage, 2)
            })
        
        return {
            "success": True,
            "data": {
                "trends": trends,
                "time_series": time_series
            }
        }
    
    except Exception as e:
        frappe.log_error("Inventory Movement Patterns Report Error", f"Error in inventory_movement_patterns_report: {str(e)}")
        return {
            "success": False,
            "message": f"Error generating movement patterns report: {str(e)}"
        }


# ==================== AGING STOCK REPORTS ====================

@frappe.whitelist()
def inventory_obsolescence_risk_report(
    company: str,
    warehouse: Optional[str] = None,
    risk_level: Optional[str] = None
) -> Dict:
    """
    Inventory Obsolescence Risk Report - Identify items with high obsolescence risk.
    
    Args:
        company: Company name (required)
        warehouse: Warehouse filter (optional)
        risk_level: Filter by risk level - low|medium|high (optional, default: all)
    
    Returns:
        dict: Obsolescence risk data
    """
    try:
        if not company:
            company = frappe.defaults.get_user_default("Company")
            if not company:
                return {
                    "success": False,
                    "message": "Company is required"
                }
        
        # Get stock with age
        bin_filters = {"actual_qty": [">", 0]}
        if warehouse:
            bin_filters["warehouse"] = warehouse
        else:
            company_warehouses = frappe.get_all(
                "Warehouse",
                filters={"company": company},
                pluck="name"
            )
            if company_warehouses:
                bin_filters["warehouse"] = ["in", company_warehouses]
        
        bins = frappe.db.sql("""
            SELECT
                b.item_code,
                b.warehouse,
                b.actual_qty,
                DATEDIFF(CURDATE(), b.creation) AS age_days,
                b.creation AS last_movement_date
            FROM
                `tabBin` b
            WHERE
                b.actual_qty > 0
        """, as_dict=True)
        
        if warehouse:
            bins = [b for b in bins if b["warehouse"] == warehouse]
        
        if not bins:
            return {
                "success": True,
                "data": []
            }
        
        item_codes = list(set(b["item_code"] for b in bins))
        
        # Get last movement dates from stock ledger
        last_movements = frappe.db.sql("""
            SELECT
                item_code,
                warehouse,
                MAX(posting_date) AS last_movement_date
            FROM
                `tabStock Ledger Entry`
            WHERE
                item_code IN %(item_codes)s
                AND is_cancelled = 0
            GROUP BY
                item_code, warehouse
        """, {"item_codes": item_codes}, as_dict=True)
        
        movement_map = {}
        for mv in last_movements:
            key = f"{mv['item_code']}_{mv['warehouse']}"
            movement_map[key] = mv["last_movement_date"]
        
        # Get item names
        items = frappe.get_all(
            "Item",
            filters={"name": ["in", item_codes]},
            fields=["name", "item_name"]
        )
        item_name_map = {item["name"]: item.get("item_name") or item["name"] for item in items}
        
        # Calculate risk scores
        result = []
        for bin in bins:
            item_code = bin["item_code"]
            warehouse_name = bin["warehouse"]
            age_days = bin["age_days"]
            current_stock = flt(bin.get("actual_qty") or 0)
            key = f"{item_code}_{warehouse_name}"
            last_movement_date = movement_map.get(key)
            
            # Calculate risk score (0-100)
            # Factors: age, no recent movement, high stock
            risk_score = 0.0
            risk_factors = []
            
            if age_days > 90:
                risk_score += 40
                risk_factors.append("high_age")
            elif age_days > 60:
                risk_score += 20
                risk_factors.append("medium_age")
            
            if last_movement_date:
                days_since_movement = date_diff(nowdate(), last_movement_date)
                if days_since_movement > 90:
                    risk_score += 30
                    risk_factors.append("no_recent_movement")
                elif days_since_movement > 60:
                    risk_score += 15
            else:
                risk_score += 30
                risk_factors.append("no_recent_movement")
            
            if current_stock > 100:  # High stock level
                risk_score += 10
                risk_factors.append("high_stock")
            
            # Determine risk level
            if risk_score >= 70:
                risk_level_str = "high"
            elif risk_score >= 40:
                risk_level_str = "medium"
            else:
                risk_level_str = "low"
            
            # Filter by risk level if specified
            if risk_level and risk_level_str != risk_level:
                continue
            
            result.append({
                "item_code": item_code,
                "item_name": item_name_map.get(item_code, item_code),
                "warehouse": warehouse_name,
                "age_days": age_days,
                "last_movement_date": str(last_movement_date) if last_movement_date else None,
                "current_stock": flt(current_stock, 2),
                "risk_score": flt(risk_score, 2),
                "risk_level": risk_level_str,
                "risk_factors": risk_factors
            })
        
        # Sort by risk_score descending
        result.sort(key=lambda x: x["risk_score"], reverse=True)
        
        return {
            "success": True,
            "data": result
        }
    
    except Exception as e:
        frappe.log_error("Inventory Obsolescence Risk Report Error", f"Error in inventory_obsolescence_risk_report: {str(e)}")
        return {
            "success": False,
            "message": f"Error generating obsolescence risk report: {str(e)}"
        }


@frappe.whitelist()
def inventory_aging_recommendations_report(
    company: str,
    warehouse: Optional[str] = None
) -> Dict:
    """
    Inventory Aging Recommendations Report - Recommended actions for aging stock items.
    
    Args:
        company: Company name (required)
        warehouse: Warehouse filter (optional)
    
    Returns:
        dict: Aging recommendations data
    """
    try:
        if not company:
            company = frappe.defaults.get_user_default("Company")
            if not company:
                return {
                    "success": False,
                    "message": "Company is required"
                }
        
        # Get stock aging data
        bins = frappe.db.sql("""
            SELECT
                b.item_code,
                b.warehouse,
                b.actual_qty,
                DATEDIFF(CURDATE(), b.creation) AS age_days
            FROM
                `tabBin` b
            WHERE
                b.actual_qty > 0
        """, as_dict=True)
        
        if warehouse:
            bins = [b for b in bins if b["warehouse"] == warehouse]
        else:
            company_warehouses = frappe.get_all(
                "Warehouse",
                filters={"company": company},
                pluck="name"
            )
            if company_warehouses:
                bins = [b for b in bins if b["warehouse"] in company_warehouses]
        
        if not bins:
            return {
                "success": True,
                "data": []
            }
        
        item_codes = list(set(b["item_code"] for b in bins))
        
        # Get item names
        items = frappe.get_all(
            "Item",
            filters={"name": ["in", item_codes]},
            fields=["name", "item_name"]
        )
        item_name_map = {item["name"]: item.get("item_name") or item["name"] for item in items}
        
        # Generate recommendations
        result = []
        for bin in bins:
            age_days = bin["age_days"]
            current_stock = flt(bin.get("actual_qty") or 0)
            
            # Determine age bracket
            if age_days <= 30:
                age_bracket = "0-30"
            elif age_days <= 60:
                age_bracket = "31-60"
            elif age_days <= 90:
                age_bracket = "61-90"
            else:
                age_bracket = "90+"
            
            # Determine recommended action and priority
            if age_days > 180:
                recommended_action = "dispose"
                priority = "high"
                reason = f"Item has not moved in {age_days} days and is likely obsolete"
            elif age_days > 120:
                recommended_action = "discount"
                priority = "high"
                reason = f"Item has been in stock for {age_days} days, consider discounting to move inventory"
            elif age_days > 90:
                recommended_action = "transfer"
                priority = "medium"
                reason = f"Item has been in stock for {age_days} days, consider transferring to another location"
            elif age_days > 60:
                recommended_action = "discount"
                priority = "medium"
                reason = f"Item has been in stock for {age_days} days, monitor closely"
            else:
                recommended_action = "monitor"
                priority = "low"
                reason = f"Item is within normal aging range ({age_days} days)"
            
            result.append({
                "item_code": bin["item_code"],
                "item_name": item_name_map.get(bin["item_code"], bin["item_code"]),
                "warehouse": bin["warehouse"],
                "age_bracket": age_bracket,
                "age_days": age_days,
                "current_stock": flt(current_stock, 2),
                "recommended_action": recommended_action,
                "priority": priority,
                "reason": reason
            })
        
        # Sort by priority and age_days
        priority_order = {"high": 3, "medium": 2, "low": 1}
        result.sort(key=lambda x: (priority_order.get(x["priority"], 0), x["age_days"]), reverse=True)
        
        return {
            "success": True,
            "data": result
        }
    
    except Exception as e:
        frappe.log_error("Inventory Aging Recommendations Report Error", f"Error in inventory_aging_recommendations_report: {str(e)}")
        return {
            "success": False,
            "message": f"Error generating aging recommendations report: {str(e)}"
        }


# ==================== PERFORMANCE METRICS REPORTS ====================

@frappe.whitelist()
def inventory_accuracy_report(
    company: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    warehouse: Optional[str] = None
) -> Dict:
    """
    Inventory Accuracy Report - Stock accuracy metrics (book vs actual).
    
    Args:
        company: Company name (required)
        start_date: Start date for stock counts (optional)
        end_date: End date for stock counts (optional)
        warehouse: Warehouse filter (optional)
    
    Returns:
        dict: Inventory accuracy data
    """
    try:
        if not company:
            company = frappe.defaults.get_user_default("Company")
            if not company:
                return {
                    "success": False,
                    "message": "Company is required"
                }
        
        # Get stock reconciliation entries (Stock Reconciliation)
        # Note: Stock Reconciliation uses 'set_warehouse' field, not 'warehouse'
        filters = {"company": company, "docstatus": 1}
        if warehouse:
            filters["set_warehouse"] = warehouse
        if start_date:
            filters["posting_date"] = [">=", start_date]
        if end_date:
            if "posting_date" in filters:
                filters["posting_date"] = ["between", [start_date or "1900-01-01", end_date]]
            else:
                filters["posting_date"] = ["<=", end_date]
        
        stock_reconciliations = frappe.get_all(
            "Stock Reconciliation",
            filters=filters,
            fields=["name", "set_warehouse", "posting_date"]
        )
        
        if not stock_reconciliations:
            return {
                "success": True,
                "data": {
                    "warehouse": warehouse or "All Warehouses",
                    "total_items_counted": 0,
                    "items_with_variance": 0,
                    "accuracy_rate": 100.0,
                    "variance_count": 0,
                    "total_variance_value": 0.0
                }
            }
        
        # Get stock reconciliation items
        # Note: Stock Reconciliation Item uses 'qty' field, not 'quantity'
        sr_names = [sr["name"] for sr in stock_reconciliations]
        item_filters = {"parent": ["in", sr_names]}
        if warehouse:
            # Also filter items by warehouse if specified
            item_filters["warehouse"] = warehouse
        
        sr_items = frappe.get_all(
            "Stock Reconciliation Item",
            filters=item_filters,
            fields=["parent", "item_code", "warehouse", "current_qty", "qty", "valuation_rate"]
        )
        
        # Calculate accuracy metrics
        total_items = len(sr_items)
        items_with_variance = 0
        total_variance_value = 0.0
        
        for item in sr_items:
            current_qty = flt(item.get("current_qty") or 0)
            counted_qty = flt(item.get("qty") or 0)  # Use 'qty' not 'quantity'
            variance = abs(counted_qty - current_qty)
            
            if variance > 0:
                items_with_variance += 1
                rate = flt(item.get("valuation_rate") or 0)
                total_variance_value += variance * rate
        
        accuracy_rate = ((total_items - items_with_variance) / total_items * 100) if total_items > 0 else 100.0
        
        warehouse_name = warehouse or "All Warehouses"
        if not warehouse and stock_reconciliations:
            warehouse_name = stock_reconciliations[0].get("set_warehouse") or "All Warehouses"
        
        return {
            "success": True,
            "data": {
                "warehouse": warehouse_name,
                "total_items_counted": total_items,
                "items_with_variance": items_with_variance,
                "accuracy_rate": flt(accuracy_rate, 2),
                "variance_count": items_with_variance,
                "total_variance_value": flt(total_variance_value, 2)
            }
        }
    
    except Exception as e:
        frappe.log_error("Inventory Accuracy Report Error", f"Error in inventory_accuracy_report: {str(e)}")
        return {
            "success": False,
            "message": f"Error generating inventory accuracy report: {str(e)}"
        }


@frappe.whitelist()
def inventory_variance_report(
    company: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    warehouse: Optional[str] = None,
    variance_threshold: Optional[float] = None
) -> Dict:
    """
    Inventory Variance Report - Detailed variance analysis from stock counts.
    
    Args:
        company: Company name (required)
        start_date: Start date for stock counts (optional)
        end_date: End date for stock counts (optional)
        warehouse: Warehouse filter (optional)
        variance_threshold: Minimum variance to include (optional)
    
    Returns:
        dict: Variance analysis data
    """
    try:
        if not company:
            company = frappe.defaults.get_user_default("Company")
            if not company:
                return {
                    "success": False,
                    "message": "Company is required"
                }
        
        # Get stock reconciliation entries
        # Note: Stock Reconciliation uses 'set_warehouse' field, not 'warehouse'
        filters = {"company": company, "docstatus": 1}
        if warehouse:
            filters["set_warehouse"] = warehouse
        if start_date:
            filters["posting_date"] = [">=", start_date]
        if end_date:
            if "posting_date" in filters:
                filters["posting_date"] = ["between", [start_date or "1900-01-01", end_date]]
            else:
                filters["posting_date"] = ["<=", end_date]
        
        stock_reconciliations = frappe.get_all(
            "Stock Reconciliation",
            filters=filters,
            fields=["name", "set_warehouse", "posting_date"]
        )
        
        if not stock_reconciliations:
            return {
                "success": True,
                "data": []
            }
        
        # Get stock reconciliation items
        # Note: Stock Reconciliation Item uses 'qty' field, not 'quantity'
        sr_names = [sr["name"] for sr in stock_reconciliations]
        item_filters = {"parent": ["in", sr_names]}
        if warehouse:
            # Also filter items by warehouse if specified
            item_filters["warehouse"] = warehouse
        
        sr_items = frappe.get_all(
            "Stock Reconciliation Item",
            filters=item_filters,
            fields=["parent", "item_code", "warehouse", "current_qty", "qty", "valuation_rate"]
        )
        
        # Get reconciliation dates
        sr_date_map = {sr["name"]: sr["posting_date"] for sr in stock_reconciliations}
        
        # Get item names
        item_codes = list(set(item["item_code"] for item in sr_items))
        items = frappe.get_all(
            "Item",
            filters={"name": ["in", item_codes]},
            fields=["name", "item_name"]
        )
        item_name_map = {item["name"]: item.get("item_name") or item["name"] for item in items}
        
        # Calculate variances
        result = []
        for item in sr_items:
            book_qty = flt(item.get("current_qty") or 0)
            counted_qty = flt(item.get("qty") or 0)  # Use 'qty' not 'quantity'
            variance_qty = counted_qty - book_qty
            rate = flt(item.get("valuation_rate") or 0)
            variance_value = variance_qty * rate
            
            # Apply variance threshold filter
            if variance_threshold is not None and abs(variance_value) < variance_threshold:
                continue
            
            variance_percentage = (variance_qty / book_qty * 100) if book_qty != 0 else 0.0
            
            result.append({
                "item_code": item["item_code"],
                "item_name": item_name_map.get(item["item_code"], item["item_code"]),
                "warehouse": item.get("warehouse"),
                "book_qty": flt(book_qty, 2),
                "counted_qty": flt(counted_qty, 2),
                "variance_qty": flt(variance_qty, 2),
                "variance_value": flt(variance_value, 2),
                "variance_percentage": flt(variance_percentage, 2),
                "reconciliation_date": str(sr_date_map.get(item["parent"]))
            })
        
        # Sort by absolute variance_value descending
        result.sort(key=lambda x: abs(x["variance_value"]), reverse=True)
        
        return {
            "success": True,
            "data": result
        }
    
    except Exception as e:
        frappe.log_error("Inventory Variance Report Error", f"Error in inventory_variance_report: {str(e)}")
        return {
            "success": False,
            "message": f"Error generating inventory variance report: {str(e)}"
        }


@frappe.whitelist()
def inventory_adjustment_trends_report(
    company: str,
    start_date: str,
    end_date: str,
    warehouse: Optional[str] = None,
    adjustment_type: str = "all"
) -> Dict:
    """
    Inventory Adjustment Trends Report - Trends in stock adjustments over time.
    
    Args:
        company: Company name (required)
        start_date: Start date (YYYY-MM-DD, required)
        end_date: End date (YYYY-MM-DD, required)
        warehouse: Warehouse filter (optional)
        adjustment_type: Filter by increase|decrease|all (default: all)
    
    Returns:
        dict: Adjustment trends data
    """
    try:
        if not company:
            company = frappe.defaults.get_user_default("Company")
            if not company:
                return {
                    "success": False,
                    "message": "Company is required"
                }
        
        # Get stock entries with adjustment type
        filters = {
            "company": company,
            "posting_date": ["between", [start_date, end_date]],
            "docstatus": 1,
            "stock_entry_type": ["in", ["Material Receipt", "Material Issue"]]
        }
        
        if warehouse:
            filters["warehouse"] = warehouse
        
        stock_entries = frappe.get_all(
            "Stock Entry",
            filters=filters,
            fields=["name", "posting_date", "stock_entry_type"]
        )
        
        if not stock_entries:
            return {
                "success": True,
                "data": []
            }
        
        # Get stock entry details
        se_names = [se["name"] for se in stock_entries]
        se_items = frappe.get_all(
            "Stock Entry Detail",
            filters={"parent": ["in", se_names]},
            fields=["parent", "qty", "basic_rate"]
        )
        
        # Create entry map
        entry_map = {se["name"]: se for se in stock_entries}
        
        # Aggregate by period
        period_data = {}
        
        for item in se_items:
            entry = entry_map.get(item["parent"])
            if not entry:
                continue
            
            posting_date = str(entry["posting_date"])
            qty = flt(item.get("qty") or 0)
            rate = flt(item.get("basic_rate") or 0)
            value = qty * rate
            
            is_increase = entry["stock_entry_type"] == "Material Receipt"
            
            # Filter by adjustment_type
            if adjustment_type == "increase" and not is_increase:
                continue
            if adjustment_type == "decrease" and is_increase:
                continue
            
            if posting_date not in period_data:
                period_data[posting_date] = {
                    "adjustment_count": 0,
                    "total_adjusted_qty": 0.0,
                    "total_adjusted_value": 0.0,
                    "increase_count": 0,
                    "decrease_count": 0
                }
            
            period_data[posting_date]["adjustment_count"] += 1
            if is_increase:
                period_data[posting_date]["total_adjusted_qty"] += qty
                period_data[posting_date]["increase_count"] += 1
            else:
                period_data[posting_date]["total_adjusted_qty"] -= qty
                period_data[posting_date]["decrease_count"] += 1
            period_data[posting_date]["total_adjusted_value"] += value
        
        # Format results
        result = []
        for period in sorted(period_data.keys()):
            data = period_data[period]
            result.append({
                "period": period,
                "adjustment_count": data["adjustment_count"],
                "total_adjusted_qty": flt(data["total_adjusted_qty"], 2),
                "total_adjusted_value": flt(data["total_adjusted_value"], 2),
                "increase_count": data["increase_count"],
                "decrease_count": data["decrease_count"]
            })
        
        return {
            "success": True,
            "data": result
        }
    
    except Exception as e:
        frappe.log_error("Inventory Adjustment Trends Report Error", f"Error in inventory_adjustment_trends_report: {str(e)}")
        return {
            "success": False,
            "message": f"Error generating adjustment trends report: {str(e)}"
        }


@frappe.whitelist()
def inventory_transfer_efficiency_report(
    company: str,
    start_date: str,
    end_date: str,
    from_warehouse: Optional[str] = None,
    to_warehouse: Optional[str] = None
) -> Dict:
    """
    Inventory Transfer Efficiency Report - Metrics on stock transfer performance.
    
    Args:
        company: Company name (required)
        start_date: Start date (YYYY-MM-DD, required)
        end_date: End date (YYYY-MM-DD, required)
        from_warehouse: Source warehouse filter (optional)
        to_warehouse: Destination warehouse filter (optional)
    
    Returns:
        dict: Transfer efficiency metrics
    """
    try:
        if not company:
            company = frappe.defaults.get_user_default("Company")
            if not company:
                return {
                    "success": False,
                    "message": "Company is required"
                }
        
        # Get stock entries with transfer type
        filters = {
            "company": company,
            "posting_date": ["between", [start_date, end_date]],
            "docstatus": ["<", 2],  # Submitted or Draft
            "stock_entry_type": "Material Transfer"
        }
        
        stock_entries = frappe.get_all(
            "Stock Entry",
            filters=filters,
            fields=["name", "posting_date", "posting_time", "docstatus", "creation", "modified"]
        )
        
        if not stock_entries:
            return {
                "success": True,
                "data": {
                    "total_transfers": 0,
                    "completed_transfers": 0,
                    "pending_transfers": 0,
                    "cancelled_transfers": 0,
                    "average_completion_time_hours": 0.0,
                    "transfer_accuracy": 100.0,
                    "on_time_rate": 100.0
                }
            }
        
        # Get stock entry details to check accuracy
        se_names = [se["name"] for se in stock_entries]
        se_items = frappe.get_all(
            "Stock Entry Detail",
            filters={"parent": ["in", se_names]},
            fields=["parent", "qty", "transfer_qty"]
        )
        
        # Calculate metrics
        total_transfers = len(stock_entries)
        completed_transfers = len([se for se in stock_entries if se["docstatus"] == 1])
        pending_transfers = len([se for se in stock_entries if se["docstatus"] == 0])
        cancelled_transfers = len([se for se in stock_entries if se["docstatus"] == 2])
        
        # Calculate average completion time (for completed transfers)
        completion_times = []
        for se in stock_entries:
            if se["docstatus"] == 1:  # Submitted
                try:
                    creation_time = get_datetime(se["creation"])
                    posting_date = getdate(se["posting_date"])
                    posting_time_str = se.get("posting_time") or "00:00:00"
                    posting_datetime = get_datetime(f"{posting_date} {posting_time_str}")
                    if posting_datetime and creation_time:
                        time_diff = (posting_datetime - creation_time).total_seconds() / 3600  # hours
                        if time_diff > 0:  # Only count positive time differences
                            completion_times.append(time_diff)
                except Exception:
                    # Skip if datetime parsing fails
                    continue
        
        avg_completion_time = sum(completion_times) / len(completion_times) if completion_times else 0.0
        
        # Calculate transfer accuracy (qty vs transfer_qty match)
        accurate_transfers = 0
        total_items = len(se_items)
        
        item_accuracy = {}
        for item in se_items:
            qty = flt(item.get("qty") or 0)
            transfer_qty = flt(item.get("transfer_qty") or 0)
            parent = item["parent"]
            
            if parent not in item_accuracy:
                item_accuracy[parent] = {"accurate": True, "items": 0}
            
            item_accuracy[parent]["items"] += 1
            if abs(qty - transfer_qty) > 0.01:  # Allow small rounding differences
                item_accuracy[parent]["accurate"] = False
        
        accurate_transfers = len([v for v in item_accuracy.values() if v["accurate"]])
        transfer_accuracy = (accurate_transfers / total_transfers * 100) if total_transfers > 0 else 100.0
        
        # On-time rate (simplified - assumes all completed transfers are on-time)
        on_time_rate = (completed_transfers / total_transfers * 100) if total_transfers > 0 else 100.0
        
        return {
            "success": True,
            "data": {
                "total_transfers": total_transfers,
                "completed_transfers": completed_transfers,
                "pending_transfers": pending_transfers,
                "cancelled_transfers": cancelled_transfers,
                "average_completion_time_hours": flt(avg_completion_time, 2),
                "transfer_accuracy": flt(transfer_accuracy, 2),
                "on_time_rate": flt(on_time_rate, 2)
            }
        }
    
    except Exception as e:
        frappe.log_error("Inventory Transfer Efficiency Report Error", f"Error in inventory_transfer_efficiency_report: {str(e)}")
        return {
            "success": False,
            "message": f"Error generating transfer efficiency report: {str(e)}"
        }


# ==================== GRN (GOODS RECEIPT NOTE) REPORTS ====================

@frappe.whitelist()
def grn_list_report(
    company: Optional[str] = None,
    supplier: Optional[str] = None,
    purchase_order: Optional[str] = None,
    warehouse: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    status: Optional[str] = None,
    docstatus: Optional[int] = None,
    page: int = 1,
    page_size: int = 20
) -> Dict:
    """
    List GRNs (Goods Receipt Notes / Purchase Receipts) with filtering and pagination.
    
    Args:
        company: Company name (optional, defaults to user's default company)
        supplier: Supplier filter (optional)
        purchase_order: Purchase Order filter (optional)
        warehouse: Warehouse filter (optional)
        start_date: Start date filter (YYYY-MM-DD, optional)
        end_date: End date filter (YYYY-MM-DD, optional)
        status: GRN status filter (optional: Received, To Bill, Completed, etc.)
        docstatus: Document status (0=Draft, 1=Submitted, 2=Cancelled, optional)
        page: Page number for pagination (default: 1)
        page_size: Number of records per page (default: 20, max: 100)
    
    Returns:
        dict: List of GRNs with pagination metadata
    """
    try:
        # Get company if not provided
        if not company:
            company = frappe.defaults.get_user_default("Company")
        
        # Validate and set pagination
        page = max(1, int(page) if page else 1)
        page_size = min(max(1, int(page_size) if page_size else 20), 100)
        offset = (page - 1) * page_size
        
        # Build filters
        filters = {}
        
        if company:
            filters["company"] = company
        
        if supplier:
            filters["supplier"] = supplier
        
        # Note: purchase_order is in child table, handle separately
        purchase_order_filter = purchase_order
        
        if warehouse:
            filters["set_warehouse"] = warehouse
        
        if status:
            filters["status"] = status
        
        if docstatus is not None:
            filters["docstatus"] = int(docstatus)
        else:
            # Default: exclude cancelled documents
            filters["docstatus"] = ["!=", 2]
        
        # Date range filter
        if start_date and end_date:
            filters["posting_date"] = ["between", [getdate(start_date), getdate(end_date)]]
        elif start_date:
            filters["posting_date"] = [">=", getdate(start_date)]
        elif end_date:
            filters["posting_date"] = ["<=", getdate(end_date)]
        
        # Handle purchase_order filter (it's in child table, so filter parent names first)
        grn_names_to_filter = None
        if purchase_order_filter:
            # Get all GRN names that have items linked to this PO
            grn_names_to_filter = frappe.get_all(
                "Purchase Receipt Item",
                filters={"purchase_order": purchase_order_filter},
                fields=["parent"],
                pluck="parent",
                distinct=True
            )
            if not grn_names_to_filter:
                # No GRNs found for this PO, return empty result
                return {
                    "success": True,
                    "data": [],
                    "meta": {
                        "page": page,
                        "page_size": page_size,
                        "total": 0,
                        "total_pages": 0
                    }
                }
            # Add name filter to main filters
            filters["name"] = ["in", grn_names_to_filter]
        
        # Get GRNs with pagination
        grns = frappe.get_all(
            "Purchase Receipt",
            filters=filters,
            fields=[
                "name",
                "supplier",
                "supplier_name",
                "company",
                "posting_date",
                "posting_time",
                "set_warehouse",
                "grand_total",
                "status",
                "docstatus",
                "is_return",
                "per_billed",
                "per_returned"
            ],
            order_by="posting_date desc, posting_time desc",
            limit_start=offset,
            limit_page_length=page_size
        )
        
        # Get total count for pagination
        total_count = frappe.db.count("Purchase Receipt", filters=filters)
        
        # Enrich GRN data with item summary and purchase_order
        for grn in grns:
            # Get item count, total quantity, and purchase_order
            items = frappe.get_all(
                "Purchase Receipt Item",
                filters={"parent": grn["name"]},
                fields=["item_code", "qty", "rate", "amount", "purchase_order"]
            )
            
            grn["items_count"] = len(items)
            grn["total_qty"] = sum(flt(item.get("qty") or 0) for item in items)
            grn["total_amount"] = flt(grn.get("grand_total") or 0, 2)
            
            # Get purchase_order from items (usually all items have same PO, but get first non-null)
            purchase_orders = [item.get("purchase_order") for item in items if item.get("purchase_order")]
            grn["purchase_order"] = purchase_orders[0] if purchase_orders else None
            
            # Format dates
            if grn.get("posting_date"):
                grn["posting_date"] = str(grn["posting_date"])
        
        return {
            "success": True,
            "data": grns,
            "meta": {
                "page": page,
                "page_size": page_size,
                "total": total_count,
                "total_pages": (total_count + page_size - 1) // page_size if page_size > 0 else 0
            }
        }
    
    except Exception as e:
        frappe.log_error("GRN List Report Error", f"Error in grn_list_report: {str(e)}")
        return {
            "success": False,
            "message": f"Error fetching GRN list: {str(e)}"
        }


@frappe.whitelist()
def grn_detail_report(grn_no: str) -> Dict:
    """
    Get detailed information for a specific GRN (Goods Receipt Note).
    
    Args:
        grn_no: GRN number (Purchase Receipt name)
    
    Returns:
        dict: Detailed GRN information including items, PO linkage, and stock status
    """
    try:
        if not grn_no:
            return {
                "success": False,
                "message": "GRN number is required"
            }
        
        # Check if GRN exists
        if not frappe.db.exists("Purchase Receipt", grn_no):
            return {
                "success": False,
                "message": f"GRN {grn_no} not found"
            }
        
        # Get GRN document
        grn = frappe.get_doc("Purchase Receipt", grn_no)
        
        # Basic GRN information
        grn_data = {
            "grn_no": grn.name,
            "supplier": grn.supplier,
            "supplier_name": grn.supplier_name,
            "company": grn.company,
            "posting_date": str(grn.posting_date) if grn.posting_date else None,
            "posting_time": str(grn.posting_time) if grn.posting_time else None,
            "set_warehouse": grn.set_warehouse,
            "purchase_order": None,  # Will be set from items
            "status": grn.status,
            "docstatus": grn.docstatus,
            "is_return": grn.is_return,
            "grand_total": flt(grn.grand_total, 2),
            "net_total": flt(grn.net_total, 2),
            "total_qty": flt(grn.total_qty, 2),
            "per_billed": flt(grn.per_billed, 2),
            "per_returned": flt(grn.per_returned, 2),
            "items": []
        }
        
        # Get items
        for item in grn.items:
            item_data = {
                "item_code": item.item_code,
                "item_name": item.item_name,
                "description": item.description,
                "qty": flt(item.qty, 2),
                "received_qty": flt(item.received_qty, 2),
                "rejected_qty": flt(item.rejected_qty, 2),
                "rate": flt(item.rate, 2),
                "amount": flt(item.amount, 2),
                "warehouse": item.warehouse,
                "uom": item.uom,
                "purchase_order": item.purchase_order,
                "purchase_order_item": item.purchase_order_item
            }
            
            # Check if item has been applied to stock
            stock_entry = frappe.db.sql("""
                SELECT name, posting_date, docstatus
                FROM `tabStock Entry`
                WHERE stock_entry_type = 'Material Receipt'
                AND docstatus = 1
                AND EXISTS (
                    SELECT 1 FROM `tabStock Entry Detail`
                    WHERE parent = `tabStock Entry`.name
                    AND item_code = %(item_code)s
                    AND t_warehouse = %(warehouse)s
                )
                ORDER BY posting_date DESC
                LIMIT 1
            """, {
                "item_code": item.item_code,
                "warehouse": item.warehouse
            }, as_dict=True)
            
            item_data["applied_to_stock"] = len(stock_entry) > 0
            if stock_entry:
                item_data["stock_entry"] = stock_entry[0].name
                item_data["stock_entry_date"] = str(stock_entry[0].posting_date) if stock_entry[0].posting_date else None
            
            grn_data["items"].append(item_data)
        
        # Get purchase_order from items (usually all items have same PO)
        purchase_orders = [item.get("purchase_order") for item in grn_data["items"] if item.get("purchase_order")]
        if purchase_orders:
            grn_data["purchase_order"] = purchase_orders[0]  # Get first non-null PO
        
        # Get Purchase Order details if linked
        if grn_data["purchase_order"]:
            po = frappe.get_doc("Purchase Order", grn_data["purchase_order"])
            grn_data["purchase_order_details"] = {
                "po_no": po.name,
                "transaction_date": str(po.transaction_date) if po.transaction_date else None,
                "status": po.status,
                "grand_total": flt(po.grand_total, 2)
            }
        
        return {
            "success": True,
            "data": grn_data
        }
    
    except Exception as e:
        frappe.log_error("GRN Detail Report Error", f"Error in grn_detail_report: {str(e)}")
        return {
            "success": False,
            "message": f"Error fetching GRN details: {str(e)}"
        }


# P&L Report
@frappe.whitelist()
def get_profit_and_loss(**kwargs):
    """
    Custom API to get ERPNext Profit and Loss Statement.
    Handles the internal requirement for period_start_date / period_end_date.
    
    Supported parameters:
    - company (required)
    - from_date, to_date
    - periodicity ("Monthly", "Quarterly", etc.)
    - cost_center, finance_book, accumulated_values, etc.
    """
    
    frappe.log_error(
        title="P&L API - Incoming Request",
        message=f"Incoming kwargs:\n{frappe.as_json(kwargs, indent=2)}"
    )

    period_start_date = (
        kwargs.get("from_date")
        or kwargs.get("start_date")
        or None
    )
    period_end_date = (
        kwargs.get("to_date")
        or kwargs.get("end_date")
        or None
    )

    # Build filters with correct keys for financial_statements.py ===
    filters = frappe._dict({
        "company": kwargs.get("company") or frappe.defaults.get_user_default("Company"),
        "period_start_date": period_start_date,
        "period_end_date": period_end_date,
        "from_date": period_start_date,
        "to_date": period_end_date,
        "periodicity": kwargs.get("periodicity", "Monthly"),
        "finance_book": kwargs.get("finance_book"),
        "cost_center": kwargs.get("cost_center"),
        "project": kwargs.get("project"),
        "accumulated_values": int(kwargs.get("accumulated_values", 0)),
        "show_zero_rows": int(kwargs.get("show_zero_rows", 1)),
        # Optional fiscal year support (can be used instead of dates)
        "from_fiscal_year": kwargs.get("from_fiscal_year"),
        "to_fiscal_year": kwargs.get("to_fiscal_year"),
    })

    if not filters.company:
        frappe.throw(_("Company is required"))

    try:
        frappe.flags.ignore_permissions = True
        columns, data, message, chart, report_summary, skip_total_row = execute(filters)

        # print("\n=== DEBUG: Report executed successfully! ===")
        # print(f"Data rows count: {len(data) if data else 0}")
        # print(f"Net Profit/Loss summary: {report_summary}")
        
        frappe.flags.ignore_permissions = False

        response = {
            "status": "success",
            "columns": columns,
            "data": data,                   
            "message": message,
            "chart": chart,
            "report_summary": report_summary,
            "filters_used": filters
        }

        # Optional: add drill-down links if requested
        if kwargs.get("with_drilldown"):
            response["drilldown_info"] = {
                "example": "Use General Ledger report with account + date filters"
            }

        return response

    except Exception as e:
        error_msg = (
            f"P&L Report failed: {str(e)}\n"
            f"Filters used: {filters}\n"
            f"Traceback: {frappe.get_traceback()}"
        )
        frappe.log_error(title="P&L API - Execution Error", message=error_msg)        
        frappe.flags.ignore_permissions = False
        
        return {
            "status": "error",
            "message": str(e),
            "filters_attempted": filters
        }
    finally:
        # Extra safety net
        frappe.flags.ignore_permissions = False