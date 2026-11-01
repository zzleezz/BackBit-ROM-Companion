"""BackBit sha.txt generation helpers (Qt-free).

File format (BackBit Chip Tester Pro manual): exactly the output of the Linux ``sha1sum`` command -
``<40 lower-case hex SHA-1><two spaces><label>``, e.g.::

    b376885ac8452b6cbf9ced81b1080bfd570d9b91  ZEROES

The tester shows the label on its 16x2 character LCD when a dumped chip's SHA-1 matches, so labels are
limited to 16 printable ASCII characters (one LCD line).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from core.database import RomDatabase

LCD_COLUMNS = 16                 # BackBit 16x2 character LCD
DEFAULT_LABEL_LIMIT = LCD_COLUMNS
DEFAULT_UPPERCASE = False        # sha1sum output is lower case
SEPARATOR = "  "                 # sha1sum separates hash and name with exactly two spaces

SOURCE_BUILTIN = "Built-in Database"
SOURCE_IMPORTED = "Imported sha.txt"
SOURCE_FILE = "Custom Added File"
SOURCE_MERGED = "Merged sha.txt"
SOURCE_USER = "User Database Entry"


@dataclass
class ShaEntry:
    sha1: str
    label: str
    source: str
    include: bool = True
    note: str = ""          # e.g. originating file name or ROM title (not written to sha.txt)


def sanitize_label(label: str) -> str:
    """Single-line printable ASCII (the LCD character set is ASCII); collapses whitespace."""
    label = label.replace("—", "-").replace("–", "-").replace("→", ">")
    label = re.sub(r"\s+", " ", label).strip()
    return "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in label)


def auto_label(machine_short: str, rom_name: str, socket: str = "", limit: int = DEFAULT_LABEL_LIMIT) -> str:
    """Build a succinct label such as 'C64 KERNAL U4' from database fields."""
    name = re.sub(r"\(.*?\)", "", rom_name)
    name = re.sub(r"\b(ROM|rom)\b", "", name)
    name = re.sub(r"\s+", " ", name.replace(" - ", " ")).strip()
    sock = socket.split(" ")[0] if socket and not socket.lower().startswith("see") else ""
    parts = [p for p in (machine_short, name, sock) if p]
    label = sanitize_label(" ".join(parts))
    if len(label) > limit and sock:
        label = sanitize_label(" ".join(p for p in (machine_short, name) if p))
    return label[:limit].rstrip() if len(label) > limit else label


def is_valid_sha1(sha1: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{40}", sha1 or ""))


def builtin_entries(db: RomDatabase, limit: int = DEFAULT_LABEL_LIMIT) -> list[ShaEntry]:
    """One entry per whole-chip hash and per bank hash in the curated (and user) database."""
    out: list[ShaEntry] = []
    seen: set[str] = set()
    for rom in db.all_roms():
        short = rom.machine_short or rom.machine_name.split(" ")[0]
        source = SOURCE_USER if rom.is_user_entry else SOURCE_BUILTIN
        if rom.sha1 and rom.sha1 not in seen:
            seen.add(rom.sha1)
            label = sanitize_label(rom.label) if rom.label else auto_label(short, rom.name, rom.socket, limit)
            out.append(ShaEntry(rom.sha1, label, source, note=rom.title))
        for bank in rom.banks:
            if bank.sha1 not in seen:
                seen.add(bank.sha1)
                label = sanitize_label(bank.label) if bank.label else auto_label(short, bank.name, "", limit)
                out.append(ShaEntry(bank.sha1, label, source, note=f"{rom.title} — {bank.name}"))
    return out


def imported_entries(db: RomDatabase) -> list[ShaEntry]:
    out, seen = [], set()
    for sha, desc in db.imported_entries():
        if sha not in seen:
            seen.add(sha)
            out.append(ShaEntry(sha, sanitize_label(desc), SOURCE_IMPORTED))
    return out


def entries_from_sha_txt(text: str, source: str = SOURCE_MERGED) -> tuple[list[ShaEntry], int]:
    parsed, invalid = RomDatabase.parse_sha_txt(text)
    return [ShaEntry(sha, sanitize_label(desc), source) for sha, desc in parsed], invalid


def entry_from_file(path: Path | str, limit: int = DEFAULT_LABEL_LIMIT) -> ShaEntry:
    path = Path(path)
    data = path.read_bytes()
    label = sanitize_label(path.stem.replace("_", " "))[:limit].rstrip()
    return ShaEntry(hashlib.sha1(data).hexdigest(), label, SOURCE_FILE, note=path.name)


def merge_entries(existing: list[ShaEntry], new: list[ShaEntry]) -> tuple[int, int]:
    """Append entries whose SHA-1 is not already present. Returns (added, duplicates)."""
    have = {e.sha1.lower() for e in existing}
    added = dup = 0
    for e in new:
        key = e.sha1.lower()
        if key in have:
            dup += 1
            continue
        have.add(key)
        existing.append(e)
        added += 1
    return added, dup


def format_sha_txt(entries: list[ShaEntry], uppercase: bool = DEFAULT_UPPERCASE) -> str:
    lines = []
    for e in entries:
        if not e.include or not is_valid_sha1(e.sha1):
            continue
        sha = e.sha1.upper() if uppercase else e.sha1.lower()
        label = sanitize_label(e.label) or "Unnamed ROM"
        lines.append(f"{sha}{SEPARATOR}{label}")
    return "\n".join(lines) + ("\n" if lines else "")


def write_sha_txt(path: Path | str, entries: list[ShaEntry], uppercase: bool = DEFAULT_UPPERCASE,
                  crlf: bool = False) -> int:
    text = format_sha_txt(entries, uppercase)
    if crlf:
        text = text.replace("\n", "\r\n")
    Path(path).write_bytes(text.encode("ascii", errors="replace"))
    return text.count("\n")
