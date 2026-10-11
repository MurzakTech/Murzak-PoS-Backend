"""
Sales, purchase and stock figures for the Frappe desk dashboards, cards, charts and reports.

One definition of "sales" everywhere: submitted Sales Invoices plus submitted POS Invoices, excluding
returns, and excluding any POS Invoice that was merged into a Sales Invoice at shift close (that Sales
Invoice already counts it). Each function takes the list of businesses to cover (see desk_scope.py);
an empty list gives an empty answer without touching the database.
"""

from __future__ import annotations

import frappe
from frappe.utils import flt

AMOUNT_FIELDS = {"grand_total", "base_net_total"}

# The two kinds of sale document and the extra rule that keeps merged receipts from counting twice
SALE_SOURCES = (
	("Sales Invoice", ""),
	("POS Invoice", "AND IFNULL(consolidated_invoice, '') = ''"),
)


def _amount_field(field: str) -> str:
	if field not in AMOUNT_FIELDS:
		raise ValueError("Unknown amount field")
	return field


def _sales_union(field: str) -> str:
	"""SQL listing every counted sale as (company, posting_date, amount)."""
	field = _amount_field(field)
	parts = [
		f"SELECT name, company, posting_date, {field} AS amount FROM `tab{doctype}` "
		f"WHERE docstatus = 1 AND is_return = 0 AND company IN %(companies)s "
		f"AND posting_date BETWEEN %(from_date)s AND %(to_date)s {extra}"
		for doctype, extra in SALE_SOURCES
	]
	return " UNION ALL ".join(parts)


def _run(sql: str, companies: list[str], from_date, to_date, **extra):
	if not companies:
		return []
	values = {"companies": tuple(companies), "from_date": from_date, "to_date": to_date, **extra}
	return frappe.db.sql(sql, values, as_dict=True)


def sales_total(companies, from_date, to_date, field: str = "grand_total") -> float:
	rows = _run(f"SELECT SUM(amount) AS total FROM ({_sales_union(field)}) s", companies, from_date, to_date)
	return flt(rows[0].total) if rows else 0.0


def sales_by_day(companies, from_date, to_date, field: str = "grand_total") -> list[dict]:
	return _run(
		f"SELECT posting_date AS date, SUM(amount) AS sales, COUNT(*) AS invoices FROM ({_sales_union(field)}) s "
		"GROUP BY posting_date ORDER BY posting_date",
		companies,
		from_date,
		to_date,
	)


def sales_by_month(companies, from_date, to_date, field: str = "grand_total") -> list[dict]:
	return _run(
		f"SELECT DATE_FORMAT(posting_date, '%%Y-%%m') AS month, SUM(amount) AS sales FROM ({_sales_union(field)}) s "
		"GROUP BY month ORDER BY month",
		companies,
		from_date,
		to_date,
	)


def purchases_by_month(companies, from_date, to_date) -> list[dict]:
	return _run(
		"SELECT DATE_FORMAT(posting_date, '%%Y-%%m') AS month, SUM(grand_total) AS purchases FROM `tabPurchase Invoice` "
		"WHERE docstatus = 1 AND is_return = 0 AND company IN %(companies)s "
		"AND posting_date BETWEEN %(from_date)s AND %(to_date)s GROUP BY month ORDER BY month",
		companies,
		from_date,
		to_date,
	)


def sales_by_payment_method(companies, from_date, to_date) -> list[dict]:
	sources = " UNION ALL ".join(
		f"SELECT p.mode_of_payment, p.base_amount AS amount FROM `tab{doctype}` d "
		f"JOIN `tabSales Invoice Payment` p ON p.parent = d.name AND p.parenttype = '{doctype}' "
		f"WHERE d.docstatus = 1 AND d.is_return = 0 AND d.company IN %(companies)s "
		f"AND d.posting_date BETWEEN %(from_date)s AND %(to_date)s {extra.replace('consolidated_invoice', 'd.consolidated_invoice')}"
		for doctype, extra in SALE_SOURCES
	)
	return _run(
		f"SELECT mode_of_payment, SUM(amount) AS amount FROM ({sources}) s "
		"GROUP BY mode_of_payment HAVING SUM(amount) <> 0 ORDER BY amount DESC",
		companies,
		from_date,
		to_date,
	)


def top_items(companies, from_date, to_date, limit: int = 10) -> list[dict]:
	sources = " UNION ALL ".join(
		f"SELECT i.item_code, i.item_name, i.qty, i.base_net_amount AS amount FROM `tab{doctype}` d "
		f"JOIN `tab{doctype} Item` i ON i.parent = d.name "
		f"WHERE d.docstatus = 1 AND d.is_return = 0 AND d.company IN %(companies)s "
		f"AND d.posting_date BETWEEN %(from_date)s AND %(to_date)s {extra.replace('consolidated_invoice', 'd.consolidated_invoice')}"
		for doctype, extra in SALE_SOURCES
	)
	return _run(
		f"SELECT item_code, MAX(item_name) AS item_name, SUM(qty) AS qty, SUM(amount) AS amount FROM ({sources}) s "
		"GROUP BY item_code ORDER BY amount DESC LIMIT %(limit)s",
		companies,
		from_date,
		to_date,
		limit=int(limit),
	)


def stock_value_by_warehouse(companies) -> list[dict]:
	if not companies:
		return []
	return frappe.db.sql(
		"""
		SELECT w.company, b.warehouse, SUM(b.stock_value) AS stock_value, SUM(b.actual_qty) AS qty
		FROM `tabBin` b JOIN `tabWarehouse` w ON w.name = b.warehouse
		WHERE w.company IN %(companies)s AND b.actual_qty > 0
		GROUP BY w.company, b.warehouse ORDER BY stock_value DESC
		""",
		{"companies": tuple(companies)},
		as_dict=True,
	)


def open_shift_count(companies) -> int:
	if not companies:
		return 0
	return frappe.db.count(
		"POS Opening Entry", {"company": ["in", companies], "status": "Open", "docstatus": 1}
	)


def unpaid_purchase_invoices(companies) -> tuple[int, float]:
	if not companies:
		return 0, 0.0
	rows = frappe.db.sql(
		"SELECT COUNT(*) AS n, SUM(outstanding_amount) AS amount FROM `tabPurchase Invoice` "
		"WHERE docstatus = 1 AND outstanding_amount > 0 AND company IN %(companies)s",
		{"companies": tuple(companies)},
		as_dict=True,
	)
	return int(rows[0].n or 0), flt(rows[0].amount)


def sales_by_item_group(companies, from_date, to_date) -> list[dict]:
	"""Quantity and revenue per day and item group, from the same counted sales."""
	sources = " UNION ALL ".join(
		f"SELECT d.company, d.posting_date, i.item_group, i.qty, i.base_net_amount AS amount FROM `tab{doctype}` d "
		f"JOIN `tab{doctype} Item` i ON i.parent = d.name "
		f"WHERE d.docstatus = 1 AND d.is_return = 0 AND d.company IN %(companies)s "
		f"AND d.posting_date BETWEEN %(from_date)s AND %(to_date)s {extra.replace('consolidated_invoice', 'd.consolidated_invoice')}"
		for doctype, extra in SALE_SOURCES
	)
	return _run(
		f"SELECT company, posting_date, item_group, SUM(qty) AS qty, SUM(amount) AS amount FROM ({sources}) s "
		"GROUP BY company, posting_date, item_group ORDER BY posting_date, company, item_group",
		companies,
		from_date,
		to_date,
	)


def stock_by_item(companies) -> list[dict]:
	if not companies:
		return []
	return frappe.db.sql(
		"""
		SELECT w.company, b.warehouse, b.item_code, i.item_name, b.actual_qty AS qty, b.valuation_rate, b.stock_value
		FROM `tabBin` b JOIN `tabWarehouse` w ON w.name = b.warehouse JOIN `tabItem` i ON i.name = b.item_code
		WHERE w.company IN %(companies)s AND b.actual_qty > 0
		ORDER BY w.company, b.warehouse, i.item_name
		""",
		{"companies": tuple(companies)},
		as_dict=True,
	)


def unsent_etims_rows(companies, cutoff) -> list[dict]:
	"""Invoices of these businesses not sent to KRA since before `cutoff`."""
	if not companies or not frappe.db.has_column("Sales Invoice", "custom_successfully_submitted"):
		return []
	return frappe.db.sql(
		"""
		SELECT company, name, posting_date, customer, grand_total, creation
		FROM `tabSales Invoice`
		WHERE company IN %(companies)s AND docstatus = 1 AND is_opening = 'No' AND update_stock = 1
			AND IFNULL(custom_successfully_submitted, 0) = 0 AND IFNULL(custom_defer_etims_submission, 0) = 0
			AND creation < %(cutoff)s
		ORDER BY creation
		""",
		{"companies": tuple(companies), "cutoff": cutoff},
		as_dict=True,
	)


def pos_invoice_days(company, from_date, to_date, owner=None, warehouse=None) -> list[dict]:
	"""
	Submitted POS Invoices not merged into a Sales Invoice, one row per day and sale-or-return, for the
	till dashboard (which counts Sales Invoices itself). Each row: posting_date, is_return, gross, net, count.
	"""
	conditions = [
		"d.docstatus = 1",
		"d.company = %(company)s",
		"d.posting_date BETWEEN %(from_date)s AND %(to_date)s",
		"IFNULL(d.consolidated_invoice, '') = ''",
	]
	values = {"company": company, "from_date": from_date, "to_date": to_date}
	if owner:
		conditions.append("d.owner = %(owner)s")
		values["owner"] = owner
	if warehouse:
		conditions.append(
			"EXISTS (SELECT 1 FROM `tabPOS Invoice Item` i WHERE i.parent = d.name AND i.warehouse = %(warehouse)s)"
		)
		values["warehouse"] = warehouse
	return frappe.db.sql(
		f"""
		SELECT d.posting_date, d.is_return, SUM(d.grand_total) AS gross, SUM(d.base_net_total) AS net, COUNT(*) AS n
		FROM `tabPOS Invoice` d WHERE {" AND ".join(conditions)}
		GROUP BY d.posting_date, d.is_return
		""",
		values,
		as_dict=True,
	)
