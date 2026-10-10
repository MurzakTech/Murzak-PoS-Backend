app_name = "techsavanna_pos"
app_title = "Techsavanna POS"
app_publisher = "Stephen"
app_description = "Techsavanna POS - Standalone POS system for ERPNext"
app_email = "stephen@techsavanna.technology"
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
# `bench --site <new site> install-app techsavanna_pos` fail on every fresh
# site with InvalidRemoteException. With an org given, no network call is made.
required_apps = ["frappe/erpnext", "navariltd/kenya_compliance"]

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "techsavanna_pos",
# 		"logo": "/assets/techsavanna_pos/logo.png",
# 		"title": "Techsavanna POS",
# 		"route": "/techsavanna_pos",
# 		"has_permission": "techsavanna_pos.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/techsavanna_pos/css/techsavanna_pos.css"
# app_include_js = "/assets/techsavanna_pos/js/techsavanna_pos.js"

# include js, css files in header of web template
# web_include_css = "/assets/techsavanna_pos/css/techsavanna_pos.css"
# web_include_js = "/assets/techsavanna_pos/js/techsavanna_pos.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "techsavanna_pos/public/scss/website"

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
# app_include_icons = "techsavanna_pos/public/icons.svg"

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
# jinja = {
# 	"methods": "techsavanna_pos.utils.jinja_methods",
# 	"filters": "techsavanna_pos.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "techsavanna_pos.install.before_install"
# after_install = "techsavanna_pos.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "techsavanna_pos.uninstall.before_uninstall"
# after_uninstall = "techsavanna_pos.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "techsavanna_pos.utils.before_app_install"
# after_app_install = "techsavanna_pos.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "techsavanna_pos.utils.before_app_uninstall"
# after_app_uninstall = "techsavanna_pos.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "techsavanna_pos.notifications.get_notification_config"

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

override_doctype_class = {"POS Invoice": "techsavanna_pos.overrides.pos_invoice_class.POSInvoice"}

# Document Events
# ---------------
# Hook on document methods and events
# Note: These hooks are for techsavanna_pos standalone functionality
# No eTIMS/Slade360 integration - those are in savanna_pos

doc_events = {
	"POS Invoice": {
		"before_submit": "techsavanna_pos.overrides.pos_invoice.before_submit",
		"on_update_after_submit": "techsavanna_pos.overrides.mpesa_integration.on_pos_invoice_update",
	},
	"Sales Invoice": {
		"before_submit": "techsavanna_pos.overrides.sales_invoice.before_submit",
		"on_update_after_submit": "techsavanna_pos.overrides.mpesa_integration.on_sales_invoice_update",
	},
	"POS Profile": {"on_update": "techsavanna_pos.overrides.mpesa_integration.on_pos_profile_update"},
	# Item hooks - for POS-specific functionality (no eTIMS)
	"Item": {
		"validate": "techsavanna_pos.overrides.item.validate",
	},
	# Stock Entry hooks - for inventory management
	"Stock Entry": {
		"on_submit": "techsavanna_pos.overrides.stock_entry.on_submit",
	},
}

# Scheduled Tasks
# ---------------

scheduler_events = {
	"daily": [
		# Removes held bills (open tabs) that were abandoned more than 30 days ago
		"techsavanna_pos.api.held_sales_api.purge_old_held_sales",
		# Removes kitchen and bar tickets from more than 90 days ago
		"techsavanna_pos.api.kitchen_api.purge_old_tickets",
	],
}

# Testing
# -------

# before_tests = "techsavanna_pos.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
#     "/api/inventory/reports/summary": "api/method/techsavanna_pos.api.report.inventory_summary_report",
#     "/api/inventory/reports/movement": "techsavanna_pos.api.inventory_reports.inventory_movement_report",
#     "/api/inventory/reports/aging": "techsavanna_pos.api.inventory_reports.stock_aging_report",
#     "/api/inventory/reports/value": "techsavanna_pos.api.inventory_reports.inventory_value_report"
# }

#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "techsavanna_pos.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["techsavanna_pos.utils.before_request"]
# after_request = ["techsavanna_pos.utils.after_request"]

# Job Events
# ----------
# before_job = ["techsavanna_pos.utils.before_job"]
# after_job = ["techsavanna_pos.utils.after_job"]

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
# 	"techsavanna_pos.auth.validate"
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
