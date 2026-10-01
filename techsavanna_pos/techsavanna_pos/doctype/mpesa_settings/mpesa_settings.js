// Copyright (c) 2024, Techsavanna POS and contributors
// For license information, please see license.txt

frappe.ui.form.on('MPESA Settings', {
	refresh: function(frm) {
		// Hide token management section in form view (auto-managed)
		frm.set_df_property('token_management_section', 'hidden', 1);
	},
	
	shortcode_type: function(frm) {
		// Update shortcode validation based on type
		if (frm.doc.shortcode_type) {
			frm.set_df_property('shortcode', 'description', 
				frm.doc.shortcode_type === 'Paybill' 
					? 'Paybill number (6 digits)'
					: 'Till number (5-6 digits)'
			);
		}
	},
	
	environment: function(frm) {
		// Show test phone number field for sandbox
		if (frm.doc.environment === 'Sandbox') {
			frm.set_df_property('test_phone_number', 'reqd', 0);
		} else {
			frm.set_df_property('test_phone_number', 'reqd', 0);
		}
	}
});

