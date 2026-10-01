"""Debug script to check why stock is not updating for POS invoices"""
import frappe

def check_invoice_stock_update(invoice_name):
    """Check if stock should be updated for an invoice"""
    print(f"\n=== Checking Invoice: {invoice_name} ===\n")
    
    # Get invoice
    if not frappe.db.exists("POS Invoice", invoice_name):
        print(f"ERROR: Invoice {invoice_name} does not exist")
        return
    
    invoice = frappe.get_doc("POS Invoice", invoice_name)
    
    print(f"Invoice Details:")
    print(f"  - Name: {invoice.name}")
    print(f"  - Docstatus: {invoice.docstatus}")
    print(f"  - Update Stock: {invoice.update_stock}")
    print(f"  - Is POS: {invoice.is_pos}")
    print(f"  - Company: {invoice.company}")
    print(f"  - Posting Date: {invoice.posting_date}")
    
    # Check items
    print(f"\nItems:")
    for item in invoice.items:
        print(f"  - Item Code: {item.item_code}")
        print(f"    Qty: {item.qty}")
        print(f"    Warehouse: {item.warehouse}")
        
        # Check if item is stock item
        is_stock_item = frappe.db.get_value("Item", item.item_code, "is_stock_item")
        print(f"    Is Stock Item: {is_stock_item}")
        
        if not is_stock_item:
            print(f"    ⚠️  WARNING: Item {item.item_code} is NOT a stock item!")
            print(f"       Stock ledger entries are only created for stock items.")
        
        # Check for stock ledger entries
        sle_count = frappe.db.count("Stock Ledger Entry", {
            "voucher_type": invoice.doctype,
            "voucher_no": invoice.name,
            "item_code": item.item_code
        })
        print(f"    Stock Ledger Entries: {sle_count}")
        
        if sle_count == 0 and is_stock_item and invoice.update_stock == 1:
            print(f"    ❌ ERROR: No stock ledger entries found!")
            print(f"       Expected entries for item {item.item_code}")
    
    # Check all stock ledger entries for this invoice
    print(f"\nAll Stock Ledger Entries for this invoice:")
    sle_list = frappe.get_all("Stock Ledger Entry", 
        filters={"voucher_type": invoice.doctype, "voucher_no": invoice.name},
        fields=["name", "item_code", "warehouse", "actual_qty", "qty_after_transaction", "voucher_type", "voucher_no"]
    )
    
    if sle_list:
        for sle in sle_list:
            print(f"  - {sle.name}: {sle.item_code} @ {sle.warehouse}, Qty: {sle.actual_qty}, Balance: {sle.qty_after_transaction}")
    else:
        print(f"  ❌ No stock ledger entries found!")
    
    # Check POS Profile
    if invoice.pos_profile:
        pos_profile = frappe.get_doc("POS Profile", invoice.pos_profile)
        print(f"\nPOS Profile: {invoice.pos_profile}")
        print(f"  - Update Stock: {pos_profile.update_stock}")
    
    print(f"\n=== End of Check ===\n")

if __name__ == "__main__":
    # Check the specific invoice
    check_invoice_stock_update("ACC-PSINV-2026-00007")

