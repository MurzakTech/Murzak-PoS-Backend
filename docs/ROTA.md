# Staff rota and clock-in

Who works when, and the hours people really worked. Two simple records and a handful of calls.

## The records

| Record | What it is |
|---|---|
| **Rota Shift** | One person's planned working time on one day (start and end). Planned by a manager or owner. |
| **Clock Entry** | The time a person really clocked in and, later, out. Written by the person themselves, or closed by a manager if they forgot. Never deleted. |

A planned shift does not run past midnight and is at most 16 hours. A night shift is planned as two shifts, one on each day. A person's shifts on one day cannot overlap (a shift that starts as another ends is fine).

## Who may do what

| Person | Plan or change the rota | See the week's rota | Clock in and out | See hours | Close a forgotten clock-in |
|---|---|---|---|---|---|
| Owner, Manager | Yes | Yes | Yes | Everyone's | Yes |
| Cashier and other staff | No | Yes (the whole business's week) | Yes, for themselves | Their own only | No |
| Another business | Refused | Refused | Refused | Refused | Refused |

Only an enabled person who belongs to the same business can be rostered.

## Clocking in

- A clock-in is matched to the shift the person is rostered for: the one that is on now, or the nearest one starting within the next 2 hours. A person can clock in up to 2 hours early.
- The entry records the minutes late. Up to 5 minutes after the start is not counted late; after that all the minutes are.
- A person with no shift around that time can still clock in; the entry simply has no planned shift and no lateness.
- A person cannot be clocked in twice. The database refuses a second open entry for the same person, so two quick taps make one entry.
- Clocking out closes the open entry. Hours are the time between the two.
- If someone forgets to clock out, `get_clock_state` marks the entry `stale` after 16 hours, and a manager closes it with the time the person really left (after the clock-in and not in the future). The entry then shows who closed it.

## Calls

All are reached as `/api/method/murzak_pos.api.rota_api.<name>` and answer `{ success, message, data }`, or `{ success: false, message, error_type }` for a refusal.

| Call | Who | What it does |
|---|---|---|
| `save_rota_shift(user, shift_date, start_time, end_time, note=None, name=None)` | Manager, owner | Plans a shift; with `name` it changes that shift. Times are "HH:MM". Refusals: `bad_date`, `bad_time`, `end_before_start`, `too_long`, `overlap`, `no_such_person`, `person_disabled`, `other_business`, `not_found`. |
| `delete_rota_shift(name)` | Manager, owner | Removes a planned shift. Clock entries already made against it keep their hours. |
| `list_rota(week_start=None)` | Anyone signed in | The week (Monday to Sunday) containing `week_start`, default this week: `shifts`, `can_plan`, and for managers `people` (who can be rostered). |
| `get_clock_state()` | Anyone signed in | `clocked_in`, the open `entry` (with `stale`), and `shifts_today` for the caller. |
| `clock_in(note=None)` | Anyone signed in | Clocks the caller in. Refusal: `already_clocked_in`. |
| `clock_out()` | Anyone signed in | Clocks the caller out. Refusal: `not_clocked_in`. |
| `close_clock_entry(name, clock_out)` | Manager, owner | Closes a forgotten entry at `clock_out` ("YYYY-MM-DD HH:MM:SS"). Refusals: `not_open`, `before_start`, `in_future`, `bad_date`, `not_found`. |
| `list_timesheet(start, end, user=None)` | Anyone signed in | Entries between two dates (at most two months) and `people`: hours, days worked, times late and minutes late, and whether they are still in. A manager sees everyone, or one person with `user`; anyone else sees only their own (`not_allowed` if they ask for another). |

## What this does not do (yet)

- No pay, overtime or leave: it records hours, it does not price them.
- No shift swaps, requests or approvals.
- No reminder when someone is late or has not clocked out.
- Clocking in does not open or close the till. A till shift and a working shift are separate.
