# Copyright (c) 2026, Murzak POS and contributors
# For license information, please see license.txt

from murzak_pos.api import desk_reports


def execute(filters=None):
	return desk_reports.payment_matching_exceptions(filters)
