"""
Makes a staging site behave like a server where kenya-compliance (eTIMS) is set up, so the sales
paths of the till can be tested. Safe to run again: each step is skipped when already done.

Run it from the bench's `sites` folder, with the site name in the SITE environment variable:

    cd ~/frappe-bench/sites && SITE=staging.localhost ../env/bin/python <repo>/deploy/staging/etims_test_setup.py

What it creates, and why (found by running the till's sale code on staging):
  - A Branch named "00". kenya-compliance reads the invoice's Branch NAME as the eTIMS branch id and
    refuses anything but "00" or "01".
  - The "Branch" accounting dimension, with "00" as its default. This is what puts a `branch` field on
    every invoice; kenya-compliance creates it only when eTIMS settings are first activated, so on a site
    without them every Sales Invoice fails with "'SalesInvoice' object has no attribute 'branch'".
    ERPNext adds the field on a background job: run `bench worker --burst` afterwards.
  - A default branch for the Administrator, so new documents are filled in.
  - A placeholder, active eTIMS settings record. kenya-compliance refuses to save a Sales Invoice
    without one. It is inserted straight into the database because saving it would phone KRA to
    register a device. Its address is a dead local port, so nothing is ever sent to KRA.
  - A "Walk-in Customer" in a non-group customer group (the app tries to create one and fails on a
    fresh site).

Only use this on a staging or test site. Never on a site with real data.
"""

import os

import frappe

SITE = os.environ["SITE"]
frappe.init(site=SITE, sites_path=os.getcwd())
frappe.connect()
frappe.set_user("Administrator")

company = frappe.db.get_value("Company", {}, "name", order_by="creation")
if not company:
	raise SystemExit("No company on this site yet. Run erpnext.setup.utils.before_tests first.")


def say(text):
	print(f"  {text}")


print(f"eTIMS test setup on {SITE} for company {company}")

# 1. Branch "00"
if not frappe.db.exists("Branch", "00"):
	branch = frappe.get_doc(doctype="Branch", branch="00")
	branch.flags.ignore_mandatory = True
	branch.insert(ignore_permissions=True)
	say("created Branch 00")

# 2. The Branch accounting dimension, defaulting to "00"
if not frappe.db.exists("Accounting Dimension", "Branch"):
	dimension = frappe.new_doc("Accounting Dimension")
	dimension.document_type = "Branch"
	dimension.set("dimension_defaults", [])
	dimension.append(
		"dimension_defaults",
		{
			"company": company,
			"reference_document": "Branch",
			"default_dimension": "00",
			"mandatory_for_pl": 1,
		},
	)
	dimension.save()
	say("created the Branch accounting dimension (its invoice fields are added by a background job)")

# 3. Default branch for the Administrator
frappe.defaults.set_user_default("branch", "00", user="Administrator")
frappe.defaults.set_user_default("Company", company, user="Administrator")

# 4. Placeholder active eTIMS settings, inserted without phoning KRA
environment = frappe.db.get_single_value("Navari KRA eTims Environment Identifier", "environment")
if not environment:
	frappe.db.set_single_value("Navari KRA eTims Environment Identifier", "environment", "Sandbox")
	environment = "Sandbox"
if not frappe.db.exists(
	"Navari KRA eTims Settings", {"company": company, "bhfid": "00", "vendor": "OSCU KRA"}
):
	settings = frappe.new_doc("Navari KRA eTims Settings")
	settings.update(
		{
			"company": company,
			"vendor": "OSCU KRA",
			"env": environment,
			"bhfid": "00",
			"tin": "P000000000A",
			"dvcsrlno": "STAGING-PLACEHOLDER",
			"server_url": "http://127.0.0.1:9/placeholder",
			"is_active": 1,
			"communication_key": "placeholder",
			"sales_control_unit_id": "SCU-PLACEHOLDER",
		}
	)
	settings.set_new_name()
	settings.db_insert()  # skips before_insert, which would call KRA to register the device
	say("created a placeholder eTIMS settings record (nothing is ever sent to KRA)")

# 5. Walk-in Customer
if not frappe.db.exists("Customer", {"customer_name": "Walk-in Customer"}):
	frappe.get_doc(
		doctype="Customer",
		customer_name="Walk-in Customer",
		customer_type="Company",
		customer_group="Individual",
		territory="All Territories",
	).insert(ignore_permissions=True)
	say("created Walk-in Customer")

frappe.db.commit()
print("done")
