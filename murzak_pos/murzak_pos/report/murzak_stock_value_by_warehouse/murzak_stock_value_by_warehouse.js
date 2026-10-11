// Copyright (c) 2026, Murzak POS and contributors
// For license information, please see license.txt

frappe.query_reports["Murzak Stock Value By Warehouse"] = {
	filters: [
		{
			"fieldname": "company",
			"label": "Business",
			"fieldtype": "Link",
			"options": "Company"
		}
	],
};
