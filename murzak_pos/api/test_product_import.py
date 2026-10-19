# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for the product spreadsheet import (ticket P7-1): the rules (`product_import_calc`), the pictures
(`product_import_images`) and the two calls (`product_import_api`). No database and no network: every Frappe call is
replaced, a small in-memory "shop" stands in for the database, and the picture fetch is given a fake resolver and a
fake connection.
"""

import io
import sys
import types
import unittest
import zipfile
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import product_import_api as api
from murzak_pos.api import product_import_calc as rules
from murzak_pos.api import product_import_images as pictures

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32


def make_facts(**more):
	facts = {
		"company": "Shop A",
		"groups": {"drinks": "Drinks", "allitemgroups": "All Item Groups"},
		"brands": {"tusker": "Tusker"},
		"uoms": {"nos": "Nos", "kg": "Kg", "box": "Box"},
		"stores": {"stores-a": "Stores - A", "backroom-a": "Backroom - A"},
		"items": {},
		"barcode_owner": {},
	}
	facts.update(more)
	return facts


def item(code="TUSK", **more):
	found = {
		"name": code,
		"item_name": "Tusker 500ml",
		"item_group": "Drinks",
		"description": "",
		"brand": "Tusker",
		"image": "",
		"is_stock_item": 1,
		"price": 250,
		"valuation_rate": 0,
		"has_stock": False,
		"disabled": 0,
		"stock_uom": "Nos",
		"barcodes": [],
		"low_levels": {},
		"custom_company": "Shop A",
	}
	found.update(more)
	return found


def plan(rows, facts=None, **more):
	return rules.plan_import(rows, facts or make_facts(), default_store="Stores - A", **more)


def zip_of(files):
	buffer = io.BytesIO()
	with zipfile.ZipFile(buffer, "w") as archive:
		for name, data in files.items():
			archive.writestr(name, data)
	return buffer.getvalue()


class TestNames(unittest.TestCase):
	def test_capitals_and_spaces_are_ignored(self):
		self.assertEqual(rules.key_of("Soft Drinks"), rules.key_of(" soft  drinks "))
		self.assertEqual(rules.tidy("  Soft   Drinks "), "Soft Drinks")


class TestReadingOneRow(unittest.TestCase):
	def clean(self, **row):
		return rules.clean_row({"item_code": "A1", "item_name": "Tea", **row}, 2)

	def test_a_plain_row_is_understood(self):
		clean, problems = self.clean(price="1,200", cost_price="800", opening_stock="12", low_stock_level="3")
		self.assertEqual(problems, [])
		self.assertEqual(
			(clean["price"], clean["cost_price"], clean["opening_stock"], clean["low_stock_level"]),
			(1200, 800, 12, 3),
		)

	def test_each_bad_cell_is_named(self):
		_clean, problems = self.clean(
			item_code="", item_name="", price="abc", cost_price="-1", opening_stock="x", barcode="12 34"
		)
		codes = {(p["field"], p["code"]) for p in problems}
		self.assertEqual(
			codes,
			{
				("item_code", "code_required"),
				("item_name", "name_required"),
				("price", "price_bad"),
				("cost_price", "cost_bad"),
				("opening_stock", "stock_bad"),
				("barcode", "barcode_bad"),
			},
		)

	def test_the_140_character_limit(self):
		_c, problems = self.clean(item_code="x" * 141, item_name="n" * 141, category="c" * 141)
		self.assertEqual({p["code"] for p in problems}, {"code_long", "name_long", "text_long"})

	def test_yes_no_and_tax_class(self):
		clean, problems = self.clean(is_service="Yes", vat_class="Zero rated")
		self.assertEqual(problems, [])
		self.assertTrue(clean["is_service"])
		self.assertEqual(clean["vat_class"], "zero_rated")
		_c, problems = self.clean(is_service="maybe", vat_class="gold")
		self.assertEqual({p["code"] for p in problems}, {"flag_bad", "vat_bad"})

	def test_what_a_picture_cell_means(self):
		self.assertEqual(self.clean(image="https://a.test/x.jpg")[0]["image"]["kind"], "link")
		self.assertEqual(
			self.clean(image="photos\\Tea.PNG")[0]["image"], {"kind": "file", "value": "Tea.PNG"}
		)
		self.assertEqual(self.clean(image="/files/tea.jpg")[0]["image"]["kind"], "path")
		self.assertEqual(self.clean(image="/files/../private/x.jpg")[0]["image"]["kind"], "")
		self.assertEqual(self.clean(image="tea.gif")[1][0]["code"], "image_name_bad")
		self.assertEqual(self.clean(image="tea.svg")[1][0]["code"], "image_name_bad")


class TestThePlan(unittest.TestCase):
	def row(self, **more):
		return {"item_code": "NEW1", "item_name": "Soda", "price": "100", **more}

	def test_a_new_row_is_created_with_the_defaults(self):
		result = plan([self.row()])
		row = result["rows"][0]
		self.assertEqual(row["action"], "create")
		self.assertEqual(
			(row["understood"]["category"], row["understood"]["unit"]), ("All Item Groups", "Nos")
		)
		self.assertEqual(row["understood"]["vat_class"], "standard")
		self.assertEqual(
			result["counts"], {"total": 1, "create": 1, "update": 0, "unchanged": 0, "invalid": 0}
		)

	def test_a_new_row_without_a_price_says_so(self):
		row = plan([{"item_code": "N", "item_name": "Soda"}])["rows"][0]
		self.assertEqual([n["code"] for n in row["notes"]], ["no_price"])

	def test_unknown_categories_are_listed_once_whatever_the_spelling(self):
		result = plan(
			[
				self.row(item_code="A", category="Snacks"),
				self.row(item_code="B", category="snacks "),
				self.row(item_code="C", category="DRINKS"),
				self.row(item_code="D", category=" Fresh  Milk"),
				self.row(item_code="E", category="fresh milk"),
			]
		)
		self.assertEqual(result["new_categories"], ["Snacks", "Fresh Milk"])
		self.assertEqual(
			[r["understood"]["category"] for r in result["rows"]],
			["Snacks", "Snacks", "Drinks", "Fresh Milk", "Fresh Milk"],
		)

	def test_a_row_that_is_left_out_creates_no_category(self):
		result = plan([self.row(category="Ghost", price="abc")])
		self.assertEqual(result["new_categories"], [])
		self.assertEqual(result["rows"][0]["action"], "invalid")

	def test_unknown_brands_are_listed_and_matched_ignoring_case(self):
		result = plan(
			[
				self.row(brand="tusker"),
				self.row(item_code="B", brand="Bidco"),
				self.row(item_code="C", brand="BIDCO "),
			]
		)
		self.assertEqual(result["new_brands"], ["Bidco"])
		self.assertEqual(result["rows"][0]["understood"]["brand"], "Tusker")

	def test_an_unknown_unit_is_reported_not_dropped(self):
		row = plan([self.row(unit="Barrel")])["rows"][0]
		self.assertEqual(row["action"], "invalid")
		self.assertEqual(row["problems"], [{"field": "unit", "code": "unit_unknown", "value": "Barrel"}])

	def test_a_unit_is_matched_by_its_stored_name_ignoring_case(self):
		self.assertEqual(plan([self.row(unit=" KG ")])["rows"][0]["understood"]["unit"], "Kg")

	def test_an_unknown_store_is_reported(self):
		row = plan([self.row(opening_stock="5", cost_price="3", store="Moon")])["rows"][0]
		self.assertEqual(row["problems"][0]["code"], "store_unknown")

	def test_a_store_is_matched_ignoring_case_and_spaces(self):
		row = plan([self.row(opening_stock="5", cost_price="3", store="back room - a")])["rows"][0]
		self.assertEqual(row["stock"]["store"], "Backroom - A")

	def test_the_same_code_twice_in_the_sheet(self):
		rows = plan([self.row(), self.row(item_code="new1")])["rows"]
		self.assertEqual([r["action"] for r in rows], ["create", "invalid"])
		self.assertEqual(rows[1]["problems"][0], {"field": "item_code", "code": "code_twice", "value": 2})

	def test_a_code_of_another_business_is_refused(self):
		facts = make_facts(items={"new1": item("NEW1", custom_company="Shop B")})
		self.assertEqual(plan([self.row()], facts)["rows"][0]["problems"][0]["code"], "code_other_business")

	def test_barcodes(self):
		facts = make_facts(barcode_owner={"600111": "OTHER"})
		rows = plan(
			[
				self.row(barcode="600111"),
				self.row(item_code="B", barcode="700"),
				self.row(item_code="C", barcode="700"),
			],
			facts,
		)["rows"]
		self.assertEqual(rows[0]["problems"][0]["code"], "barcode_taken")
		self.assertEqual(rows[1]["action"], "create")
		self.assertEqual(rows[2]["problems"][0]["code"], "barcode_twice")

	def test_a_barcode_that_is_already_on_this_product_is_fine(self):
		facts = make_facts(items={"tusk": item()}, barcode_owner={"600111": "TUSK"})
		facts["items"]["tusk"]["barcodes"] = ["600111"]
		row = plan([{"item_code": "TUSK", "item_name": "Tusker 500ml", "barcode": "600111"}], facts)["rows"][
			0
		]
		self.assertEqual(row["action"], "unchanged")


class TestStock(unittest.TestCase):
	def row(self, **more):
		return {
			"item_code": "S1",
			"item_name": "Rice",
			"price": "200",
			"opening_stock": "10",
			"cost_price": "150",
			**more,
		}

	def test_new_product_with_stock_and_cost(self):
		result = plan([self.row()])
		self.assertEqual(result["rows"][0]["stock"], {"qty": 10, "store": "Stores - A", "cost": 150})
		self.assertEqual(result["stock"], {"rows": 1, "units": 10, "stores": ["Stores - A"]})

	def test_stock_without_a_cost_is_left_out_and_said(self):
		row = plan([self.row(cost_price="")])["rows"][0]
		self.assertIsNone(row["stock"])
		self.assertIn("stock_needs_cost", [n["code"] for n in row["notes"]])
		self.assertEqual(row["action"], "create")

	def test_a_service_takes_no_stock(self):
		row = plan([self.row(is_service="yes", low_stock_level="2")])["rows"][0]
		self.assertIsNone(row["stock"])
		self.assertTrue(row["understood"]["is_service"])
		self.assertIn("service_no_stock", [n["code"] for n in row["notes"]])
		self.assertIsNone(row["low_stock_level"])

	def test_a_product_with_stock_history_gets_no_second_opening_stock(self):
		facts = make_facts(items={"s1": item("S1", has_stock=True, price=200)})
		row = plan([self.row()], facts)["rows"][0]
		self.assertIsNone(row["stock"])
		self.assertIn("stock_has_history", [n["code"] for n in row["notes"]])

	def test_an_existing_product_with_no_history_may_get_stock(self):
		facts = make_facts(items={"s1": item("S1", price=200)})
		row = plan([self.row()], facts)["rows"][0]
		self.assertEqual(row["action"], "update")
		self.assertEqual(row["stock"]["qty"], 10)

	def test_no_store_at_all(self):
		result = rules.plan_import([self.row()], make_facts(), default_store=None)
		self.assertIn("stock_no_store", [n["code"] for n in result["rows"][0]["notes"]])

	def test_the_low_stock_level_goes_to_the_store(self):
		row = plan([self.row(low_stock_level="4")])["rows"][0]
		self.assertEqual(row["low_stock_level"], 4)


class TestUpdatingAnExistingProduct(unittest.TestCase):
	def facts(self, **more):
		return make_facts(items={"tusk": item("TUSK", **more)})

	def one(self, row, **more):
		return plan([{"item_code": "tusk", "item_name": "Tusker 500ml", **row}], self.facts(**more))["rows"][
			0
		]

	def test_a_row_that_matches_changes_nothing(self):
		row = self.one({"price": "250", "category": "drinks", "brand": "TUSKER"})
		self.assertEqual((row["action"], row["changes"]), ("unchanged", []))
		self.assertEqual(row["item_code"], "TUSK")

	def test_only_filled_cells_change(self):
		row = self.one({"price": "300", "description": "Cold"})
		self.assertEqual(row["action"], "update")
		self.assertEqual(
			[(c["field"], c["from"], c["to"]) for c in row["changes"]],
			[("description", "", "Cold"), ("price", 250, 300)],
		)

	def test_blank_cells_never_overwrite(self):
		row = self.one({"price": "", "category": "", "brand": "", "description": ""}, description="Old text")
		self.assertEqual(row["action"], "unchanged")

	def test_the_unit_of_an_existing_product_is_kept(self):
		row = self.one({"unit": "Kg"})
		self.assertEqual(row["action"], "unchanged")
		self.assertEqual(row["understood"]["unit"], "Nos")
		self.assertEqual(row["notes"][0], {"code": "unit_kept", "value": "Nos"})

	def test_an_existing_picture_is_kept(self):
		row = self.one({"image": "https://pics.test/a.jpg"}, image="/files/old.jpg")
		self.assertEqual(row["action"], "unchanged")
		self.assertEqual(row["notes"][0]["code"], "image_kept")
		self.assertIsNone(row["picture"])

	def test_a_new_picture_is_added(self):
		row = self.one({"image": "https://pics.test/a.jpg"})
		self.assertEqual(row["action"], "update")
		self.assertEqual(row["picture"]["kind"], "link")

	def test_cost_is_not_changed_when_the_product_has_stock(self):
		row = self.one({"cost_price": "99"}, has_stock=True, valuation_rate=80)
		self.assertEqual(row["action"], "unchanged")
		self.assertEqual(row["notes"][0]["code"], "cost_kept")

	def test_a_switched_off_product_is_updated_and_flagged(self):
		row = self.one({"price": "999"}, disabled=1)
		self.assertEqual(row["action"], "update")
		self.assertEqual(row["notes"][0]["code"], "disabled_item")

	def test_whether_it_is_a_service_is_not_changed(self):
		row = self.one({"is_service": "yes"})
		self.assertFalse(row["understood"]["is_service"])
		self.assertEqual(row["notes"][0]["code"], "type_kept")

	def test_low_stock_level_changes_only_when_different(self):
		same = self.one({"low_stock_level": "5"}, low_levels={"Stores - A": 5})
		self.assertEqual(same["action"], "unchanged")
		different = self.one({"low_stock_level": "6"}, low_levels={"Stores - A": 5})
		self.assertEqual(different["changes"][0]["to"], 6)

	def test_a_second_barcode_is_added(self):
		row = self.one({"barcode": "999"}, barcodes=["111"])
		self.assertEqual(row["changes"][0]["field"], "barcode")


class TestThePicturesInTheSheet(unittest.TestCase):
	def row(self, **more):
		return {"item_code": "P1", "item_name": "Pic", "price": "1", **more}

	def test_a_file_name_without_a_zip(self):
		row = plan([self.row(image="p.jpg")])["rows"][0]
		self.assertEqual(row["action"], "create")
		self.assertEqual(row["notes"][-1]["code"], "image_no_zip")
		self.assertIsNone(row["picture"])

	def test_a_file_in_the_zip_matches_ignoring_case_and_spaces(self):
		row = plan([self.row(image="My Photo.JPG")], photos={"myphoto.jpg": None})["rows"][0]
		self.assertEqual(row["picture"]["kind"], "file")

	def test_a_missing_or_bad_file_does_not_stop_the_product(self):
		rows = plan(
			[self.row(image="gone.jpg"), self.row(item_code="P2", image="bad.png")],
			photos={"bad.png": "image_type"},
		)["rows"]
		self.assertEqual([r["action"] for r in rows], ["create", "create"])
		self.assertEqual(rows[0]["notes"][-1]["code"], "image_missing")
		self.assertEqual(rows[1]["notes"][-1]["code"], "image_file_bad")

	def test_a_refused_link_does_not_stop_the_product(self):
		def refuse(url):
			raise pictures.ImageProblem("link_refused")

		row = plan([self.row(image="http://127.0.0.1/x.png")], link_check=refuse)["rows"][0]
		self.assertEqual(row["action"], "create")
		self.assertEqual(
			row["notes"][-1],
			{"code": "image_link_bad", "value": {"name": "http://127.0.0.1/x.png", "error": "link_refused"}},
		)


class TestPictureBytes(unittest.TestCase):
	def test_the_type_is_read_from_the_bytes_not_the_name(self):
		self.assertEqual(pictures.sniff_image(PNG), "png")
		self.assertEqual(pictures.sniff_image(JPG), "jpg")
		self.assertEqual(pictures.sniff_image(WEBP), "webp")
		self.assertIsNone(pictures.sniff_image(b"<svg onload=alert(1)>"))
		self.assertIsNone(pictures.sniff_image(b"GIF89a"))
		self.assertIsNone(pictures.sniff_image(b"MZ\x90\x00"))

	def test_the_size_limit(self):
		self.assertEqual(pictures.check_image_bytes(PNG + b"\x00" * (2 * 1024 * 1024 - len(PNG))), "png")
		with self.assertRaises(pictures.ImageProblem) as raised:
			pictures.check_image_bytes(PNG + b"\x00" * (2 * 1024 * 1024))
		self.assertEqual(raised.exception.code, "image_big")
		with self.assertRaises(pictures.ImageProblem):
			pictures.check_image_bytes(b"")

	def test_the_saved_file_name(self):
		self.assertEqual(pictures.safe_file_name("ab/../c d", "jpg"), "ab-c-d.jpg")
		self.assertEqual(pictures.safe_file_name("", "png"), "product.png")


class TestTheZip(unittest.TestCase):
	def test_pictures_are_found_by_name_ignoring_case_and_folders(self):
		photos, report = pictures.read_zip(
			zip_of(
				{"photos/Tea Bag.JPG": JPG, "rice.png": PNG, "notes.txt": b"hi", "__MACOSX/._rice.png": b"x"}
			)
		)
		self.assertEqual(sorted(photos), ["rice.png", "teabag.jpg"])
		self.assertEqual(photos["rice.png"]["kind"], "png")
		self.assertEqual(report, {"files": 2, "ignored": 2})

	def test_names_that_climb_out_are_ignored_and_nothing_is_written(self):
		photos, report = pictures.read_zip(
			zip_of(
				{
					"../../etc/evil.png": PNG,
					"/abs/evil.png": PNG,
					"C:/evil.png": PNG,
					"..\\evil.png": PNG,
					"ok.png": PNG,
				}
			)
		)
		self.assertEqual(list(photos), ["ok.png"])
		self.assertEqual(report["ignored"], 4)

	def test_a_picture_that_is_not_one_is_marked_bad(self):
		photos, _r = pictures.read_zip(zip_of({"a.png": b"not a picture", "b.jpg": PNG}))
		self.assertEqual(photos["a.png"]["error"], "image_type")
		self.assertEqual(photos["b.jpg"]["kind"], "png")  # the bytes decide, not the ending

	def test_a_picture_over_2mb_is_marked_bad(self):
		photos, _r = pictures.read_zip(zip_of({"big.jpg": JPG + b"\x00" * (2 * 1024 * 1024)}))
		self.assertEqual(photos["big.jpg"]["error"], "image_big")

	def test_two_files_with_the_same_name_are_marked(self):
		photos, _r = pictures.read_zip(zip_of({"a/x.png": PNG, "b/X.png": PNG}))
		self.assertEqual(photos["x.png"]["error"], "image_twice")

	def test_not_a_zip(self):
		with self.assertRaises(pictures.ImageProblem) as raised:
			pictures.read_zip(b"this is not a zip file")
		self.assertEqual(raised.exception.code, "zip_bad")

	def test_too_many_files(self):
		with patch.object(pictures, "MAX_ZIP_FILES", 2), self.assertRaises(pictures.ImageProblem) as raised:
			pictures.read_zip(zip_of({"a.png": PNG, "b.png": PNG, "c.png": PNG}))
		self.assertEqual(raised.exception.code, "zip_many")

	def test_too_big_when_unpacked(self):
		with (
			patch.object(pictures, "MAX_ZIP_UNPACKED", 100),
			self.assertRaises(pictures.ImageProblem) as raised,
		):
			pictures.read_zip(zip_of({"a.png": PNG + b"\x00" * 200}))
		self.assertEqual(raised.exception.code, "zip_big")

	def test_a_zip_file_over_the_size_cap(self):
		with patch.object(pictures, "MAX_ZIP_BYTES", 10), self.assertRaises(pictures.ImageProblem) as raised:
			pictures.read_zip(zip_of({"a.png": PNG}))
		self.assertEqual(raised.exception.code, "zip_big")

	def test_a_linked_file_is_ignored(self):
		buffer = io.BytesIO()
		with zipfile.ZipFile(buffer, "w") as archive:
			link = zipfile.ZipInfo("link.png")
			link.external_attr = (0o120777) << 16
			archive.writestr(link, "/etc/passwd")
		photos, report = pictures.read_zip(buffer.getvalue())
		self.assertEqual((photos, report["ignored"]), ({}, 1))


def resolver_for(table):
	def resolve(host, port, type=None):
		if host not in table:
			raise OSError("no such host")
		return [(2, 1, 6, "", (address, port)) for address in table[host]]

	return resolve


class TestWhichAddressesAreAllowed(unittest.TestCase):
	def test_public_and_private_addresses(self):
		for allowed in ("8.8.8.8", "93.184.216.34", "2606:4700:4700::1111"):
			self.assertTrue(pictures.is_public_address(allowed), allowed)
		for refused in (
			"127.0.0.1",
			"10.0.0.5",
			"172.16.3.4",
			"192.168.1.1",
			"169.254.169.254",
			"100.64.0.1",
			"0.0.0.0",
			"224.0.0.1",
			"::1",
			"fe80::1",
			"fc00::1",
			"::ffff:127.0.0.1",
			"::ffff:10.1.1.1",
			"not an address",
			"",
		):
			self.assertFalse(pictures.is_public_address(refused), refused)


class TestCheckLink(unittest.TestCase):
	resolve = staticmethod(
		resolver_for(
			{
				"pics.example.com": ["93.184.216.34"],
				"internal.example.com": ["10.0.0.9"],
				"mixed.example.com": ["93.184.216.34", "127.0.0.1"],
				"local.example.com": ["127.0.0.1"],
				"meta.example.com": ["169.254.169.254"],
				"v6.example.com": ["::1"],
			}
		)
	)

	def refused(self, url, code="link_refused"):
		with self.assertRaises(pictures.ImageProblem) as raised:
			pictures.check_link(url, self.resolve)
		self.assertEqual(raised.exception.code, code, url)

	def test_a_public_link_is_allowed(self):
		link = pictures.check_link("https://pics.example.com/a/b.jpg?x=1", self.resolve)
		self.assertEqual(
			link,
			{
				"scheme": "https",
				"host": "pics.example.com",
				"port": 443,
				"target": "/a/b.jpg?x=1",
				"address": "93.184.216.34",
			},
		)
		self.assertEqual(pictures.check_link("http://pics.example.com", self.resolve)["target"], "/")

	def test_private_addresses_are_refused(self):
		for url in (
			"http://internal.example.com/x.png",
			"http://mixed.example.com/x.png",
			"http://local.example.com/x.png",
			"http://meta.example.com/latest/meta-data",
			"http://v6.example.com/x.png",
		):
			self.refused(url)

	def test_address_literals_are_checked_too(self):
		literal = resolver_for(
			{
				"127.0.0.1": ["127.0.0.1"],
				"[::1]": ["::1"],
				"2130706433": ["127.0.0.1"],
				"0x7f.1": ["127.0.0.1"],
			}
		)
		for url in ("http://127.0.0.1/x", "http://[::1]/x", "http://2130706433/x", "http://0x7f.1/x"):
			with self.assertRaises(pictures.ImageProblem, msg=url):
				pictures.check_link(url, literal)

	def test_other_schemes_ports_and_passwords_are_refused(self):
		for url in (
			"ftp://pics.example.com/a.png",
			"file:///etc/passwd",
			"gopher://pics.example.com/",
			"javascript:alert(1)",
			"https://user:pw@pics.example.com/a.png",
			"http://pics.example.com:22/a.png",
			"http://pics.example.com:8080/a.png",
			"http:///a.png",
			"//pics.example.com/a.png",
			"http://pics.example.com/a b.png",
			"http://pics.example.com/\r\nHost: evil",
			"",
			"http://pics.example.com/" + "a" * 600,
		):
			self.refused(url)

	def test_a_name_that_does_not_resolve(self):
		self.refused("http://nowhere.example.com/a.png", "link_unreachable")


class FakeReply:
	def __init__(self, status=200, body=PNG, headers=None):
		self.status, self.body, self.headers = status, body, headers or {}
		self.released = False

	def stream(self, size):
		for start in range(0, len(self.body), size):
			yield self.body[start : start + size]

	def release_conn(self):
		self.released = True


class TestFetchImage(unittest.TestCase):
	resolve = staticmethod(
		resolver_for({"pics.example.com": ["93.184.216.34"], "evil.example.com": ["10.0.0.1"]})
	)

	def fetch(self, url, replies):
		seen = []

		def opener(link):
			seen.append(link)
			return replies.pop(0)

		result = pictures.fetch_image(url, self.resolve, opener)
		return result, seen

	def test_a_good_picture_is_fetched_from_the_checked_address(self):
		(data, kind), seen = self.fetch("https://pics.example.com/a.png", [FakeReply()])
		self.assertEqual((data, kind), (PNG, "png"))
		self.assertEqual(seen[0]["address"], "93.184.216.34")
		self.assertEqual(seen[0]["host"], "pics.example.com")

	def test_a_redirect_to_a_private_address_is_refused(self):
		redirect = FakeReply(302, b"", {"Location": "http://evil.example.com/x.png"})
		with self.assertRaises(pictures.ImageProblem) as raised:
			self.fetch("https://pics.example.com/a.png", [redirect])
		self.assertEqual(raised.exception.code, "link_refused")

	def test_a_redirect_to_a_public_address_is_followed(self):
		redirect = FakeReply(301, b"", {"Location": "/other.png"})
		(data, _kind), seen = self.fetch("https://pics.example.com/a.png", [redirect, FakeReply()])
		self.assertEqual(data, PNG)
		self.assertEqual(seen[1]["target"], "/other.png")

	def test_too_many_redirects(self):
		replies = [FakeReply(302, b"", {"Location": "/again"}) for _n in range(10)]
		with self.assertRaises(pictures.ImageProblem):
			self.fetch("https://pics.example.com/a.png", replies)

	def test_a_page_that_is_not_a_picture(self):
		with self.assertRaises(pictures.ImageProblem) as raised:
			self.fetch("https://pics.example.com/a.png", [FakeReply(body=b"<html>nope</html>")])
		self.assertEqual(raised.exception.code, "image_type")

	def test_a_picture_that_is_too_big_is_stopped_while_reading(self):
		big = FakeReply(body=PNG + b"\x00" * (2 * 1024 * 1024 + 10))
		with self.assertRaises(pictures.ImageProblem) as raised:
			self.fetch("https://pics.example.com/a.png", [big])
		self.assertEqual(raised.exception.code, "image_big")
		self.assertTrue(big.released)

	def test_a_declared_size_over_the_limit_is_refused_before_reading(self):
		reply = FakeReply(headers={"Content-Length": str(10 * 1024 * 1024)})
		with self.assertRaises(pictures.ImageProblem) as raised:
			self.fetch("https://pics.example.com/a.png", [reply])
		self.assertEqual(raised.exception.code, "image_big")

	def test_an_error_status_and_a_broken_connection(self):
		with self.assertRaises(pictures.ImageProblem) as raised:
			self.fetch("https://pics.example.com/a.png", [FakeReply(404, b"")])
		self.assertEqual(raised.exception.code, "link_unreachable")

		def broken(link):
			raise OSError("reset")

		with self.assertRaises(pictures.ImageProblem) as raised:
			pictures.fetch_image("https://pics.example.com/a.png", self.resolve, broken)
		self.assertEqual(raised.exception.code, "link_unreachable")

	def test_the_real_opener_connects_to_the_address_not_the_name(self):
		pool = MagicMock()
		pool.urlopen.return_value = FakeReply()
		with patch("urllib3.HTTPSConnectionPool", return_value=pool) as https:
			link = pictures.check_link("https://pics.example.com/a.png?v=2", self.resolve)
			pictures.fetch_image("https://pics.example.com/a.png?v=2", self.resolve)
		self.assertEqual(https.call_args.args[0], "93.184.216.34")
		self.assertEqual(https.call_args.kwargs["server_hostname"], "pics.example.com")
		self.assertEqual(https.call_args.kwargs["assert_hostname"], "pics.example.com")
		self.assertEqual(pool.urlopen.call_args.args, ("GET", "/a.png?v=2"))
		self.assertEqual(pool.urlopen.call_args.kwargs["headers"]["Host"], "pics.example.com")
		self.assertFalse(pool.urlopen.call_args.kwargs["redirect"])
		self.assertEqual(link["address"], "93.184.216.34")


# ---------------------------------------------------------------------------------------------------
# The calls
# ---------------------------------------------------------------------------------------------------


class Shop:
	"""A tiny stand-in for the database: what the shop has, changed by the fake calls the import makes."""

	def __init__(self):
		self.groups = {"Drinks", "All Item Groups"}
		self.brands = {"Tusker"}
		self.items = {}
		self.created = []
		self.updates = []
		self.stock_entries = []
		self.pictures = []
		self.costs = []
		self.warnings = []
		self.fail_codes = set()
		self.stock_fails = False

	def facts(self, rows, company):
		return make_facts(
			groups={rules.key_of(g): g for g in self.groups},
			brands={rules.key_of(b): b for b in self.brands},
			items={rules.key_of(code): dict(entry) for code, entry in self.items.items()},
		)


class CallCase(unittest.TestCase):
	def setUp(self):
		self.shop = Shop()
		self.started = []
		db = MagicMock()
		db.exists.side_effect = lambda kind, name=None: (
			(kind == "Item Group" and name in self.shop.groups)
			or (kind == "Brand" and name in self.shop.brands)
		)
		db.get_value.side_effect = lambda kind, *a, **k: None if kind == "File" else "Shop A"
		db.set_value.side_effect = self.set_value
		self.db = db
		self.start((frappe, "session"), new=frappe._dict(user="beth@shop-a.test"))
		self.start((frappe, "db"), new=db, create=True)
		self.start((frappe, "local"), new=MagicMock(), create=True)
		self.start((frappe, "log_error"), create=True)
		self.start((frappe, "clear_messages"), create=True)
		self.start((frappe, "flags"), new=frappe._dict(), create=True)
		self.start((frappe, "message_log"), new=[], create=True)
		self.start((frappe, "get_traceback"), create=True, return_value="detail")
		self.start((frappe, "get_roles"), create=True, return_value=["System Manager"])
		self.start("frappe.translate.get_all_translations", return_value={})
		self.start("murzak_pos.api.access_control.get_user_companies", return_value={"Shop A"})
		self.start((api, "resolve_company"), return_value="Shop A")
		self.start((api, "_facts"), side_effect=self.shop.facts)
		self.start((api.products, "_store_for"), return_value="Stores - A")
		self.start((api.products, "_check_store"))
		self.start((api.products, "_create_product"), side_effect=self.create)
		self.start((api.products, "_add_cost"), side_effect=lambda *a: self.shop.costs.append(a))
		self.start((api.products, "_add_warning"), side_effect=lambda *a: self.shop.warnings.append(a))
		self.start((api.products, "_relax_etims"))
		self.start((api.products, "_selling_price_list"), return_value="Standard Selling")
		self.start((api, "_make_category"), side_effect=lambda name: self.shop.groups.add(name))
		self.start((api, "_make_brand"), side_effect=lambda name: self.shop.brands.add(name))
		self.start((api, "_set_price"), side_effect=self.set_price)
		self.start((frappe, "get_doc"), create=True, side_effect=self.get_doc)
		fake = types.ModuleType("murzak_pos.api.product_api")
		fake.bulk_import_opening_stock = self.stock
		self.modules = patch.dict(sys.modules, {"murzak_pos.api.product_api": fake})
		self.modules.start()
		self.addCleanup(self.modules.stop)

	def start(self, target, **kwargs):
		patcher = patch.object(*target, **kwargs) if isinstance(target, tuple) else patch(target, **kwargs)
		mock = patcher.start()
		self.addCleanup(patcher.stop)
		return mock

	def create(self, **values):
		if values["item_code"] in self.shop.fail_codes:
			raise frappe.ValidationError("The product category does not exist.")
		self.shop.created.append(values)
		self.shop.items[values["item_code"]] = {
			"name": values["item_code"],
			"item_name": values["item_name"],
			"item_group": values["item_group"],
			"description": values["description"] or "",
			"brand": values["brand"] or "",
			"image": "",
			"is_stock_item": 1 if values["is_stock_item"] else 0,
			"price": values["standard_rate"],
			"valuation_rate": 0,
			"has_stock": False,
			"disabled": 0,
			"stock_uom": values["stock_uom"],
			"barcodes": [values["barcode"]] if values["barcode"] else [],
			"low_levels": {},
			"custom_company": "Shop A",
		}
		return {"message": "ok"}

	def set_price(self, code, price, company):
		self.shop.items[code]["price"] = price
		self.shop.updates.append((code, "price", price))

	def set_value(self, kind, code, field, value):
		self.shop.pictures.append((code, value))
		self.shop.items[code]["image"] = value

	def get_doc(self, kind, name=None):
		doc = MagicMock()
		doc.file_url = "/files/saved.png"
		doc.appended = []
		doc.append.side_effect = lambda table, row: doc.appended.append((table, row))
		doc.get.return_value = []
		return doc

	def stock(self, stock_data, company, warehouse=None, posting_date=None):
		if self.shop.stock_fails:
			raise frappe.ValidationError("Valuation rate required")
		self.shop.stock_entries.append(stock_data)
		for entry in stock_data:
			self.shop.items[entry["item_code"]]["has_stock"] = True
		return {"stock_reconciliation": "MAT-RECO-0001"}


ROWS = [
	{"item_code": "A1", "item_name": "Soda", "price": "100", "category": "Fizzy", "brand": "Bidco"},
	{
		"item_code": "A2",
		"item_name": "Rice 1kg",
		"price": "200",
		"category": "fizzy ",
		"opening_stock": "10",
		"cost_price": "150",
		"unit": "kg",
	},
	{"item_code": "A3", "item_name": "", "price": "5"},
]


class TestPreview(CallCase):
	def test_it_says_what_will_happen_per_row_and_changes_nothing(self):
		reply = api.preview_product_import(rows=ROWS)
		self.assertTrue(reply["success"])
		data = reply["data"]
		self.assertEqual(data["counts"], {"total": 3, "create": 2, "update": 0, "unchanged": 0, "invalid": 1})
		self.assertEqual(data["new_categories"], ["Fizzy"])
		self.assertEqual(data["categories_line"], "1 new category will be created: Fizzy.")
		self.assertEqual(data["new_brands"], ["Bidco"])
		self.assertEqual([r["action"] for r in data["rows"]], ["create", "create", "invalid"])
		self.assertEqual(data["rows"][0]["will"], "Will be added as a new product.")
		self.assertEqual(data["rows"][2]["problems"][0]["message"], "The product name is empty.")
		self.assertEqual(data["stock"], {"rows": 1, "units": 10, "stores": ["Stores - A"]})
		self.assertEqual(self.shop.created, [])
		self.assertEqual(self.shop.groups, {"Drinks", "All Item Groups"})

	def test_three_new_categories_are_named(self):
		rows = [
			{"item_code": f"C{n}", "item_name": "x", "category": name}
			for n, name in enumerate(["A", "B", "C"])
		]
		data = api.preview_product_import(rows=rows)["data"]
		self.assertEqual(data["categories_line"], "3 new categories will be created: A, B, C.")

	def test_too_many_rows_and_no_rows(self):
		rows = [{"item_code": str(n), "item_name": "x"} for n in range(rules.MAX_ROWS + 1)]
		with self.assertRaises(frappe.ValidationError):
			api.preview_product_import(rows=rows)
		with self.assertRaises(frappe.ValidationError):
			api.preview_product_import(rows=[])
		with self.assertRaises(frappe.ValidationError):
			api.preview_product_import(rows="not json")

	def test_rows_may_arrive_as_json_text(self):
		import json

		data = api.preview_product_import(rows=json.dumps(ROWS))["data"]
		self.assertEqual(data["counts"]["create"], 2)

	def test_a_zip_that_is_not_found_is_reported(self):
		data = api.preview_product_import(rows=ROWS, photos_file="/private/files/gone.zip")["data"]
		self.assertEqual(data["photos"]["problem"], "The ZIP of photos was not found. Add it again.")
		self.assertFalse(data["photos"]["zip_used"])

	def test_the_zip_file_must_be_the_callers_own(self):
		self.db.get_value.return_value = frappe._dict(name="f1", owner="someone-else@x.test")
		with self.assertRaises(pictures.ImageProblem):
			api._zip_content("/private/files/a.zip")
		with self.assertRaises(pictures.ImageProblem):
			api._zip_content("/etc/passwd")
		with self.assertRaises(pictures.ImageProblem):
			api._zip_content("")


class TestImport(CallCase):
	def test_new_products_categories_brands_and_stock(self):
		reply = api.import_products(rows=ROWS)
		data = reply["data"]
		self.assertTrue(reply["success"])
		self.assertEqual(
			data["counts"], {"created": 2, "updated": 0, "unchanged": 0, "failed": 0, "left_out": 1}
		)
		self.assertEqual(data["categories_created"], ["Fizzy"])
		self.assertEqual(data["brands_created"], ["Bidco"])
		self.assertEqual([c["item_code"] for c in self.shop.created], ["A1", "A2"])
		self.assertEqual(self.shop.created[1]["stock_uom"], "Kg")
		self.assertEqual(self.shop.created[1]["item_group"], "Fizzy")
		self.assertEqual(
			self.shop.stock_entries,
			[[{"item_code": "A2", "qty": 10, "valuation_rate": 150, "warehouse": "Stores - A"}]],
		)
		self.assertEqual(data["stock"], {"rows": 1, "reference": "MAT-RECO-0001", "problem": ""})
		self.assertEqual(self.shop.costs[0][0], "A2")
		self.assertEqual([r["status"] for r in data["rows"]], ["created", "created", "left_out"])

	def test_the_same_sheet_twice_changes_nothing_the_second_time(self):
		api.import_products(rows=ROWS)
		before = (len(self.shop.created), len(self.shop.stock_entries), set(self.shop.groups))
		again = api.import_products(rows=ROWS)["data"]
		self.assertEqual(
			again["counts"], {"created": 0, "updated": 0, "unchanged": 2, "failed": 0, "left_out": 1}
		)
		self.assertEqual(again["categories_created"], [])
		self.assertEqual(again["stock"]["rows"], 0)
		self.assertEqual(
			(len(self.shop.created), len(self.shop.stock_entries), set(self.shop.groups)), before
		)
		self.assertEqual([r["status"] for r in again["rows"]], ["unchanged", "unchanged", "left_out"])

	def test_a_changed_price_updates_and_a_third_run_is_quiet(self):
		api.import_products(rows=ROWS)
		changed = [dict(ROWS[0], price="120"), ROWS[1]]
		second = api.import_products(rows=changed)["data"]
		self.assertEqual(second["counts"]["updated"], 1)
		self.assertEqual(second["rows"][0]["message"], "Updated: price.")
		self.assertEqual(self.shop.updates, [("A1", "price", 120)])
		third = api.import_products(rows=changed)["data"]
		self.assertEqual(
			third["counts"], {"created": 0, "updated": 0, "unchanged": 2, "failed": 0, "left_out": 0}
		)
		self.assertEqual(len(self.shop.created), 2)

	def test_one_failed_row_does_not_stop_the_others(self):
		self.shop.fail_codes = {"A1"}
		data = api.import_products(rows=ROWS)["data"]
		self.assertEqual([r["status"] for r in data["rows"]], ["failed", "created", "left_out"])
		self.assertEqual(data["rows"][0]["message"], "The product category does not exist.")
		self.db.rollback.assert_called()

	def test_a_failed_stock_entry_keeps_the_products_and_a_retry_adds_the_stock(self):
		self.shop.stock_fails = True
		first = api.import_products(rows=ROWS)["data"]
		self.assertEqual(first["counts"]["created"], 2)
		self.assertEqual(first["stock"]["rows"], 0)
		self.assertIn("Import the same file again", first["stock"]["problem"])
		self.shop.stock_fails = False
		second = api.import_products(rows=ROWS)["data"]
		self.assertEqual(second["stock"], {"rows": 1, "reference": "MAT-RECO-0001", "problem": ""})
		self.assertEqual(len(self.shop.stock_entries), 1)
		self.assertEqual(len(self.shop.created), 2)
		third = api.import_products(rows=ROWS)["data"]
		self.assertEqual(third["stock"]["rows"], 0)
		self.assertEqual(len(self.shop.stock_entries), 1)

	def test_a_picture_from_the_zip_is_saved_once_on_a_product_without_one(self):
		photos = {"soda.png": {"name": "Soda.png", "data": PNG, "kind": "png"}}
		with patch.object(api, "_read_photos", return_value=(photos, "", {"files": 1, "ignored": 0})):
			rows = [{"item_code": "A1", "item_name": "Soda", "price": "1", "image": "soda.png"}]
			first = api.import_products(rows=rows, photos_file="/private/files/p.zip")["data"]
			self.assertEqual(first["pictures_added"], 1)
			self.assertEqual(self.shop.pictures, [("A1", "/files/saved.png")])
			second = api.import_products(rows=rows, photos_file="/private/files/p.zip")["data"]
		self.assertEqual(second["counts"]["unchanged"], 1)
		self.assertEqual(len(self.shop.pictures), 1)

	def test_a_refused_picture_link_does_not_stop_the_product(self):
		rows = [{"item_code": "A1", "item_name": "Soda", "price": "1", "image": "http://127.0.0.1/a.png"}]
		with patch.object(pictures, "check_link", side_effect=pictures.ImageProblem("link_refused")):
			data = api.import_products(rows=rows)["data"]
		self.assertEqual(data["counts"]["created"], 1)
		self.assertEqual(data["pictures_added"], 0)
		self.assertIn("cannot be used", " ".join(data["rows"][0]["notes"]))

	def test_a_picture_that_fails_to_download_is_a_note_not_a_failure(self):
		rows = [
			{"item_code": "A1", "item_name": "Soda", "price": "1", "image": "https://pics.example.com/a.png"}
		]
		with (
			patch.object(pictures, "check_link"),
			patch.object(pictures, "fetch_image", side_effect=pictures.ImageProblem("image_big")),
		):
			data = api.import_products(rows=rows)["data"]
		self.assertEqual(data["rows"][0]["status"], "created")
		self.assertEqual(data["pictures_added"], 0)
		self.assertIn("bigger than 2 MB", " ".join(data["rows"][0]["notes"]))

	def test_a_cost_price_needs_the_cost_permission(self):
		with (
			patch(
				"murzak_pos.api.product_import_api.role_tiers.require_access",
				side_effect=frappe.PermissionError,
			),
			self.assertRaises(frappe.PermissionError),
		):
			api.import_products(rows=ROWS)
		self.assertEqual(self.shop.created, [])

	def test_a_sheet_without_costs_does_not_need_it(self):
		rows = [{"item_code": "Z1", "item_name": "Plain", "price": "1"}]
		with patch(
			"murzak_pos.api.product_import_api.role_tiers.require_access", side_effect=frappe.PermissionError
		):
			self.assertEqual(api.import_products(rows=rows)["data"]["counts"]["created"], 1)


class TestTheSource(unittest.TestCase):
	def test_no_em_dash_anywhere(self):
		import os

		here = os.path.dirname(os.path.abspath(__file__))
		for name in ("product_import_calc.py", "product_import_images.py", "product_import_api.py"):
			with open(os.path.join(here, name), encoding="utf-8") as handle:
				self.assertNotIn("\u2014", handle.read(), name)

	def test_the_old_calls_are_untouched(self):
		import os

		here = os.path.dirname(os.path.abspath(__file__))
		with open(os.path.join(here, "product_import_api.py"), encoding="utf-8") as handle:
			source = handle.read()
		for old in ("bulk_import_products(", "bulk_create_products("):
			self.assertNotIn(old, source)
