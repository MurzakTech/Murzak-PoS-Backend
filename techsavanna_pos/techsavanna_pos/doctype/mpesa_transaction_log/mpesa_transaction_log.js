// Copyright (c) 2024, Techsavanna POS and contributors
// For license information, please see license.txt

frappe.ui.form.on('MPESA Transaction Log', {
	refresh: function(frm) {
		// Make form read-only for non-system managers
		if (!frappe.user.has_role('System Manager')) {
			frm.set_read_only();
		}
		
		// Add custom button to view payment entry if linked
		if (frm.doc.payment_entry) {
			frm.add_custom_button(__('View Payment Entry'), function() {
				frappe.set_route('Form', 'Payment Entry', frm.doc.payment_entry);
			});
		}
		
		// Add custom button to view invoice if linked
		if (frm.doc.invoice) {
			frm.add_custom_button(__('View Invoice'), function() {
				frappe.set_route('Form', frm.doc.invoice_type, frm.doc.invoice);
			});
		}
	},
	
	transaction_type: function(frm) {
		// Show/hide fields based on transaction type
		if (frm.doc.transaction_type === 'STK Push') {
			frm.set_df_property('merchant_request_id', 'reqd', 0);
			frm.set_df_property('checkout_request_id', 'reqd', 1);
			frm.set_df_property('conversation_id', 'reqd', 0);
		} else if (frm.doc.transaction_type in ['B2C', 'B2B']) {
			frm.set_df_property('merchant_request_id', 'reqd', 0);
			frm.set_df_property('checkout_request_id', 'reqd', 0);
			frm.set_df_property('conversation_id', 'reqd', 1);
		}
	},
	
	invoice_type: function(frm) {
		// Update invoice field options based on invoice type
		if (frm.doc.invoice_type) {
			frm.set_df_property('invoice', 'options', frm.doc.invoice_type);
		}
	}
});

