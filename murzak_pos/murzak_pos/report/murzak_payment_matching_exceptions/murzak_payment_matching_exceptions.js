// Copyright (c) 2026, Murzak POS and contributors
// For license information, please see license.txt

frappe.query_reports["Murzak Payment Matching Exceptions"] = {
	filters: [
		{
			"fieldname": "company",
			"label": "Business",
			"fieldtype": "Link",
			"options": "Company"
		},
		{
			"fieldname": "days",
			"label": "Receipts older than (days)",
			"fieldtype": "Int",
			"default": 3
		}
	],
};
