"""BackBit Chip Tester Pro V2 ZIF placement (Qt-free so it can be unit-tested).

The tester has a 48-pin ZIF socket: ZIF pins 1-24 run down the left side and 25-48 up the right side, so
pin 1 faces pin 48 on the top row and pin 24 faces pin 25 on the bottom row.

The BackBit TOP-JUSTIFIES every chip: chip pin 1 sits in ZIF pin 1 with the pin-1 notch pointing up, and an
N-pin chip fills the top N/2 rows, leaving the bottom rows empty. (Turning the knob clockwise selects Offset
Mode, which shifts the chip; this module describes the default top-justified placement.)
"""

from __future__ import annotations

from dataclasses import dataclass

ZIF_PINS = 48
ZIF_ROWS = ZIF_PINS // 2


def valid_chip(pins: int | None) -> bool:
    return pins is not None and pins % 2 == 0 and 4 <= pins <= ZIF_PINS


def zif_pin(chip_pins: int, chip_pin: int) -> int:
    """ZIF pin number that chip pin ``chip_pin`` of an ``chip_pins``-pin DIP lands in."""
    if not valid_chip(chip_pins) or not 1 <= chip_pin <= chip_pins:
        raise ValueError(f"invalid chip pin {chip_pin} for a {chip_pins}-pin chip")
    half = chip_pins // 2
    if chip_pin <= half:
        return chip_pin                                   # left side, top rows
    return ZIF_PINS - chip_pins + chip_pin                # right side: last chip pin -> ZIF 48 (top right)


def zif_row(zif: int) -> int:
    """Row (1 = top) of a ZIF pin."""
    return zif if zif <= ZIF_ROWS else ZIF_PINS + 1 - zif


@dataclass(frozen=True)
class Placement:
    chip_pins: int
    used_rows: int                    # top rows occupied
    empty_left: tuple[int, int] | None    # first..last empty ZIF pin on the left (None if full)
    empty_right: tuple[int, int] | None   # first..last empty ZIF pin on the right

    @property
    def empty_rows(self) -> int:
        return ZIF_ROWS - self.used_rows

    @property
    def empty_range(self) -> tuple[int, int] | None:
        """Contiguous empty ZIF pin range (the bottom rows: left side down to 24, right side from 25)."""
        if not self.empty_rows:
            return None
        return self.used_rows + 1, ZIF_PINS - self.used_rows

    def describe(self) -> list[str]:
        n, half = self.chip_pins, self.chip_pins // 2
        lines = [f"Chip pin 1 -> ZIF pin {zif_pin(n, 1)}  •  Chip pin {half} -> ZIF pin {zif_pin(n, half)}",
                 f"Chip pin {half + 1} -> ZIF pin {zif_pin(n, half + 1)}  •  "
                 f"Chip pin {n} -> ZIF pin {zif_pin(n, n)}"]
        if self.empty_range:
            a, b = self.empty_range
            rows = "row" if self.empty_rows == 1 else "rows"
            lines.append(f"Leave EMPTY: ZIF pins {a}–{b} (bottom {self.empty_rows} {rows})")
        else:
            lines.append("Chip fills the whole 48-pin socket.")
        return lines


def placement(chip_pins: int) -> Placement:
    if not valid_chip(chip_pins):
        raise ValueError(f"a {chip_pins}-pin chip does not fit the 48-pin ZIF")
    half = chip_pins // 2
    if half == ZIF_ROWS:
        return Placement(chip_pins, half, None, None)
    return Placement(chip_pins, half, (half + 1, ZIF_ROWS), (ZIF_ROWS + 1, ZIF_PINS - half))
