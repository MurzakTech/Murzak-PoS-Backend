"""
Dashboard Metrics API
Provides comprehensive dashboard metrics for POS system with filtering by staff and warehouse
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt, nowdate, getdate, add_days, add_months, get_first_day, get_last_day
from frappe.query_builder import DocType, functions as fn
from pypika.terms import Case

from techsavanna_pos.api.payment_gateway_common import resolve_company
from techsavanna_pos.api.profit_calc import summarize_profit


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


def _build_base_filters(
    company: Optional[str] = None,
    warehouse: Optional[str] = None,
    staff: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> tuple:
    """Build base filters for queries."""
    filters = {}
    
    if company:
        # Refuse a company the caller does not belong to
        company = resolve_company(company)
        filters["company"] = company
    else:
        company = _get_default_company()
        if company:
            filters["company"] = company
    
    if from_date and to_date:
        filters["posting_date"] = ["between", [from_date, to_date]]
    elif from_date:
        filters["posting_date"] = [">=", from_date]
    elif to_date:
        filters["posting_date"] = ["<=", to_date]
    
    if staff:
        filters["owner"] = staff
    
    return filters, company


def _get_warehouse_filter(warehouse: Optional[str] = None) -> Dict:
    """Get warehouse filter for item-level queries."""
    warehouse_filter = {}
    if warehouse:
        warehouse_filter["warehouse"] = warehouse
    return warehouse_filter


@frappe.whitelist()
def get_dashboard_metrics(
    company: Optional[str] = None,
    warehouse: Optional[str] = None,
    staff: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    period: str = "30days",  # "30days", "month", "year", "custom"
) -> Dict:
    """
    Get comprehensive dashboard metrics with filtering by staff and warehouse.
    
    Args:
        company: Company name (optional, uses default if not provided)
        warehouse: Warehouse name to filter by (optional)
        staff: User/Staff name to filter by (optional)
        from_date: Start date for custom period (YYYY-MM-DD)
        to_date: End date for custom period (YYYY-MM-DD)
        period: Time period - "30days", "month", "year", or "custom"
    
    Returns:
        dict: Complete dashboard metrics
    """
    try:
        # Set date range based on period
        if period == "30days":
            to_date = to_date or nowdate()
            from_date = from_date or str(add_days(getdate(to_date), -30))
        elif period == "month":
            to_date = to_date or nowdate()
            from_date = from_date or str(get_first_day(to_date))
            to_date = str(get_last_day(to_date))
        elif period == "year":
            to_date = to_date or nowdate()
            from_date = from_date or str(get_first_day(to_date, "year"))
            to_date = str(get_last_day(to_date, "year"))
        # "custom" uses provided from_date and to_date
        
        filters, company = _build_base_filters(company, warehouse, staff, from_date, to_date)
        warehouse_filter = _get_warehouse_filter(warehouse)
        
        # Get all metrics
        stats = _get_sales_stats(filters, warehouse_filter, company)
        stats.update(_get_purchase_stats(filters, warehouse_filter, company))
        stats.update(_get_financial_stats(filters, company))
        stats.update(_get_additional_metrics(filters, warehouse_filter, company))
        stats.update(_get_profit_stats(filters, warehouse_filter, stats.get("totalExpense", 0.0)))
        
        # Get time series data
        sales_last_30_days = _get_daily_sales_data(filters, warehouse_filter, company)
        monthly_sales = _get_monthly_sales_data(filters, warehouse_filter, company)
        
        # Get detailed lists
        sales_due = _get_sales_due(filters, company)
        purchases_due = _get_purchases_due(filters, company)
        stock_alerts = _get_stock_alerts(warehouse_filter, company)
        pending_shipments = _get_pending_shipments(filters, company)
        
        return {
            "success": True,
            "data": {
                "stats": stats,
                "salesLast30Days": sales_last_30_days,
                "monthlySales": monthly_sales,
                "salesDue": sales_due,
                "purchasesDue": purchases_due,
                "stockAlerts": stock_alerts,
                "pendingShipments": pending_shipments,
            },
            "filters": {
                "company": company,
                "warehouse": warehouse,
                "staff": staff,
                "from_date": from_date,
                "to_date": to_date,
                "period": period,
            },
        }
    except Exception as e:
        frappe.log_error("Dashboard Metrics Error", f"Error getting dashboard metrics: {str(e)}")
        return {
            "success": False,
            "message": f"Error getting dashboard metrics: {str(e)}",
        }


def _get_sales_stats(filters: Dict, warehouse_filter: Dict, company: str) -> Dict:
    """Get sales statistics."""
    SalesInvoice = DocType("Sales Invoice")
    SalesInvoiceItem = DocType("Sales Invoice Item")
    
    # Base query for sales invoices
    base_query = (
        frappe.qb.from_(SalesInvoice)
        .where(SalesInvoice.docstatus == 1)
        .where(SalesInvoice.is_return == 0)
    )
    
    # Apply filters
    if filters.get("company"):
        base_query = base_query.where(SalesInvoice.company == filters["company"])
    if filters.get("owner"):
        base_query = base_query.where(SalesInvoice.owner == filters["owner"])
    if filters.get("posting_date"):
        if isinstance(filters["posting_date"], list):
            if filters["posting_date"][0] == ">=":
                base_query = base_query.where(SalesInvoice.posting_date >= filters["posting_date"][1])
            elif filters["posting_date"][0] == "<=":
                base_query = base_query.where(SalesInvoice.posting_date <= filters["posting_date"][1])
            elif filters["posting_date"][0] == "between":
                base_query = base_query.where(
                    SalesInvoice.posting_date.between(
                        filters["posting_date"][1][0],
                        filters["posting_date"][1][1]
                    )
                )
        else:
            base_query = base_query.where(SalesInvoice.posting_date >= filters["posting_date"])
    
    # Total Sales (gross)
    total_sales_result = (
        base_query.select(fn.Sum(SalesInvoice.grand_total).as_("total"))
        .run(as_dict=True)
    )
    total_sales = flt(total_sales_result[0].get("total")) if total_sales_result and total_sales_result[0].get("total") else 0.0
    
    # Net Sales (after returns)
    net_sales_result = (
        base_query.select(fn.Sum(SalesInvoice.base_net_total).as_("total"))
        .run(as_dict=True)
    )
    net_sales = flt(net_sales_result[0].get("total")) if net_sales_result and net_sales_result[0].get("total") else 0.0
    
    # Sales Returns
    returns_query = (
        frappe.qb.from_(SalesInvoice)
        .where(SalesInvoice.docstatus == 1)
        .where(SalesInvoice.is_return == 1)
    )
    
    if filters.get("company"):
        returns_query = returns_query.where(SalesInvoice.company == filters["company"])
    if filters.get("owner"):
        returns_query = returns_query.where(SalesInvoice.owner == filters["owner"])
    if filters.get("posting_date"):
        if isinstance(filters["posting_date"], list) and filters["posting_date"][0] == "between":
            returns_query = returns_query.where(
                SalesInvoice.posting_date.between(
                    filters["posting_date"][1][0],
                    filters["posting_date"][1][1]
                )
            )
    
    returns_result = (
        returns_query
        .select(fn.Sum(fn.Abs(SalesInvoice.grand_total)).as_("total"))
        .run(as_dict=True)
    )
    sales_returns = flt(returns_result[0].get("total")) if returns_result and returns_result[0].get("total") else 0.0
    
    # Sales Returns Count
    returns_count_result = (
        returns_query
        .select(fn.Count(SalesInvoice.name).as_("count"))
        .run(as_dict=True)
    )
    sales_returns_count = int(returns_count_result[0].get("count")) if returns_count_result and returns_count_result[0].get("count") else 0
    
    # If warehouse filter, need to join with items
    if warehouse_filter.get("warehouse"):
        # Filter by warehouse through items
        sales_with_warehouse = (
            frappe.qb.from_(SalesInvoice)
            .join(SalesInvoiceItem)
            .on(SalesInvoice.name == SalesInvoiceItem.parent)
            .where(SalesInvoice.docstatus == 1)
            .where(SalesInvoice.is_return == 0)
            .where(SalesInvoiceItem.warehouse == warehouse_filter["warehouse"])
        )
        
        if filters.get("company"):
            sales_with_warehouse = sales_with_warehouse.where(SalesInvoice.company == filters["company"])
        if filters.get("owner"):
            sales_with_warehouse = sales_with_warehouse.where(SalesInvoice.owner == filters["owner"])
        if filters.get("posting_date"):
            if isinstance(filters["posting_date"], list) and filters["posting_date"][0] == "between":
                sales_with_warehouse = sales_with_warehouse.where(
                    SalesInvoice.posting_date.between(
                        filters["posting_date"][1][0],
                        filters["posting_date"][1][1]
                    )
                )
        
        total_sales_result = (
            sales_with_warehouse
            .select(fn.Sum(SalesInvoice.grand_total).as_("total"))
            .run(as_dict=True)
        )
        total_sales = flt(total_sales_result[0].get("total")) if total_sales_result and total_sales_result[0].get("total") else 0.0
        
        net_sales_result = (
            sales_with_warehouse
            .select(fn.Sum(SalesInvoice.base_net_total).as_("total"))
            .run(as_dict=True)
        )
        net_sales = flt(net_sales_result[0].get("total")) if net_sales_result and net_sales_result[0].get("total") else 0.0
    
    return {
        "totalSales": total_sales,
        "netSales": net_sales,
        "salesReturns": sales_returns,
        "salesReturnsCount": sales_returns_count,
    }


def _get_purchase_stats(filters: Dict, warehouse_filter: Dict, company: str) -> Dict:
    """Get purchase statistics."""
    PurchaseInvoice = DocType("Purchase Invoice")
    PurchaseInvoiceItem = DocType("Purchase Invoice Item")
    
    # Base query for purchase invoices
    base_query = (
        frappe.qb.from_(PurchaseInvoice)
        .where(PurchaseInvoice.docstatus == 1)
        .where(PurchaseInvoice.is_return == 0)
    )
    
    # Apply filters
    if filters.get("company"):
        base_query = base_query.where(PurchaseInvoice.company == filters["company"])
    if filters.get("owner"):
        base_query = base_query.where(PurchaseInvoice.owner == filters["owner"])
    if filters.get("posting_date"):
        if isinstance(filters["posting_date"], list) and filters["posting_date"][0] == "between":
            base_query = base_query.where(
                PurchaseInvoice.posting_date.between(
                    filters["posting_date"][1][0],
                    filters["posting_date"][1][1]
                )
            )
    
    # Total Purchases
    total_purchases_result = (
        base_query.select(fn.Sum(PurchaseInvoice.grand_total).as_("total"))
        .run(as_dict=True)
    )
    total_purchases = flt(total_purchases_result[0].get("total")) if total_purchases_result and total_purchases_result[0].get("total") else 0.0
    
    # Net Purchases
    net_purchases_result = (
        base_query.select(fn.Sum(PurchaseInvoice.base_net_total).as_("total"))
        .run(as_dict=True)
    )
    net_purchases = flt(net_purchases_result[0].get("total")) if net_purchases_result and net_purchases_result[0].get("total") else 0.0
    
    # Purchase Returns
    returns_query = (
        frappe.qb.from_(PurchaseInvoice)
        .where(PurchaseInvoice.docstatus == 1)
        .where(PurchaseInvoice.is_return == 1)
    )
    
    if filters.get("company"):
        returns_query = returns_query.where(PurchaseInvoice.company == filters["company"])
    if filters.get("owner"):
        returns_query = returns_query.where(PurchaseInvoice.owner == filters["owner"])
    if filters.get("posting_date"):
        if isinstance(filters["posting_date"], list) and filters["posting_date"][0] == "between":
            returns_query = returns_query.where(
                PurchaseInvoice.posting_date.between(
                    filters["posting_date"][1][0],
                    filters["posting_date"][1][1]
                )
            )
    
    returns_result = (
        returns_query
        .select(fn.Sum(fn.Abs(PurchaseInvoice.grand_total)).as_("total"))
        .run(as_dict=True)
    )
    purchase_returns = flt(returns_result[0].get("total")) if returns_result and returns_result[0].get("total") else 0.0
    
    # Purchase Returns Count
    returns_count_result = (
        returns_query
        .select(fn.Count(PurchaseInvoice.name).as_("count"))
        .run(as_dict=True)
    )
    purchase_returns_count = int(returns_count_result[0].get("count")) if returns_count_result and returns_count_result[0].get("count") else 0
    
    # If warehouse filter, filter through items
    if warehouse_filter.get("warehouse"):
        purchases_with_warehouse = (
            frappe.qb.from_(PurchaseInvoice)
            .join(PurchaseInvoiceItem)
            .on(PurchaseInvoice.name == PurchaseInvoiceItem.parent)
            .where(PurchaseInvoice.docstatus == 1)
            .where(PurchaseInvoice.is_return == 0)
            .where(PurchaseInvoiceItem.warehouse == warehouse_filter["warehouse"])
        )
        
        if filters.get("company"):
            purchases_with_warehouse = purchases_with_warehouse.where(PurchaseInvoice.company == filters["company"])
        if filters.get("owner"):
            purchases_with_warehouse = purchases_with_warehouse.where(PurchaseInvoice.owner == filters["owner"])
        if filters.get("posting_date"):
            if isinstance(filters["posting_date"], list) and filters["posting_date"][0] == "between":
                purchases_with_warehouse = purchases_with_warehouse.where(
                    PurchaseInvoice.posting_date.between(
                        filters["posting_date"][1][0],
                        filters["posting_date"][1][1]
                    )
                )
        
        total_purchases_result = (
            purchases_with_warehouse
            .select(fn.Sum(PurchaseInvoice.grand_total).as_("total"))
            .run(as_dict=True)
        )
        total_purchases = flt(total_purchases_result[0].get("total")) if total_purchases_result and total_purchases_result[0].get("total") else 0.0
        
        net_purchases_result = (
            purchases_with_warehouse
            .select(fn.Sum(PurchaseInvoice.base_net_total).as_("total"))
            .run(as_dict=True)
        )
        net_purchases = flt(net_purchases_result[0].get("total")) if net_purchases_result and net_purchases_result[0].get("total") else 0.0
    
    return {
        "totalPurchases": total_purchases,
        "netPurchases": net_purchases,
        "purchaseReturns": purchase_returns,
        "purchaseReturnsCount": purchase_returns_count,
    }


def _get_financial_stats(filters: Dict, company: str) -> Dict:
    """Get financial statistics (outstanding invoices, expenses)."""
    SalesInvoice = DocType("Sales Invoice")
    PurchaseInvoice = DocType("Purchase Invoice")
    
    # Outstanding Sales Invoices
    outstanding_query = (
        frappe.qb.from_(SalesInvoice)
        .where(SalesInvoice.docstatus == 1)
        .where(SalesInvoice.outstanding_amount > 0)
    )
    
    if filters.get("company"):
        outstanding_query = outstanding_query.where(SalesInvoice.company == filters["company"])
    if filters.get("owner"):
        outstanding_query = outstanding_query.where(SalesInvoice.owner == filters["owner"])
    
    outstanding_result = (
        outstanding_query
        .select(fn.Sum(SalesInvoice.outstanding_amount).as_("total"))
        .run(as_dict=True)
    )
    invoices_due = flt(outstanding_result[0].get("total")) if outstanding_result and outstanding_result[0].get("total") else 0.0
    
    # Outstanding Invoices Count
    count_result = (
        outstanding_query
        .select(fn.Count(SalesInvoice.name).as_("count"))
        .run(as_dict=True)
    )
    invoices_due_count = int(count_result[0].get("count")) if count_result and count_result[0].get("count") else 0
    
    # Operating expenses from the general ledger (see _get_operating_expenses)
    total_expense = _get_operating_expenses(filters, company)
    
    return {
        "invoicesDue": invoices_due,
        "invoicesDueCount": invoices_due_count,
        "totalExpense": total_expense,
    }


def _get_additional_metrics(filters: Dict, warehouse_filter: Dict, company: str) -> Dict:
    """Get additional metrics such as the average transaction value."""
    SalesInvoice = DocType("Sales Invoice")
    
    # Base query
    base_query = (
        frappe.qb.from_(SalesInvoice)
        .where(SalesInvoice.docstatus == 1)
        .where(SalesInvoice.is_return == 0)
    )
    
    if filters.get("company"):
        base_query = base_query.where(SalesInvoice.company == filters["company"])
    if filters.get("owner"):
        base_query = base_query.where(SalesInvoice.owner == filters["owner"])
    if filters.get("posting_date"):
        if isinstance(filters["posting_date"], list) and filters["posting_date"][0] == "between":
            base_query = base_query.where(
                SalesInvoice.posting_date.between(
                    filters["posting_date"][1][0],
                    filters["posting_date"][1][1]
                )
            )
    
    # Average Transaction Value
    avg_result = (
        base_query
        .select(fn.Avg(SalesInvoice.grand_total).as_("avg"))
        .run(as_dict=True)
    )
    average_transaction = flt(avg_result[0].get("avg")) if avg_result and avg_result[0].get("avg") else 0.0
    
    # profitMargin comes from _get_profit_stats, which uses real costs
    return {
        "averageTransaction": round(average_transaction, 2),
    }


def _apply_date_filter(query, field, filters: Dict):
    """Apply the posting_date filter built by _build_base_filters, in any of its three forms."""
    date_filter = filters.get("posting_date")
    if not date_filter:
        return query
    if isinstance(date_filter, list):
        op, value = date_filter[0], date_filter[1]
        if op == "between":
            return query.where(field.between(value[0], value[1]))
        if op == ">=":
            return query.where(field >= value)
        if op == "<=":
            return query.where(field <= value)
        return query
    return query.where(field >= date_filter)


# Expense account types that are not operating expenses: the cost of stock sold is
# worked out separately (counting it here too would subtract it twice), and the rest
# are stock valuation and rounding entries.
NON_OPERATING_EXPENSE_TYPES = (
    "Cost of Goods Sold",
    "Stock Adjustment",
    "Expenses Included In Valuation",
    "Expenses Included In Asset Valuation",
    "Round Off",
)


def _get_operating_expenses(filters: Dict, company: Optional[str]) -> float:
    """Operating expenses for the period: everything booked to expense accounts in the
    general ledger (journal entries, supplier bills for rent or power, payments and so
    on), except the cost of stock sold and stock valuation entries.

    The previous version filtered Journal Entry Account.account_type == "Expense",
    a value ERPNext never uses ("Expense Account", "Direct Expense" and so on are), so
    expenses always came back as 0. Expenses are company-wide: they are not split by
    store or staff member.
    """
    GLEntry = DocType("GL Entry")
    Account = DocType("Account")

    excluded_accounts = []
    if company:
        excluded_accounts = [
            a for a in frappe.get_cached_value("Company", company, ["default_expense_account", "stock_adjustment_account"]) or [] if a
        ]

    query = (
        frappe.qb.from_(GLEntry)
        .join(Account)
        .on(Account.name == GLEntry.account)
        .where(GLEntry.is_cancelled == 0)
        .where(Account.root_type == "Expense")
        .where((Account.account_type.isnull()) | (Account.account_type.notin(NON_OPERATING_EXPENSE_TYPES)))
    )
    if company:
        query = query.where(GLEntry.company == company)
    if excluded_accounts:
        query = query.where(GLEntry.account.notin(excluded_accounts))
    query = _apply_date_filter(query, GLEntry.posting_date, filters)

    result = query.select(fn.Sum(GLEntry.debit - GLEntry.credit).as_("total")).run(as_dict=True)
    return flt(result[0].get("total")) if result and result[0].get("total") else 0.0


def _get_profit_stats(filters: Dict, warehouse_filter: Dict, operating_expenses: float) -> Dict:
    """Real profit for the dashboard, replacing the old fixed 70% cost assumption.

    Sales and cost come from the same submitted Sales Invoice lines (returns included,
    which reduce both), filtered by company, staff, store and period like the rest of
    the dashboard. The arithmetic and its rules are in profit_calc.summarize_profit.
    """
    SalesInvoice = DocType("Sales Invoice")
    SalesInvoiceItem = DocType("Sales Invoice Item")
    Item = DocType("Item")

    query = (
        frappe.qb.from_(SalesInvoiceItem)
        .join(SalesInvoice)
        .on((SalesInvoice.name == SalesInvoiceItem.parent) & (SalesInvoiceItem.parenttype == "Sales Invoice"))
        .left_join(Item)
        .on(Item.name == SalesInvoiceItem.item_code)
        .where(SalesInvoice.docstatus == 1)
    )
    if filters.get("company"):
        query = query.where(SalesInvoice.company == filters["company"])
    if filters.get("owner"):
        query = query.where(SalesInvoice.owner == filters["owner"])
    if warehouse_filter.get("warehouse"):
        query = query.where(SalesInvoiceItem.warehouse == warehouse_filter["warehouse"])
    query = _apply_date_filter(query, SalesInvoice.posting_date, filters)

    rows = (
        query.select(
            SalesInvoiceItem.item_code,
            SalesInvoiceItem.warehouse,
            fn.Max(Item.is_stock_item).as_("is_stock_item"),
            fn.Sum(SalesInvoiceItem.base_net_amount).as_("net_amount"),
            fn.Sum(SalesInvoiceItem.stock_qty * SalesInvoiceItem.incoming_rate).as_("recorded_cost"),
            fn.Sum(
                Case().when(fn.Coalesce(SalesInvoiceItem.incoming_rate, 0) == 0, SalesInvoiceItem.stock_qty).else_(0)
            ).as_("uncosted_qty"),
        )
        .groupby(SalesInvoiceItem.item_code, SalesInvoiceItem.warehouse)
        .run(as_dict=True)
    )

    # Where no cost was recorded, use the current valuation: the store's first, then the item's
    needs_rate = [r for r in rows if r.get("is_stock_item") and flt(r.get("uncosted_qty"))]
    if needs_rate:
        item_codes = list({r["item_code"] for r in needs_rate})
        bin_rates = {
            (b.item_code, b.warehouse): flt(b.valuation_rate)
            for b in frappe.get_all(
                "Bin",
                filters={"item_code": ["in", item_codes]},
                fields=["item_code", "warehouse", "valuation_rate"],
            )
        }
        item_rates = {
            i.name: flt(i.valuation_rate)
            for i in frappe.get_all("Item", filters={"name": ["in", item_codes]}, fields=["name", "valuation_rate"])
        }
        for r in needs_rate:
            r["fallback_rate"] = bin_rates.get((r["item_code"], r["warehouse"])) or item_rates.get(r["item_code"]) or 0.0

    return summarize_profit(rows, operating_expenses)


def _get_daily_sales_data(filters: Dict, warehouse_filter: Dict, company: str) -> List[Dict]:
    """Get daily sales data for last 30 days."""
    SalesInvoice = DocType("Sales Invoice")
    SalesInvoiceItem = DocType("Sales Invoice Item")
    
    # Get last 30 days
    if filters.get("posting_date") and isinstance(filters["posting_date"], list):
        if filters["posting_date"][0] == "between":
            end_date = getdate(filters["posting_date"][1][1])
            start_date = getdate(filters["posting_date"][1][0])
        elif filters["posting_date"][0] == "<=":
            end_date = getdate(filters["posting_date"][1])
            start_date = add_days(end_date, -30)
        else:
            end_date = nowdate()
            start_date = add_days(end_date, -30)
    else:
        end_date = nowdate()
        start_date = add_days(end_date, -30)
    
    # Base query
    if warehouse_filter.get("warehouse"):
        base_query = (
            frappe.qb.from_(SalesInvoice)
            .join(SalesInvoiceItem)
            .on(SalesInvoice.name == SalesInvoiceItem.parent)
            .where(SalesInvoice.docstatus == 1)
            .where(SalesInvoice.is_return == 0)
            .where(SalesInvoiceItem.warehouse == warehouse_filter["warehouse"])
            .where(SalesInvoice.posting_date.between(start_date, end_date))
        )
    else:
        base_query = (
            frappe.qb.from_(SalesInvoice)
            .where(SalesInvoice.docstatus == 1)
            .where(SalesInvoice.is_return == 0)
            .where(SalesInvoice.posting_date.between(start_date, end_date))
        )
    
    if filters.get("company"):
        base_query = base_query.where(SalesInvoice.company == filters["company"])
    if filters.get("owner"):
        base_query = base_query.where(SalesInvoice.owner == filters["owner"])
    
    # Get sales by date
    if warehouse_filter.get("warehouse"):
        sales_data = (
            base_query
            .select(
                SalesInvoice.posting_date.as_("date"),
                fn.Sum(SalesInvoice.grand_total).as_("sales")
            )
            .groupby(SalesInvoice.posting_date)
            .run(as_dict=True)
        )
    else:
        sales_data = (
            base_query
            .select(
                SalesInvoice.posting_date.as_("date"),
                fn.Sum(SalesInvoice.grand_total).as_("sales")
            )
            .groupby(SalesInvoice.posting_date)
            .run(as_dict=True)
        )
    
    # Get returns by date
    returns_query = (
        frappe.qb.from_(SalesInvoice)
        .where(SalesInvoice.docstatus == 1)
        .where(SalesInvoice.is_return == 1)
        .where(SalesInvoice.posting_date.between(start_date, end_date))
    )
    
    if filters.get("company"):
        returns_query = returns_query.where(SalesInvoice.company == filters["company"])
    if filters.get("owner"):
        returns_query = returns_query.where(SalesInvoice.owner == filters["owner"])
    
    returns_data = (
        returns_query
        .select(
            SalesInvoice.posting_date.as_("date"),
            fn.Sum(fn.Abs(SalesInvoice.grand_total)).as_("returns")
        )
        .groupby(SalesInvoice.posting_date)
        .run(as_dict=True)
    )
    
    # Combine data
    sales_dict = {str(item["date"]): item["sales"] for item in sales_data}
    returns_dict = {str(item["date"]): item["returns"] for item in returns_data}
    
    # Generate all dates
    result = []
    current_date = start_date
    while current_date <= end_date:
        date_str = str(current_date)
        result.append({
            "date": date_str,
            "sales": flt(sales_dict.get(date_str, 0)),
            "returns": flt(returns_dict.get(date_str, 0)),
        })
        current_date = add_days(current_date, 1)
    
    return result


def _get_monthly_sales_data(filters: Dict, warehouse_filter: Dict, company: str) -> List[Dict]:
    """Get monthly sales data."""
    SalesInvoice = DocType("Sales Invoice")
    SalesInvoiceItem = DocType("Sales Invoice Item")
    
    # Base query
    if warehouse_filter.get("warehouse"):
        base_query = (
            frappe.qb.from_(SalesInvoice)
            .join(SalesInvoiceItem)
            .on(SalesInvoice.name == SalesInvoiceItem.parent)
            .where(SalesInvoice.docstatus == 1)
            .where(SalesInvoice.is_return == 0)
            .where(SalesInvoiceItem.warehouse == warehouse_filter["warehouse"])
        )
    else:
        base_query = (
            frappe.qb.from_(SalesInvoice)
            .where(SalesInvoice.docstatus == 1)
            .where(SalesInvoice.is_return == 0)
        )
    
    if filters.get("company"):
        base_query = base_query.where(SalesInvoice.company == filters["company"])
    if filters.get("owner"):
        base_query = base_query.where(SalesInvoice.owner == filters["owner"])
    if filters.get("posting_date"):
        if isinstance(filters["posting_date"], list) and filters["posting_date"][0] == "between":
            base_query = base_query.where(
                SalesInvoice.posting_date.between(
                    filters["posting_date"][1][0],
                    filters["posting_date"][1][1]
                )
            )
    
    # Get sales by month
    if warehouse_filter.get("warehouse"):
        sales_data = (
            base_query
            .select(
                fn.DateFormat(SalesInvoice.posting_date, "%Y-%m").as_("month"),
                fn.Sum(SalesInvoice.grand_total).as_("sales"),
                fn.Sum(SalesInvoice.base_net_total).as_("net")
            )
            .groupby(fn.DateFormat(SalesInvoice.posting_date, "%Y-%m"))
            .run(as_dict=True)
        )
    else:
        sales_data = (
            base_query
            .select(
                fn.DateFormat(SalesInvoice.posting_date, "%Y-%m").as_("month"),
                fn.Sum(SalesInvoice.grand_total).as_("sales"),
                fn.Sum(SalesInvoice.base_net_total).as_("net")
            )
            .groupby(fn.DateFormat(SalesInvoice.posting_date, "%Y-%m"))
            .run(as_dict=True)
        )
    
    # Get returns by month
    returns_query = (
        frappe.qb.from_(SalesInvoice)
        .where(SalesInvoice.docstatus == 1)
        .where(SalesInvoice.is_return == 1)
    )
    
    if filters.get("company"):
        returns_query = returns_query.where(SalesInvoice.company == filters["company"])
    if filters.get("owner"):
        returns_query = returns_query.where(SalesInvoice.owner == filters["owner"])
    if filters.get("posting_date"):
        if isinstance(filters["posting_date"], list) and filters["posting_date"][0] == "between":
            returns_query = returns_query.where(
                SalesInvoice.posting_date.between(
                    filters["posting_date"][1][0],
                    filters["posting_date"][1][1]
                )
            )
    
    returns_data = (
        returns_query
        .select(
            fn.DateFormat(SalesInvoice.posting_date, "%Y-%m").as_("month"),
            fn.Sum(fn.Abs(SalesInvoice.grand_total)).as_("returns")
        )
        .groupby(fn.DateFormat(SalesInvoice.posting_date, "%Y-%m"))
        .run(as_dict=True)
    )
    
    # Combine and format
    returns_dict = {item["month"]: item["returns"] for item in returns_data}
    
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    
    result = []
    for item in sales_data:
        month_num = int(item["month"].split("-")[1]) - 1
        result.append({
            "month": month_names[month_num],
            "sales": flt(item["sales"]),
            "returns": flt(returns_dict.get(item["month"], 0)),
            "net": flt(item["net"]),
        })
    
    return result


def _get_sales_due(filters: Dict, company: str) -> List[Dict]:
    """Get outstanding sales invoices."""
    SalesInvoice = DocType("Sales Invoice")
    
    query = (
        frappe.qb.from_(SalesInvoice)
        .where(SalesInvoice.docstatus == 1)
        .where(SalesInvoice.outstanding_amount > 0)
    )
    
    if filters.get("company"):
        query = query.where(SalesInvoice.company == filters["company"])
    if filters.get("owner"):
        query = query.where(SalesInvoice.owner == filters["owner"])
    
    results = (
        query
        .select(
            SalesInvoice.name,
            SalesInvoice.customer_name.as_("customer"),
            SalesInvoice.outstanding_amount.as_("amount"),
            SalesInvoice.due_date.as_("dueDate"),
            SalesInvoice.status
        )
        .orderby(SalesInvoice.due_date)
        .limit(20)
        .run(as_dict=True)
    )
    
    # Format status
    formatted_results = []
    for item in results:
        status = "Overdue" if getdate(item.get("dueDate")) < getdate() else "Due Soon"
        formatted_results.append({
            "id": item["name"],
            "customer": item["customer"],
            "amount": flt(item["amount"]),
            "dueDate": str(item["dueDate"]) if item.get("dueDate") else None,
            "status": status,
        })
    
    return formatted_results


def _get_purchases_due(filters: Dict, company: str) -> List[Dict]:
    """Get outstanding purchase invoices."""
    PurchaseInvoice = DocType("Purchase Invoice")
    
    query = (
        frappe.qb.from_(PurchaseInvoice)
        .where(PurchaseInvoice.docstatus == 1)
        .where(PurchaseInvoice.outstanding_amount > 0)
    )
    
    if filters.get("company"):
        query = query.where(PurchaseInvoice.company == filters["company"])
    if filters.get("owner"):
        query = query.where(PurchaseInvoice.owner == filters["owner"])
    
    results = (
        query
        .select(
            PurchaseInvoice.name,
            PurchaseInvoice.supplier_name.as_("supplier"),
            PurchaseInvoice.outstanding_amount.as_("amount"),
            PurchaseInvoice.due_date.as_("dueDate"),
            PurchaseInvoice.status
        )
        .orderby(PurchaseInvoice.due_date)
        .limit(20)
        .run(as_dict=True)
    )
    
    # Format status
    formatted_results = []
    for item in results:
        status = "Overdue" if getdate(item.get("dueDate")) < getdate() else "Due Soon"
        formatted_results.append({
            "id": item["name"],
            "supplier": item["supplier"],
            "amount": flt(item["amount"]),
            "dueDate": str(item["dueDate"]) if item.get("dueDate") else None,
            "status": status,
        })
    
    return formatted_results


def _get_stock_alerts(warehouse_filter: Dict, company: str) -> List[Dict]:
    """Get stock alerts for low stock items."""
    Item = DocType("Item")
    Bin = DocType("Bin")
    
    query = (
        frappe.qb.from_(Item)
        .left_join(Bin)
        .on(Item.name == Bin.item_code)
        .where(Item.is_stock_item == 1)
        .where(Item.disabled == 0)
    )
    
    if warehouse_filter.get("warehouse"):
        query = query.where(Bin.warehouse == warehouse_filter["warehouse"])
    
    # Get items with stock below minimum
    results = (
        query
        .select(
            Item.name.as_("item_code"),
            Item.item_name.as_("product"),
            Bin.actual_qty.as_("currentStock"),
            Item.min_order_qty.as_("minStock")
        )
        .where(Bin.actual_qty < Item.min_order_qty)
        .where(Bin.actual_qty >= 0)
        .limit(20)
        .run(as_dict=True)
    )
    
    formatted_results = []
    for item in results:
        current = flt(item.get("currentStock", 0))
        minimum = flt(item.get("minStock", 0))
        
        if current == 0:
            status = "Critical"
        elif current < minimum * 0.5:
            status = "Critical"
        elif current < minimum:
            status = "Low"
        else:
            status = "Warning"
        
        formatted_results.append({
            "id": item["item_code"],
            "product": item["product"],
            "currentStock": current,
            "minStock": minimum,
            "status": status,
        })
    
    return formatted_results


def _get_pending_shipments(filters: Dict, company: str) -> List[Dict]:
    """Get pending shipments/delivery notes."""
    DeliveryNote = DocType("Delivery Note")
    
    query = (
        frappe.qb.from_(DeliveryNote)
        .where(DeliveryNote.docstatus == 1)
        .where(DeliveryNote.status.isin(["To Deliver", "To Bill"]))
    )
    
    if filters.get("company"):
        query = query.where(DeliveryNote.company == filters["company"])
    if filters.get("owner"):
        query = query.where(DeliveryNote.owner == filters["owner"])
    
    results = (
        query
        .select(
            DeliveryNote.name.as_("orderId"),
            DeliveryNote.customer_name.as_("customer"),
            DeliveryNote.status,
            DeliveryNote.posting_date.as_("estDelivery")
        )
        .orderby(DeliveryNote.posting_date)
        .limit(20)
        .run(as_dict=True)
    )
    
    # Get item counts
    DeliveryNoteItem = DocType("Delivery Note Item")
    formatted_results = []
    for item in results:
        item_count = frappe.db.get_value(
            "Delivery Note Item",
            {"parent": item["orderId"]},
            ["count(name)"],
            as_dict=True
        )
        
        status_map = {
            "To Deliver": "Processing",
            "To Bill": "Processing",
            "Completed": "Shipped",
        }
        
        formatted_results.append({
            "id": item["orderId"],
            "orderId": item["orderId"],
            "customer": item["customer"],
            "items": int(item_count) if item_count else 0,
            "status": status_map.get(item["status"], "Processing"),
            "estDelivery": str(item["estDelivery"]) if item.get("estDelivery") else None,
        })
    
    return formatted_results
