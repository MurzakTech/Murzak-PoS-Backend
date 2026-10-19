"""
Pictures for the product import (ticket P7-1): a ZIP of photos, and picture links. No database, no Frappe.

Everything here takes plain bytes and text so each rule can be tested alone:

- `check_image_bytes`: is this a real jpg, png or webp, and at most 2 MB? The type is read from the first bytes of
  the file, never from its name, so a renamed program or an SVG with a script in it is refused.
- `read_zip`: opens a ZIP in memory (nothing is ever written to disk under a name from the ZIP, so a name such as
  `../../etc/x` cannot escape), with caps on the number of files and the size after unpacking, and returns the
  pictures by file name.
- `check_link` and `fetch_image`: a picture link is fetched by the server only for http and https on the usual
  ports, and never when the address is a private one (the shop's own network, the machine itself, the cloud
  metadata address). The check looks at every address the name resolves to, and the connection is made to the
  address that was checked, so a name that changes its answer between the check and the fetch cannot get through.
  Redirects are followed by hand, at most three, and each one is checked again.
"""

from __future__ import annotations

import io
import ipaddress
import re
import socket
import stat
import zipfile
from urllib.parse import urljoin, urlsplit

MAX_IMAGE_BYTES = 2 * 1024 * 1024
ALLOWED_TYPES = ("jpg", "png", "webp")
ALLOWED_EXTENSIONS = ("jpg", "jpeg", "png", "webp")

MAX_ZIP_BYTES = 60 * 1024 * 1024  # the ZIP file itself
MAX_ZIP_FILES = 1000  # entries in the ZIP
MAX_ZIP_UNPACKED = 250 * 1024 * 1024  # all entries together, as declared in the ZIP

MAX_LINK_LENGTH = 500
MAX_REDIRECTS = 3
ALLOWED_PORTS = (80, 443)

_CONTROL = re.compile(r"[\x00-\x1f\x7f\s]")


class ImageProblem(Exception):
	"""A picture, a ZIP or a link that cannot be used. `code` is a short word the API turns into a sentence."""

	def __init__(self, code):
		super().__init__(code)
		self.code = code


def squash(text) -> str:
	"""A file name or label for comparing: lower case, no spaces."""
	return re.sub(r"\s+", "", str(text or "")).casefold()


# --- the picture itself -----------------------------------------------------------------------------


def sniff_image(data: bytes):
	"""'jpg', 'png' or 'webp' from the first bytes of the file, or None for anything else."""
	if data[:3] == b"\xff\xd8\xff":
		return "jpg"
	if data[:8] == b"\x89PNG\r\n\x1a\n":
		return "png"
	if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
		return "webp"
	return None


def extension_of(name) -> str:
	"""The lower case ending of a file name without the dot ('' when there is none)."""
	text = str(name or "").strip().lower()
	return text.rsplit(".", 1)[1] if "." in text.rsplit("/", 1)[-1] else ""


def check_image_bytes(data: bytes):
	"""The kind of picture ('jpg', 'png', 'webp'). Raises ImageProblem for an empty, too big or unknown file."""
	if not data:
		raise ImageProblem("image_empty")
	if len(data) > MAX_IMAGE_BYTES:
		raise ImageProblem("image_big")
	kind = sniff_image(data)
	if kind is None:
		raise ImageProblem("image_type")
	return kind


def safe_file_name(code, kind) -> str:
	"""A file name for the saved picture: the product code (letters, numbers, dash) and the picture's real kind."""
	base = re.sub(r"[^A-Za-z0-9_-]+", "-", str(code or "")).strip("-")[:60] or "product"
	return f"{base}.{kind}"


# --- the ZIP ----------------------------------------------------------------------------------------


def clean_entry_name(name):
	"""
	The plain file name of a ZIP entry, or None when the entry should be ignored: folders, names that climb out
	with `..`, absolute paths, drive letters, hidden files and the `__MACOSX` copies a Mac adds.
	"""
	text = str(name or "").replace("\\", "/")
	if (
		not text
		or text.endswith("/")
		or text.startswith("/")
		or re.match(r"^[A-Za-z]:", text)
		or "\x00" in text
	):
		return None
	parts = text.split("/")
	if ".." in parts or "__MACOSX" in parts or any(p.startswith(".") for p in parts):
		return None
	return parts[-1] or None


def read_zip(data: bytes):
	"""
	Open a ZIP of photos in memory.

	Returns (photos, report). `photos` maps squash(file name) to {"name", "data", "kind"} for each usable picture
	and to {"name", "error"} for one that is not usable (too big, not a real picture, a second file with the same
	name). `report` is {"files": n pictures found, "ignored": n entries skipped}.
	Raises ImageProblem("zip_bad") for a file that is not a ZIP or is damaged or locked, "zip_big" when it is too
	big, "zip_many" when it holds too many files.
	"""
	if len(data) > MAX_ZIP_BYTES:
		raise ImageProblem("zip_big")
	try:
		archive = zipfile.ZipFile(io.BytesIO(data))
		infos = archive.infolist()
	except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError):
		raise ImageProblem("zip_bad")
	if len(infos) > MAX_ZIP_FILES:
		raise ImageProblem("zip_many")
	if sum(max(i.file_size, 0) for i in infos) > MAX_ZIP_UNPACKED:
		raise ImageProblem("zip_big")

	photos: dict = {}
	ignored = 0
	for info in infos:
		name = clean_entry_name(info.filename)
		link = stat.S_ISLNK(info.external_attr >> 16) if info.external_attr >> 16 else False
		if name is None or link or extension_of(name) not in ALLOWED_EXTENSIONS:
			if not info.is_dir():
				ignored += 1
			continue
		key = squash(name)
		if key in photos:
			photos[key] = {"name": name, "error": "image_twice"}
			continue
		if info.flag_bits & 0x1:
			photos[key] = {"name": name, "error": "zip_locked"}
			continue
		if info.file_size > MAX_IMAGE_BYTES:
			photos[key] = {"name": name, "error": "image_big"}
			continue
		try:
			with archive.open(info) as handle:
				content = handle.read(MAX_IMAGE_BYTES + 1)
			photos[key] = {"name": name, "data": content, "kind": check_image_bytes(content)}
		except ImageProblem as problem:
			photos[key] = {"name": name, "error": problem.code}
		except (zipfile.BadZipFile, OSError, RuntimeError, ValueError, NotImplementedError):
			photos[key] = {"name": name, "error": "zip_bad"}
	return photos, {"files": len(photos), "ignored": ignored}


def photo_index(photos: dict) -> dict:
	"""What the preview needs from the pictures: squash(name) -> None when usable, else the error code."""
	return {key: photo.get("error") for key, photo in photos.items()}


# --- picture links ----------------------------------------------------------------------------------


def is_public_address(text) -> bool:
	"""True only for an ordinary internet address. Private, loopback, link-local, reserved and multicast are not."""
	try:
		address = ipaddress.ip_address(str(text).split("%", 1)[0])
	except ValueError:
		return False
	mapped = getattr(address, "ipv4_mapped", None)
	if mapped is not None:
		address = mapped
	return address.is_global and not address.is_multicast


def _resolve(host, port, resolver):
	try:
		found = resolver(host, port, type=socket.SOCK_STREAM)
	except (OSError, UnicodeError):
		raise ImageProblem("link_unreachable")
	return [item[4][0] for item in found if item and item[4]]


def check_link(url, resolver=socket.getaddrinfo):
	"""
	Is the server allowed to fetch this link? Returns {"scheme", "host", "port", "target", "address"} where
	`address` is the checked address to connect to. Raises ImageProblem("link_refused") for a link that is not
	plain http or https, has a password in it, uses an odd port or points at a private address, and
	"link_unreachable" when the name does not resolve.
	"""
	text = str(url or "").strip()
	if not text or len(text) > MAX_LINK_LENGTH or _CONTROL.search(text):
		raise ImageProblem("link_refused")
	try:
		parts = urlsplit(text)
		port = parts.port
	except ValueError:
		raise ImageProblem("link_refused")
	if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
		raise ImageProblem("link_refused")
	port = port or (443 if parts.scheme == "https" else 80)
	if port not in ALLOWED_PORTS:
		raise ImageProblem("link_refused")
	addresses = _resolve(parts.hostname, port, resolver)
	if not addresses:
		raise ImageProblem("link_unreachable")
	# Every address the name gives must be public: one private answer among several is still refused
	if not all(is_public_address(a) for a in addresses):
		raise ImageProblem("link_refused")
	target = parts.path or "/"
	if parts.query:
		target += "?" + parts.query
	return {
		"scheme": parts.scheme,
		"host": parts.hostname,
		"port": port,
		"target": target,
		"address": addresses[0],
	}


def _open_pinned(link):
	"""One GET to the checked address (not to the name), with the name sent as the Host and, for https, as SNI."""
	import urllib3

	timeout = urllib3.Timeout(connect=5, read=10)
	if link["scheme"] == "https":
		pool = urllib3.HTTPSConnectionPool(
			link["address"],
			port=link["port"],
			timeout=timeout,
			retries=False,
			cert_reqs="CERT_REQUIRED",
			server_hostname=link["host"],
			assert_hostname=link["host"],
		)
	else:
		pool = urllib3.HTTPConnectionPool(link["address"], port=link["port"], timeout=timeout, retries=False)
	default_port = 443 if link["scheme"] == "https" else 80
	host_header = link["host"] if link["port"] == default_port else f"{link['host']}:{link['port']}"
	return pool.urlopen(
		"GET",
		link["target"],
		headers={
			"Host": host_header,
			"User-Agent": "MurzakPOS-import/1",
			"Accept": "image/jpeg,image/png,image/webp",
		},
		redirect=False,
		retries=False,
		preload_content=False,
	)


def fetch_image(url, resolver=socket.getaddrinfo, opener=_open_pinned):
	"""
	Fetch the picture a link points at and return (bytes, kind). Raises ImageProblem for a refused or unreachable
	link, a reply that is not a picture, or a picture over 2 MB. `resolver` and `opener` can be replaced in tests.
	"""
	current = str(url or "").strip()
	for _hop in range(MAX_REDIRECTS + 1):
		link = check_link(current, resolver)
		try:
			reply = opener(link)
		except ImageProblem:
			raise
		except Exception:  # urllib3 has its own error classes; all of them mean "could not fetch"
			raise ImageProblem("link_unreachable")
		try:
			status = int(getattr(reply, "status", 0))
			headers = getattr(reply, "headers", {}) or {}
			if status in (301, 302, 303, 307, 308) and headers.get("Location"):
				current = urljoin(current, headers.get("Location"))
				continue
			if status != 200:
				raise ImageProblem("link_unreachable")
			declared = headers.get("Content-Length")
			if declared and str(declared).isdigit() and int(declared) > MAX_IMAGE_BYTES:
				raise ImageProblem("image_big")
			content = bytearray()
			for chunk in reply.stream(64 * 1024):
				content.extend(chunk)
				if len(content) > MAX_IMAGE_BYTES:
					raise ImageProblem("image_big")
		except ImageProblem:
			raise
		except Exception:
			raise ImageProblem("link_unreachable")
		finally:
			try:
				reply.release_conn()
			except Exception:
				pass
		data = bytes(content)
		return data, check_image_bytes(data)
	raise ImageProblem("link_unreachable")
