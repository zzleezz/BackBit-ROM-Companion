"""Chip pinouts for the BackBit ROM settings, and mapping of diagnosed faulty signals to physical pins.

Qt-free so it can be unit-tested. A fault found by the compare engine is a *signal* (``D3``, ``A12``, ``CE`` ...);
this module turns it into the chip pin that carries it and the ZIF pin it sits in (see :mod:`core.zif`).

Sources for the tables:
  * JEDEC 24/28/32-pin EPROMs (2716, 2532, 2732, 2764, 27128, 27256, 27512, 27C010-27C080): SB-Projects
    EPROM footprint charts (sbprojects.net/knowledge/footprints/eprom).
  * MOS / Commodore 2364: Commodore Semiconductor Group 2364 data sheet (2/80).
  * Motorola MCM68364 / MCM68766 / MCM68A332 / MCM68A316E: Motorola MCM68766 data sheet, "industry standard
    pinouts" figure; 2316 / 2332 mask ROMs follow the same pin layout.
  * 27C400 (40-pin, 16-bit): Atmel AT27C400 data sheet PDIP pin configuration, cross-checked against the
    amiga-stuff.com 4Mbit EPROM socket table.

Only pins confirmed by those sources are listed. Mask ROM chip selects are factory-programmed, so they are named
CS rather than given a polarity. The MOS 6540 (proprietary pinout) has no table, and the 28-pin 1Mbit mask ROM
(231024) lists only the address/data pins shared by every 28-pin JEDEC part.
"""

from __future__ import annotations

from dataclasses import dataclass

from core import zif

# Control-pin roles that diagnoses refer to; a pinout maps each role to the pins that carry it.
ROLE_NAMES = {
    "CE": "chip enable / chip select",
    "OE": "output enable",
    "VCC": "+5V supply",
    "GND": "ground",
}


@dataclass(frozen=True)
class Pinout:
    setting: str                 # BackBit menu setting this table describes
    pins: int
    data_bits: int               # 8, or 16 for word-wide parts
    names: dict[int, str]        # chip pin -> name printed on the data sheet
    roles: dict[str, tuple[int, ...]]   # "CE" / "OE" / "VCC" / "GND" -> chip pins with that role
    note: str = ""

    def pin_of(self, signal: str) -> tuple[int, ...]:
        """Chip pins carrying ``signal`` (a data line ``D3``, address line ``A12`` or a role from ROLE_NAMES)."""
        if signal in self.roles:
            return self.roles[signal]
        for pin, name in self.names.items():
            if signal in _signal_aliases(name):
                return (pin,)
        return ()


def _signal_aliases(name: str) -> set[str]:
    """Signals a data-sheet pin name stands for: ``D15 / A-1`` -> {D15}, ``A15`` -> {A15}."""
    out = set()
    for part in name.replace("/", " ").split():
        if len(part) > 1 and part[0] in "AD" and part[1:].isdigit():
            out.add(part)
    return out


def _dip24(extra: dict[int, str]) -> dict[int, str]:
    names = {1: "A7", 2: "A6", 3: "A5", 4: "A4", 5: "A3", 6: "A2", 7: "A1", 8: "A0",
             9: "D0", 10: "D1", 11: "D2", 12: "GND", 13: "D3", 14: "D4", 15: "D5", 16: "D6", 17: "D7",
             22: "A9", 23: "A8", 24: "VCC"}
    names.update(extra)
    return names


def _dip28(extra: dict[int, str]) -> dict[int, str]:
    names = {3: "A7", 4: "A6", 5: "A5", 6: "A4", 7: "A3", 8: "A2", 9: "A1", 10: "A0",
             11: "D0", 12: "D1", 13: "D2", 14: "GND", 15: "D3", 16: "D4", 17: "D5", 18: "D6", 19: "D7",
             21: "A10", 23: "A11", 24: "A9", 25: "A8", 28: "VCC"}
    names.update(extra)
    return names


def _dip32(extra: dict[int, str]) -> dict[int, str]:
    names = {2: "A16", 3: "A15", 4: "A12", 5: "A7", 6: "A6", 7: "A5", 8: "A4", 9: "A3", 10: "A2", 11: "A1",
             12: "A0", 13: "D0", 14: "D1", 15: "D2", 16: "GND", 17: "D3", 18: "D4", 19: "D5", 20: "D6", 21: "D7",
             22: "/CE", 23: "A10", 25: "A11", 26: "A9", 27: "A8", 28: "A13", 29: "A14", 32: "VCC"}
    names.update(extra)
    return names


def _roles(names: dict[int, str], ce: tuple[int, ...], oe: tuple[int, ...] = ()) -> dict[str, tuple[int, ...]]:
    return {"CE": ce, "OE": oe,
            "VCC": tuple(p for p, n in names.items() if n == "VCC"),
            "GND": tuple(p for p, n in names.items() if n == "GND")}


def _make(setting: str, pins: int, names: dict[int, str], ce: tuple[int, ...], oe: tuple[int, ...] = (),
          data_bits: int = 8, note: str = "") -> Pinout:
    return Pinout(setting, pins, data_bits, names, _roles(names, ce, oe), note)


_MASK_NOTE = "Mask ROM chip selects are factory-programmed (active-high or active-low per part)."

_TABLES = [
    # ---- 24-pin
    _make("2316", 24, _dip24({18: "CS3", 19: "A10", 20: "CS1", 21: "CS2"}), ce=(18, 20, 21), note=_MASK_NOTE),
    _make("2332", 24, _dip24({18: "A11", 19: "A10", 20: "CS1", 21: "CS2"}), ce=(20, 21), note=_MASK_NOTE),
    _make("2364", 24, _dip24({18: "A11", 19: "A10", 20: "CS", 21: "A12"}), ce=(20,), note=_MASK_NOTE),
    _make("68364", 24, _dip24({18: "A11", 19: "A10", 20: "CS", 21: "A12"}), ce=(20,), note=_MASK_NOTE),
    _make("68764", 24, _dip24({18: "A11", 19: "A10", 20: "/E /VPP", 21: "A12"}), ce=(20,)),
    _make("68766", 24, _dip24({18: "A11", 19: "A10", 20: "/G /VPP", 21: "A12"}), ce=(), oe=(20,)),
    _make("2716", 24, _dip24({18: "/CE /PGM", 19: "A10", 20: "/OE", 21: "VPP"}), ce=(18,), oe=(20,)),
    _make("2532", 24, _dip24({18: "A11", 19: "A10", 20: "/PD /PGM", 21: "VPP"}), ce=(20,)),
    _make("2732", 24, _dip24({18: "/CE /PGM", 19: "A10", 20: "/OE /VPP", 21: "A11"}), ce=(18,), oe=(20,)),
    # ---- 28-pin
    _make("2764", 28, _dip28({1: "VPP", 2: "A12", 20: "/CE", 22: "/OE", 26: "NC", 27: "/PGM"}), ce=(20,), oe=(22,)),
    _make("27128", 28, _dip28({1: "VPP", 2: "A12", 20: "/CE", 22: "/OE", 26: "A13", 27: "/PGM"}),
          ce=(20,), oe=(22,)),
    _make("27256", 28, _dip28({1: "VPP", 2: "A12", 20: "/CE", 22: "/OE", 26: "A13", 27: "A14"}), ce=(20,), oe=(22,)),
    _make("23256", 28, _dip28({2: "A12", 20: "CS", 22: "CS / OE", 26: "A13", 27: "A14"}), ce=(20, 22),
          note=_MASK_NOTE + " Pin 1 varies between makers and is not shown."),
    _make("27512", 28, _dip28({1: "A15", 2: "A12", 20: "/CE", 22: "/OE /VPP", 26: "A13", 27: "A14"}),
          ce=(20,), oe=(22,)),
    _make("231024", 28, _dip28({}), ce=(),
          note="1Mbit 28-pin mask ROM: only the address/data pins shared by all 28-pin JEDEC parts are shown; "
               "A12-A16 and the chip select are maker-specific."),
    # ---- 32-pin
    _make("27C010", 32, _dip32({1: "VPP", 24: "/OE", 30: "NC", 31: "/PGM"}), ce=(22,), oe=(24,)),
    _make("27C020", 32, _dip32({1: "VPP", 24: "/OE", 30: "A17", 31: "/PGM"}), ce=(22,), oe=(24,)),
    _make("27C040", 32, _dip32({1: "VPP", 24: "/OE", 30: "A17", 31: "A18"}), ce=(22,), oe=(24,)),
    _make("27C080", 32, _dip32({1: "A19", 24: "/OE /VPP", 30: "A17", 31: "A18"}), ce=(22,), oe=(24,)),
    # ---- 40-pin, 16-bit (read word-wide: BYTE/VPP high, pin 29 is D15)
    _make("27400", 40, {1: "A17", 2: "A7", 3: "A6", 4: "A5", 5: "A4", 6: "A3", 7: "A2", 8: "A1", 9: "A0",
                        10: "/CE", 11: "GND", 12: "/OE", 13: "D0", 14: "D8", 15: "D1", 16: "D9", 17: "D2",
                        18: "D10", 19: "D3", 20: "D11", 21: "VCC", 22: "D4", 23: "D12", 24: "D5", 25: "D13",
                        26: "D6", 27: "D14", 28: "D7", 29: "D15 / A-1", 30: "GND", 31: "BYTE /VPP", 32: "A16",
                        33: "A15", 34: "A14", 35: "A13", 36: "A12", 37: "A11", 38: "A10", 39: "A9", 40: "A8"},
          ce=(10,), oe=(12,), data_bits=16,
          note="Read word-wide: A0 is the word address, so each address line covers 2 bytes of the file."),
]

PINOUTS: dict[str, Pinout] = {p.setting: p for p in _TABLES}
_ALIASES = {"27C1001": "27C010", "27C4001": "27C040", "27C400": "27400", "23128": "27128", "6540": None}


def _norm(setting: str) -> str:
    return setting.strip().upper().replace(" ", "")


def pinout_for(setting: str | None) -> Pinout | None:
    """Pinout of a BackBit setting (``2764``, ``27C010``, ``27C1001`` ...), or None when it is not known."""
    if not setting:
        return None
    s = _norm(setting)
    if s in _ALIASES:
        target = _ALIASES[s]
        return PINOUTS.get(target) if target else None
    if s in PINOUTS:
        return PINOUTS[s]
    # 27C64 -> 2764, 27C256 -> 27256 ... (CMOS parts share the NMOS pinout)
    if s.startswith("27C") and ("27" + s[3:]) in PINOUTS:
        return PINOUTS["27" + s[3:]]
    if s.startswith("27") and ("27C" + s[2:]) in PINOUTS:
        return PINOUTS["27C" + s[2:]]
    return None


def setting_choices() -> list[str]:
    """Settings with a pinout, in package order (for the chip-type picker)."""
    return [p.setting for p in _TABLES]


@dataclass(frozen=True)
class SuspectPin:
    signal: str                  # D3, A12, CE ...
    chip_pin: int
    zif_pin: int
    pin_name: str                # data-sheet name of that chip pin
    reason: str                  # diagnosis title that named the signal
    level: str = "error"

    @property
    def side(self) -> str:
        return "left" if self.zif_pin <= zif.ZIF_ROWS else "right"

    @property
    def row(self) -> int:
        return zif.zif_row(self.zif_pin)

    def describe(self) -> str:
        what = self.signal if self.signal not in ROLE_NAMES else f"{self.pin_name} ({ROLE_NAMES[self.signal]})"
        return (f"{what}: chip pin {self.chip_pin} → ZIF pin {self.zif_pin} "
                f"({self.side} side, row {self.row} from the top)")


def map_signals(signals_by_reason: list[tuple[str, str, tuple[str, ...]]], pinout: Pinout | None
                ) -> tuple[list[SuspectPin], list[str]]:
    """Turn ``(level, reason, signals)`` entries into physical pins.

    Returns the suspect pins (one per chip pin, first reason wins, errors before warnings) and the signals that
    could not be placed on this pinout (unknown chip type, or a pin the table does not list)."""
    pins: dict[int, SuspectPin] = {}
    missing: list[str] = []
    order = {"error": 0, "warn": 1, "info": 2, "ok": 3}
    for level, reason, signals in sorted(signals_by_reason, key=lambda e: order.get(e[0], 9)):
        for sig in signals:
            chip_pins = pinout.pin_of(sig) if pinout else ()
            if not chip_pins:
                if pinout and sig in ROLE_NAMES:
                    continue           # this part has no such control pin (e.g. no /OE on a 2364)
                if sig not in missing:
                    missing.append(sig)
                continue
            for cp in chip_pins:
                if cp not in pins:
                    pins[cp] = SuspectPin(sig, cp, zif.zif_pin(pinout.pins, cp), pinout.names.get(cp, sig),
                                          reason, level)
    return sorted(pins.values(), key=lambda s: s.chip_pin), missing
