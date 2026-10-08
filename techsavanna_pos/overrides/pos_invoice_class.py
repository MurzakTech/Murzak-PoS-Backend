"""Override POS Invoice class to ensure parent on_submit is called"""
import frappe
from erpnext.accounts.doctype.pos_invoice.pos_invoice import POSInvoice as ERPNextPOSInvoice
from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice


class POSInvoice(ERPNextPOSInvoice):
    """
    Custom POS Invoice class that ensures parent's on_submit is called.
    
    The ERPNext POS Invoice's on_submit() method doesn't call super().on_submit(),
    which means it never runs the parent Sales Invoice's on_submit() that includes
    the stock ledger update logic. This override fixes that.
    """
    
    def on_submit(self):
        """Override on_submit to call parent method for stock ledger updates"""
        # Debug logging
        frappe.logger().info(
            f"POS Invoice {self.name}: Custom on_submit called - "
            f"update_stock={self.update_stock}, docstatus={self.docstatus}"
        )
        
        # Set default values for Sales Invoice-specific attributes that POS Invoice might not have
        # These are used in make_gle_for_rounding_adjustment() and other methods
        # Use setattr to ensure the attribute exists before calling SalesInvoice.on_submit()
        sales_invoice_only_attrs = {
            'use_company_roundoff_cost_center': 0,
            'is_consolidated': 0,
            'dont_create_loyalty_points': 0,
        }
        for attr, default_value in sales_invoice_only_attrs.items():
            if not hasattr(self, attr):
                setattr(self, attr, default_value)
        
        # Set default values for Sales Invoice Item-specific attributes that POS Invoice Item might not have
        # These are accessed when iterating through self.items in SalesInvoice.on_submit()
        sales_invoice_item_only_attrs = {
            'scio_detail': None,
            'discount_account': None,
            'incoming_rate': 0,
            'stock_uom_rate': 0,
            'sales_invoice_item': None,
            'company_total_stock': 0,
            'pos_invoice': None,
        }
        for item in self.items:
            for attr, default_value in sales_invoice_item_only_attrs.items():
                if not hasattr(item, attr):
                    setattr(item, attr, default_value)
        
        # IMPORTANT: Call SalesInvoice's on_submit() directly (not ERPNext POS Invoice's)
        # This ensures stock ledger updates and GL entries are created
        # The ERPNext POS Invoice's on_submit() doesn't call super(), so we skip it
        # and call the Sales Invoice's on_submit() directly
        frappe.logger().info(
            f"POS Invoice {self.name}: Calling SalesInvoice.on_submit() for stock ledger update"
        )
        SalesInvoice.on_submit(self)
        
        frappe.logger().info(
            f"POS Invoice {self.name}: SalesInvoice.on_submit() completed, now running POS-specific logic"
        )
        
        # Then run the POS Invoice specific logic
        # (This is what the original POS Invoice.on_submit() does)
        # Note: Some methods like make_bundle_for_sales_purchase_return() are already
        # called by parent, but calling them again is safe (idempotent)
        
        # create the loyalty point ledger entry if the customer is enrolled in any loyalty program
        if not self.is_return and self.loyalty_program:
            self.make_loyalty_point_entry()
        elif self.is_return and self.return_against and self.loyalty_program:
            against_psi_doc = frappe.get_doc("POS Invoice", self.return_against)
            against_psi_doc.delete_loyalty_point_entry()
            against_psi_doc.make_loyalty_point_entry()
        if self.redeem_loyalty_points and self.loyalty_points:
            self.apply_loyalty_points()
        self.check_phone_payments()
        self.set_status(update=True)
        
        # These are called by parent's on_submit, but we call them again for POS-specific handling
        # They're idempotent, so it's safe
        self.make_bundle_for_sales_purchase_return()
        for table_name in ["items", "packed_items"]:
            self.make_bundle_using_old_serial_batch_fields(table_name)
            self.submit_serial_batch_bundle(table_name)

        if self.coupon_code:
            from erpnext.accounts.doctype.pricing_rule.utils import update_coupon_code_count
            update_coupon_code_count(self.coupon_code, "used")
        self.clear_unallocated_mode_of_payments()

        # Newer ERPNext only: "invoice_type_in_pos" and this method do not exist on ERPNext 15,
        # where reading the field would stop every return (refund) sale with an AttributeError
        if (
            self.is_return
            and getattr(self, "invoice_type_in_pos", None) == "Sales Invoice"
            and hasattr(self, "create_and_add_consolidated_sales_invoice")
        ):
            self.create_and_add_consolidated_sales_invoice()

