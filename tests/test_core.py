"""Core (Qt-free) unit tests. Run:  python -m unittest discover -s tests -v"""

import hashlib
import json
import random
import shutil
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.analyzer import analyze_bytes, compare_images  # noqa: E402
from core.database import DEFAULT_DB_PATH, RomDatabase, guess_pins  # noqa: E402
from core.database import PACK_FORMAT, rom_to_dict  # noqa: E402
from core import zif  # noqa: E402
from core.shatxt import (DEFAULT_LABEL_LIMIT, DEFAULT_UPPERCASE, SEPARATOR, auto_label, builtin_entries, entries_from_sha_txt,  # noqa: E402
                         format_sha_txt, merge_entries, sanitize_label, ShaEntry, write_sha_txt)


def sha(d: bytes) -> str:
    return hashlib.sha1(d).hexdigest()


def rnd(n: int, seed: int) -> bytes:
    r = random.Random(seed)
    return bytes(r.randrange(256) for _ in range(n))


class TempDbMixin:
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.master = self.tmp / "roms_database.json"
        shutil.copy(DEFAULT_DB_PATH, self.master)
        self.db = RomDatabase(self.master, self.tmp / "imported.json", self.tmp / "user_db.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestShippedDatabase(TempDbMixin, unittest.TestCase):
    def test_integrity(self):
        raw = json.loads(DEFAULT_DB_PATH.read_text(encoding="utf-8"))
        ids = set()
        for m in raw["machines"]:
            for r in m["roms"]:
                self.assertNotIn(r["id"], ids)
                ids.add(r["id"])
                self.assertTrue(r["backbit"], r["id"])
                self.assertIn(r["pins"], (24, 28, 32, 40, 42), r["id"])
                self.assertLessEqual(len(r.get("label", "")), 16, r["id"])      # 16x2 LCD line
                for b in r.get("banks", []):
                    self.assertLessEqual(len(b.get("label", "")), 16, (r["id"], b["name"]))
        self.assertGreaterEqual(len(raw["machines"]), 35)

    def test_known_hashes(self):
        self.assertEqual(self.db.lookup_sha1("5ea7c2b824672e914525d1d5c419d71b84a426a2")[0].rom.id, "zx48_rom")
        # Phase 2 combined 32KB hashes and corrected sockets
        self.assertEqual(self.db.lookup_sha1("8cafb292af58617907b9e6b9093d3588a75849b8")[0].rom.socket, "IC8")
        self.assertEqual(self.db.lookup_sha1("16375d42ea109b47edded7a16028de7fdb3013a1")[0].rom.id, "zx128_rom")
        self.assertEqual(self.db.rom_by_id("cpc464_os_basic").socket, "IC104")
        self.assertEqual(self.db.rom_by_id("cpc6128_amsdos").socket, "IC204")
        # +3 bank hashes resolve to the IC7 chip
        bank = self.db.lookup_sha1("e319ed08b4d53a5e421a75ea00ea02039ba6555b")[0]
        self.assertEqual((bank.rom.id, bank.bank.offset), ("p3_40_ic7", 0))

    def test_combined_crc_consistency(self):
        """Whole-chip CRC32 of every multi-bank entry must equal the CRC composition of its banks."""
        def combine(a, b, n):
            return (zlib.crc32(b"\0" * n, a) ^ b ^ zlib.crc32(b"\0" * n)) & 0xFFFFFFFF
        checked = 0
        for rom in self.db.all_roms():
            if rom.crc32 and len(rom.banks) == 2 and all(b.crc32 for b in rom.banks):
                b0, b1 = rom.banks
                self.assertEqual(f"{combine(int(b0.crc32, 16), int(b1.crc32, 16), b1.size):08x}", rom.crc32, rom.id)
                checked += 1
        self.assertGreaterEqual(checked, 4)

    def test_phase3_hardware_confirmations(self):
        for rid, sock in (("d64_1", "IC17"), ("d64_2", "IC18")):
            r = self.db.rom_by_id(rid)
            self.assertEqual((r.socket, r.pins), (sock, 28))
            self.assertEqual([o.setting for o in r.backbit], ["27128", "27128"])   # no native 23128 setting
            self.assertFalse(any("does not record socket" in w for w in r.warnings))
        for rid in ("ace_a", "ace_b"):
            self.assertEqual(self.db.rom_by_id(rid).socket, "C1 / D1 (MAME: z1 / z2)")
        for lane, sock in enumerate(("IC24", "IC25", "IC26", "IC27")):
            r = self.db.rom_by_id(f"arc311_{lane}")
            self.assertEqual(r.socket, sock)
            self.assertTrue(any("Confirmed silkscreen" in n for n in r.notes))
        self.assertIn("LSB", " ".join(self.db.rom_by_id("arc200_0").notes))
        self.assertIn("MSB", " ".join(self.db.rom_by_id("arc200_3").notes))
        self.assertEqual([self.db.rom_by_id(f"lynx128_{i}").socket for i in (1, 2, 3)], ["IC1", "IC2", "IC3"])
        self.assertTrue(all(any("Confirmed" in n for n in self.db.rom_by_id(r).notes)
                            for r in ("lynx48_1", "lynx96_44", "lynx128_2")))
        for rid in ("a2_e0", "a2_e8", "a2_f0"):
            self.assertTrue(any("independent verification" in n for n in self.db.rom_by_id(rid).notes), rid)

    def test_search(self):
        self.assertTrue(any(r.id == "c64_kernal_r3" for r in self.db.search("901227 u4")))
        self.assertTrue(any(r.id == "d32_lo" for r in self.db.search("dragon 68364")))


class TestDatabaseManager(TempDbMixin, unittest.TestCase):
    def test_add_edit_delete_persist(self):
        data = rnd(8192, 1)
        res = self.db.upsert_rom("My Test Machine", "Acme", {
            "name": "Boot ROM", "label": "Test Boot", "size": 8192, "pins": 24, "chip_type": "2364 mask ROM",
            "backbit": [{"setting": "2364", "pins": 24, "chip": "mask"}], "sha1": sha(data),
            "crc32": f"{zlib.crc32(data):08x}", "socket": "U1", "part_numbers": ["123"], "warnings": ["w"]})
        self.assertTrue(res.master_written)
        self.assertEqual(self.db.lookup_sha1(sha(data))[0].rom.id, res.rom_id)
        # survives a reload, and is in both the master file and the overlay
        db2 = RomDatabase(self.master, self.tmp / "imported.json", self.tmp / "user_db.json")
        self.assertIsNotNone(db2.rom_by_id(res.rom_id))
        self.assertIn(res.rom_id, self.master.read_text(encoding="utf-8"))
        # overlay alone restores the entry if the master is replaced by a fresh copy (app update)
        shutil.copy(DEFAULT_DB_PATH, self.master)
        db3 = RomDatabase(self.master, self.tmp / "imported.json", self.tmp / "user_db.json")
        self.assertEqual(db3.rom_by_id(res.rom_id).machine_name, "My Test Machine")
        # edit an existing built-in entry
        rom = db3.rom_by_id("c64_char")
        from core.database import rom_to_dict
        d = rom_to_dict(rom)
        d["socket"] = "U5 (edited)"
        db3.upsert_rom(rom.machine_name, "", d, rom_id=rom.id)
        self.assertEqual(db3.rom_by_id("c64_char").socket, "U5 (edited)")
        self.assertTrue(db3.rom_by_id("c64_char").user_modified)
        # delete
        db3.delete_rom(res.rom_id)
        self.assertIsNone(db3.rom_by_id(res.rom_id))
        db4 = RomDatabase(self.master, self.tmp / "imported.json", self.tmp / "user_db.json")
        self.assertIsNone(db4.rom_by_id(res.rom_id))
        self.assertEqual(db4.rom_by_id("c64_char").socket, "U5 (edited)")

    def test_guess_pins(self):
        self.assertEqual(guess_pins("2764", 24), 28)
        self.assertEqual(guess_pins("27C010", 28), 32)
        self.assertEqual(guess_pins("2532", 28), 24)
        self.assertEqual(guess_pins("weird", 40), 40)


    def test_read_only_master_keeps_overlay(self):
        """Packaged builds: the master is read-only, edits must still persist in the user overlay."""
        before = self.master.read_bytes()
        self.db.master_writable = False
        res = self.db.upsert_rom("Bench", "", {"name": "X", "size": 2048, "pins": 24,
                                               "backbit": [{"setting": "2716", "pins": 24, "chip": ""}],
                                               "sha1": sha(b"x" * 2048)})
        self.assertFalse(res.master_written)
        self.assertIn("read-only", res.message)
        self.assertEqual(self.master.read_bytes(), before)
        db2 = RomDatabase(self.master, self.tmp / "imported.json", self.tmp / "user_db.json")
        self.assertIsNotNone(db2.rom_by_id(res.rom_id))


class TestRomPacks(TempDbMixin, unittest.TestCase):
    """File > Export User Database Additions / Import Database JSON."""

    def other_db(self, name="other"):
        d = self.tmp / name
        d.mkdir()
        shutil.copy(DEFAULT_DB_PATH, d / "roms_database.json")
        return RomDatabase(d / "roms_database.json", d / "imported.json", d / "user_db.json")

    def rom(self, name, data, **extra):
        r = {"name": name, "label": name[:20], "size": len(data), "pins": 28, "chip_type": "2764 EPROM",
             "backbit": [{"setting": "2764", "pins": 28, "chip": "EPROM"}], "sha1": sha(data),
             "crc32": f"{zlib.crc32(data):08x}", "socket": "U7", "part_numbers": ["DT-781"]}
        r.update(extra)
        return r

    def test_export_requires_user_entries(self):
        with self.assertRaises(ValueError):
            self.db.export_user_pack(self.tmp / "p.json")

    def test_export_import_roundtrip(self):
        dead, jiffy = rnd(8192, 11), rnd(8192, 12)
        self.db.upsert_rom("Bench Cartridges", "Various", self.rom("Dead Test 781", dead))
        self.db.upsert_rom("Commodore 64 / 64C", "", self.rom("JiffyDOS KERNAL", jiffy))
        d = rom_to_dict(self.db.rom_by_id("c64_char"))
        d["socket"] = "U5 (edited)"
        self.db.upsert_rom("Commodore 64 / 64C", "", d, rom_id="c64_char")
        pack_path = self.tmp / "pack.json"
        self.assertEqual(self.db.export_user_pack(pack_path, "1.0.0"), 3)
        pack = json.loads(pack_path.read_text(encoding="utf-8"))
        self.assertEqual((pack["format"], pack["format_version"], pack["rom_count"]), (PACK_FORMAT, 1, 3))
        self.assertEqual({m["name"] for m in pack["machines"]}, {"Bench Cartridges", "Commodore 64 / 64C"})

        other = self.other_db()
        plan = other.plan_import(pack_path)
        self.assertEqual((len(plan.to_add), len(plan.conflicts), plan.unchanged, plan.duplicates),
                         (2, 1, 0, 0))
        res = other.apply_import(plan)                       # keep the recipient's c64_char
        self.assertEqual((res.added, res.replaced, res.skipped), (2, 0, 1))
        self.assertTrue(res.master_written)
        hit = other.lookup_sha1(sha(dead))[0].rom
        self.assertEqual((hit.machine_name, hit.socket, hit.manufacturer), ("Bench Cartridges", "U7", "Various"))
        self.assertTrue(hit.source.startswith("Imported pack"))
        self.assertEqual(other.lookup_sha1(sha(jiffy))[0].rom.machine_id, "c64")
        self.assertEqual(other.rom_by_id("c64_char").socket, "U5")

        # importing again is idempotent; the conflict can then be accepted
        plan2 = other.plan_import(pack_path)
        self.assertEqual((len(plan2.to_add), len(plan2.conflicts), plan2.unchanged), (0, 1, 2))
        res2 = other.apply_import(plan2, replace_conflicts=True)
        self.assertEqual((res2.added, res2.replaced), (0, 1))
        self.assertEqual(other.rom_by_id("c64_char").socket, "U5 (edited)")
        # persisted in the overlay of the recipient
        again = RomDatabase(other.db_path, other.user_store, other.user_db_path)
        self.assertEqual(again.lookup_sha1(sha(dead))[0].rom.name, "Dead Test 781")

    def test_full_database_is_a_valid_pack(self):
        plan = self.db.plan_import(DEFAULT_DB_PATH)
        self.assertEqual((len(plan.to_add), len(plan.conflicts), len(plan.invalid)), (0, 0, 0))
        self.assertEqual(plan.unchanged, self.db.builtin_rom_count)
        self.assertEqual(self.db.apply_import(plan).added, 0)

    def test_duplicates_invalid_and_bad_files(self):
        known = self.db.rom_by_id("zx48_rom")
        good = self.rom("New ROM", rnd(4096, 5), id="")
        pack = {"format": PACK_FORMAT, "format_version": 1, "machines": [{"name": "Mixed", "roms": [
            dict(rom_to_dict(known), id="renamed_copy"),                 # same SHA-1, other id -> duplicate
            good, dict(good, id="dupe_in_pack"),                         # second copy inside the pack
            self.rom("Bad hash", b"1", sha1="xyz"),
            self.rom("Bad pins", b"2", pins=30),
            {"name": "No settings", "size": 10, "pins": 24, "backbit": []},
        ]}, {"roms": []}]}
        plan = self.db.plan_import(pack)
        self.assertEqual((len(plan.to_add), plan.duplicates, len(plan.invalid)), (1, 2, 4))
        res = self.db.apply_import(plan)
        self.assertEqual(res.added, 1)
        self.assertEqual(self.db.lookup_sha1(good["sha1"])[0].rom.machine_name, "Mixed")
        for bad in ({"no": "machines"}, {"format": "something-else", "machines": []},
                    {"format": PACK_FORMAT, "format_version": 99, "machines": []}):
            with self.assertRaises(ValueError):
                self.db.plan_import(bad)
        broken = self.tmp / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.db.plan_import(broken)


class TestInspector(TempDbMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.k = rnd(8192, 2)
        self.b = rnd(8192, 3)
        for name, d in (("K", self.k), ("B", self.b)):
            self.db.upsert_rom("Synthetic", "", {"name": name, "size": 8192, "pins": 24,
                                                 "backbit": [{"setting": "2364", "pins": 24, "chip": ""}],
                                                 "sha1": sha(d)}, write_master=False)

    def test_doubled_and_banks(self):
        r = analyze_bytes(self.k * 2, self.db)
        self.assertEqual(r.repeat.count, 2)
        self.assertTrue(r.repeat.matches)
        r = analyze_bytes(self.b + self.k, self.db)
        self.assertEqual(len(r.layout), 2)
        self.assertTrue(all(s.matches for s in r.layout))

    def test_stuck_bit_health(self):
        r = analyze_bytes(bytes(x & ~0x10 for x in self.k), self.db)
        self.assertEqual([b.bit for b in r.health.stuck_bits], [4])


class TestByteSwap(TempDbMixin, unittest.TestCase):
    """16-bit ROMs (Amiga Kickstart) dumped with the opposite byte order are still identified / compared."""

    def setUp(self):
        super().setUp()
        self.k512 = rnd(512 * 1024, 21)
        self.k256 = rnd(256 * 1024, 22)
        for name, d in (("KS 512", self.k512), ("KS 256", self.k256)):
            self.db.upsert_rom("Synthetic Amiga", "", {"name": name, "size": len(d), "pins": 40,
                                                       "backbit": [{"setting": "27400", "pins": 40, "chip": ""}],
                                                       "sha1": sha(d)}, write_master=False)

    def test_swap16(self):
        from core.analyzer import swap16
        self.assertEqual(swap16(b"\x01\x02\x03\x04"), b"\x02\x01\x04\x03")
        self.assertEqual(swap16(swap16(self.k256)), self.k256)

    def test_inspector_identifies_swapped_and_doubled(self):
        from core.analyzer import swap16
        r = analyze_bytes(self.k512, self.db)                       # same byte order: plain exact match
        self.assertTrue(r.whole_matches)
        self.assertIsNone(r.swapped)
        r = analyze_bytes(swap16(self.k512), self.db)               # opposite byte order
        self.assertFalse(r.whole_matches)
        self.assertTrue(r.identified)
        self.assertEqual(r.swapped.matches[0].rom.name, "KS 512")
        self.assertIsNone(r.swapped.unit_size)
        self.assertTrue(any("BYTE-SWAPPED MATCH" in f.text for f in r.summary))
        self.assertFalse(any("No database match" in f.text for f in r.summary))
        r = analyze_bytes(self.k256 * 2, self.db)                   # 256KB chip read with the 512K setting
        self.assertEqual(r.repeat.matches[0].rom.name, "KS 256")
        self.assertIsNone(r.swapped)
        r = analyze_bytes(swap16(self.k256) * 2, self.db)           # ... and with the other byte order
        self.assertEqual((r.swapped.unit_size, r.swapped.count), (256 * 1024, 2))
        self.assertEqual(r.swapped.matches[0].rom.name, "KS 256")
        r = analyze_bytes(rnd(512 * 1024, 99), self.db)             # unrelated data is still unidentified
        self.assertFalse(r.identified)
        self.assertIsNone(r.swapped)
        self.assertIsNone(analyze_bytes(b"\xff" * 4096, self.db).swapped)

    def test_compare_swapped(self):
        from core.analyzer import swap16
        r = compare_images(swap16(self.k512), self.k512)
        self.assertEqual(r.verdict, "content_match")
        self.assertIn("Byte-Swapped", r.banner)
        self.assertEqual(r.view_a, r.view_b)
        r = compare_images(swap16(self.k256) * 2, self.k256)         # swapped AND doubled
        self.assertEqual(r.verdict, "content_match")
        self.assertIn("doubled", r.banner)
        bad = bytearray(swap16(self.k512))
        bad[100] ^= 0x08
        self.assertEqual(compare_images(bytes(bad), self.k512).verdict, "different")   # real faults still reported


class TestZifPlacement(unittest.TestCase):
    """48-pin ZIF, chips TOP-justified: chip pin 1 = ZIF pin 1, bottom rows empty."""

    def test_28_pin_matches_manual_example(self):
        self.assertEqual([zif.zif_pin(28, k) for k in (1, 14, 15, 28)], [1, 14, 35, 48])
        place = zif.placement(28)
        self.assertEqual((place.used_rows, place.empty_rows, place.empty_range), (14, 10, (15, 34)))
        self.assertEqual(place.describe(), [
            "Chip pin 1 -> ZIF pin 1  •  Chip pin 14 -> ZIF pin 14",
            "Chip pin 15 -> ZIF pin 35  •  Chip pin 28 -> ZIF pin 48",
            "Leave EMPTY: ZIF pins 15–34 (bottom 10 rows)"])

    def test_all_packages(self):
        for n, empty in ((24, (13, 36)), (32, (17, 32)), (40, (21, 28)), (48, None)):
            p = zif.placement(n)
            self.assertEqual(zif.zif_pin(n, 1), 1)
            self.assertEqual(zif.zif_pin(n, n), 48)               # last pin always faces pin 1
            self.assertEqual(zif.zif_pin(n, n // 2), n // 2)
            self.assertEqual(p.empty_range, empty, n)
            # chip pins k and n+1-k always share a row (DIP geometry)
            for k in range(1, n // 2 + 1):
                self.assertEqual(zif.zif_row(zif.zif_pin(n, k)), zif.zif_row(zif.zif_pin(n, n + 1 - k)))
            self.assertEqual(len({zif.zif_pin(n, k) for k in range(1, n + 1)}), n)
        self.assertEqual(zif.placement(48).describe()[-1], "Chip fills the whole 48-pin socket.")
        self.assertIn("(bottom 1 row)", zif.placement(46).describe()[-1])
        self.assertEqual((zif.zif_row(1), zif.zif_row(48), zif.zif_row(24), zif.zif_row(25)), (1, 1, 24, 24))

    def test_invalid(self):
        for bad in (None, 3, 27, 50, 2):
            self.assertFalse(zif.valid_chip(bad))
        with self.assertRaises(ValueError):
            zif.placement(50)
        with self.assertRaises(ValueError):
            zif.zif_pin(28, 29)

    def test_no_native_23128_setting(self):
        """The BackBit has no 23128 menu entry: every 23128 mask ROM is read as 27128 with the Pin 27 warning."""
        from core.database import (PIN27_WARNING_23128, PIN27_WARNING_SINCLAIR, normalize_backbit_option,
                                   validate_rom_dict)
        with tempfile.TemporaryDirectory() as tmp:
            db = RomDatabase(DEFAULT_DB_PATH, Path(tmp) / "i.json")
        masks = [r for r in db.all_roms() if "23128" in r.chip_type]
        self.assertGreaterEqual(len(masks), 30)
        for r in db.all_roms():
            self.assertNotIn("23128", [o.setting for o in r.backbit], r.id)
        for r in masks:
            first = r.backbit[0]
            self.assertEqual((first.setting, first.pins), ("27128", 28), r.id)
            self.assertIn("Pin 27 to Pin 28 (+5V)", first.warning, r.id)
            self.assertTrue(all(not o.warning for o in r.backbit[1:]), r.id)   # genuine EPROM: no interposer
        zx = db.rom_by_id("zx48_rom")
        self.assertEqual(zx.backbit[0].menu_text, "Menu: ROM -> 28-Pin -> 27128")
        self.assertEqual(zx.backbit[0].warning, PIN27_WARNING_SINCLAIR)
        self.assertIn("active-high CS2", zx.backbit[0].warning)
        self.assertIn("blank read", zx.backbit[0].warning)
        for rid in ("bbcb_os12", "oric1_basic10", "atmos_basic11b", "c64_251913"):
            self.assertEqual(db.rom_by_id(rid).backbit[0].warning, PIN27_WARNING_23128, rid)
        self.assertFalse(any("matches the chip physically fitted" in w for w in zx.warnings))
        # legacy data (user overlays, shared packs) using "23128" is converted on load / import
        n = normalize_backbit_option({"setting": "23128", "pins": 28, "chip": "Original mask ROM"})
        self.assertEqual((n["setting"], n["warning"]), ("27128", PIN27_WARNING_23128))
        self.assertEqual(normalize_backbit_option({"setting": "27128", "pins": 28, "chip": "27128 EPROM"}).get("warning"),
                         None)
        kept = normalize_backbit_option({"setting": "23128", "pins": 28, "chip": "x", "warning": "custom"})
        self.assertEqual(kept["warning"], "custom")
        out, err = validate_rom_dict({"name": "Old pack ROM", "size": 16384, "pins": 28,
                                      "backbit": [{"setting": "23128", "pins": 28, "chip": "Original 23128 mask ROM"}]})
        self.assertEqual(err, "")
        self.assertEqual(out["backbit"][0]["setting"], "27128")
        self.assertIn("warning", out["backbit"][0])

    def test_amiga_roms(self):
        """Kickstart hashes from MAME amiga/amiga.cpp, read with the BackBit 40-pin 27400 (512K) setting."""
        with tempfile.TemporaryDirectory() as tmp:
            db = RomDatabase(DEFAULT_DB_PATH, Path(tmp) / "i.json")
        expect = {   # id: (sha1, crc32, size)
            "a500_ks13": ("891e9a547772fe0c6c19b610baf8bc4ea7fcb785", "c4f0f55f", 256 * 1024),
            "a500_ks204": ("c5839f5cb98a7a8947065c3ed2f14f5f42e334a1", "c3bdb240", 512 * 1024),
            "a600_ks205_350": ("02843c4253bbd29aba535b0aa3bd9a85034ecde4", "43b0df7b", 512 * 1024),
            "a1200_ks31_hi": ("b8800f5f909298109ea69690b1b8523fa22ddb37", "08dbf275", 256 * 1024),
            "a1200_ks31_lo": ("90e331be1970b0e53f53a9b0390b51b59b3869c2", "16c07bf8", 256 * 1024),
            "a4000_ks31_70_hi": ("138d8cb43b8312fe16d69070de607469b3d4078e", "f9cbecc9", 256 * 1024),
            "a1000_u5n": ("2155b4887f064c5e01e0a2ebb4a0cc2a3e88d9e8", "76bd46ec", 32 * 1024),
        }
        for rid, (sha1, crc, size) in expect.items():
            r = db.rom_by_id(rid)
            self.assertEqual((r.sha1, r.crc32, r.size), (sha1, crc, size), rid)
        amiga = [r for r in db.all_roms() if r.machine_id.startswith("amiga") or r.machine_id == "cdtv"]
        self.assertGreaterEqual(len(amiga), 50)
        for r in amiga:
            if r.pins == 40:
                self.assertEqual(r.backbit[0].menu_text, "Menu: ROM -> 40-Pin -> 27400", r.id)
                self.assertTrue(any("byte order" in w for w in r.warnings), r.id)
                self.assertEqual(any(w.startswith("256KB (2Mbit) chip") for w in r.warnings),
                                 r.size == 256 * 1024, r.id)
        self.assertEqual(db.rom_by_id("a1200_ks31_hi").socket, "U6A")
        self.assertEqual(db.rom_by_id("a4000_ks30_lo").socket, "U176")
        self.assertEqual(zif.placement(40).describe()[-1], "Leave EMPTY: ZIF pins 21–28 (bottom 4 rows)")
        # the same ROM in several machines resolves to every machine, under one sha.txt label
        hits = db.lookup_sha1("c5839f5cb98a7a8947065c3ed2f14f5f42e334a1")
        self.assertEqual({m.rom.machine_id for m in hits}, {"amiga500", "amiga500p", "amiga600"})
        self.assertEqual({m.rom.label for m in hits}, {"KS2.04 37.175"})

    def test_settings_match_backbit_menu(self):
        """Every option uses an entry on the tester's ROM menu (store.backbit.io list, confirmed on a real unit)."""
        from core.database import (NOTE_27100, WARNING_23256, WARNING_531000, WARNING_68364, WARNING_68764,
                                   normalize_backbit_option)
        menu = {24: {"2316", "2716", "2332", "2532", "2732", "2364"},
                28: {"6540", "2564", "2764", "27128", "27256", "27512", "531000"},
                32: {"27100-0", "27100-1", "27200", "27040", "27080"}, 40: {"271024", "27400"}, 42: {"27800"}}
        with tempfile.TemporaryDirectory() as tmp:
            db = RomDatabase(DEFAULT_DB_PATH, Path(tmp) / "i.json")
        raw = json.loads(DEFAULT_DB_PATH.read_text(encoding="utf-8"))
        for m in raw["machines"]:
            for r in m["roms"]:
                for o in r["backbit"]:
                    self.assertIn(o["setting"], menu[o["pins"]], (r["id"], o))
        cpc = db.rom_by_id("cpc464_os_basic")
        self.assertEqual((cpc.backbit[0].setting, cpc.backbit[0].warning), ("27256", WARNING_23256))
        self.assertEqual(cpc.backbit[1].warning, "")                          # the 27256 EPROM option
        # Spectrum 128 / +2 / +2A / +3 ROMs are 27256-compatible by design: read directly, no select warning
        for rid in ("zx128_rom", "zxp2_rom", "p3_40_ic7", "p3_41_ic8"):
            r = db.rom_by_id(rid)
            self.assertEqual((r.backbit[0].setting, r.backbit[0].warning), ("27256", ""), rid)
            self.assertIn("27256-compatible", r.backbit[0].chip, rid)
        self.assertEqual(db.rom_by_id("zx128_rom").socket, "IC5")             # IC2 is the Z80, not a ROM
        p2 = db.rom_by_id("zxp2_rom")
        self.assertEqual(p2.socket, "IC8")
        self.assertTrue(any("pin 1 towards the bottom" in w for w in p2.warnings))
        # +2A / +2B / +3: two separate 32KB chips per ROM version, IC7 (ROM 0+1) and IC8 (ROM 2+3)
        for ver, p7, p8 in (("40", "40092", "40093"), ("41", "40092u", "40093u"),
                            ("40es", "40094", "40101"), ("41es", "40094s", "40101s")):
            for sock, part in (("IC7", p7), ("IC8", p8)):
                r = db.rom_by_id(f"p3_{ver}_{sock.lower()}")
                self.assertEqual((r.socket, r.size, r.part_numbers[0]), (sock, 32768, part))
                self.assertIn(sock, r.name)
                self.assertTrue(any("pin 1 towards the bottom" in w and sock in w for w in r.warnings), r.id)
                self.assertTrue(any(p7 in w and p8 in w for w in r.warnings), r.id)          # names its partner
        # MAME specpls3.cpp: Spanish v4.1 pair
        self.assertEqual(db.lookup_sha1("c525bd23f79ca968d34a0efdcc47b2eb342007f5")[0].rom.id, "p3_41es_ic7")
        self.assertEqual(db.lookup_sha1("56eb124d44ee8c8daef130be4d7e735ec412c4ba")[0].rom.id, "p3_41es_ic8")
        self.assertNotIn("warning", normalize_backbit_option(
            {"setting": "23256", "pins": 28, "chip": "Original 23256 mask ROM (27256-compatible pinout)"}))
        self.assertEqual(db.rom_by_id("bbcm_mos320").backbit[0].warning, WARNING_531000)
        d32 = db.rom_by_id("d32_lo")
        self.assertEqual([(o.setting, o.warning) for o in d32.backbit[:2]],
                         [("2364", WARNING_68364), ("2364", WARNING_68764)])
        arc = db.rom_by_id("arc200_0")
        self.assertEqual([o.setting for o in arc.backbit], ["27100-0", "27100-1"])
        self.assertIn(NOTE_27100, arc.notes)
        self.assertEqual(db.rom_by_id("arc311_0").backbit[0].setting, "27040")
        # names typed in the Database Manager or found in old packs are mapped to the menu
        for typed, want in (("23256", "27256"), ("231024", "531000"), ("68364", "2364"), ("27C010", "27100-0"),
                            ("27c040", "27040"), ("27C256", "27256"), ("27C800", "27800"), ("2764", "2764")):
            self.assertEqual(normalize_backbit_option({"setting": typed, "pins": 28, "chip": ""})["setting"],
                             want, typed)
        n = normalize_backbit_option({"setting": "27256", "pins": 28, "chip": "Original 23256 mask ROM"})
        self.assertEqual(n["warning"], WARNING_23256)
        self.assertNotIn("warning", normalize_backbit_option({"setting": "27C040", "pins": 32, "chip": "EPROM"}))

    def test_cd32(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = RomDatabase(DEFAULT_DB_PATH, Path(tmp) / "i.json")
        r = db.rom_by_id("cd32_391640")
        self.assertEqual((r.sha1, r.crc32, r.size, r.pins), ("816ce6c5077875850c7d43452230a9ba3a2902db", "a4fbc94a",
                                                             1024 * 1024, 42))
        self.assertEqual(r.backbit[0].menu_text, "Menu: ROM -> 42-Pin -> 27800")
        self.assertTrue(any("byte order" in w for w in r.warnings))
        self.assertEqual(zif.placement(42).describe()[-1], "Leave EMPTY: ZIF pins 22–27 (bottom 3 rows)")

    def test_menu_badge_pin_count_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = RomDatabase(DEFAULT_DB_PATH, Path(tmp) / "i.json")
        opt = db.rom_by_id("d64_1").backbit[0]
        self.assertEqual(opt.menu_text, "Menu: ROM -> 28-Pin -> 27128")
        self.assertEqual(db.rom_by_id("c64_kernal_r3").backbit[0].menu_text, "Menu: ROM -> 24-Pin -> 2364")


class TestCompare(unittest.TestCase):
    def setUp(self):
        self.ref = rnd(8192, 7)

    def titles(self, r):
        return " | ".join(d.title + ": " + d.text for d in r.diagnoses)

    def test_identical(self):
        self.assertEqual(compare_images(self.ref, self.ref).verdict, "identical")

    def test_doubled(self):
        r = compare_images(self.ref * 2, self.ref)
        self.assertEqual(r.verdict, "content_match")
        self.assertIn("doubled", r.banner)
        r = compare_images(self.ref * 4, self.ref)
        self.assertIn("quadrupled", r.banner)

    def test_padded_half(self):
        r = compare_images(b"\xff" * 8192 + self.ref, self.ref)
        self.assertEqual(r.verdict, "content_match")
        self.assertIn("upper half", r.banner)
        self.assertIn("0xFF padding", r.banner)

    def test_stuck_data_bit(self):
        a = bytes(x & ~0x08 for x in self.ref)
        r = compare_images(a, self.ref)
        self.assertEqual(r.verdict, "different")
        self.assertIn("Data bit D3 in File A is stuck LOW (all mismatches are solely on bit D3)", self.titles(r))
        a = bytes(x | 0x40 for x in self.ref)
        self.assertIn("D6 in File A is stuck HIGH", self.titles(compare_images(a, self.ref)))

    def test_shorted_lines(self):
        def and_short(x):
            v = (x >> 1 & 1) & (x >> 2 & 1)
            return (x & ~0x06) | (v << 1) | (v << 2)
        r = compare_images(bytes(and_short(x) for x in self.ref), self.ref)
        self.assertIn("D1 and D2 appear shorted together (AND-short)", self.titles(r))

        def or_short(x):
            v = (x >> 5 & 1) | (x >> 7 & 1)
            return (x & ~0xA0) | (v << 5) | (v << 7)
        r = compare_images(bytes(or_short(x) for x in self.ref), self.ref)
        self.assertIn("D5 and D7 appear shorted together (OR-short)", self.titles(r))

    def test_address_lines(self):
        for k in (0, 3, 11, 12):
            for high in (False, True):
                a = bytearray(len(self.ref))
                for i in range(len(a)):
                    a[i] = self.ref[(i | (1 << k)) if high else (i & ~(1 << k))]
                r = compare_images(bytes(a), self.ref)
                self.assertIn(f"Address line A{k} stuck {'HIGH' if high else 'LOW'}", self.titles(r), (k, high))

    def test_small_patch_and_bitrot(self):
        a = bytearray(self.ref)
        for off in (0x100, 0x1234):
            a[off] ^= 0x01
        r = compare_images(bytes(a), self.ref)
        self.assertIn("Small patch", self.titles(r))
        self.assertIn("0x1234", self.titles(r))
        b = bytearray(self.ref)
        for off in range(0, 8192, 97):
            b[off] |= 0x80 if not b[off] & 0x80 else 0x01 if not b[off] & 0x01 else 0x00
        r = compare_images(bytes(b), self.ref)
        self.assertIn("Bits drifting to 1", self.titles(r))

    def test_unrelated(self):
        r = compare_images(rnd(8192, 99), self.ref)
        self.assertIn("Files look unrelated", self.titles(r))


class TestShaTxt(TempDbMixin, unittest.TestCase):
    def test_labels_and_format(self):
        self.assertEqual(sanitize_label("C64—KERNAL\t r3 é"), "C64-KERNAL r3 ?")
        self.assertEqual(DEFAULT_LABEL_LIMIT, 16)                      # BackBit 16x2 character LCD
        self.assertLessEqual(len(auto_label("C64", "KERNAL ROM - Revision 3", "U4")), 16)
        self.assertLessEqual(len(auto_label("Commodore 128", "BASIC High ROM - Revision 4", "U34")), 16)
        entries = builtin_entries(self.db)
        self.assertGreater(len(entries), 150)
        self.assertEqual(len({e.sha1 for e in entries}), len(entries))
        too_long = [e.label for e in entries if len(e.label) > 16]
        self.assertEqual(too_long, [])
        self.assertEqual(len({e.label for e in entries}), len(entries), "labels must be unique per hash")
        txt = format_sha_txt(entries)
        for line in txt.splitlines():
            # sha1sum layout: hash at offset 0..39, two spaces at 40..41, label from offset 42
            self.assertRegex(line, r"^[0-9a-f]{40}  [^ ].{0,15}$")
        self.assertRegex(format_sha_txt(entries[:1], uppercase=True), r"^[0-9A-F]{40}  ")

    def test_sha1sum_format_is_default(self):
        """BackBit manual: exactly the Linux sha1sum output - lower-case hash, two spaces, label."""
        self.assertFalse(DEFAULT_UPPERCASE)
        self.assertEqual(SEPARATOR, "  ")
        txt = format_sha_txt([ShaEntry("B376885AC8452B6CBF9CED81B1080BFD570D9B91", "ZEROES", "t")])
        self.assertEqual(txt, "b376885ac8452b6cbf9ced81b1080bfd570d9b91  ZEROES\n")   # manual's example
        out = self.tmp / "sha.txt"
        write_sha_txt(out, [ShaEntry("cd" * 20, "Y", "t")])
        self.assertEqual(out.read_bytes(), ("cd" * 20 + "  Y\n").encode())
        write_sha_txt(out, [ShaEntry("cd" * 20, "Y", "t")], crlf=True)
        self.assertEqual(out.read_bytes(), ("cd" * 20 + "  Y\r\n").encode())
        # real sha1sum output of an all-zero 8KB image, and our own output, both parse back
        zeros = hashlib.sha1(bytes(8192)).hexdigest()
        parsed, invalid = entries_from_sha_txt(format_sha_txt([ShaEntry(zeros, "ZEROES 8K", "t")]) +
                                               f"{zeros}  ZEROES\n{zeros} *binary-mode.bin\n")
        self.assertEqual(invalid, 0)
        self.assertEqual([e.label for e in parsed], ["ZEROES 8K", "ZEROES", "binary-mode.bin"])

    def test_merge_dedup(self):
        lst = [ShaEntry("a" * 40, "One", "x")]
        new, invalid = entries_from_sha_txt(f"{'A' * 40} Dup\n{'b' * 40} Two\nnot a line\n")
        self.assertEqual(invalid, 1)
        self.assertEqual(merge_entries(lst, new), (1, 1))
        self.assertEqual([e.label for e in lst], ["One", "Two"])
        lst[1].include = False
        self.assertEqual(format_sha_txt(lst).count("\n"), 1)


if __name__ == "__main__":
    unittest.main()
