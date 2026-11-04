import unittest

from murzak_pos.api.etims_optional import (
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

    def test_an_older_product_saves_when_the_business_is_not_set_up_for_etims(self):
        doc = _Doc(**CORE, custom_prevent_etims_registration=0)
        self.assertTrue(relax_etims_mandatory(doc, company_uses_etims=lambda company: False))
        self.assertTrue(doc.flags.ignore_mandatory)

    def test_the_rules_stay_for_a_business_that_sends_to_etims(self):
        doc = _Doc(**CORE, custom_prevent_etims_registration=0)
        asked = []
        self.assertFalse(relax_etims_mandatory(doc, company_uses_etims=lambda company: asked.append(company) or True))
        self.assertEqual(asked, ["Empire Sly"])
        self.assertFalse(getattr(doc.flags, "ignore_mandatory", False))

    def test_a_missing_core_detail_is_still_reported_for_a_business_not_on_etims(self):
        doc = _Doc(**dict(CORE, item_group=""), custom_prevent_etims_registration=0)
        self.assertFalse(relax_etims_mandatory(doc, company_uses_etims=lambda company: False))


if __name__ == "__main__":
    unittest.main()


class _Row:
    def __init__(self, item_code="ITEM039", qty=1):
        self._v = {"item_code": item_code, "qty": qty}

    def get(self, field, default=None):
        return self._v.get(field, default)


class _Bill(_Doc):
    def __init__(self, rows=None, **values):
        values.setdefault("supplier", "Gilanis Wholesalers")
        values.setdefault("company", "Linda mwananchi")
        values["items"] = [_Row()] if rows is None else rows
        super().__init__(**values)

    def get(self, field, default=None):
        return self._values.get(field, default)


class TestRelaxPurchaseInvoice(unittest.TestCase):
    def relax(self, bill, uses=False, prevented=False):
        from murzak_pos.api.etims_optional import relax_purchase_invoice

        return relax_purchase_invoice(bill, lambda company: uses, lambda code: prevented)

    def test_business_without_etims_may_save_without_tax_fields(self):
        bill = _Bill()
        self.assertTrue(self.relax(bill))
        self.assertTrue(bill.flags.ignore_mandatory)

    def test_etims_business_keeps_the_tax_fields_required(self):
        bill = _Bill()
        self.assertFalse(self.relax(bill, uses=True))
        self.assertFalse(getattr(bill.flags, "ignore_mandatory", False))

    def test_etims_business_with_only_unsent_products_may_skip_them(self):
        self.assertTrue(self.relax(_Bill(), uses=True, prevented=True))

    def test_etims_business_with_one_sent_product_keeps_them_required(self):
        self.assertFalse(self.relax(_Bill(rows=[_Row("A"), _Row("B")]), uses=True, prevented=False))

    def test_a_missing_core_detail_is_still_reported(self):
        for bill in (_Bill(supplier=""), _Bill(rows=[_Row(qty=0)]), _Bill(rows=[_Row(item_code="")])):
            self.assertFalse(self.relax(bill))
            self.assertFalse(getattr(bill.flags, "ignore_mandatory", False))
