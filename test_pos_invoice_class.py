"""Test script to verify POS Invoice class override is working"""
import frappe

def test_pos_invoice_class():
    """Test if the custom POS Invoice class is being used"""
    # Get the controller class for POS Invoice
    from frappe.model.base_document import get_controller
    controller = get_controller("POS Invoice")
    
    print(f"POS Invoice controller class: {controller}")
    print(f"Module: {controller.__module__}")
    print(f"Name: {controller.__name__}")
    
    # Check if it's our custom class
    if "techsavanna_pos" in controller.__module__:
        print("✓ Custom POS Invoice class is being used!")
    else:
        print("✗ Custom POS Invoice class is NOT being used - using default ERPNext class")
    
    # Check if it has the on_submit method
    if hasattr(controller, "on_submit"):
        print("✓ on_submit method exists")
        import inspect
        source = inspect.getsource(controller.on_submit)
        if "super(POSInvoice" in source or "super().on_submit()" in source:
            print("✓ on_submit calls super() - stock ledger should update!")
        else:
            print("✗ on_submit does NOT call super()")
    else:
        print("✗ on_submit method not found")

if __name__ == "__main__":
    frappe.init(site="your-site-name")  # Replace with your site name
    frappe.connect()
    test_pos_invoice_class()

