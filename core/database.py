"""ROM database: built-in JSON database, user additions/edits, and imported BackBit sha.txt entries.

* ``data/roms_database.json`` (the master) holds curated entries.
* User additions / edits / deletions made in the Database Manager are written to BOTH the master file
  (so a shared folder or build includes them) AND an overlay file in the per-user app-data directory
  (``user_database.json``) which is re-applied on every start, so the changes survive an app update
  that replaces the master file.
* Imported ``sha.txt`` files (``<40-char SHA1> <description>``) are kept in ``imported_sha.json``.
* ROM packs (``.json``) let users share Database Manager additions: ``export_user_pack`` / ``plan_import`` +
  ``apply_import``. A full ``roms_database.json`` is also accepted as a pack.

Where these files live is decided by :mod:`core.paths` (OS-native user data folder or portable mode).
"""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from core.paths import APP_DIR_NAME, master_db_path, master_db_writable, user_data_dir  # noqa: F401 (re-export)

DEFAULT_DB_PATH = master_db_path()
PACK_FORMAT = "backbit-rom-pack"
PACK_VERSION = 1
VALID_PINS = (24, 28, 32, 40, 42)
USER_SOURCE = "User entry (Database Manager)"

_SHA_LINE = re.compile(r"^\s*([0-9a-fA-F]{40})(?:\s+\*?(.*?))?\s*$")

# Pin count of common EPROM / mask ROM types and BackBit menu settings, used to guess a setting's package.
KNOWN_PINS = {
    "2316": 24, "2716": 24, "2332": 24, "2532": 24, "2732": 24, "2364": 24, "68364": 24, "68764": 24, "68766": 24,
    "2564": 28, "2764": 28, "23128": 28, "27128": 28, "23256": 28, "27256": 28, "23512": 28, "27512": 28,
    "231024": 28, "531000": 28, "6540": 28,
    "27C010": 32, "27C1001": 32, "27C020": 32, "27C040": 32, "27C4001": 32, "27C080": 32,
    "27100-0": 32, "27100-1": 32, "27200": 32, "27040": 32, "27080": 32,
    "27400": 40, "271024": 40, "27800": 42,
}

# The BackBit ROM menu (store.backbit.io product page, confirmed on a Chip Tester Pro): 24-pin 2316 2716 2332
# 2532 2732 2364; 28-pin 6540 2564 2764 27128 27256 27512 531000; 32-pin 27100-0 / 27100-1 (128K) 27200 (256K)
# 27040 (512K) 27080 (1M); 40-pin 271024 27400; 42-pin 27800. Chip types the menu lists under another name, or
# not at all, are mapped below to the entry to use.


def _mask_select_warning(mask: str, setting: str, pins: str) -> str:
    return (f"The BackBit has no {mask} setting, so this mask ROM is read with the {setting} setting. Address and "
            f"data pins match, but mask ROM chip selects ({pins}) have factory-programmed polarity. If the read is "
            f"blank or every byte is the same, check the chip's data sheet: lift any active-HIGH select out of the "
            f"tester with an interposer socket and tie it to Pin 28 (+5V).")


PIN27_WARNING_SINCLAIR = (
    "Use an interposer socket to tie Pin 27 to Pin 28 (+5V). Sinclair 23128 mask ROMs use Pin 27 as an "
    "active-high CS2, whereas the BackBit 27128 setting expects it to be /PGM. Failing to pull Pin 27 high "
    "will result in a blank read.")
PIN27_WARNING_23128 = (
    "Use an interposer socket to tie Pin 27 to Pin 28 (+5V). The BackBit has no 23128 setting, so this mask "
    "ROM is read with the 27128 setting, which treats Pin 27 as the EPROM's /PGM. On 23128 mask ROMs Pin 27 is "
    "a chip select (CS2, active-high on most parts); failing to pull Pin 27 high will result in a blank read. "
    "Mask ROM select polarities are factory-programmed: if this chip's datasheet shows Pin 27 as an "
    "active-LOW select, tie it to Pin 14 (GND) instead.")
WARNING_23256 = _mask_select_warning("23256", "27256", "Pins 20 and 22, and Pin 1 on some makers' parts")
WARNING_23512 = _mask_select_warning("23512", "27512", "Pins 20 and 22")
WARNING_531000 = (
    "Read with the BackBit 531000 setting (28-pin 1Mbit mask ROM). 28-pin 1Mbit mask ROMs are not standardised: "
    "the top address lines and the chip select differ between makers. If the read is blank, or the image repeats "
    "itself, compare this chip's pinout with a 531000 data sheet before trusting the dump.")
WARNING_68364 = (
    "The BackBit has no 68364 setting. The MCM68364 has the same pin layout as the 2364 (A11 on Pin 18, A12 on "
    "Pin 21, chip select on Pin 20), so it is read with the 2364 setting. Its chip-select polarity is set at the "
    "factory: if the read is blank, use an interposer socket to tie Pin 20 to Pin 24 (+5V) for an active-high "
    "select, or to Pin 12 (GND) for an active-low one.")
WARNING_68764 = (
    "The BackBit has no 68764 / 68766 setting. These EPROMs have the 2364 pin layout (A11 on Pin 18, A12 on "
    "Pin 21, /E or /G on Pin 20), so they are read with the 2364 setting. If the read is blank, use an interposer "
    "socket to tie Pin 20 to Pin 12 (GND).")
NOTE_27100 = ("The menu has two 128K entries, 27100-0 and 27100-1. Read with 27100-0 first; if the read fails or "
              "is blank, try 27100-1.")

# chip type -> (BackBit menu setting, warning attached to options read this way, chip text when it lacks the type)
SETTING_MAP: dict[str, tuple[str, str, str]] = {
    "23128": ("27128", PIN27_WARNING_23128, "23128 type"),
    "23256": ("27256", WARNING_23256, "23256 type"),
    "23512": ("27512", WARNING_23512, "23512 type"),
    "231024": ("531000", WARNING_531000, ""),
    "68364": ("2364", WARNING_68364, "MCM68364 type"),
    "68A364": ("2364", WARNING_68364, "MCM68364 type"),
    "68764": ("2364", WARNING_68764, "MCM68764 type"),
    "68766": ("2364", WARNING_68764, "MCM68766 type"),
    # CMOS / JEDEC EPROM names -> the menu's names (same pinout, no interposer)
    "27C16": ("2716", "", ""), "27C32": ("2732", "", ""), "27C64": ("2764", "", ""), "27C128": ("27128", "", ""),
    "27C256": ("27256", "", ""), "27C512": ("27512", "", ""),
    "27C010": ("27100-0", "", ""), "27C1001": ("27100-0", "", ""), "27C100": ("27100-0", "", ""),
    "27010": ("27100-0", "", ""), "27C020": ("27200", "", ""), "27C2001": ("27200", "", ""),
    "27020": ("27200", "", ""), "27C040": ("27040", "", ""), "27C4001": ("27040", "", ""),
    "27C080": ("27080", "", ""), "27C8001": ("27080", "", ""),
    "27C400": ("27400", "", ""), "27C1024": ("271024", "", ""), "27C800": ("27800", "", ""),
}
# Backwards-compatible view: chip type -> menu setting.
NO_NATIVE_SETTING = {k: v[0] for k, v in SETTING_MAP.items()}


def normalize_backbit_option(opt: dict) -> dict:
    """Map a chip type the tester's menu doesn't list (23128, 23256, 68364, 27C010 ...) to the menu setting to use
    and make sure a mask ROM read that way carries its interposer / select warning. Also covers an option already
    on the menu setting whose chip is such a mask ROM (as entered in the Database Manager), e.g. a 27128 option for
    a "23128 mask ROM". A chip described as compatible with the menu setting (e.g. "27256-compatible") gets no
    warning. Returns a new dict; an existing warning is kept."""
    opt = dict(opt)
    setting = str(opt.get("setting", "")).strip()
    chip = str(opt.get("chip", ""))
    mapped = SETTING_MAP.get(setting.upper())
    warning = ""
    if mapped:
        target, warning, chip_note = mapped
        opt["setting"] = target
        if chip_note and setting.upper() not in chip.upper():
            opt["chip"] = f"{chip or 'Original mask ROM'} ({chip_note})".strip()
    else:
        for src, (target, warn, _note) in SETTING_MAP.items():
            if warn and setting.upper() == target.upper() and src in chip.upper() and "mask" in chip.lower():
                warning = warn
                break
    # a chip documented as pin-compatible with the setting it is read with (e.g. "27256-compatible": Sinclair
    # specified the Spectrum 128 ROM that way) reads directly, so the generic select-polarity warning is skipped
    if warning and f"{opt['setting']}-compatible".upper() in str(opt.get("chip", "")).upper():
        warning = ""
    if warning and not str(opt.get("warning", "")).strip():
        opt["warning"] = warning
    return opt


def guess_pins(setting: str, default: int) -> int:
    def norm(x: str) -> str:
        return x.upper().replace(" ", "").replace("27C", "27")
    s = norm(setting)
    for key, pins in KNOWN_PINS.items():
        if s == norm(key):
            return pins
    return default


def format_size(size: int) -> str:
    if size >= 1024 and size % 1024 == 0:
        return f"{size:,} bytes ({size // 1024}KB)"
    if size >= 1024:
        return f"{size:,} bytes ({size / 1024:.2f}KB)"
    return f"{size:,} bytes"


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s or "item"


def _atomic_write_json(path: Path, data: dict, indent: int | None = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=indent, ensure_ascii=False) + "\n"
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="." + path.stem, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@dataclass(frozen=True)
class BackBitOption:
    setting: str
    pins: int
    chip: str
    warning: str = ""          # hardware / polarity warning specific to reading this chip with this setting

    @property
    def menu_text(self) -> str:
        # The BackBit menu lists ROM tests by pin count first, then by chip number.
        return f"Menu: ROM -> {self.pins}-Pin -> {self.setting}"


@dataclass(frozen=True)
class RomBank:
    name: str
    offset: int
    size: int
    sha1: str
    crc32: str | None
    label: str = ""


@dataclass
class RomEntry:
    id: str
    machine_id: str
    machine_name: str
    name: str
    description: str
    part_numbers: list[str]
    socket: str
    size: int
    pins: int
    chip_type: str
    backbit: list[BackBitOption]
    sha1: str | None
    crc32: str | None
    source: str
    replacement: str
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    banks: list[RomBank] = field(default_factory=list)
    label: str = ""
    machine_short: str = ""
    manufacturer: str = ""
    user_modified: bool = False

    @property
    def title(self) -> str:
        return f"{self.machine_name} — {self.name}"

    @property
    def is_user_entry(self) -> bool:
        return self.user_modified or self.source == USER_SOURCE


@dataclass
class Machine:
    id: str
    name: str
    manufacturer: str
    year: int | None
    roms: list[RomEntry]
    short: str = ""


@dataclass(frozen=True)
class Match:
    """One database hit for a SHA-1 (or CRC32) lookup."""
    sha1: str
    description: str
    source: str               # "builtin" or "sha.txt"
    rom: RomEntry | None = None
    bank: RomBank | None = None  # set when the hash matched one bank of a multi-bank chip

    @property
    def label(self) -> str:
        if self.rom is None:
            return self.description
        if self.bank is not None:
            return f"{self.rom.title} — {self.bank.name}"
        return self.rom.title


@dataclass
class ImportResult:
    path: str
    lines_read: int = 0
    added: int = 0
    duplicates: int = 0
    invalid: int = 0


@dataclass
class SaveResult:
    rom_id: str
    master_written: bool
    message: str = ""


class DatabaseError(Exception):
    pass


@dataclass
class PackItem:
    machine_name: str
    manufacturer: str
    short: str
    year: int | None
    rom: dict                 # normalised master-format ROM dict (keeps the pack's "id" if any)
    target_id: str | None     # existing ROM id to replace (conflicts only)


@dataclass
class ImportPlan:
    """Dry-run result of a ROM pack import; nothing is written until ``apply_import``."""
    source: str
    to_add: list[PackItem] = field(default_factory=list)
    conflicts: list[PackItem] = field(default_factory=list)   # same id, different content
    unchanged: int = 0                                          # same id, identical content
    duplicates: int = 0                                         # SHA-1 already known under another id
    invalid: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.to_add) + len(self.conflicts) + self.unchanged + self.duplicates + len(self.invalid)


@dataclass
class PackImportResult:
    added: int
    replaced: int
    skipped: int
    master_written: bool
    message: str = ""


def validate_rom_dict(r: dict) -> tuple[dict | None, str]:
    """Return (normalised master-format ROM dict, "") or (None, error message)."""
    if not isinstance(r, dict):
        return None, "entry is not an object"
    name = str(r.get("name", "")).strip()
    if not name:
        return None, "missing ROM name"
    try:
        size, pins = int(r.get("size", 0)), int(r.get("pins", 0))
    except (TypeError, ValueError):
        return None, f"{name}: size/pins must be integers"
    if size <= 0 or size > 64 * 1024 * 1024:
        return None, f"{name}: invalid size {size}"
    if pins not in VALID_PINS:
        return None, f"{name}: pins must be one of {VALID_PINS}"
    sha1, crc = r.get("sha1"), r.get("crc32")
    if sha1 and not _norm_sha(sha1):
        return None, f"{name}: invalid SHA-1"
    if crc and not _norm_crc(crc):
        return None, f"{name}: invalid CRC32"
    backbit = []
    for o in r.get("backbit") or []:
        if not isinstance(o, dict) or not str(o.get("setting", "")).strip():
            return None, f"{name}: invalid BackBit setting"
        try:
            opt = {"setting": str(o["setting"]).strip(), "pins": int(o.get("pins", pins)),
                   "chip": str(o.get("chip", ""))}
            if str(o.get("warning", "")).strip():
                opt["warning"] = str(o["warning"]).strip()
            backbit.append(normalize_backbit_option(opt))
        except (TypeError, ValueError):
            return None, f"{name}: invalid BackBit pin count"
    if not backbit:
        return None, f"{name}: at least one BackBit setting is required"
    banks = []
    for b in r.get("banks") or []:
        try:
            if not _norm_sha(b.get("sha1")):
                raise ValueError
            banks.append({"name": str(b.get("name", "")), "label": str(b.get("label", "")),
                          "offset": int(b["offset"]), "size": int(b["size"]), "sha1": _norm_sha(b["sha1"]),
                          "crc32": _norm_crc(b.get("crc32"))})
        except (TypeError, ValueError, KeyError, AttributeError):
            return None, f"{name}: invalid bank entry"

    def strs(key):
        v = r.get(key) or []
        return [str(x) for x in (v if isinstance(v, list) else [v])]

    out = {
        "id": str(r.get("id", "")).strip(), "name": name, "label": str(r.get("label", ""))[:64],
        "description": str(r.get("description", "")), "part_numbers": strs("part_numbers"),
        "socket": str(r.get("socket", "")), "size": size, "pins": pins, "chip_type": str(r.get("chip_type", "")),
        "backbit": backbit, "sha1": _norm_sha(sha1), "crc32": _norm_crc(crc), "source": str(r.get("source", "")),
        "replacement": str(r.get("replacement", "")), "warnings": strs("warnings"), "notes": strs("notes"),
    }
    if banks:
        out["banks"] = banks
    return out, ""


def _comparable(d: dict) -> dict:
    """ROM dict reduced to its hardware content (ids and provenance don't make two entries different)."""
    d = dict(d)
    for key in ("id", "machine_id", "source"):
        d.pop(key, None)
    d.setdefault("banks", [])
    return d


def rom_to_dict(rom: RomEntry) -> dict:
    d = {
        "id": rom.id, "name": rom.name, "label": rom.label, "description": rom.description,
        "part_numbers": list(rom.part_numbers), "socket": rom.socket, "size": rom.size, "pins": rom.pins,
        "chip_type": rom.chip_type,
        "backbit": [{"setting": o.setting, "pins": o.pins, "chip": o.chip,
                     **({"warning": o.warning} if o.warning else {})} for o in rom.backbit],
        "sha1": rom.sha1, "crc32": rom.crc32, "source": rom.source, "replacement": rom.replacement,
        "warnings": list(rom.warnings), "notes": list(rom.notes),
    }
    if rom.banks:
        d["banks"] = [{"name": b.name, "label": b.label, "offset": b.offset, "size": b.size,
                       "sha1": b.sha1, "crc32": b.crc32} for b in rom.banks]
    return d


class RomDatabase:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH, user_store: Path | str | None = None,
                 user_db: Path | str | None = None):
        self.db_path = Path(db_path)
        self.master_writable = master_db_writable(self.db_path)
        self.user_store = Path(user_store) if user_store else user_data_dir() / "imported_sha.json"
        self.user_db_path = Path(user_db) if user_db else self.user_store.parent / "user_database.json"
        self.machines: list[Machine] = []
        self._raw: dict = {}
        self._overlay: dict = {"schema_version": 1, "machines": {}, "roms": {}, "deleted": []}
        self._roms_by_id: dict[str, RomEntry] = {}
        self._builtin_sha: dict[str, list[Match]] = {}
        self._builtin_crc: dict[str, list[Match]] = {}
        # sha1 -> list of descriptions (several sha.txt files can describe the same hash)
        self._imported: dict[str, list[str]] = {}
        self.load()

    # ------------------------------------------------------------------ loading
    def load(self) -> None:
        try:
            self._raw = json.loads(self.db_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise DatabaseError(f"ROM database not found: {self.db_path}") from exc
        except json.JSONDecodeError as exc:
            raise DatabaseError(f"ROM database is not valid JSON ({self.db_path}): {exc}") from exc
        self._load_overlay()
        self._apply_overlay(self._raw)
        self._parse(self._raw)
        self._load_imported()

    def _load_overlay(self) -> None:
        self._overlay = {"schema_version": 1, "machines": {}, "roms": {}, "deleted": []}
        if not self.user_db_path.exists():
            return
        try:
            data = json.loads(self.user_db_path.read_text(encoding="utf-8"))
            self._overlay["machines"] = dict(data.get("machines", {}))
            self._overlay["roms"] = dict(data.get("roms", {}))
            self._overlay["deleted"] = list(data.get("deleted", []))
        except (OSError, json.JSONDecodeError):
            try:
                self.user_db_path.replace(self.user_db_path.with_suffix(".corrupt.json"))
            except OSError:
                pass

    def _apply_overlay(self, raw: dict) -> None:
        """Merge user additions/edits/deletions into a master-format dict (idempotent)."""
        machines = raw.setdefault("machines", [])
        by_id = {m["id"]: m for m in machines}
        deleted = set(self._overlay["deleted"])
        for m in machines:
            m["roms"] = [r for r in m.get("roms", []) if r["id"] not in deleted]
        for rom_id, entry in self._overlay["roms"].items():
            entry = copy.deepcopy(entry)
            mid = entry.pop("machine_id")
            for m in machines:  # remove from wherever it lives now (it may have moved machine)
                m["roms"] = [r for r in m["roms"] if r["id"] != rom_id]
            if mid not in by_id:
                info = self._overlay["machines"].get(mid, {"name": mid})
                by_id[mid] = {"id": mid, "name": info.get("name", mid), "short": info.get("short", ""),
                              "manufacturer": info.get("manufacturer", ""), "year": info.get("year"), "roms": []}
                machines.append(by_id[mid])
            by_id[mid]["roms"].append(entry)
        for mid, info in self._overlay["machines"].items():
            if mid in by_id:
                for k in ("name", "manufacturer", "short"):
                    if info.get(k):
                        by_id[mid][k] = info[k]
        raw["machines"] = [m for m in machines if m["roms"]]

    def _parse(self, raw: dict) -> None:
        self.machines.clear()
        self._roms_by_id.clear()
        self._builtin_sha.clear()
        self._builtin_crc.clear()
        user_ids = set(self._overlay["roms"])

        for m in raw.get("machines", []):
            machine = Machine(m["id"], m["name"], m.get("manufacturer", ""), m.get("year"), [], m.get("short", ""))
            for r in m.get("roms", []):
                rom = RomEntry(
                    id=r["id"], machine_id=machine.id, machine_name=machine.name, name=r["name"],
                    description=r.get("description", ""), part_numbers=list(r.get("part_numbers", [])),
                    socket=r.get("socket", ""), size=int(r["size"]), pins=int(r["pins"]),
                    chip_type=r.get("chip_type", ""),
                    backbit=[BackBitOption(n["setting"], int(n["pins"]), n.get("chip", ""), n.get("warning", ""))
                             for n in map(normalize_backbit_option, r.get("backbit", []))],
                    sha1=_norm_sha(r.get("sha1")), crc32=_norm_crc(r.get("crc32")),
                    source=r.get("source", ""), replacement=r.get("replacement", ""),
                    warnings=list(r.get("warnings", [])), notes=list(r.get("notes", [])),
                    banks=[RomBank(b["name"], int(b["offset"]), int(b["size"]), _norm_sha(b["sha1"]),
                                   _norm_crc(b.get("crc32")), b.get("label", ""))
                           for b in r.get("banks", []) if _norm_sha(b.get("sha1"))],
                    label=r.get("label", ""), machine_short=machine.short, manufacturer=machine.manufacturer,
                    user_modified=r["id"] in user_ids,
                )
                machine.roms.append(rom)
                self._roms_by_id[rom.id] = rom
                if rom.sha1:
                    self._builtin_sha.setdefault(rom.sha1, []).append(Match(rom.sha1, rom.title, "builtin", rom))
                if rom.crc32:
                    self._builtin_crc.setdefault(rom.crc32, []).append(Match(rom.sha1 or "", rom.title, "builtin", rom))
                for bank in rom.banks:
                    match = Match(bank.sha1, f"{rom.title} — {bank.name}", "builtin", rom, bank)
                    self._builtin_sha.setdefault(bank.sha1, []).append(match)
                    if bank.crc32:
                        self._builtin_crc.setdefault(bank.crc32, []).append(match)
            self.machines.append(machine)

    def _load_imported(self) -> None:
        self._imported = {}
        if not self.user_store.exists():
            return
        try:
            data = json.loads(self.user_store.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            try:
                self.user_store.replace(self.user_store.with_suffix(".corrupt.json"))
            except OSError:
                pass
            return
        for sha, descs in data.get("entries", {}).items():
            sha = _norm_sha(sha)
            if sha and isinstance(descs, list):
                self._imported[sha] = [str(d) for d in descs]

    def _save_imported(self) -> None:
        _atomic_write_json(self.user_store, {"schema_version": 1, "entries": self._imported})

    # ------------------------------------------------------------------ queries
    @property
    def builtin_rom_count(self) -> int:
        return len(self._roms_by_id)

    @property
    def builtin_hash_count(self) -> int:
        return len(self._builtin_sha)

    @property
    def imported_count(self) -> int:
        return len(self._imported)

    @property
    def user_rom_count(self) -> int:
        return len(self._overlay["roms"])

    def imported_entries(self) -> list[tuple[str, str]]:
        return [(sha, desc) for sha, descs in self._imported.items() for desc in descs]

    def rom_by_id(self, rom_id: str) -> RomEntry | None:
        return self._roms_by_id.get(rom_id)

    def machine_by_id(self, machine_id: str) -> Machine | None:
        return next((m for m in self.machines if m.id == machine_id), None)

    def all_roms(self) -> list[RomEntry]:
        return [rom for m in self.machines for rom in m.roms]

    def lookup_sha1(self, sha1: str) -> list[Match]:
        sha1 = _norm_sha(sha1)
        if not sha1:
            return []
        matches = list(self._builtin_sha.get(sha1, []))
        builtin_labels = {m.label.lower() for m in matches}
        for desc in self._imported.get(sha1, []):
            if desc.lower() not in builtin_labels:
                matches.append(Match(sha1, desc, "sha.txt"))
        return matches

    def lookup_crc32(self, crc32: str) -> list[Match]:
        """CRC32 hits from the curated DB (sha.txt files carry no CRC)."""
        return list(self._builtin_crc.get(_norm_crc(crc32) or "", []))

    def search(self, text: str) -> list[RomEntry]:
        """Case-insensitive search over machine, ROM name, part numbers, socket, chip type and settings."""
        terms = [t for t in text.lower().split() if t]
        if not terms:
            return self.all_roms()
        out = []
        for rom in self.all_roms():
            hay = " ".join([rom.machine_name, rom.manufacturer, rom.name, rom.description, rom.socket,
                            rom.chip_type, rom.label, " ".join(rom.part_numbers),
                            " ".join(o.setting for o in rom.backbit)]).lower()
            if all(t in hay for t in terms):
                out.append(rom)
        return out

    # ------------------------------------------------------------------ Database Manager (add / edit / delete)
    def machine_id_for_name(self, name: str) -> str | None:
        name_l = name.strip().lower()
        for m in self.machines:
            if m.name.lower() == name_l:
                return m.id
        for mid, info in self._overlay["machines"].items():
            if info.get("name", "").lower() == name_l:
                return mid
        return None

    def new_rom_id(self, machine_id: str, rom_name: str) -> str:
        base = slugify(f"{machine_id}_{rom_name}")[:60]
        rid, n = base, 2
        while rid in self._roms_by_id or rid in self._overlay["roms"]:
            rid, n = f"{base}_{n}", n + 1
        return rid

    def upsert_rom(self, machine_name: str, manufacturer: str, rom: dict, rom_id: str | None = None,
                   write_master: bool = True) -> SaveResult:
        """Add (rom_id=None) or replace a ROM entry. ``rom`` is a master-format ROM dict without 'id'.

        Returns whether the master JSON could also be updated (it may be read-only in a packaged build)."""
        rid = self._stage_rom(machine_name, manufacturer, rom, rom_id)
        return self._commit(rid, write_master)

    def _stage_rom(self, machine_name: str, manufacturer: str, rom: dict, rom_id: str | None = None,
                   short: str = "", year: int | None = None) -> str:
        """Put one ROM into the overlay without writing anything to disk. Returns its id."""
        machine_name = machine_name.strip()
        if not machine_name:
            raise ValueError("Machine name is required.")
        if not str(rom.get("name", "")).strip():
            raise ValueError("ROM name is required.")
        mid = self.machine_id_for_name(machine_name) or ("user_" + slugify(machine_name))
        info = dict(self._overlay["machines"].get(mid, {}))
        existing_machine = self.machine_by_id(mid)
        info["name"] = machine_name
        if manufacturer.strip():
            info["manufacturer"] = manufacturer.strip()
        elif existing_machine:
            info.setdefault("manufacturer", existing_machine.manufacturer)
        if existing_machine and existing_machine.short:
            info.setdefault("short", existing_machine.short)
        if short and not info.get("short"):
            info["short"] = short
        if year and not info.get("year"):
            info["year"] = year
        self._overlay["machines"][mid] = info

        rid = rom_id or self.new_rom_id(mid, rom["name"])
        entry = copy.deepcopy(rom)
        entry["id"] = rid
        entry["machine_id"] = mid
        entry["sha1"] = _norm_sha(entry.get("sha1"))
        entry["crc32"] = _norm_crc(entry.get("crc32"))
        self._overlay["roms"][rid] = entry
        if rid in self._overlay["deleted"]:
            self._overlay["deleted"].remove(rid)
        return rid

    def delete_rom(self, rom_id: str, write_master: bool = True) -> SaveResult:
        if rom_id not in self._roms_by_id:
            raise KeyError(rom_id)
        self._overlay["roms"].pop(rom_id, None)
        if rom_id not in self._overlay["deleted"]:
            self._overlay["deleted"].append(rom_id)
        return self._commit(rom_id, write_master)

    def _commit(self, rom_id: str, write_master: bool) -> SaveResult:
        _atomic_write_json(self.user_db_path, self._overlay)
        self._apply_overlay(self._raw)
        master_ok, msg = False, ""
        if write_master and not self.master_writable:
            msg = ("Saved to your user database. The bundled master database is read-only in a packaged build; "
                   "use File > Export User Database Additions to share your entries.")
        elif write_master:
            try:
                _atomic_write_json(self.db_path, self._raw, indent=2)
                master_ok = True
            except OSError as exc:
                msg = (f"Saved to your user database, but the master file could not be updated "
                       f"({self.db_path}): {exc}")
        self._parse(self._raw)
        return SaveResult(rom_id, master_ok, msg)

    # ------------------------------------------------------------------ ROM pack export / import
    def export_user_pack(self, path: Path | str, app_version: str = "") -> int:
        """Write every user-added or user-edited ROM to a shareable pack. Returns the ROM count."""
        machines: dict[str, dict] = {}
        for rid in self._overlay["roms"]:
            rom = self.rom_by_id(rid)
            if rom is None:
                continue
            m = self.machine_by_id(rom.machine_id)
            entry = machines.setdefault(rom.machine_id, {
                "id": rom.machine_id, "name": rom.machine_name, "short": m.short if m else "",
                "manufacturer": rom.manufacturer, "year": m.year if m else None, "roms": []})
            entry["roms"].append(rom_to_dict(rom))
        count = sum(len(m["roms"]) for m in machines.values())
        if not count:
            raise ValueError("There are no user-added or edited ROMs to export.")
        pack = {"format": PACK_FORMAT, "format_version": PACK_VERSION, "app": "BackBit ROM Companion",
                "app_version": app_version, "exported": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "rom_count": count, "machines": list(machines.values())}
        _atomic_write_json(Path(path), pack, indent=2)
        return count

    def plan_import(self, source: Path | str | dict) -> ImportPlan:
        """Dry run: classify every ROM of a pack (or a full roms_database.json) against the current DB."""
        if isinstance(source, dict):
            data, label = source, "(memory)"
        else:
            label = str(source)
            try:
                data = json.loads(self.read_text_file(source))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Not a valid JSON file: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("machines"), list):
            raise ValueError("Not a BackBit ROM pack or ROM database (no 'machines' list).")
        if data.get("format") not in (None, PACK_FORMAT):
            raise ValueError(f"Unknown pack format: {data.get('format')!r}")
        if int(data.get("format_version", PACK_VERSION) or PACK_VERSION) > PACK_VERSION:
            raise ValueError("This pack was written by a newer version of the app - please update to import it.")
        plan = ImportPlan(label)
        known_sha = {r.sha1 for r in self.all_roms() if r.sha1}
        seen_sha: set[str] = set()
        for m in data["machines"]:
            if not isinstance(m, dict) or not str(m.get("name", "")).strip():
                plan.invalid.append("machine without a name")
                continue
            year = m.get("year") if isinstance(m.get("year"), int) else None
            for raw in m.get("roms") or []:
                rom, err = validate_rom_dict(raw)
                if rom is None:
                    plan.invalid.append(f"{m['name']}: {err}")
                    continue
                item = PackItem(str(m["name"]).strip(), str(m.get("manufacturer", "")), str(m.get("short", "")),
                                year, rom, None)
                existing = self.rom_by_id(rom["id"]) if rom["id"] else None
                if existing is not None:
                    if _comparable(rom_to_dict(existing)) == _comparable(rom):
                        plan.unchanged += 1
                    else:
                        item.target_id = existing.id
                        plan.conflicts.append(item)
                elif rom["sha1"] and (rom["sha1"] in known_sha or rom["sha1"] in seen_sha):
                    plan.duplicates += 1
                else:
                    plan.to_add.append(item)
                if rom["sha1"]:
                    seen_sha.add(rom["sha1"])
        return plan

    def apply_import(self, plan: ImportPlan, replace_conflicts: bool = False,
                     write_master: bool = True) -> PackImportResult:
        """Write a planned import (one disk commit). Conflicts are only replaced when asked to."""
        items = list(plan.to_add) + (list(plan.conflicts) if replace_conflicts else [])
        added = replaced = 0
        for item in items:
            rom = dict(item.rom)
            pack_id = rom.pop("id", "")
            if item.target_id:
                rid = item.target_id
                replaced += 1
            else:
                free = pack_id and pack_id not in self._roms_by_id and pack_id not in self._overlay["roms"]
                rid = pack_id if free else None
                added += 1
            if not rom.get("source"):
                rom["source"] = f"Imported pack: {Path(plan.source).name}"
            self._stage_rom(item.machine_name, item.manufacturer, rom, rid, item.short, item.year)
        skipped = plan.total - added - replaced - len(plan.invalid)
        if not items:
            return PackImportResult(0, 0, skipped, False, "Nothing new to import.")
        res = self._commit("", write_master)
        return PackImportResult(added, replaced, skipped, res.master_written, res.message)

    # ------------------------------------------------------------------ sha.txt import
    @staticmethod
    def parse_sha_txt(text: str) -> tuple[list[tuple[str, str]], int]:
        """Parse BackBit sha.txt content. Returns ([(sha1, description)], invalid_line_count)."""
        entries: list[tuple[str, str]] = []
        invalid = 0
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", ";", "//")):
                continue
            m = _SHA_LINE.match(stripped)
            if not m:
                invalid += 1
                continue
            desc = (m.group(2) or "").strip() or "(no description)"
            entries.append((m.group(1).lower(), desc))
        return entries, invalid

    @staticmethod
    def read_text_file(path: Path | str) -> str:
        raw = Path(path).read_bytes()
        for enc in ("utf-8-sig", "cp1252"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("latin-1")

    def import_sha_txt(self, path: Path | str) -> ImportResult:
        entries, invalid = self.parse_sha_txt(self.read_text_file(path))
        result = ImportResult(str(path), lines_read=len(entries) + invalid, invalid=invalid)
        for sha, desc in entries:
            descs = self._imported.setdefault(sha, [])
            if any(d.lower() == desc.lower() for d in descs):
                result.duplicates += 1
            else:
                descs.append(desc)
                result.added += 1
        if result.added:
            self._save_imported()
        return result

    def clear_imported(self) -> int:
        count = len(self._imported)
        self._imported = {}
        if self.user_store.exists():
            self.user_store.unlink()
        return count


def _norm_sha(value: str | None) -> str | None:
    if not value:
        return None
    value = str(value).strip().lower()
    return value if re.fullmatch(r"[0-9a-f]{40}", value) else None


def _norm_crc(value: str | None) -> str | None:
    if not value:
        return None
    value = str(value).strip().lower().removeprefix("0x")
    return value.zfill(8) if re.fullmatch(r"[0-9a-f]{1,8}", value) else None
