"""
Alerts for business owners: stock nearing expiry, items at reorder level, a summary when a shift
closes, and invoices that have not reached KRA. Sent by email (and optionally SMS) without anyone
having to sign in.

Each business has its own "Business Alert Settings" (who is told, which alerts, what time). Every query
is limited to that business, a failure in one business never stops the others, and each alert is sent
at most once for its day (or its closing entry): the alert is first recorded in "Business Alert Log"
under a unique key, and only the process that recorded it sends it. The log is also the list of recent
alerts the till shows.

The wording and timing rules are in alert_rules.py.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, flt, getdate, now, now_datetime

from murzak_pos.api import alert_rules as rules
from murzak_pos.api.payment_gateway_common import resolve_company

SETTINGS = "Business Alert Settings"
LOG = "Business Alert Log"
# Who may read or change a business's alert settings
SETTINGS_ROLES = {"System Manager", "Accounts Manager", "Sales Manager"}


# --- settings -------------------------------------------------------------------------------------


def ensure_alert_settings(company: str, recipients: str | None = None) -> str:
	"""The business's alert settings, made with the defaults if it has none. Safe to run again."""
	if frappe.db.exists(SETTINGS, company):
		return company
	doc = frappe.get_doc({"doctype": SETTINGS, "company": company, "email_recipients": recipients or ""})
	doc.insert(ignore_permissions=True)
	return doc.name


def _require_manager() -> None:
	if frappe.session.user == "Administrator":
		return
	if not SETTINGS_ROLES.intersection(frappe.get_roles()):
		frappe.throw(_("Only a manager can change alert settings."), frappe.PermissionError)


def _settings_dict(doc) -> dict:
	fields = (
		"company",
		"enabled",
		"email_recipients",
		"sms_numbers",
		"alert_expiry",
		"alert_low_stock",
		"alert_shift_summary",
		"alert_unsent_etims",
		"expiry_window_long",
		"expiry_window_short",
		"send_hour",
	)
	return {field: doc.get(field) for field in fields}


@frappe.whitelist()
def get_alert_settings(company: str | None = None) -> dict:
	_require_manager()
	company = resolve_company(company)
	ensure_alert_settings(company)
	return {"success": True, "data": _settings_dict(frappe.get_doc(SETTINGS, company))}


@frappe.whitelist(methods=["POST"])
def save_alert_settings(settings, company: str | None = None) -> dict:
	_require_manager()
	company = resolve_company(company)
	if isinstance(settings, str):
		import json

		settings = json.loads(settings or "{}")
	ensure_alert_settings(company)
	doc = frappe.get_doc(SETTINGS, company)
	for field in _settings_dict(doc):
		if field != "company" and field in settings:
			doc.set(field, settings[field])
	doc.save(ignore_permissions=True)
	return {"success": True, "data": _settings_dict(doc)}


@frappe.whitelist()
def list_alerts(company: str | None = None, limit: int = 30) -> dict:
	"""The business's recent alerts, newest first: the till's bell list."""
	company = resolve_company(company)
	rows = frappe.get_all(
		LOG,
		filters={"company": company},
		fields=["name", "alert_type", "reference", "sent_at", "subject", "summary"],
		order_by="creation desc",
		limit_page_length=max(1, min(cint(limit) or 30, 100)),
	)
	return {"success": True, "data": rows}


# --- sending --------------------------------------------------------------------------------------


def _claim(company: str, alert_type: str, reference: str, subject: str, summary: str) -> str | None:
	"""Record that this alert is being sent. None when it was already sent (or is being sent by another process)."""
	key = rules.dedupe_key(company, alert_type, reference)
	if frappe.db.exists(LOG, {"dedupe_key": key}):
		return None
	try:
		log = frappe.get_doc(
			{
				"doctype": LOG,
				"company": company,
				"alert_type": alert_type,
				"reference": reference,
				"sent_at": now(),
				"subject": subject[:140],
				"summary": summary[:1000],
				"dedupe_key": key,
			}
		)
		log.insert(ignore_permissions=True)
		frappe.db.commit()
		return log.name
	except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
		frappe.db.rollback()
		return None


def _send_sms(numbers: list[str], text: str) -> None:
	"""Best effort: a business that has not set up SMS on the server simply gets email only."""
	if not numbers or not frappe.db.get_single_value("SMS Settings", "sms_gateway_url"):
		return
	from frappe.core.doctype.sms_settings.sms_settings import send_sms

	from murzak_pos.api.payment_gateway_common import normalize_kenyan_phone

	receivers = []
	for number in numbers:
		try:
			receivers.append(normalize_kenyan_phone(number))
		except Exception:
			continue
	if receivers:
		send_sms(receivers, text[:300])


def _deliver(settings, alert_type: str, reference: str, message) -> bool:
	"""Send one message to the business once. `message` is (subject, html, text) or None for nothing to say."""
	if not message:
		return False
	subject, html, text = message
	log_name = _claim(settings.company, alert_type, reference, subject, text)
	if not log_name:
		return False
	try:
		recipients = rules.parse_recipients(settings.email_recipients)
		if recipients:
			frappe.sendmail(
				recipients=recipients,
				subject=subject,
				message=html,
				reference_doctype=LOG,
				reference_name=log_name,
			)
		_send_sms(rules.parse_numbers(settings.sms_numbers), text)
		frappe.db.commit()
		return True
	except Exception:
		# Forget the claim so the next run tries again, and tell the administrator
		frappe.db.rollback()
		frappe.delete_doc(LOG, log_name, ignore_permissions=True, force=True)
		frappe.db.commit()
		frappe.log_error(
			"Business alert failed", f"{alert_type} for {settings.company}\n{frappe.get_traceback()}"
		)
		return False


# --- what each alert looks at ---------------------------------------------------------------------


def expiring_stock(company: str, days: int) -> list[dict]:
	from murzak_pos.api.expiry_api import get_expiry_alerts

	return get_expiry_alerts(company=company, days=days, include_expired=1).get("data") or []


def low_stock_rows(company: str) -> list[dict]:
	"""Items whose stock in a warehouse of this business is at or below that warehouse's reorder level."""
	warehouses = frappe.get_all("Warehouse", filters={"company": company, "is_group": 0}, pluck="name")
	if not warehouses:
		return []
	return frappe.db.sql(
		"""
		SELECT b.item_code, i.item_name, b.warehouse, b.actual_qty, ir.warehouse_reorder_level AS reorder_level
		FROM `tabBin` b
		INNER JOIN `tabItem` i ON i.name = b.item_code
		INNER JOIN `tabItem Reorder` ir ON ir.parent = b.item_code AND ir.warehouse = b.warehouse
		WHERE i.disabled = 0 AND i.is_stock_item = 1
			AND ir.warehouse_reorder_level > 0
			AND b.actual_qty <= ir.warehouse_reorder_level
			AND b.warehouse IN %(warehouses)s
		ORDER BY b.actual_qty ASC, b.item_code
		""",
		{"warehouses": warehouses},
		as_dict=True,
	)


def unsent_etims(company: str) -> tuple[int, str | None]:
	"""(how many, oldest time) of this business's invoices not sent to KRA for over an hour."""
	if not frappe.db.exists("DocType", "Navari KRA eTims Settings"):
		return 0, None
	if not frappe.db.exists("Navari KRA eTims Settings", {"company": company, "is_active": 1}):
		return 0, None
	if not frappe.db.has_column("Sales Invoice", "custom_successfully_submitted"):
		return 0, None
	rows = frappe.db.sql(
		"""
		SELECT COUNT(*) AS n, MIN(creation) AS oldest FROM `tabSales Invoice`
		WHERE company = %(company)s AND docstatus = 1 AND is_opening = 'No' AND update_stock = 1
			AND IFNULL(custom_successfully_submitted, 0) = 0
			AND IFNULL(custom_defer_etims_submission, 0) = 0
			AND creation < %(cutoff)s
		""",
		{"company": company, "cutoff": add_to_date(now_datetime(), hours=-1)},
		as_dict=True,
	)
	return cint(rows[0].n), str(rows[0].oldest) if rows[0].oldest else None


def shift_data(closing) -> dict:
	"""Plain figures for a closed shift's summary."""
	modes = []
	for row in closing.get("payment_reconciliation") or []:
		expected = flt(row.expected_amount)
		modes.append(
			{
				"mode": row.mode_of_payment,
				"sales": expected - flt(row.opening_amount),
				"expected": expected,
				# A till that does not record a count leaves both the count and the difference at zero
				"counted": flt(row.closing_amount)
				if (flt(row.closing_amount) or flt(row.difference))
				else None,
				"difference": flt(row.difference),
			}
		)
	returns = [t for t in closing.get("pos_transactions") or [] if cint(t.is_return)]
	start, end = getdate(closing.period_start_date), getdate(closing.period_end_date)
	voids = frappe.db.count(
		"POS Invoice",
		{
			"company": closing.company,
			"pos_profile": closing.pos_profile,
			"docstatus": 2,
			"posting_date": ["between", [start, end]],
		},
	)
	voids += frappe.db.count(
		"Sales Invoice",
		{
			"company": closing.company,
			"pos_profile": closing.pos_profile,
			"is_pos": 1,
			"docstatus": 2,
			"posting_date": ["between", [start, end]],
		},
	)
	return {
		"company": closing.company,
		"pos_profile": closing.pos_profile,
		"user": closing.user,
		"period_start": str(closing.period_start_date),
		"period_end": str(closing.period_end_date),
		"grand_total": flt(closing.grand_total),
		"modes": modes,
		"refunds_count": len(returns),
		"refunds_total": sum(abs(flt(t.grand_total)) for t in returns),
		"voids_count": voids,
	}


# --- jobs -----------------------------------------------------------------------------------------


def _settings_for(company: str):
	return frappe.get_doc(SETTINGS, company) if frappe.db.exists(SETTINGS, company) else None


def run_for_business(settings, today=None, hour=None) -> dict:
	"""Every alert due for one business right now. Returns what was sent, for tests and the log."""
	company = settings.company
	today = str(today or getdate())
	hour = now_datetime().hour if hour is None else hour
	sent = {}
	if not cint(settings.enabled):
		return sent

	if rules.due_for_daily_alerts(hour, settings.send_hour):
		if cint(settings.alert_expiry):
			message = rules.expiry_digest(
				company,
				expiring_stock(company, cint(settings.expiry_window_long)),
				cint(settings.expiry_window_short),
				cint(settings.expiry_window_long),
			)
			sent[rules.EXPIRY] = _deliver(settings, rules.EXPIRY, today, message)
		if cint(settings.alert_low_stock):
			sent[rules.LOW_STOCK] = _deliver(
				settings, rules.LOW_STOCK, today, rules.low_stock_digest(company, low_stock_rows(company))
			)

	if cint(settings.alert_payment_matching) and rules.due_for_daily_alerts(hour, settings.send_hour):
		if getdate(today).weekday() == rules.WEEKLY_DAY:
			from murzak_pos.api.payment_matching_api import summary_counts

			sent[rules.PAYMENT_MATCHING] = _deliver(
				settings,
				rules.PAYMENT_MATCHING,
				rules.week_reference(today),
				rules.payment_matching_digest(company, summary_counts(company)),
			)

	if cint(settings.alert_unsent_etims):
		count, oldest = unsent_etims(company)
		sent[rules.UNSENT_ETIMS] = _deliver(
			settings, rules.UNSENT_ETIMS, today, rules.etims_digest(company, count, oldest)
		)
	return sent


def run_hourly_alerts() -> None:
	"""Scheduled every hour. One business failing never stops the others."""
	for company in frappe.get_all(SETTINGS, filters={"enabled": 1}, pluck="name"):
		try:
			run_for_business(frappe.get_doc(SETTINGS, company))
		except Exception:
			frappe.db.rollback()
			frappe.log_error("Business alerts run", f"{company}\n{frappe.get_traceback()}")


def queue_shift_summary(doc, method=None) -> None:
	"""When a shift's closing entry is submitted: send its summary in the background so closing stays quick."""
	try:
		settings = _settings_for(doc.company)
		if not settings or not cint(settings.enabled) or not cint(settings.alert_shift_summary):
			return
		frappe.enqueue(
			"murzak_pos.api.alerts.send_shift_summary",
			queue="short",
			enqueue_after_commit=True,
			closing_entry=doc.name,
		)
	except Exception:
		frappe.log_error("Queue shift summary", frappe.get_traceback())


def send_shift_summary(closing_entry: str) -> bool:
	closing = frappe.get_doc("POS Closing Entry", closing_entry)
	settings = _settings_for(closing.company)
	if not settings or not cint(settings.enabled) or not cint(settings.alert_shift_summary):
		return False
	return _deliver(settings, rules.SHIFT_SUMMARY, closing.name, rules.shift_summary(shift_data(closing)))
