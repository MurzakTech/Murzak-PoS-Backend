# Approvals (WS8.2)

A business can ask for a second person's approval on two things: **refunds** (sales returns) and **purchase orders**
above an amount it chooses. Nothing is approved by default: a limit of 0 switches the check off.

The calls are in `murzak_pos/api/approvals_api.py`; the rules are in `approval_rules.py`.

## Calls

| Call | Who | What it does |
| --- | --- | --- |
| `get_approval_settings(company)` | System Manager, Accounts Manager | The business's two limits. |
| `save_approval_settings(refund_limit, purchase_order_limit, company)` | same | Changes the limits. |
| `list_approvals(company, status?, page_size?)` | any member of the business | Requests, newest first. |
| `approve_request(name)` | approver | Approves and submits the refund or order. |
| `reject_request(name, reason?)` | approver | Turns the request down; the draft stays. |

## How it works

1. A manager records a refund with `create_sales_return`, or a purchase order with `upsert_purchase_order(submit=1)`
   or `submit_purchase_order`.
2. If the amount is above the limit, the document is saved as a **draft** and an Approval Request is made. The reply has
   `approval_required: true` and the request name, so the screen can say "sent for approval". A refund or order at or
   below the limit is submitted as before.
3. An approver approves it. The document is submitted at that moment. The approval covers the amount the approver saw;
   if the draft is made larger afterwards it needs a new approval.
4. A document above the limit cannot be submitted any other way, either: a check on submit refuses it unless an
   approved request covers its amount. This holds for the Frappe desk and any other route.

## Who may approve

* Refunds: System Manager or Accounts Manager.
* Purchase orders: System Manager, Accounts Manager or Purchase Manager.
* Nobody approves their own request, except a System Manager (the owner), who has nobody above them.
* A manager can only decide requests of a business they belong to.

## Audit trail

Approval requests and limits are tracked records, so who asked, who decided and when appear in the audit trail under the
module **Approvals**.

## Why not the Frappe Workflow engine

A workflow applies to a whole document type for the whole site, so one business's rule would apply to every other
business. These limits are set per business, so they are checked in the app instead.

## Limits

* Only refunds recorded as Sales Invoice returns (`create_sales_return`) are covered. A POS Invoice return made
  directly in the Frappe desk is not.
