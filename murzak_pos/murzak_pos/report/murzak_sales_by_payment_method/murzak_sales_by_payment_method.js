// Copyright (c) 2026, Murzak POS and contributors
// For license information, please see license.txt

frappe.query_reports["Murzak Sales By Payment Method"] = {
	filters: [
		{
			"fieldname": "company",
			"label": "Business",
			"fieldtype": "Link",
			"options": "Company"
		},
		{
			"fieldname": "from_date",
			"label": "From",
			"fieldtype": "Date"
		},
		{
			"fieldname": "to_date",
			"label": "To",
			"fieldtype": "Date"
		}
	],
};
