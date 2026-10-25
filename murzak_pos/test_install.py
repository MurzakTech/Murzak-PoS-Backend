# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""Tests for what a new shop gets when the app is installed. No database is needed."""

import unittest
from unittest.mock import patch

from murzak_pos import hooks, install


class TestAfterInstall(unittest.TestCase):
	def test_the_hook_points_at_the_install_step(self):
		self.assertEqual(hooks.after_install, "murzak_pos.install.after_install")

	def test_a_new_shop_gets_the_same_steps_as_a_migrate(self):
		with (
			patch.object(install, "ensure_owner_role") as owner_role,
			patch.object(install, "ensure_online_orders_clerk_role") as clerk_role,
			patch.object(install, "install_print_formats") as formats,
			patch.object(install, "enable_languages") as languages,
			patch.object(install, "pick_earliest_expiry_first") as batch_order,
			patch.object(install, "use_kenya_time") as kenya_time,
		):
			install.after_install()
		owner_role.assert_called_once_with()
		clerk_role.assert_called_once_with()
		formats.assert_called_once_with()
		languages.assert_called_once_with()
		batch_order.assert_called_once_with()
		kenya_time.assert_called_once_with()

	def test_everything_a_migrate_sets_up_is_also_set_up_on_install(self):
		"""If a step is added to after_migrate, a new shop must get it too."""
		migrate_steps = {path.rsplit(".", 1)[1] for path in hooks.after_migrate}
		install_steps = {name for name in vars(install) if name in migrate_steps}
		self.assertEqual(install_steps, migrate_steps)
