# New look on by default for every store

**Decision (owner):** the redesigned screens are the default for all stores.

## What changes

`new_look_api.get_new_look` now answers **on** for a business that has no saved choice. Before, it answered off.

| Business state | Answer before | Answer now |
|---|---|---|
| Never chose (no saved key) | off | **on** |
| An owner turned it on | on | on |
| An owner turned it off (saved as off) | off | off, and stays off |
| Saved value is empty | off | on (counted as never chosen) |

`set_new_look` is unchanged: owners only, one business at a time. Switching a business off still returns its old screens at once.
Nothing is written to any record by this change, and no migration is needed: the switch still lives in Frappe's own `DefaultValue` table.

## Why a business can still be off

- An owner switched it off in Settings. That saved choice is kept.
- The business's back end has not been updated with the new server methods. The screens ask the server and fall back to the
  current screens when the question fails.

## Rollout notes

- This takes effect for every business on the site the moment it is deployed. A cashier with a cart open will see the new look on
  the next page load; carts stay on the device (see the rollout note, step 3).
- To put one business back on the old screens: Settings, New look, off.
- Fixed numbers for the tests: no key is on; "0", "false", "no", "off" (any case, with spaces) are off; "1" and "true" are on.
