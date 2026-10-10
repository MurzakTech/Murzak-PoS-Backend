import unittest

from techsavanna_pos.api.etims_optional import (
    is_etims_prevented,
    missing_core_fields,
    relax_etims_mandatory,
)


class _Flags:
    pass


class _Doc:
    """Just enough of a Frappe document for these checks."""

    def __init__(self, **values):
        self._values = values
        self.flags = _Flags()

    def get(self, field):
        return self._values.get(field)


CORE = dict(item_code="EM-SSD-0E34", item_name="SSD Storage", item_group="Products", stock_uom="Nos", custom_company="Empire Sly")


class TestEtimsOptional(unittest.TestCase):
    def test_prevented_flag_values(self):
        self.assertTrue(is_etims_prevented(1))
        self.assertTrue(is_etims_prevented("1"))
        self.assertTrue(is_etims_prevented(True))
        self.assertFalse(is_etims_prevented(0))
        self.assertFalse(is_etims_prevented(None))
        self.assertFalse(is_etims_prevented(""))

    def test_missing_core_fields(self):
        values = dict(CORE, item_group="  ", stock_uom=None)
        self.assertEqual(missing_core_fields(values.get), ["item_group", "stock_uom"])
        self.assertEqual(missing_core_fields(CORE.get), [])

    def test_relaxes_when_not_sent_to_etims_and_core_complete(self):
        doc = _Doc(**CORE, custom_prevent_etims_registration=1)
        self.assertTrue(relax_etims_mandatory(doc))
        self.assertTrue(doc.flags.ignore_mandatory)

    def test_keeps_rules_for_etims_products(self):
        doc = _Doc(**CORE, custom_prevent_etims_registration=0)
        self.assertFalse(relax_etims_mandatory(doc))
        self.assertFalse(getattr(doc.flags, "ignore_mandatory", False))

    def test_keeps_rules_when_core_detail_missing(self):
        doc = _Doc(**dict(CORE, item_name=""), custom_prevent_etims_registration=1)
        self.assertFalse(relax_etims_mandatory(doc))
        self.assertFalse(getattr(doc.flags, "ignore_mandatory", False))


if __name__ == "__main__":
    unittest.main()
