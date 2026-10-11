# Live screen messages (for the frontend)

The server tells a till screen that a payment or a kitchen ticket changed, the moment it happens. The message only says "refresh": the screen then reads the facts from the usual calls. Polling stays as the safety net, so a missed message costs a delay and never a wrong total.

## The message

One event name, `murzak_pos_event`, with a small JSON body. Two kinds:

```json
{ "kind": "payment", "doctype": "MPESA Transaction Log", "name": "ifbm93t695", "status": "Success",
  "reference": "T1791749578", "amount": 100, "gateway": "M-Pesa", "company": "Wind Power LLC" }

{ "kind": "kitchen", "doctype": "Kitchen Ticket", "name": "it1vmp1l7e", "status": "Ready",
  "station": "Bar", "number": 1, "company": "Wind Power LLC" }
```

Never in a message: keys, tokens, phone numbers, names, raw provider replies.

## Who receives what

| Message | Sent to |
|---|---|
| Payment reached a final answer (paid, failed, cancelled) | The user who started the payment. A payment nobody started at the till (a direct till or paybill payment) goes to the business's users |
| Kitchen ticket created or its status changed | Every enabled user of that business |

A user never receives another business's messages, and one cashier never receives another cashier's payment messages. Payments still waiting (Pending) send nothing.

## Connecting

1. Open the connection to the page's own address, not the API's address (Frappe's socket server refuses a page whose address differs from the socket's). The Nginx rules that make this work are in `deploy/nginx/realtime-proxy.conf`.
2. Start with the **polling** transport and let it upgrade to WebSocket. A browser cannot put an `Authorization` header on a WebSocket, but polling can, and the sign-in check runs only when the connection starts.
3. Listen for `murzak_pos_event`. On a `payment` message, re-read that payment. On a `kitchen` message, re-read the board.
4. While the socket is connected, poll every 15 seconds. While it is down, poll every 2 to 3 seconds as before.

## Tested on staging

| Check | Result |
|---|---|
| Simulated M-Pesa callback, event reaches the cashier who started it | Delivered 2 ms after the callback finished |
| A second signed-in user at the same time | Received nothing |
| Kitchen ticket created, then marked Ready | Both staff users received both messages |
| 50 clients connected, 30 events sent over 15 seconds | All 50 received all 30. (The plan's 10-minute run is still to do on a production-like server) |
| Socket server stopped, simulated payment callback | The payment was recorded as Success as usual (the message is only a nudge) |

One staging limit worth knowing: staging runs Frappe's single-process development web server, which dropped 12 of 50 connections that all opened in the same instant. Opened 150 ms apart, all 50 connected. Production runs several worker processes; a client should also reconnect by itself.
