# P5-3 contract: the Team list

One new read-only call for Settings, Team. Nothing else on the server is added or changed. Adding a person, changing a
person and deactivating one use the staff calls that already exist (`murzak_pos.api.staff_api`), which the server already
limits to an owner.

## Why a new call

The Team page shows each person as Owner, Manager or Cashier. That kind is decided on the server from the roles a person
holds (`role_tiers.get_tier`); no existing call returns it. `staff_api.get_staff_users` returns the roles but not the
kind, and copying the rule into the browser would let the two drift apart. `list_team` returns the kind, worked out by
`team_calc.tier_for`, which reads the same two role sets as `role_tiers` (a test checks the two agree for every role).

## `murzak_pos.api.team_api.list_team`

`GET /api/method/murzak_pos.api.team_api.list_team`

| Parameter | Type | Meaning |
|---|---|---|
| `company` | text, optional | The business. Only a business the caller belongs to is accepted (`resolve_company`); left out, the caller's own is used |
| `enabled_only` | 0 or 1, optional | 1 leaves out deactivated people. Default 0 |

### Reply

```json
{
  "message": {
    "success": true,
    "data": {
      "company": "Shop A",
      "me": "amina@shop-a.test",
      "members": [
        {
          "email": "amina@shop-a.test",
          "first_name": "Amina",
          "last_name": "Otieno",
          "full_name": "Amina Otieno",
          "phone": "0712345678",
          "enabled": true,
          "tier": "owner",
          "roles": ["System Manager"],
          "created": "2026-10-01 09:00:00",
          "last_sign_in": "2026-10-16 08:12:44"
        }
      ],
      "counts": { "total": 3, "active": 3, "owners": 1, "managers": 1, "cashiers": 1 },
      "tiers": {
        "grant": { "owner": ["System Manager"], "manager": ["Sales Manager", "Purchase Manager", "Stock Manager", "Accounts Manager", "Item Manager"], "cashier": ["Sales User"] },
        "owner_roles": ["System Manager"],
        "manager_roles": ["Accounts Manager", "Accounts User", "Item Manager", "Purchase Manager", "Sales Manager", "Stock Manager"]
      }
    }
  }
}
```

- `tier` is `owner`, `manager` or `cashier`.
- `roles` are the roles the person was given; the roles Frappe gives everybody (`All`, `Guest`, `Desk User`, `Administrator`) are left out.
- Members come owners first, then managers, then cashiers; within a kind active people first, then by name.
- `counts` count active people only for the three kinds; `total` includes deactivated people.
- `tiers.grant` is `role_tiers.ROLES_TO_GRANT`: the roles to give to make someone that kind. `owner_roles` and `manager_roles` are the roles that make an owner or a manager, so the page can take them away when someone moves down a kind. The page uses them with the existing `assign_roles_to_staff` and `remove_roles_from_staff` calls; it never replaces a person's other roles.
- No password, key, API secret or token is ever in the reply.

### Who may use it

| Caller | Result |
|---|---|
| Not signed in | `AuthenticationError` "Please sign in first." |
| Cashier | `PermissionError` "Only an owner or a manager can see the team." (checked before anything is read) |
| Manager | allowed (read only) |
| Owner | allowed |

Changing the team stays owner only: `create_staff_user`, `update_staff_user`, `assign_roles_to_staff`,
`remove_roles_from_staff`, `disable_staff_user` and `enable_staff_user` all begin with `require_manager()` already.

### What it reads

`User` rows whose `custom_company` is the business (the same filter `get_staff_users` uses), and their `Has Role` rows in
one query. Fields read from `User`: `name`, `email`, `first_name`, `last_name`, `full_name`, `mobile_no`, `enabled`,
`creation`, `last_login`. It writes nothing.

### Assumptions that could not be checked here (no database)

- `User.last_login` and `User.mobile_no` exist on the site's User (they are standard Frappe fields).
- Every person of a business has `custom_company` set (the owner at company creation, staff at creation by `create_staff_user`), as `get_staff_users` assumes.
- `frappe.permissions.AUTOMATIC_ROLES` is the list of roles to hide (the same list `staff_api` uses).

## Files

`murzak_pos/api/team_calc.py` (plain functions), `murzak_pos/api/team_api.py` (the call), `murzak_pos/api/test_team_api.py`
(no-database tests), one Swahili row in `murzak_pos/translations/sw.csv`. No `EXPECTED_GUARDS` entry: the call opens no
record by its number (it lists the caller's own business).
