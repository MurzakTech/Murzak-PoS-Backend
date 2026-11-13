"""
What a new shop gets when the app is installed on its site.

Frappe runs `after_migrate` only on `bench migrate`, never on `bench install-app`. The website creates each paying
shop's site with `bench new-site` then `install-app`, so without this a new shop would miss the print formats and
Swahili until the next time the server is updated. These are the same two steps `after_migrate` runs.
"""

from __future__ import annotations

from murzak_pos.api.batch_order import keep_auto_batch_pick_on, pick_earliest_expiry_first
from murzak_pos.api.currency_symbol import use_kes_symbol
from murzak_pos.api.language_api import enable_languages
from murzak_pos.api.print_api import install_print_formats
from murzak_pos.api.role_tiers import ensure_owner_role
from murzak_pos.api.scheduler_setup import ensure_scheduler_on
from murzak_pos.api.shop_timezone import use_kenya_time


def after_install() -> None:
	"""Set up the owner role, the print formats, the languages, the batch order and Kenya's time on a freshly installed shop. Safe to run again."""
	ensure_owner_role()
	install_print_formats()
	enable_languages()
	pick_earliest_expiry_first()
	keep_auto_batch_pick_on()
	use_kenya_time()
	use_kes_symbol()
	ensure_scheduler_on()
