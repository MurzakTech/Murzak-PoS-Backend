"""
The product spreadsheet import (ticket P7-1): `preview_product_import` and `import_products`.

Both new. The old `bulk_import_products`, `bulk_create_products` and `create_product` are left exactly as they are
(the old Bulk import screen keeps calling them). Both new calls read the shop once (`_facts`), hand the rows to
`product_import_calc.plan_import` and get the same plan; the preview only shows it, the import carries it out.

What the import writes, and through which existing call
- A new product: the existing `create_product` (so the price list, tax template and eTIMS rules are the ones
  everyone else gets). Then, as separate small steps that never undo the product: the cost price, the low-stock
  level and the picture. An update changes only the cells that are filled in.
- A new category or brand: an Item Group under "All Item Groups" (not a folder), a Brand. Listed in the answer.
- Opening stock: the existing `bulk_import_opening_stock` (one Stock Reconciliation for the whole sheet).
- Pictures: a ZIP uploaded with the sheet (read in memory, see product_import_images) or a link the server fetches
  only when it points at the public internet. The picture is saved as a public File on the product.

Nothing here is destructive: no product, price list, stock entry or account is deleted or rewritten, and sending
the same sheet again changes nothing that is already up to date.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import flt

from murzak_pos.api import product_import_calc as rules
from murzak_pos.api import product_import_images as pictures
from murzak_pos.api import products_list_api as products
from murzak_pos.api import products_list_calc as listing
from murzak_pos.api import role_tiers
from murzak_pos.api.access_control import ensure_record_access, require_catalogue_role
from murzak_pos.api.payment_gateway_common import resolve_company

CHUNK = 200


# --- words ------------------------------------------------------------------------------------------


def _problem_words():
	"""The reason a row is left out, in the caller's language. {0} is the value from the row."""
	return {
		"code_required": _("The item code is empty."),
		"code_long": _("The item code is longer than 140 characters."),
		"code_bad": _("The item code has hidden characters."),
		"code_twice": _("This item code was already used on row {0}."),
		"code_other_business": _("The code {0} belongs to another business."),
		"name_required": _("The item name is empty."),
		"name_long": _("The item name is longer than 140 characters."),
		"text_long": _("This text is too long: {0}"),
		"price_bad": _("The price {0} is not a number."),
		"cost_bad": _("The cost price {0} is not a number."),
		"stock_bad": _("The opening stock {0} is not a number."),
		"low_bad": _("The low stock level {0} is not a number."),
		"too_big": _("The number {0} is too big. Check it."),
		"barcode_bad": _("The barcode cannot have spaces or hidden characters."),
		"barcode_twice": _("This barcode is also on row {0}."),
		"barcode_taken": _("This barcode is already used by product {0}."),
		"flag_bad": _("Is a service must be yes or no (found {0})."),
		"vat_bad": _("The tax class {0} is not known. Use standard, zero rated or exempt."),
		"image_name_bad": _("The picture {0} must be a link or a .jpg, .png or .webp file name."),
		"unit_unknown": _("The unit {0} is not one of this shop's units."),
		"store_unknown": _("The store {0} is not one of this business's stores."),
	}


def _note_words():
	"""Things worth a look that do not stop the row."""
	return {
		"no_price": _("No price was given, so the price will be 0. Set it before selling."),
		"service_no_stock": _("This is a service, so the stock and low stock level are ignored."),
		"type_kept": _("The product already exists, so whether it is a service was not changed."),
		"unit_kept": _("The unit stays {0}: a unit cannot be changed on an existing product."),
		"vat_kept": _("The tax class of an existing product is not changed here."),
		"disabled_item": _("This product is switched off. It is updated but stays off."),
		"cost_kept": _("The cost price was not changed because the product already has stock."),
		"stock_no_store": _("No store was chosen, so the opening stock is left out."),
		"stock_has_history": _("The product already has stock records, so the opening stock is left out."),
		"stock_needs_cost": _(
			"Opening stock needs a cost price. The product is saved and the stock is left out."
		),
		"low_no_store": _("No store was chosen, so the low stock level is left out."),
		"image_no_zip": _("The picture {0} is a file name but no ZIP of photos was added."),
		"image_missing": _("The picture {0} was not found in the ZIP."),
		"image_file_bad": _("The picture {0} cannot be used: {1}"),
		"image_link_bad": _("The picture link {0} cannot be used: {1}"),
		"image_kept": _("The product already has a picture, so it was kept."),
	}


def _image_words():
	return {
		"image_empty": _("the file is empty"),
		"image_big": _("it is bigger than 2 MB"),
		"image_type": _("it is not a jpg, png or webp picture"),
		"image_twice": _("two files in the ZIP have this name"),
		"zip_locked": _("it is password protected"),
		"zip_bad": _("the ZIP is damaged"),
		"link_refused": _("this address is not allowed"),
		"link_unreachable": _("the address could not be reached"),
	}


def _field_words():
	"""Names of the cells, for 'will be updated: price, brand'."""
	return {
		"item_name": _("name"),
		"category": _("category"),
		"description": _("description"),
		"brand": _("brand"),
		"price": _("price"),
		"cost_price": _("cost price"),
		"barcode": _("barcode"),
		"low_stock_level": _("low stock level"),
		"image": _("picture"),
		"opening_stock": _("opening stock"),
	}


def _say(table, entry):
	"""One problem or note as a sentence."""
	template = table.get(entry["code"], entry["code"])
	value = entry.get("value")
	if isinstance(value, dict):
		images = _image_words()
		return template.format(value.get("name", ""), images.get(value.get("error"), value.get("error", "")))
	return template.format(value if value is not None else "")


def _file_wide(code):
	return {
		"zip_bad": _("The ZIP of photos cannot be opened. It may be damaged or not a ZIP."),
		"zip_big": _("The ZIP of photos is too big. Keep it under 60 MB."),
		"zip_many": _("The ZIP holds too many files. Keep it to 1,000 pictures or fewer."),
		"zip_missing": _("The ZIP of photos was not found. Add it again."),
	}.get(code, _("The ZIP of photos cannot be used."))


# --- reading the shop -------------------------------------------------------------------------------


def _chunks(values):
	values = list(values)
	for start in range(0, len(values), CHUNK):
		yield values[start : start + CHUNK]


def _by_key(names):
	return {rules.key_of(n): n for n in names if n}


def _read_rows(rows):
	"""The rows as a list of dicts, whether they came as a list or as the JSON text of one."""
	if isinstance(rows, str):
		try:
			rows = json.loads(rows)
		except ValueError:
			frappe.throw(_("The rows could not be read. Please try again."), frappe.ValidationError)
	if not isinstance(rows, list) or not rows:
		frappe.throw(_("There are no products to import."), frappe.ValidationError)
	if len(rows) > rules.MAX_ROWS:
		frappe.throw(
			_("The file has {0} products. Please split it into files of {1} or fewer.").format(
				len(rows), rules.MAX_ROWS
			),
			frappe.ValidationError,
		)
	return rows


def _facts(rows, company):
	"""Everything the plan needs to know about the shop, read in a few queries."""
	codes = sorted(
		{str(r.get("item_code")).strip() for r in rows if isinstance(r, dict) and r.get("item_code")}
	)
	barcodes = sorted(
		{str(r.get("barcode")).strip() for r in rows if isinstance(r, dict) and r.get("barcode")}
	)

	stores = {}
	for row in frappe.get_all(
		"Warehouse", filters={"company": company, "is_group": 0}, fields=["name", "warehouse_name"]
	):
		stores[rules.key_of(row["name"])] = row["name"]
		if row.get("warehouse_name"):
			stores.setdefault(rules.key_of(row["warehouse_name"]), row["name"])

	items: dict = {}
	price_list = products._selling_price_list()
	for part in _chunks(codes):
		found = frappe.get_all(
			"Item",
			filters={"name": ["in", part]},
			fields=[
				"name",
				"item_name",
				"item_group",
				"description",
				"brand",
				"image",
				"is_stock_item",
				"valuation_rate",
				"disabled",
				"stock_uom",
				"standard_rate",
				"custom_company",
			],
		)
		names = [f["name"] for f in found]
		prices = products._prices(names, price_list)
		marks = products._barcodes(names)
		history = {
			r["item_code"]
			for r in frappe.get_all(
				"Stock Ledger Entry",
				filters={"item_code": ["in", names], "is_cancelled": 0},
				fields=["item_code"],
				group_by="item_code",
			)
		}
		levels: dict = {}
		for r in frappe.get_all(
			"Item Reorder",
			filters={"parent": ["in", names], "parenttype": "Item"},
			fields=["parent", "warehouse", "warehouse_reorder_level"],
		):
			levels.setdefault(r["parent"], {})[r["warehouse"]] = flt(r["warehouse_reorder_level"])
		for entry in found:
			price, _where = listing.pick_price(
				prices.get(entry["name"]), entry["stock_uom"], entry["standard_rate"]
			)
			items[rules.key_of(entry["name"])] = {
				**entry,
				"price": price,
				"has_stock": entry["name"] in history,
				"barcodes": marks.get(entry["name"], []),
				"low_levels": levels.get(entry["name"], {}),
			}

	owner: dict = {}
	for part in _chunks(barcodes):
		for row in frappe.get_all(
			"Item Barcode", filters={"barcode": ["in", part]}, fields=["barcode", "parent"]
		):
			owner[row["barcode"]] = row["parent"]

	return {
		"company": company,
		"groups": _by_key(frappe.get_all("Item Group", pluck="name")),
		"brands": _by_key(frappe.get_all("Brand", pluck="name")),
		"uoms": _by_key(frappe.get_all("UOM", pluck="name")),
		"stores": stores,
		"items": items,
		"barcode_owner": owner,
	}


def _zip_content(photos_file):
	"""The bytes of the ZIP the caller uploaded. Only the caller's own upload is read."""
	url = str(photos_file or "").strip()
	if not url.startswith(("/private/files/", "/files/")):
		raise pictures.ImageProblem("zip_missing")
	found = frappe.db.get_value("File", {"file_url": url}, ["name", "owner"], as_dict=True)
	if not found or found.get("owner") not in (frappe.session.user, "Administrator"):
		raise pictures.ImageProblem("zip_missing")
	content = frappe.get_doc("File", found["name"]).get_content()
	return content if isinstance(content, bytes) else str(content).encode("latin-1", "ignore")


def _read_photos(photos_file):
	"""(pictures by name, a problem sentence). Without a ZIP: (None, '')."""
	if not photos_file:
		return None, "", {}
	try:
		photos, report = pictures.read_zip(_zip_content(photos_file))
	except pictures.ImageProblem as problem:
		return None, str(_file_wide(problem.code)), {}
	return photos, "", report


def _check_link(url):
	pictures.check_link(url)


def _plan(rows, company, warehouse, photos_file):
	rows = _read_rows(rows)
	facts = _facts(rows, company)
	photos, zip_problem, zip_report = _read_photos(photos_file)
	plan = rules.plan_import(
		rows,
		facts,
		default_store=warehouse or products._store_for(company, None),
		photos=pictures.photo_index(photos) if photos is not None else None,
		link_check=_check_link,
	)
	return plan, photos, zip_problem, zip_report


# --- what the page shows ----------------------------------------------------------------------------


def _will(row):
	if row["action"] == "invalid":
		return _("Left out. Fix the problem in your file and import again.")
	if row["action"] == "create":
		return _("Will be added as a new product.")
	if row["action"] == "unchanged":
		return _("No changes. This product is already up to date.")
	words = _field_words()
	return _("Will be updated: {0}.").format(
		", ".join(words.get(c["field"], c["field"]) for c in row["changes"])
	)


def _shape_row(row):
	problems, notes = _problem_words(), _note_words()
	return {
		"row": row["row"],
		"item_code": row["item_code"],
		"item_name": row["item_name"],
		"action": row["action"],
		"will": _will(row),
		"understood": row["understood"],
		"changes": row["changes"],
		"problems": [{"field": p["field"], "message": _say(problems, p)} for p in row["problems"]],
		"notes": [_say(notes, n) for n in row["notes"]],
	}


def _summary_line(plan):
	counts = plan["counts"]
	return _("{0} new, {1} to update, {2} already up to date, {3} left out.").format(
		counts["create"], counts["update"], counts["unchanged"], counts["invalid"]
	)


@frappe.whitelist(methods=["POST"])
def preview_product_import(rows=None, company=None, warehouse=None, photos_file=None):
	"""
	Read the sheet against the shop and say, for every row, what was understood and what the import will do.
	Changes nothing. See docs/redesign/P7-1-contract.md.
	"""
	require_catalogue_role()
	ensure_record_access("Warehouse", warehouse)
	company = resolve_company(company)
	products._check_store(warehouse, company)
	try:
		plan, photos, zip_problem, zip_report = _plan(rows, company, warehouse, photos_file)
	except frappe.ValidationError:
		raise
	except Exception:
		frappe.log_error("Product import preview error", frappe.get_traceback())
		return {"success": False, "message": _("The file could not be checked. Please try again.")}

	categories = plan["new_categories"]
	brands = plan["new_brands"]
	return {
		"success": True,
		"data": {
			"rows": [_shape_row(r) for r in plan["rows"]],
			"counts": plan["counts"],
			"summary": _summary_line(plan),
			"new_categories": categories,
			"new_brands": brands,
			"categories_line": (
				_("{0} new categories will be created: {1}.").format(len(categories), ", ".join(categories))
				if len(categories) != 1
				else _("1 new category will be created: {0}.").format(categories[0])
			)
			if categories
			else "",
			"brands_line": (
				_("{0} new brands will be created: {1}.").format(len(brands), ", ".join(brands))
				if len(brands) != 1
				else _("1 new brand will be created: {0}.").format(brands[0])
			)
			if brands
			else "",
			"stock": plan["stock"],
			"store": warehouse or products._store_for(company, None) or "",
			"photos": {
				"zip_used": photos is not None,
				"zip_files": zip_report.get("files", 0),
				"zip_ignored": zip_report.get("ignored", 0),
				"problem": zip_problem,
			},
			"limits": {"max_rows": rules.MAX_ROWS},
		},
	}


# --- carrying it out --------------------------------------------------------------------------------


def _make_category(name):
	doc = frappe.new_doc("Item Group")
	doc.item_group_name = name
	doc.parent_item_group = rules.NEW_PRODUCT_CATEGORY
	doc.is_group = 0
	doc.insert(ignore_permissions=True)


def _make_brand(name):
	doc = frappe.new_doc("Brand")
	doc.brand = name
	doc.insert(ignore_permissions=True)


def _create_missing(names, kind, maker):
	"""Create the categories or brands that do not exist yet. Returns the ones created."""
	made = []
	for name in names:
		if frappe.db.exists(kind, name):
			continue
		maker(name)
		made.append(name)
	if made:
		frappe.db.commit()
	return made


def _picture_url(code, row, photos):
	"""Save the row's picture as a public file on the product and return its address."""
	picture = row["picture"]
	if picture["kind"] == "path":
		return picture["value"]
	if picture["kind"] == "file":
		photo = (photos or {}).get(rules.key_of(picture["value"]))
		if not photo or photo.get("error"):
			raise pictures.ImageProblem((photo or {}).get("error") or "zip_missing")
		data, kind = photo["data"], photo["kind"]
	else:
		data, kind = pictures.fetch_image(picture["value"])
	file_name = pictures.safe_file_name(code, kind)
	known = frappe.db.get_value(
		"File", {"attached_to_doctype": "Item", "attached_to_name": code, "file_name": file_name}, "file_url"
	)
	if known:
		return known
	doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name,
			"attached_to_doctype": "Item",
			"attached_to_name": code,
			"attached_to_field": "image",
			"is_private": 0,
			"content": data,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.file_url


def _set_level(doc, store, level):
	"""Put the low-stock level on the product's Reorder table: change the store's row, or add one."""
	for row in doc.get("reorder_levels") or []:
		if row.get("warehouse") == store:
			row.warehouse_reorder_level = listing.quantity(level)
			return
	doc.append("reorder_levels", listing.reorder_row(store, level))


def _set_price(code, price, company):
	"""The selling price on an existing product: its price list entry (the existing call) and its standard rate."""
	from murzak_pos.api.product_api import set_product_price

	price_list = products._selling_price_list()
	if not price_list:
		raise frappe.ValidationError(_("This business has no selling price list."))
	set_product_price(item_code=code, price=listing.money(price), price_list=price_list, company=company)
	frappe.db.set_value("Item", code, "standard_rate", listing.money(price))


def _words_for_error(error):
	message = str(error).strip()
	frappe.clear_messages()
	return message or str(_("This product could not be saved."))


def _create_one(row, company, photos, notes):
	"""A new product. The product comes first; the extra steps never undo it."""
	u = row["understood"]
	code = row["item_code"]
	products._create_product(
		item_code=code,
		item_name=row["item_name"],
		item_group=u["category"],
		stock_uom=u["unit"],
		standard_rate=u["price"] or 0,
		description=u["description"] or None,
		is_stock_item=not u["is_service"],
		brand=u["brand"] or None,
		barcode=u["barcode"] or None,
		company=company,
		vat_class=u["vat_class"] or None,
	)
	store = u["store"]
	if row.get("low_stock_level"):
		ok, _r = products._step(
			notes, "low_stock_warning", lambda: products._add_warning(code, store, row["low_stock_level"])
		)
	cost = u["cost_price"]
	if cost:
		products._step(notes, "cost_price", lambda: products._add_cost(code, store, company, cost))
	_add_picture(code, row, photos, notes)


def _add_picture(code, row, photos, notes):
	if not row.get("picture"):
		return False
	try:
		url = _picture_url(code, row, photos)
		frappe.db.set_value("Item", code, "image", url)
		return True
	except pictures.ImageProblem as problem:
		notes.append(
			"picture:"
			+ str(
				_("The picture could not be added: {0}.").format(
					_image_words().get(problem.code, problem.code)
				)
			)
		)
	except Exception:
		frappe.log_error("Product import picture error", frappe.get_traceback())
		notes.append("picture:" + str(_("The picture could not be added.")))
	return False


def _update_one(row, company, photos, notes):
	"""An existing product: only the cells that were filled in and differ."""
	code = row["item_code"]
	changed = {c["field"]: c["to"] for c in row["changes"]}
	u = row["understood"]
	doc_fields = {"item_name", "category", "description", "brand", "barcode", "low_stock_level"}
	if doc_fields & set(changed):
		doc = frappe.get_doc("Item", code)
		if "item_name" in changed:
			doc.item_name = changed["item_name"]
		if "category" in changed:
			doc.item_group = changed["category"]
		if "description" in changed:
			doc.description = changed["description"]
		if "brand" in changed:
			doc.brand = changed["brand"]
		if "barcode" in changed:
			doc.append("barcodes", {"barcode": changed["barcode"]})
		if "low_stock_level" in changed:
			_set_level(doc, u["store"], changed["low_stock_level"])
		products._relax_etims(doc)
		doc.save(ignore_permissions=True)
	if "price" in changed:
		_set_price(code, changed["price"], company)
	if "cost_price" in changed:
		cost = changed["cost_price"]
		products._step(notes, "cost_price", lambda: products._add_cost(code, u["store"], company, cost))
	if "image" in changed:
		_add_picture(code, row, photos, notes)


def _step_words(step):
	if step == "picture":
		return ""
	return str(products._note_words(step))


def _add_stock(stock_rows, company, warehouse):
	"""One opening stock entry for all rows, through the existing call. Returns (reference or '', problem sentence)."""
	from murzak_pos.api.product_api import bulk_import_opening_stock

	data = [
		{
			"item_code": r["item_code"],
			"qty": r["stock"]["qty"],
			"valuation_rate": r["stock"]["cost"],
			"warehouse": r["stock"]["store"],
		}
		for r in stock_rows
	]
	try:
		reply = bulk_import_opening_stock(stock_data=data, company=company, warehouse=warehouse)
	except Exception:
		frappe.log_error("Product import stock error", frappe.get_traceback())
		frappe.clear_messages()
		return "", str(
			_(
				"The products were saved but the opening stock was not added. Import the same file again to add it."
			)
		)
	return str((reply or {}).get("stock_reconciliation") or ""), ""


@frappe.whitelist(methods=["POST"])
def import_products(rows=None, company=None, warehouse=None, photos_file=None):
	"""
	Carry out the plan `preview_product_import` shows. Safe to repeat: products that already match are left alone.
	See docs/redesign/P7-1-contract.md.
	"""
	require_catalogue_role()
	ensure_record_access("Warehouse", warehouse)
	company = resolve_company(company)
	products._check_store(warehouse, company)
	try:
		plan, photos, zip_problem, _zip_report = _plan(rows, company, warehouse, photos_file)
	except frappe.ValidationError:
		raise
	except Exception:
		frappe.log_error("Product import error", frappe.get_traceback())
		return {"success": False, "message": _("The file could not be imported. Please try again.")}

	if any(r["understood"].get("cost_price") is not None for r in plan["rows"]):
		role_tiers.require_access("cost_of_goods")

	done = [r for r in plan["rows"] if r["action"] in ("create", "update")]
	categories_made = _create_missing(plan["new_categories"], "Item Group", _make_category)
	brands_made = _create_missing(plan["new_brands"], "Brand", _make_brand)

	results = []
	stock_rows = []
	counts = {"created": 0, "updated": 0, "unchanged": 0, "failed": 0, "left_out": 0}
	pictures_added = 0
	for row in plan["rows"]:
		shaped = _shape_row(row)
		result = {
			"row": row["row"],
			"item_code": row["item_code"],
			"item_name": row["item_name"],
			"understood": row["understood"],
			"notes": shaped["notes"],
			"problems": shaped["problems"],
		}
		if row["action"] == "invalid":
			counts["left_out"] += 1
			result["status"] = "left_out"
			results.append(result)
			continue
		if row["action"] == "unchanged":
			counts["unchanged"] += 1
			result["status"] = "unchanged"
			result["message"] = shaped["will"]
			results.append(result)
			continue
		step_notes: list = []
		try:
			if row["action"] == "create":
				_create_one(row, company, photos, step_notes)
			else:
				_update_one(row, company, photos, step_notes)
			frappe.db.commit()
		except Exception as error:
			frappe.db.rollback()
			if not isinstance(error, frappe.ValidationError | frappe.PermissionError):
				frappe.log_error("Product import row error", frappe.get_traceback())
			counts["failed"] += 1
			result["status"] = "failed"
			result["message"] = (
				_words_for_error(error)
				if isinstance(error, frappe.ValidationError | frappe.PermissionError)
				else str(_("This product could not be saved. Check the row and import again."))
			)
			results.append(result)
			continue
		counts["created" if row["action"] == "create" else "updated"] += 1
		result["status"] = "created" if row["action"] == "create" else "updated"
		result["message"] = (
			_("Added as a new product.")
			if row["action"] == "create"
			else _("Updated: {0}.").format(
				", ".join(_field_words().get(c["field"], c["field"]) for c in row["changes"])
			)
		)
		for step in step_notes:
			if step.startswith("picture:"):
				result["notes"].append(step[len("picture:") :])
			else:
				result["notes"].append(_step_words(step))
		if row.get("picture") and not any(n.startswith("picture:") for n in step_notes):
			pictures_added += 1
		if row.get("stock"):
			stock_rows.append(row)
		results.append(result)

	stock = {"rows": 0, "reference": "", "problem": ""}
	if stock_rows:
		reference, problem = _add_stock(stock_rows, company, warehouse)
		stock = {"rows": 0 if problem else len(stock_rows), "reference": reference, "problem": problem}
		for result in results:
			if any(result["item_code"] == r["item_code"] for r in stock_rows):
				result["stock"] = "failed" if problem else "added"
				if problem:
					result["notes"].append(problem)

	frappe.local.response["http_status_code"] = 200
	return {
		"success": True,
		"data": {
			"rows": results,
			"counts": counts,
			"total": len(plan["rows"]),
			"categories_created": categories_made,
			"brands_created": brands_made,
			"stock": stock,
			"pictures_added": pictures_added,
			"photos_problem": zip_problem,
			"changed_something": bool(done),
			"message": _("Import finished."),
		},
	}
