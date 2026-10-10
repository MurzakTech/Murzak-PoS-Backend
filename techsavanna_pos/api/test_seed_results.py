import unittest

from techsavanna_pos.api.seed_results import readable_seed_error, seed_result_status


class TestSeedResultStatus(unittest.TestCase):
    def test_all_saved_without_quantities_is_success(self):
        self.assertEqual(seed_result_status(created=10, failed=0, skipped=0, stock_needed=False, stock_created=False), "success")

    def test_all_saved_with_stock_recorded_is_success(self):
        self.assertEqual(seed_result_status(created=3, failed=0, skipped=0, stock_needed=True, stock_created=True), "success")

    def test_everything_already_existed_is_success(self):
        self.assertEqual(seed_result_status(created=0, failed=0, skipped=4, stock_needed=False, stock_created=False), "success")

    def test_stock_not_recorded_is_partial(self):
        self.assertEqual(seed_result_status(created=3, failed=0, skipped=0, stock_needed=True, stock_created=False), "partial_success")

    def test_some_failed_is_partial(self):
        self.assertEqual(seed_result_status(created=7, failed=3, skipped=0, stock_needed=False, stock_created=False), "partial_success")

    def test_nothing_saved_is_failed(self):
        self.assertEqual(seed_result_status(created=0, failed=10, skipped=0, stock_needed=False, stock_created=False), "failed")
        self.assertEqual(seed_result_status(created=0, failed=0, skipped=0, stock_needed=False, stock_created=False), "failed")


class TestReadableSeedError(unittest.TestCase):
    def test_mandatory_error(self):
        self.assertEqual(
            readable_seed_error("[Item, EM-ELEC-014]: custom_item_classification, stock_uom"),
            "Missing required details: item classification, stock uom.",
        )

    def test_plain_message_kept(self):
        self.assertEqual(readable_seed_error("Item Group <b>Phones</b> does not exist"), "Item Group Phones does not exist")

    def test_empty(self):
        self.assertEqual(readable_seed_error(""), "Could not be saved.")


if __name__ == "__main__":
    unittest.main()
