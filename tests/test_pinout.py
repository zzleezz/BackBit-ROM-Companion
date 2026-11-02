"""Pin tables and the mapping of diagnosed faults to chip / ZIF pins (core.pinout + core.analyzer)."""

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.analyzer import check_health, compare_images, swap16  # noqa: E402
from core.database import DEFAULT_DB_PATH, RomDatabase  # noqa: E402
from core.pinout import PINOUTS, map_signals, pinout_for  # noqa: E402


def rnd(n, seed):
    r = random.Random(seed)
    return bytes(r.randrange(256) for _ in range(n))


def suspects(result, setting):
    return map_signals([(d.level, d.title, d.signals) for d in result.diagnoses if d.signals], pinout_for(setting))


class TestPinTables(unittest.TestCase):
    def test_tables_are_complete_and_consistent(self):
        for setting, po in PINOUTS.items():
            with self.subTest(setting=setting):
                self.assertTrue(all(1 <= p <= po.pins for p in po.names), "pin number out of range")
                for k in range(po.data_bits):                     # every data line is on exactly one pin
                    self.assertEqual(len(po.pin_of(f"D{k}")), 1, f"D{k}")
                for role in ("VCC", "GND"):
                    self.assertTrue(po.roles[role], role)
                signals = [s for n in po.names.values() for s in n.replace("/", " ").split()
                           if s[0] in "AD" and s[1:].isdigit()]
                self.assertEqual(len(signals), len(set(signals)), "a signal is on two pins")

    def test_datasheet_spot_checks(self):
        # Commodore 2364 data sheet: A12 on pin 21, A11 on 18, A10 on 19, CS on 20, O1 (D0) on pin 9
        p = pinout_for("2364")
        self.assertEqual((p.pin_of("A12"), p.pin_of("A11"), p.pin_of("A10"), p.pin_of("D0")), ((21,), (18,), (19,), (9,)))
        self.assertEqual(p.roles["CE"], (20,))
        # 2532 vs 2732: pins 18 / 21 swap roles
        self.assertEqual(pinout_for("2532").pin_of("A11"), (18,))
        self.assertEqual(pinout_for("2732").pin_of("A11"), (21,))
        self.assertEqual(pinout_for("2716").pin_of("A11"), ())
        # 28-pin JEDEC: A14 on pin 27 for 27256, A15 on pin 1 for 27512, A13 on 26
        self.assertEqual(pinout_for("27256").pin_of("A14"), (27,))
        self.assertEqual(pinout_for("27512").pin_of("A15"), (1,))
        self.assertEqual(pinout_for("27128").pin_of("A13"), (26,))
        # 32-pin: A17 pin 30, A18 pin 31 on 27C040
        self.assertEqual((pinout_for("27C040").pin_of("A17"), pinout_for("27C040").pin_of("A18")), ((30,), (31,)))
        # 40-pin 27C400 word mode: D8 on 14, D15 on 29, A0 on 9, A17 on 1
        p = pinout_for("27400")
        self.assertEqual((p.pin_of("D8"), p.pin_of("D15"), p.pin_of("A0"), p.pin_of("A17")), ((14,), (29,), (9,), (1,)))
        self.assertEqual(p.data_bits, 16)

    def test_aliases(self):
        self.assertIs(pinout_for("27C1001"), PINOUTS["27100-0"])
        self.assertIs(pinout_for("27100-1"), PINOUTS["27100-0"])
        self.assertIs(pinout_for("27C040"), PINOUTS["27040"])
        self.assertIs(pinout_for("68364"), PINOUTS["2364"])
        self.assertIs(pinout_for("23256"), PINOUTS["27256"])
        self.assertIs(pinout_for("231024"), PINOUTS["531000"])
        self.assertIs(pinout_for("27C256"), PINOUTS["27256"])
        self.assertIs(pinout_for("23128"), PINOUTS["27128"])
        self.assertIsNone(pinout_for("6540"))      # proprietary pinout - no table
        self.assertIsNone(pinout_for(""))

    def test_every_database_setting_resolves(self):
        db = RomDatabase(DEFAULT_DB_PATH)
        settings = {o.setting for r in db.all_roms() for o in r.backbit}
        unknown = {s for s in settings if pinout_for(s) is None}
        self.assertEqual(unknown, {"6540", "27800"})


class TestFaultToPin(unittest.TestCase):
    ref = rnd(8192, 8)

    def test_stuck_data_bit_on_2364(self):
        r = compare_images(bytes(x & ~0x08 for x in self.ref), self.ref)
        pins, missing = suspects(r, "2364")
        self.assertEqual(missing, [])
        self.assertEqual([(s.signal, s.chip_pin, s.zif_pin, s.side) for s in pins], [("D3", 13, 37, "right")])

    def test_stuck_address_line_on_2764(self):
        a = bytearray(self.ref)
        for i in range(len(a)):
            if i >> 12 & 1:
                a[i] = self.ref[i & ~(1 << 12)]
        r = compare_images(bytes(a), self.ref)
        pins, _ = suspects(r, "2764")
        self.assertEqual([(s.signal, s.chip_pin, s.zif_pin) for s in pins], [("A12", 2, 2)])

    def test_shorted_lines_give_two_pins(self):
        def and_short(x):
            both = (x >> 1 & 1) & (x >> 5 & 1)
            return (x & ~0x22) | (both << 1) | (both << 5)
        r = compare_images(bytes(and_short(x) for x in self.ref), self.ref)
        pins, _ = suspects(r, "2716")
        self.assertEqual({s.signal for s in pins}, {"D1", "D5"})

    def test_blank_dump_points_at_select_pins(self):
        r = compare_images(b"\xff" * 8192, self.ref)
        pins, missing = suspects(r, "2764")
        self.assertEqual({s.chip_pin for s in pins}, {20, 22})       # /CE and /OE
        self.assertEqual(missing, [])
        pins, missing = suspects(r, "2364")                         # only one select, no /OE
        self.assertEqual([s.chip_pin for s in pins], [20])
        self.assertEqual(missing, [])

    def test_unknown_chip_reports_signals(self):
        r = compare_images(bytes(x | 0x40 for x in self.ref), self.ref)
        pins, missing = suspects(r, "6540")
        self.assertEqual((pins, missing), ([], ["D6"]))

    def test_health_findings_carry_signals(self):
        rep = check_health(bytes(x & ~0x10 for x in self.ref))
        self.assertIn(("D4",), [f.signals for f in rep.findings])


class TestSixteenBit(unittest.TestCase):
    ref = rnd(64 * 1024, 3)

    def faulty(self):
        a = bytearray(self.ref)
        for i in range(0, len(a), 2):        # high byte, bit 2 -> D10
            a[i] &= ~0x04
        return bytes(a)

    def test_high_byte_data_line(self):
        r = compare_images(self.faulty(), self.ref, bus_width=16)
        self.assertEqual(r.bus_width, 16)
        self.assertEqual(len(r.bit_mismatch), 16)
        self.assertGreater(r.bit_mismatch[10], 0)
        self.assertEqual(sum(r.bit_mismatch) - r.bit_mismatch[10], 0)
        pins, _ = suspects(r, "27400")
        self.assertEqual([(s.signal, s.chip_pin, s.zif_pin) for s in pins], [("D10", 18, 18)])
        self.assertIn("all mismatches are solely on bit D10", r.diagnoses[0].text)

    def test_low_byte_first_dump(self):
        r = compare_images(self.faulty(), self.ref, bus_width=16, low_byte_first=True)
        self.assertEqual([d.title for d in r.diagnoses], ["Stuck data bit D2"])

    def test_swapped_faulty_dump_is_normalised(self):
        r = compare_images(swap16(self.faulty()), self.ref, bus_width=16, low_byte_first=True)
        self.assertEqual([d.title for d in r.diagnoses], ["Byte order normalised", "Stuck data bit D10"])

    def test_word_address_line(self):
        a = bytearray(self.ref)
        for w in range(len(a) // 2):
            if w >> 3 & 1:
                src = (w & ~8) * 2
                a[2 * w:2 * w + 2] = self.ref[src:src + 2]
        r = compare_images(bytes(a), self.ref, bus_width=16)
        self.assertEqual([d.title for d in r.diagnoses], ["Address line A3 stuck LOW"])   # once, not per lane
        self.assertIn("8-word (16 bytes)", r.diagnoses[0].text)
        pins, _ = suspects(r, "27400")
        self.assertEqual([(s.signal, s.chip_pin) for s in pins], [("A3", 6)])
        # the same dump read as 8-bit would blame byte-address A4
        self.assertEqual([d.title for d in compare_images(bytes(a), self.ref).diagnoses], ["Address line A4 stuck LOW"])


if __name__ == "__main__":
    unittest.main()
