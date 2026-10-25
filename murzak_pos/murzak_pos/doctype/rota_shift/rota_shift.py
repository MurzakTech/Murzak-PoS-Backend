# Copyright (c) 2026, Murzak POS and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class RotaShift(Document):
	"""A planned working shift for one person on one day (staff rota). Written and changed only through
	murzak_pos.api.rota_api, which checks who may plan shifts and that a person's shifts do not overlap."""
