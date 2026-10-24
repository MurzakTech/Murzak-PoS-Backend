app_name = "murzak_pos"
app_title = "Murzak POS"
app_publisher = "Stephen"
app_description = "Murzak POS - Standalone POS system for ERPNext"
app_email = "info@murzaktech.tech"
app_license = "mit"

# Apps
# ------------------

# kenya_compliance provides the "Navari KRA eTims Settings" doctype that
# api/onboarding_api.py creates during company onboarding. Left undeclared,
# this app installs cleanly and then fails at RUNTIME the first time anyone
# runs eTIMS onboarding -- which is exactly how the missing dependency went
# unnoticed. Declaring it makes a missing dependency fail at install time.
#
# Added locally 2026-09-05. This belongs upstream in
# Shavia-bit/savanna_pos_tech -- a local edit here is lost on the next pull.
# Written as "org/app" on purpose. Frappe's installer runs every entry through
# parse_app_name() BEFORE checking whether the app is already installed, and a
# bare name triggers a live GitHub lookup that only searches the frappe/ and
# erpnext/ orgs. kenya_compliance lives under navariltd, so the bare form made
# `bench --site <new site> install-app murzak_pos` fail on every fresh
# site with InvalidRemoteException. With an org given, no network call is made.
required_apps = ["frappe/erpnext", "navariltd/kenya_compliance"]

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "murzak_pos",
# 		"logo": "/assets/murzak_pos/logo.png",
# 		"title": "Murzak POS",
# 		"route": "/murzak_pos",
# 		"has_permission": "murzak_pos.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/murzak_pos/css/murzak_pos.css"
# app_include_js = "/assets/murzak_pos/js/murzak_pos.js"

# include js, css files in header of web template
# web_include_css = "/assets/murzak_pos/css/murzak_pos.css"
# web_include_js = "/assets/murzak_pos/js/murzak_pos.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "murzak_pos/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "murzak_pos/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
jinja = {"methods": ["murzak_pos.api.print_api.murzak_qr", "murzak_pos.api.print_api.murzak_company"]}

# Installation
# ------------

# Refresh the print formats (api/print_api.py) after every update
after_migrate = [
	# The role a business owner holds instead of System Manager (api/role_tiers.py)
	"murzak_pos.api.role_tiers.ensure_owner_role",
	"murzak_pos.api.print_api.install_print_formats",
	# Switch Swahili on (translations/sw.csv)
	"murzak_pos.api.language_api.enable_languages",
]

# before_install = "murzak_pos.install.before_install"
# A new shop's site only gets `install-app`, never a migrate, so it needs the same two steps (install.py)
after_install = "murzak_pos.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "murzak_pos.uninstall.before_uninstall"
# after_uninstall = "murzak_pos.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "murzak_pos.utils.before_app_install"
# after_app_install = "murzak_pos.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "murzak_pos.utils.before_app_uninstall"
# after_app_uninstall = "murzak_pos.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "murzak_pos.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

override_doctype_class = {
	"POS Invoice": "murzak_pos.overrides.pos_invoice_class.POSInvoice",
	"POS Closing Entry": "murzak_pos.overrides.pos_closing_entry_class.POSClosingEntry",
}

# Document Events
# ---------------
# Hook on document methods and events
# Note: These hooks are for murzak_pos standalone functionality
# No eTIMS/Slade360 integration - those are in savanna_pos

doc_events = {
	"POS Invoice": {
		# A cashier's limits on dates and discounts (api/cashier_limits.py)
		"validate": "murzak_pos.api.cashier_limits.enforce_on_sale",
		"before_submit": "murzak_pos.overrides.pos_invoice.before_submit",
		"on_update_after_submit": "murzak_pos.overrides.mpesa_integration.on_pos_invoice_update",
		# Stock moved: tell the business's online shop (api/ecommerce_api.py)
		"on_submit": "murzak_pos.api.ecommerce_api.queue_stock_push",
		"on_cancel": "murzak_pos.api.ecommerce_api.queue_stock_push",
	},
	"Sales Invoice": {
		"validate": "murzak_pos.api.cashier_limits.enforce_on_sale",
		"before_submit": [
			"murzak_pos.overrides.sales_invoice.before_submit",
			# A refund above the business's limit needs a manager's approval (api/approvals_api.py)
			"murzak_pos.api.approvals_api.enforce_on_submit",
		],
		"on_update_after_submit": "murzak_pos.overrides.mpesa_integration.on_sales_invoice_update",
		"on_submit": "murzak_pos.api.ecommerce_api.queue_stock_push",
		"on_cancel": "murzak_pos.api.ecommerce_api.queue_stock_push",
	},
	# An online order reserves stock, so the other shops see less of it
	"Sales Order": {
		"on_submit": "murzak_pos.api.ecommerce_api.queue_stock_push",
		"on_cancel": "murzak_pos.api.ecommerce_api.queue_stock_push",
	},
	"Purchase Order": {"before_submit": "murzak_pos.api.approvals_api.enforce_on_submit"},
	# Who belongs to which business is changed only by the app itself (api/company_binding_guard.py)
	"User": {"validate": "murzak_pos.api.company_binding_guard.guard_user_company"},
	"User Permission": {
		"validate": "murzak_pos.api.company_binding_guard.guard_user_permission",
		"on_trash": "murzak_pos.api.company_binding_guard.guard_user_permission",
	},
	"Company": {"validate": "murzak_pos.api.company_binding_guard.guard_company_owner"},
	# Live screen messages (api/realtime_events.py): payments and kitchen tickets
	"MPESA Transaction Log": {
		"on_update": [
			"murzak_pos.api.realtime_events.on_payment_update",
			# A paid M-Pesa prompt for an online order marks the order paid (api/online_orders.py)
			"murzak_pos.api.online_orders.on_payment_update",
		]
	},
	"POS Gateway Transaction": {"on_update": "murzak_pos.api.realtime_events.on_payment_update"},
	"Kitchen Ticket": {"on_update": "murzak_pos.api.realtime_events.on_kitchen_ticket_update"},
	# A summary email when a shift is closed (api/alerts.py)
	"POS Closing Entry": {"on_submit": "murzak_pos.api.alerts.queue_shift_summary"},
	"POS Profile": {
		"validate": "murzak_pos.api.vat_setup.validate_pos_profile",
		"on_update": "murzak_pos.overrides.mpesa_integration.on_pos_profile_update",
	},
	# Item hooks - for POS-specific functionality (no eTIMS)
	"Item": {
		"validate": "murzak_pos.overrides.item.validate",
	},
	# Stock Entry hooks - for inventory management
	"Stock Entry": {
		"on_submit": "murzak_pos.overrides.stock_entry.on_submit",
	},
}

# Scheduled Tasks
# ---------------

scheduler_events = {
	"hourly": [
		# Expiry, low-stock and unsent-invoice alerts for every business that has them switched on
		"murzak_pos.api.alerts.run_hourly_alerts",
		# Stock that moved without a sale (deliveries, counts) reaches each business's online shop
		"murzak_pos.api.ecommerce_api.run_hourly_stock_sync",
	],
	"daily": [
		# Removes held bills (open tabs) that were abandoned more than 30 days ago
		"murzak_pos.api.held_sales_api.purge_old_held_sales",
		# Removes kitchen and bar tickets from more than 90 days ago
		"murzak_pos.api.kitchen_api.purge_old_tickets",
	],
}

# Testing
# -------

# before_tests = "murzak_pos.install.before_tests"

# Overriding Methods
# ------------------------------
#
override_whitelisted_methods = {
	# Frappe's own call sets a person's defaults without checking them; this one refuses a company they do not
	# belong to (api/session_defaults.py)
	"frappe.core.doctype.session_default_settings.session_default_settings.set_session_default_values": (
		"murzak_pos.api.session_defaults.set_session_default_values"
	),
}
# override_whitelisted_methods = {
#     "/api/inventory/reports/summary": "api/method/murzak_pos.api.report.inventory_summary_report",
#     "/api/inventory/reports/movement": "murzak_pos.api.inventory_reports.inventory_movement_report",
#     "/api/inventory/reports/aging": "murzak_pos.api.inventory_reports.stock_aging_report",
#     "/api/inventory/reports/value": "murzak_pos.api.inventory_reports.inventory_value_report"
# }

#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "murzak_pos.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["murzak_pos.utils.before_request"]

# Adds the security headers that stop the backend's pages being shown inside other websites and make
# browsers keep using HTTPS (see murzak_pos/security_headers.py)
after_request = ["murzak_pos.security_headers.add_security_headers"]

# Job Events
# ----------
# before_job = ["murzak_pos.utils.before_job"]
# after_job = ["murzak_pos.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"murzak_pos.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []
