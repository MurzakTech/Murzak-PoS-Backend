"""
What a new shop gets when the app is installed on its site.

Frappe runs `after_migrate` only on `bench migrate`, never on `bench install-app`. The website creates each paying
shop's site with `bench new-site` then `install-app`, so without this a new shop would miss the print formats and
Swahili until the next time the server is updated. These are the same two steps `after_migrate` runs.
"""

from __future__ import annotations

from murzak_pos.api.language_api import enable_languages
from murzak_pos.api.print_api import install_print_formats


def after_install() -> None:
	"""Set up the print formats and the languages on a freshly installed shop. Safe to run again."""
	install_print_formats()
	enable_languages()
