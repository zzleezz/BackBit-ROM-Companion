"""ROM image analysis: hashing, bank splitting / repeat detection and hardware fault heuristics.

Pure Python (no Qt) so it can be unit-tested and reused by later phases.
"""

from __future__ import annotations

import hashlib
import re
import zlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from core.database import Match, RomDatabase

KB = 1024
BANK_SIZES = (4 * KB, 8 * KB, 16 * KB, 32 * KB)
MAX_FILE_SIZE = 16 * 1024 * KB  # 16MB - far larger than any 8-bit era ROM

# Common JEDEC-style parts by size, used for hints when nothing matches.
CHIP_HINTS = {
    2 * KB: "2716 EPROM / 2316 mask ROM (24-pin)",
    4 * KB: "2732 / 2532 EPROM / 2332 mask ROM (24-pin)",
    8 * KB: "2764 EPROM (28-pin) / 2364 mask ROM (24-pin)",
    16 * KB: "27128 EPROM / 23128 mask ROM (28-pin)",
    32 * KB: "27256 EPROM / 23256 mask ROM (28-pin)",
    64 * KB: "27512 EPROM (28-pin)",
    128 * KB: "27C010 EPROM (32-pin)",
    256 * KB: "27C020 EPROM (32-pin, 8-bit) or a 2Mbit 40-pin 16-bit ROM (Amiga Kickstart 1.x, or one half of "
              "an A1200 / A3000 / A4000 pair)",
    512 * KB: "27C040 EPROM (32-pin, 8-bit) or 27C400 (40-pin, 16-bit - Amiga Kickstart 2.x / 3.x)",
    1024 * KB: "27C080 EPROM (32-pin, 8-bit) or 27C800 (42-pin, 16-bit - Amiga CD32 system ROM)",
}


def swap16(data: bytes) -> bytes:
    """Swap the two bytes of every 16-bit word (b0 b1 b2 b3 -> b1 b0 b3 b2). Odd lengths keep the last byte."""
    out = bytearray(data)
    out[0:len(data) - 1:2], out[1:len(data):2] = data[1:len(data):2], data[0:len(data) - 1:2]
    return bytes(out)


@dataclass(frozen=True)
class HashSet:
    sha1: str
    md5: str
    crc32: str


def compute_hashes(data: bytes) -> HashSet:
    return HashSet(
        sha1=hashlib.sha1(data).hexdigest(),
        md5=hashlib.md5(data).hexdigest(),
        crc32=f"{zlib.crc32(data) & 0xFFFFFFFF:08x}",
    )


def hex_range(offset: int, size: int) -> str:
    width = 4 if offset + size <= 0x10000 else 6
    return f"0x{offset:0{width}X} - 0x{offset + size - 1:0{width}X}"


# --------------------------------------------------------------------------- health check
@dataclass
class BitStat:
    bit: int
    ones: int
    total: int

    @property
    def zeros(self) -> int:
        return self.total - self.ones

    @property
    def stuck(self) -> str | None:
        if self.total == 0:
            return None
        if self.ones == 0:
            return "low"
        if self.ones == self.total:
            return "high"
        return None

    @property
    def ratio(self) -> float:
        return self.ones / self.total if self.total else 0.0


@dataclass
class Finding:
    level: str  # "ok", "info", "warn", "error"
    text: str
    signals: tuple[str, ...] = ()   # chip signals implicated (D3, A12, CE, VCC ...) for the pin map


@dataclass
class HealthReport:
    size: int
    bits: list[BitStat]
    unique_values: int
    blank_value: int | None  # 0xFF / 0x00 / other when every byte is identical
    findings: list[Finding] = field(default_factory=list)

    @property
    def is_blank(self) -> bool:
        return self.blank_value is not None

    @property
    def stuck_bits(self) -> list[BitStat]:
        return [b for b in self.bits if b.stuck]


def check_health(data: bytes, repeat_unit: int | None = None) -> HealthReport:
    size = len(data)
    hist = Counter(data)
    bits = [BitStat(b, sum(c for v, c in hist.items() if v >> b & 1), size) for b in range(8)]
    blank = next(iter(hist)) if len(hist) == 1 else None
    report = HealthReport(size, bits, len(hist), blank)
    f = report.findings

    if size == 0:
        f.append(Finding("error", "File is empty (0 bytes)."))
        return report

    if blank is not None:
        if blank == 0xFF:
            f.append(Finding("error", f"BLANK: all {size:,} bytes are 0xFF. The chip is erased/unprogrammed, "
                                      "or it was never enabled (wrong BackBit type / chip-select polarity, "
                                      "chip not seated, or dead output drivers floating high).", ("CE", "OE")))
        elif blank == 0x00:
            f.append(Finding("error", f"BLANK: all {size:,} bytes are 0x00. Typical of a chip that is not powered, "
                                      "a shorted output bus, or a part inserted backwards.",
                                ("VCC", "GND", "CE", "OE")))
        else:
            f.append(Finding("error", f"All {size:,} bytes are 0x{blank:02X}. The data bus is not changing at all "
                                      "- the chip is almost certainly not being read."))
        return report

    for b in report.stuck_bits:
        state = "LOW (0)" if b.stuck == "low" else "HIGH (1)"
        f.append(Finding("error", f"WARNING: Bit D{b.bit} is stuck {state} across all {size:,} bytes! "
                                  "Check ZIF contact on that data pin or a faulty ROM output driver.",
                           (f"D{b.bit}",)))

    # Data lines that always carry the same value -> likely bridged / shorted.
    active = [b.bit for b in bits if not b.stuck]
    values = list(hist)
    for i, a in enumerate(active):
        for c in active[i + 1:]:
            if all((v >> a & 1) == (v >> c & 1) for v in values):
                f.append(Finding("warn", f"Data bits D{a} and D{c} are identical in every byte - possible "
                                         "short/solder bridge between those data lines.", (f"D{a}", f"D{c}")))

    # Address lines with no effect (A_k mirror) below the repeat unit -> likely open/stuck address line.
    limit = repeat_unit if repeat_unit else size
    k = 0
    while (1 << (k + 1)) <= limit and (1 << k) < size:
        if size % (1 << (k + 1)) == 0 and _address_line_mirrored(data, k):
            f.append(Finding("warn", f"Address line A{k} appears to have no effect: every pair of bytes "
                                     f"differing only in A{k} is identical. Suspect a stuck/open A{k} pin "
                                     "(ZIF contact, cracked leg, or internal fault).", (f"A{k}",)))
        k += 1

    ff = hist.get(0xFF, 0) / size
    if ff > 0.9:
        f.append(Finding("info", f"{ff:.0%} of bytes are 0xFF - mostly empty. Normal for a part-filled EPROM, "
                                 "suspicious for a mask ROM."))

    if not any(x.level in ("warn", "error") for x in f):
        f.insert(0, Finding("ok", "All 8 data bits toggle, no address-line mirroring below the image size "
                                  "and no bridged data lines detected."))
    return report


def _address_line_mirrored(data: bytes, k: int) -> bool:
    step = 1 << k
    block = step << 1
    mv = memoryview(data)
    if k >= 6:
        for base in range(0, len(data), block):
            if mv[base:base + step] != mv[base + step:base + block]:
                return False
        return True
    # Small k: compare strided views of the whole buffer (fast C-level comparison).
    for j in range(step):
        if data[j::block] != data[j + step::block]:
            return False
    return True


# --------------------------------------------------------------------------- bank analysis
@dataclass
class BankSlice:
    offset: int
    size: int
    sha1: str
    crc32: str
    matches: list[Match]
    blank_value: int | None = None
    duplicate_of: int | None = None  # offset of an earlier identical slice

    @property
    def range_text(self) -> str:
        return hex_range(self.offset, self.size)


@dataclass
class RepeatInfo:
    unit_size: int
    count: int
    unit_hashes: HashSet
    matches: list[Match]

    @property
    def word(self) -> str:
        return {2: "Doubled", 4: "Quadrupled", 8: "Octupled"}.get(self.count, f"{self.count}x Repeated")


@dataclass
class SwapInfo:
    """Match found only after swapping the bytes of every 16-bit word."""
    hashes: HashSet                 # of the byte-swapped image
    matches: list[Match]            # whole swapped image, or its repeat unit when ``unit_size`` is set
    unit_size: int | None = None
    count: int = 1


@dataclass
class AnalysisResult:
    path: str | None
    size: int
    hashes: HashSet
    whole_matches: list[Match]
    crc_only_matches: list[Match]
    repeat: RepeatInfo | None
    layout: list[BankSlice]
    slices_by_size: dict[int, list[BankSlice]]
    health: HealthReport
    summary: list[Finding]
    swapped: SwapInfo | None = None

    @property
    def identified(self) -> bool:
        return bool(self.whole_matches or (self.repeat and self.repeat.matches)
                    or any(s.matches for s in self.layout) or (self.swapped and self.swapped.matches))


def find_repeat_unit(data: bytes, min_unit: int = 1 * KB) -> int | None:
    """Smallest power-of-two unit such that the image is that unit repeated (>=2 times)."""
    size = len(data)
    unit = min_unit
    while unit <= size // 2:
        if size % unit == 0 and data == data[:unit] * (size // unit):
            return unit
        unit <<= 1
    return None


def _slice(data: bytes, offset: int, size: int, db: RomDatabase) -> BankSlice:
    chunk = data[offset:offset + size]
    h = compute_hashes(chunk)
    blank = chunk[0] if chunk and chunk.count(chunk[0]) == len(chunk) else None
    return BankSlice(offset, size, h.sha1, h.crc32, db.lookup_sha1(h.sha1), blank)


def analyze_bytes(data: bytes, db: RomDatabase, path: str | None = None) -> AnalysisResult:
    size = len(data)
    hashes = compute_hashes(data)
    whole = db.lookup_sha1(hashes.sha1)
    crc_only = [] if whole else db.lookup_crc32(hashes.crc32)
    summary: list[Finding] = []

    # Repeat (doubled / quadrupled image) detection
    repeat = None
    unit = find_repeat_unit(data) if size >= 2 * KB else None
    if unit is not None:
        unit_data = data[:unit]
        uh = compute_hashes(unit_data)
        repeat = RepeatInfo(unit, size // unit, uh, db.lookup_sha1(uh.sha1))

    health = check_health(data, repeat.unit_size if repeat else None)

    # 16-bit ROMs (e.g. Amiga Kickstarts) are saved high-byte-first by some readers and low-byte-first by
    # others, so try the byte-swapped image when the dump does not match as-is.
    swapped = None
    if not whole and not (repeat and repeat.matches) and size >= 2 * KB and size % 2 == 0 and not \
            (data.count(data[0]) == size):
        sdata = swap16(data)
        sh = compute_hashes(sdata)
        sm = db.lookup_sha1(sh.sha1)
        if sm:
            swapped = SwapInfo(sh, sm)
        elif repeat is not None and repeat.unit_size % 2 == 0:
            uh = compute_hashes(sdata[:repeat.unit_size])
            um = db.lookup_sha1(uh.sha1)
            if um:
                swapped = SwapInfo(uh, um, repeat.unit_size, repeat.count)

    # Bank slicing (only when the whole file is not already identified)
    slices_by_size: dict[int, list[BankSlice]] = {}
    layout: list[BankSlice] = []
    if not whole and not swapped and not health.is_blank and size >= 8 * KB:
        for bs in BANK_SIZES:
            if bs < size and size % bs == 0:
                slices = [_slice(data, off, bs, db) for off in range(0, size, bs)]
                seen: dict[str, int] = {}
                for s in slices:
                    if s.sha1 in seen:
                        s.duplicate_of = seen[s.sha1]
                    else:
                        seen[s.sha1] = s.offset
                slices_by_size[bs] = slices
        layout = _build_layout(slices_by_size, size)

    # ---- summary
    if whole:
        for m in whole:
            summary.append(Finding("ok", f"EXACT MATCH: {m.label}" + (" [sha.txt]" if m.source == "sha.txt" else "")))
    elif crc_only:
        for m in crc_only:
            summary.append(Finding("warn", f"CRC32 matches {m.label} but SHA-1 does not - treat as a probable "
                                           "bad dump or a CRC collision; re-read the chip."))

    if swapped:
        label = swapped.matches[0].label
        what = (f"the {swapped.unit_size // KB}KB image repeated {swapped.count}x"
                if swapped.unit_size else "the whole image")
        summary.append(Finding("ok", f"BYTE-SWAPPED MATCH: {label}. With the two bytes of every 16-bit word "
                                     f"swapped, {what} matches the database. This is normal for 16-bit ROMs: "
                                     "readers and programmers differ in which byte they save first. The chip "
                                     "is good; only the file's byte order differs."))

    if repeat and not health.is_blank and not (swapped and not repeat.matches):
        label = repeat.matches[0].label if repeat.matches else None
        unit_txt = f"{repeat.unit_size // KB}KB" if repeat.unit_size >= KB else f"{repeat.unit_size} bytes"
        if repeat.count == 2:
            msg = f"Doubled Image Detected: Both {unit_txt} halves are identical"
        else:
            msg = f"{repeat.word} Image Detected: the same {unit_txt} block appears {repeat.count} times"
        if label:
            verb = "match" if repeat.count == 2 else "matches"
            summary.append(Finding("ok", f"{msg} and {verb} {label}."))
        else:
            summary.append(Finding("info", f"{msg} (no database match for the {unit_txt} unit). "
                                           "Either the image was deliberately burned multiple times, or the "
                                           "top address line(s) were not connected while reading."))

    identified_banks = [s for s in layout if s.matches]
    if not whole and identified_banks and not (repeat and repeat.matches):
        distinct = {s.matches[0].label for s in identified_banks}
        kind = "Multi-Bank ROM Detected" if len(distinct) > 1 or len(layout) > 1 else "Bank match"
        summary.append(Finding("ok", f"{kind}: {len(identified_banks)} of {len(layout)} region(s) identified."))
        for s in identified_banks:
            summary.append(Finding("ok", f"  {s.range_text}  ({s.size // KB}KB): {s.matches[0].label}"))
        for s in layout:
            if not s.matches:
                if s.blank_value is not None:
                    summary.append(Finding("info", f"  {s.range_text}  ({s.size // KB}KB): blank (all 0x{s.blank_value:02X})"))
                else:
                    summary.append(Finding("warn", f"  {s.range_text}  ({s.size // KB}KB): unidentified"))

    if not whole and not crc_only and not identified_banks and not (repeat and repeat.matches) and not swapped \
            and not health.is_blank:
        hint = CHIP_HINTS.get(size)
        msg = "No database match for the whole image or any 4/8/16/32KB bank."
        if hint:
            msg += f" Size suggests a {hint}."
        msg += " Import a BackBit sha.txt (File menu) to extend recognition."
        summary.append(Finding("warn", msg))
        if any(x.level in ("warn", "error") for x in health.findings):
            summary.append(Finding("error", "The health check found hardware-level faults: a faulty read can never "
                                            "hash-match. Re-seat the chip, clean the pins and re-dump before "
                                            "concluding the ROM is unknown."))

    if size % KB and size > 0:
        summary.append(Finding("warn", f"Odd file size ({size:,} bytes) - not a whole number of KB. "
                                       "The dump may be truncated or include a header."))

    return AnalysisResult(path, size, hashes, whole, crc_only, repeat, layout, slices_by_size, health, summary,
                          swapped)


def _build_layout(slices_by_size: dict[int, list[BankSlice]], size: int) -> list[BankSlice]:
    """Greedy cover of the image: at each offset prefer the largest identified bank."""
    lookup = {(s.offset, s.size): s for slices in slices_by_size.values() for s in slices}
    sizes = sorted(slices_by_size, reverse=True)
    if not sizes:
        return []
    smallest_identified = min((s.size for sl in slices_by_size.values() for s in sl if s.matches), default=None)
    # Unidentified regions are shown at the granularity of the smallest matched bank (or 16KB/largest).
    fallback = smallest_identified or (16 * KB if 16 * KB in slices_by_size else sizes[0])
    out: list[BankSlice] = []
    off = 0
    while off < size:
        chosen = None
        for bs in sizes:
            s = lookup.get((off, bs))
            if s and s.matches:
                chosen = s
                break
        if chosen is None:
            bs = fallback if (off, fallback) in lookup else min(slices_by_size)
            chosen = lookup[(off, bs)]
        out.append(chosen)
        off += chosen.size
    return out


def analyze_file(path: str | Path, db: RomDatabase) -> AnalysisResult:
    path = Path(path)
    size = path.stat().st_size
    if size > MAX_FILE_SIZE:
        raise ValueError(f"{path.name} is {size:,} bytes - too large to be a vintage ROM image "
                         f"(limit {MAX_FILE_SIZE // (1024 * KB)}MB).")
    return analyze_bytes(path.read_bytes(), db, str(path))


# =========================================================================== Tab 3: compare & diagnostics
@dataclass
class Diagnosis:
    level: str   # "ok", "info", "warn", "error"
    title: str
    text: str
    signals: tuple[str, ...] = ()   # chip signals implicated (D3, A12, CE ...) - see core.pinout


@dataclass
class CompareResult:
    size_a: int
    size_b: int
    verdict: str            # "identical", "content_match", "different", "empty"
    banner: str
    view_a: bytes           # byte arrays aligned for the side-by-side hex view
    view_b: bytes
    compared_size: int = 0
    diff_count: int = 0
    bit_mismatch: list[int] = field(default_factory=lambda: [0] * 8)
    flips_0_to_1: int = 0   # bits that are 1 in A but 0 in B
    flips_1_to_0: int = 0
    diff_offsets: list[int] = field(default_factory=list)
    diagnoses: list[Diagnosis] = field(default_factory=list)
    bus_width: int = 8      # 16 when the files were diagnosed as 16-bit words (bit_mismatch then has 16 entries)

    @property
    def diff_percent(self) -> float:
        return 100.0 * self.diff_count / self.compared_size if self.compared_size else 0.0


def _pad_kind(chunk: bytes) -> int | None:
    if chunk and chunk.count(chunk[0]) == len(chunk) and chunk[0] in (0x00, 0xFF):
        return chunk[0]
    return None


def _quick_diff_count(a: bytes, b: bytes) -> int:
    x = (int.from_bytes(a, "big") ^ int.from_bytes(b, "big")).to_bytes(len(a), "big")
    return len(a) - x.count(0)


def _size_text(n: int) -> str:
    return f"{n // KB}KB" if n >= KB and n % KB == 0 else f"{n:,} bytes"


def compare_images(a: bytes, b: bytes, _try_swap: bool = True, bus_width: int = 8,
                   low_byte_first: bool = False) -> CompareResult:
    """Compare a dump (A) with a reference (B), normalising doubled / padded / byte-swapped images first.

    ``bus_width`` 16 diagnoses faults on a 16-bit (40-pin) ROM as word data lines D0-D15 and word address
    lines; ``low_byte_first`` tells which byte of each word File A stores first (see :func:`diagnose_pair`)."""
    la, lb = len(a), len(b)
    if la == 0 or lb == 0:
        return CompareResult(la, lb, "empty", "Load two non-empty files to compare.", a, b)
    if a == b:
        r = CompareResult(la, lb, "identical", f"100% Bit-for-Bit Identical ({la:,} bytes)", a, b, compared_size=la)
        r.diagnoses.append(Diagnosis("ok", "Identical", "Every byte matches. The dump is a perfect copy of the reference."))
        return r

    # 16-bit ROMs: the same chip saved with the opposite byte order is a content match, not a fault
    if _try_swap and la % 2 == 0 and la >= 2 and a.count(a[0]) != la:
        sa = swap16(a)
        rs = compare_images(sa, b, False, bus_width, not low_byte_first)
        if rs.verdict in ("identical", "content_match"):
            tail = "" if rs.verdict == "identical" else f" - {rs.banner}"
            r = CompareResult(la, lb, "content_match",
                              f"Byte-Swapped Content Match: File A is File B with the bytes of every 16-bit word "
                              f"swapped{tail}", rs.view_a, rs.view_b, compared_size=rs.compared_size)
            r.diagnoses.append(Diagnosis(
                "info", "Byte-swapped 16-bit image",
                "After swapping the two bytes of every 16-bit word, File A matches File B. Typical of a 16-bit ROM "
                "(e.g. an Amiga Kickstart) read or saved with the opposite byte order - the chip itself is fine. "
                "The hex view shows File A byte-swapped."))
            r.diagnoses.extend(d for d in rs.diagnoses if d.title != "Identical")
            return r
        # A faulty 16-bit dump saved in the other byte order: diagnose it aligned with the reference
        if bus_width == 16 and la == lb and _quick_diff_count(sa, b) < _quick_diff_count(a, b):
            r = diagnose_pair(sa, b, 16, not low_byte_first)
            r.diagnoses.insert(0, Diagnosis(
                "info", "Byte order normalised",
                "File A is closer to File B with the two bytes of every 16-bit word swapped, so it was swapped "
                "before diagnosis (the hex view shows it swapped). Data-line numbers still refer to the chip's "
                "own pins."))
            return r

    if la != lb:
        a_big = la > lb
        big, small = (a, b) if a_big else (b, a)
        nb, ns = len(big), len(small)
        big_name, small_name = ("A", "B") if a_big else ("B", "A")
        if nb % ns == 0:
            n = nb // ns
            if big == small * n:
                word = {2: "doubled", 4: "quadrupled", 8: "octupled"}.get(n, f"repeated {n}×")
                tiled = small * n
                r = CompareResult(la, lb, "content_match",
                                  f"Size Mismatch, Content Match: File {big_name} is File {small_name} {word} "
                                  f"({_size_text(ns)} × {n})",
                                  a if a_big else tiled, tiled if a_big else b, compared_size=nb)
                r.diagnoses.append(Diagnosis("info", "Repeated image",
                                             f"File {big_name} contains File {small_name} {n} times. Typical of a "
                                             f"{_size_text(ns)} ROM burned {n}× into a larger EPROM, or of reading "
                                             f"a smaller chip with a larger chip setting (unused top address lines)."))
                return r
            best_slot, best_diff = 0, None
            for k in range(n):
                seg = big[k * ns:(k + 1) * ns]
                if seg == small:
                    rest = big[:k * ns] + big[(k + 1) * ns:]
                    pad = _pad_kind(rest)
                    where = ("lower half" if k == 0 else "upper half") if n == 2 else f"block at offset 0x{k * ns:X}"
                    tail = f"the rest is 0x{pad:02X} padding" if pad is not None else "the rest holds other data"
                    banner = f"Size Mismatch, Content Match: File {small_name} matches the {where} of File {big_name}; {tail}"
                    fill = bytes([pad if pad is not None else 0xFF])
                    placed = fill * (k * ns) + small + fill * ((n - k - 1) * ns)
                    r = CompareResult(la, lb, "content_match", banner,
                                      a if a_big else placed, placed if a_big else b, compared_size=ns)
                    r.diagnoses.append(Diagnosis("info", "Contained image", banner + "."))
                    return r
                dcount = _quick_diff_count(seg, small)
                if best_diff is None or dcount < best_diff:
                    best_slot, best_diff = k, dcount
            seg = big[best_slot * ns:(best_slot + 1) * ns]
            r = diagnose_pair(seg if a_big else a, b if a_big else seg, bus_width, low_byte_first)
            r.size_a, r.size_b = la, lb
            r.diagnoses.insert(0, Diagnosis(
                "info", "Size normalised",
                f"Files differ in size ({la:,} vs {lb:,} bytes). File {small_name} was compared with the "
                f"closest-matching {ns:,}-byte block of File {big_name} (offset 0x{best_slot * ns:X})."))
            return r
        m = min(la, lb)
        r = diagnose_pair(a[:m], b[:m], bus_width, low_byte_first)
        r.size_a, r.size_b = la, lb
        r.view_a, r.view_b = a, b
        r.diagnoses.insert(0, Diagnosis("warn", "Size mismatch",
                                        f"Sizes {la:,} and {lb:,} bytes are not multiples of each other - only the "
                                        f"first {m:,} bytes were compared. A truncated read or a file header is likely."))
        return r

    return diagnose_pair(a, b, bus_width, low_byte_first)


def diagnose_pair(a: bytes, b: bytes, bus_width: int = 8, low_byte_first: bool = False) -> CompareResult:
    """Bitwise fault analysis of a same-size dump (A) against a reference (B).

    ``bus_width`` 16 analyses the files as 16-bit words (40-pin 27C400-type ROMs): data lines are D0-D15 and
    address lines are word addresses. ``low_byte_first`` says whether File A holds the low byte (D0-D7) of each
    word at the even offset."""
    n = len(a)
    ia, ib = int.from_bytes(a, "big"), int.from_bytes(b, "big")
    ix = ia ^ ib
    xb = ix.to_bytes(n, "big")
    diff_count = n - xb.count(0)
    flips_01 = (ix & ia).bit_count()
    flips_10 = (ix & ib).bit_count()
    diff_offsets = [m.start() for m in re.finditer(rb"[^\x00]", xb)]
    words = bus_width == 16 and n % 2 == 0 and n >= 4

    pct = 100.0 * diff_count / n
    banner = f"Different: {diff_count:,} / {n:,} bytes differ ({pct:.2f}%)"
    r = CompareResult(n, n, "different", banner, a, b, n, diff_count, _bit_mismatch(xb), flips_01, flips_10,
                      diff_offsets, bus_width=16 if words else 8)
    d = r.diagnoses

    ha = Counter(a)
    if len(ha) == 1:
        v = next(iter(ha))
        roles = ("CE", "OE") if v == 0xFF else ("VCC", "GND", "CE", "OE")
        d.append(Diagnosis("error", "File A is blank",
                           f"Every byte of File A is 0x{v:02X}. The chip was not read at all: wrong BackBit type, "
                           f"chip-select polarity, chip not seated / inserted backwards, or a dead chip.", roles))
        return r

    if words:
        hi = 1 if low_byte_first else 0          # offset of the high byte (D8-D15) within each word
        lanes = ((a[hi::2], b[hi::2], 8), (a[1 - hi::2], b[1 - hi::2], 0))
        r.bit_mismatch = [0] * 16
        dirty = [la != lb for la, lb, _ in lanes]
        seen: set[str] = set()
        for idx, (la, lb, base) in enumerate(lanes):
            lane_x = (int.from_bytes(la, "big") ^ int.from_bytes(lb, "big")).to_bytes(len(la), "big")
            for k, c in enumerate(_bit_mismatch(lane_x)):
                r.bit_mismatch[base + k] = c
            if not dirty[idx]:
                continue
            for dg in _line_faults(la, lb, base, "word", not dirty[1 - idx]):
                if dg.title not in seen:           # an address-line fault shows in both byte lanes
                    seen.add(dg.title)
                    d.append(dg)
    else:
        d.extend(_line_faults(a, b, 0, "byte", True))
    hardware_fault = bool(d)

    # small patch / bit rot / localisation
    if not hardware_fault:
        if diff_count < 16:
            lst = ", ".join(f"0x{o:04X} (B={b[o]:02X} A={a[o]:02X})" for o in diff_offsets)
            d.append(Diagnosis("warn", "Small patch",
                               f"Only {diff_count} byte(s) differ and no hardware line fault fits: likely patched ROM, "
                               f"speedloader mod, or mild EPROM bit-rot (charge leakage). Offsets: {lst}"))
        if flips_10 == 0 and flips_01 > 0:
            d.append(Diagnosis("warn", "Bits drifting to 1",
                               f"Every differing bit is 1 in A where the reference has 0 ({flips_01:,} bits) - the "
                               f"signature of EPROM charge leakage (cells drifting towards the erased state). "
                               f"Re-program the EPROM from a verified image."))
        elif flips_01 == 0 and flips_10 > 0:
            d.append(Diagnosis("info", "Bits only cleared",
                               f"Every differing bit is 0 in A where the reference has 1 ({flips_10:,} bits): typical "
                               f"of a deliberately patched image or a weak/missing pull-up on the data bus."))
        if diff_offsets and diff_count >= 16:
            lo, hi_off = diff_offsets[0], diff_offsets[-1]
            span = hi_off - lo + 1
            if span <= n // 2:
                d.append(Diagnosis("info", "Differences are localised",
                                   f"All differences lie in {hex_range(lo, span)} ({span:,} of {n:,} bytes): a patched "
                                   f"routine, a different revision of one module, or a partial bank fault."))
        if pct > 40:
            d.append(Diagnosis("warn", "Files look unrelated",
                               f"{pct:.0f}% of bytes differ with no line-fault pattern. These are probably different "
                               f"ROMs or revisions rather than a hardware fault - check the reference file."))
    if not d:
        d.append(Diagnosis("info", "No specific fault pattern",
                           "The differences do not match a stuck bit, shorted data lines or an address-line fault."))
    return r


def _bit_mismatch(xor_bytes: bytes) -> list[int]:
    hx = Counter(xor_bytes)
    return [sum(c for v, c in hx.items() if v >> k & 1) for k in range(8)]


def _line_faults(a: bytes, b: bytes, data_base: int, unit: str, other_clean: bool) -> list[Diagnosis]:
    """Stuck data bits, shorted data lines and stuck address lines of one byte lane.

    ``data_base`` is added to bit numbers (8 for the high byte of a 16-bit word); ``unit`` is "byte" or "word"
    (what one address step selects); ``other_clean`` is False when another lane also has differences, so this
    lane's faults cannot explain *all* of them."""
    n = len(a)
    d: list[Diagnosis] = []
    xb = (int.from_bytes(a, "big") ^ int.from_bytes(b, "big")).to_bytes(n, "big")
    diff_or = 0
    for v in set(xb):
        diff_or |= v
    ha, hb = Counter(a), Counter(b)
    a_ones = [sum(c for v, c in ha.items() if v >> k & 1) for k in range(8)]
    b_ones = [sum(c for v, c in hb.items() if v >> k & 1) for k in range(8)]

    def dn(k: int) -> str:
        return f"D{data_base + k}"

    # 1. stuck data bits
    stuck: dict[int, str] = {}
    for k in range(8):
        if a_ones[k] == 0 and b_ones[k] > 0:
            stuck[k] = "LOW"
        elif a_ones[k] == n and b_ones[k] < n:
            stuck[k] = "HIGH"
    stuck_mask = sum(1 << k for k in stuck)
    only = other_clean and (diff_or & ~stuck_mask) == 0
    for k, state in stuck.items():
        if only and len(stuck) == 1:
            txt = (f"DIAGNOSIS: Data bit {dn(k)} in File A is stuck {state} (all mismatches are solely on bit "
                   f"{dn(k)}). Likely dead output transistor on ROM or poor ZIF pin contact.")
        else:
            txt = (f"DIAGNOSIS: Data bit {dn(k)} in File A is stuck {state} ({'0' if state == 'LOW' else '1'}) while "
                   f"the reference toggles it ({b_ones[k]:,} of {n:,} {unit}s have {dn(k)}=1 in B). "
                   f"Check ZIF contact / ROM output driver.")
        d.append(Diagnosis("error", f"Stuck data bit {dn(k)}", txt, (dn(k),)))
    if only and len(stuck) > 1:
        bits = ", ".join(dn(k) for k in stuck)
        d.append(Diagnosis("error", "Stuck bits explain all differences",
                           f"All mismatches are on the stuck bits ({bits}). Several dead outputs usually point "
                           f"to a failed chip rather than contacts.", tuple(dn(k) for k in stuck)))

    # 2. shorted / bridged data lines
    diff_bits = [k for k in range(8) if diff_or >> k & 1]
    if diff_bits and len(diff_bits) <= 2 and not stuck:
        pairs = Counter(zip(a, b))
        candidates = [(diff_bits[0], diff_bits[1])] if len(diff_bits) == 2 else \
            [(diff_bits[0], j) for j in range(8) if j != diff_bits[0]]
        for i, j in candidates:
            if any((av >> i & 1) != (av >> j & 1) for (av, _bv) in pairs):
                continue  # A must always carry equal values on the two lines
            states = {(av >> i & 1) for (av, bv) in pairs if (bv >> i & 1) != (bv >> j & 1)}
            if not states:
                continue
            if states == {0}:
                kind, how = "AND-short", "both lines are pulled LOW whenever they should differ"
            elif states == {1}:
                kind, how = "OR-short", "both lines are pulled HIGH whenever they should differ"
            else:
                kind, how = "bridge", "the two lines always read the same value"
            lo_b, hi_b = dn(min(i, j)), dn(max(i, j))
            d.append(Diagnosis("error", f"Shorted data lines {lo_b}/{hi_b}",
                               f"DIAGNOSIS: Data lines {lo_b} and {hi_b} appear shorted together ({kind}): {how}. "
                               f"Look for a solder bridge, debris between ZIF contacts or a damaged chip.",
                               (lo_b, hi_b)))
            break

    # 3. stuck / open address lines
    if diff_or:
        k = 0
        while (1 << k) < n:
            for high in (False, True):
                if n % (2 << k) == 0 and _addr_line_fault(a, b, k, high):
                    lvl = "HIGH" if high else "LOW"
                    block = _size_text(1 << k) if unit == "byte" else \
                        f"{1 << k:,}-word ({_size_text(2 << k)})"
                    d.append(Diagnosis("error", f"Address line A{k} stuck {lvl}",
                                       f"DIAGNOSIS: Address line A{k} stuck {lvl} - File A equals the reference read "
                                       f"with A{k} forced {lvl}: every {block} block repeats its "
                                       f"{'upper' if high else 'lower'} neighbour. Check the A{k} pin (ZIF contact, "
                                       f"cracked leg) or a wrong BackBit chip type that maps that pin to a control "
                                       f"line.", (f"A{k}",)))
            k += 1
    return d


def _addr_line_fault(a: bytes, b: bytes, k: int, high: bool) -> bool:
    """True if A == B read with address line k forced low (or high)."""
    step = 1 << k
    block = step << 1
    n = len(a)
    if k >= 6:
        ma, mb = memoryview(a), memoryview(b)
        for base in range(0, n, block):
            src = mb[base + step:base + block] if high else mb[base:base + step]
            if ma[base:base + step] != src or ma[base + step:base + block] != src:
                return False
        return True
    for j in range(step):
        src = b[j + step::block] if high else b[j::block]
        if a[j::block] != src or a[j + step::block] != src:
            return False
    return True
