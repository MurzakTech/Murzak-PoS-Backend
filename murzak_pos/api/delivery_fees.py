"""
Delivery fees for the hosted online store: free, one flat fee, a fee for each area, or a fee by distance.

The rules a shop sets, in plain words:

- free: delivery costs the customer nothing;
- flat: one fee for every delivery;
- area: a fee for each named area ("Westlands 200", "Kasarani 300"); the customer picks their area, and an area not on the
  list is not delivered to;
- distance: a starting fee that covers the first few kilometres, then a price for each extra kilometre, up to a longest
  distance the shop will deliver. The customer shares their location (a map pin or the phone's location).

For all but "free", the shop can also say "free delivery on orders above this amount".

About distance: it is the straight line between the shop and the customer, multiplied by a road factor (1.3 unless the
shop changes it) because roads are longer than a straight line. It is an estimate that needs no paid map service. The
shop should test a few addresses it knows and adjust the road factor until the fees feel right.

The fee is always worked out here, on the server, from the shop's rules. The customer's browser can ask what the fee
would be, but never sets it.

Nothing here touches the database, so every rule can be tested on its own.
"""

from __future__ import annotations

import math

from frappe import _

MODES = ("free", "flat", "area", "distance")
MAX_AREAS = 50
MAX_FEE = 100000
EARTH_RADIUS_KM = 6371.0088
DEFAULT_ROAD_FACTOR = 1.3


class DeliveryRuleError(ValueError):
	"""A delivery rule is not usable. The message is fit to show the owner."""


def number(value, label: str, minimum: float = 0, maximum: float = MAX_FEE) -> float:
	"""A finite number within limits, or a DeliveryRuleError that names the field."""
	try:
		result = float(value if value not in (None, "") else 0)
	except (TypeError, ValueError):
		raise DeliveryRuleError(_("{0} must be a number.").format(label))
	if not math.isfinite(result) or not (minimum <= result <= maximum):
		raise DeliveryRuleError(_("{0} must be between {1} and {2}.").format(label, minimum, maximum))
	return result


def clean_areas(areas) -> list[dict]:
	"""The areas a shop delivers to: a name and a fee each, no repeats."""
	if areas in (None, ""):
		return []
	if not isinstance(areas, list):
		raise DeliveryRuleError(_("The delivery areas must be a list."))
	if len(areas) > MAX_AREAS:
		raise DeliveryRuleError(_("You can list at most {0} delivery areas.").format(MAX_AREAS))
	cleaned, seen = [], set()
	for entry in areas:
		if not isinstance(entry, dict):
			raise DeliveryRuleError(_("Each delivery area needs a name and a fee."))
		name = str(entry.get("name") or "").strip()[:60]
		if not name:
			raise DeliveryRuleError(_("Each delivery area needs a name."))
		if name.lower() in seen:
			raise DeliveryRuleError(_("The delivery area {0} is listed twice.").format(name))
		seen.add(name.lower())
		cleaned.append({"name": name, "fee": number(entry.get("fee"), _("The fee for {0}").format(name))})
	return cleaned


def coordinates(latitude, longitude) -> tuple[float, float] | None:
	"""A place on the map, or None when none was given. A place that cannot exist is an error."""
	if latitude in (None, "") or longitude in (None, ""):
		return None
	lat = number(latitude, _("Latitude"), -90, 90)
	lng = number(longitude, _("Longitude"), -180, 180)
	return lat, lng


def distance_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
	"""Straight-line distance over the earth's surface, in kilometres (the haversine formula)."""
	p1, p2 = math.radians(lat1), math.radians(lat2)
	dp, dl = p2 - p1, math.radians(lng2 - lng1)
	a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
	return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def quote(settings: dict, subtotal: float, area=None, latitude=None, longitude=None) -> dict:
	"""
	What delivery would cost for goods worth `subtotal`.

	Returns {"deliverable": bool, "fee": float, "distance_km": float or None, "message": text or None}. When delivery is
	not possible the message says why, in words a customer can act on.
	"""
	mode = settings.get("delivery_mode") or "free"
	result = {"deliverable": True, "fee": 0.0, "distance_km": None, "message": None}
	if mode == "free":
		return result

	if mode == "flat":
		fee = float(settings.get("delivery_flat_fee") or 0)
	elif mode == "area":
		wanted = str(area or "").strip().lower()
		match = next((a for a in settings.get("delivery_areas") or [] if a["name"].lower() == wanted), None)
		if not match:
			return {
				**result,
				"deliverable": False,
				"message": _("Please choose one of the areas we deliver to."),
			}
		fee = float(match["fee"])
	elif mode == "distance":
		place = coordinates(latitude, longitude)
		shop = coordinates(settings.get("shop_latitude"), settings.get("shop_longitude"))
		if place is None:
			return {
				**result,
				"deliverable": False,
				"message": _("Please share your location so we can work out the delivery fee."),
			}
		if shop is None:
			return {
				**result,
				"deliverable": False,
				"message": _("This shop has not set where it is, so it cannot deliver yet."),
			}
		km = distance_km(*shop, *place) * float(settings.get("road_factor") or DEFAULT_ROAD_FACTOR)
		result["distance_km"] = round(km, 1)
		longest = float(settings.get("distance_max_km") or 0)
		if longest and km > longest:
			return {
				**result,
				"deliverable": False,
				"message": _("Sorry, we deliver up to {0} km and you are about {1} km away.").format(
					longest, round(km, 1)
				),
			}
		extra = max(0.0, km - float(settings.get("distance_base_km") or 0))
		fee = float(settings.get("distance_base_fee") or 0) + extra * float(
			settings.get("distance_per_km") or 0
		)
	else:
		return {**result, "deliverable": False, "message": _("Delivery is not set up for this shop.")}

	free_above = float(settings.get("free_delivery_above") or 0)
	if free_above and subtotal >= free_above:
		fee = 0.0
	# Whole shillings, rounded up, so the shop is never a few cents short
	result["fee"] = float(math.ceil(round(fee, 2)))
	return result


def validate_rules(raw: dict, current: dict) -> dict:
	"""
	The delivery settings from a form, checked, with anything left out kept as it was.
	Returns only the delivery keys; raises DeliveryRuleError for the first problem.
	"""

	def pick(key):
		return raw.get(key, current.get(key))

	mode = str(pick("delivery_mode") or "free")
	if mode not in MODES:
		raise DeliveryRuleError(_("Choose free, flat, by area or by distance."))

	rules = {
		"delivery_mode": mode,
		"delivery_flat_fee": number(pick("delivery_flat_fee"), _("The flat delivery fee")),
		"delivery_areas": clean_areas(pick("delivery_areas")),
		"free_delivery_above": number(pick("free_delivery_above"), _("The free delivery amount")),
		"distance_base_km": number(pick("distance_base_km"), _("The included distance"), 0, 500),
		"distance_base_fee": number(pick("distance_base_fee"), _("The starting delivery fee")),
		"distance_per_km": number(pick("distance_per_km"), _("The fee for each extra kilometre")),
		"distance_max_km": number(pick("distance_max_km"), _("The longest delivery distance"), 0, 500),
		"road_factor": number(pick("road_factor") or DEFAULT_ROAD_FACTOR, _("The road factor"), 1, 3),
	}
	shop = coordinates(pick("shop_latitude"), pick("shop_longitude"))
	rules["shop_latitude"], rules["shop_longitude"] = shop if shop else (None, None)
	return rules


def problems_before_opening(rules: dict) -> list[str]:
	"""What is still missing for these rules to work, in the owner's words."""
	mode = rules["delivery_mode"]
	if mode == "area" and not rules["delivery_areas"]:
		return [_("at least one delivery area")]
	if mode == "distance":
		missing = []
		if rules["shop_latitude"] is None:
			missing.append(_("the shop's location"))
		if not rules["distance_max_km"]:
			missing.append(_("the longest delivery distance"))
		return missing
	return []
