// Copyright (c) 2026, Murzak POS and contributors
// For license information, please see license.txt

frappe.query_reports["Murzak Expiry"] = {
	filters: [
		{
			"fieldname": "company",
			"label": "Business",
			"fieldtype": "Link",
			"options": "Company"
		},
		{
			"fieldname": "days",
			"label": "Within (days)",
			"fieldtype": "Int",
			"default": 30
		}
	],
};
