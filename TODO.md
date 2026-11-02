# To-do

Open items for BackBit ROM Companion. Each one needs a fact checked on a real BackBit Chip Tester Pro V2 before the code can change.

## Needs a check on the tester

- [ ] **Kickstart byte order.** Dump one known Kickstart chip with the `27400` setting and load it in Tab 2 (Inspector).
  - If it shows **BYTE-SWAPPED MATCH**, the BackBit saves the other byte order. Store the swapped hashes in the database so the tester's `sha.txt` matches directly.
  - Also check whether a 256 KB Kickstart 1.x chip reads as a doubled 512 KB image.
- [x] **CD32 support.** The tester has `42-Pin -> 27800`; the CD32 system ROM (391640-03) is in the database.
- [x] **Menu settings.** Checked against the tester's ROM menu. 23256 -> `27256`, 231024 -> `531000`, 68364 / 68764 -> `2364`, 27C010 -> `27100-0` / `27100-1`, 27C020 -> `27200`, 27C040 -> `27040`, 27C080 -> `27080` (`SETTING_MAP` in `core/database.py`).
- [ ] **27100-0 vs 27100-1.** The menu has two 128K entries and BackBit doesn't document the difference. Read a known 27C010 with each and note which works; then drop the other option from the database.
- [ ] **Mask ROM select polarity.** The 23256 (now `27256`) and MCM68364 (now `2364`) entries carry a warning to use an interposer if the read is blank. When one of these chips is read for real, record whether it needed one and make the warning specific.
- [ ] **CDTV extended ROMs.** Confirm the chip package of 391008-01 (U34) and 391009-01 (U35), then add them. They are left out for now.

## Testing and release

- [ ] Run the macOS and Linux builds on real machines. CI only smoke-tests them; only Windows has been tried by hand.
- [ ] Code-sign the builds to remove the Windows SmartScreen and macOS Gatekeeper warnings. This needs a signing certificate and an Apple Developer ID.

## Upgrades

- [x] **Pinpoint faulty pins from a bad dump.** Compare-tab faults (stuck or shorted data lines, stuck address lines, blank reads) now name the chip pin and ZIF pin to check, highlighted on a ZIF drawing. 16-bit 40-pin ROMs are diagnosed as D0-D15.
  - Follow-up: the MOS 6540 (proprietary pinout) and the 42-pin `27800` have no pin table, and the 28-pin 1Mbit mask ROM (`531000`) only maps A0-A11 and D0-D7. Add them when a data sheet is checked.
  - Follow-up: once the Kickstart byte order is confirmed on the tester (see above), make it the default in the 16-bit byte-order picker.
- [ ] **Prepare files for burning a replacement.** Once a chip is identified, produce a burn-ready file and name the EPROM to use: pad small images to fit a larger EPROM, split a Kickstart into HI/LO halves, join halves, swap byte order. Warn about pin differences (e.g. a 2364 replacement needs an adapter).
- [ ] **Import more machines from MAME.** A built-in importer that reads MAME's ROM lists and adds whole machines with sockets, sizes and hashes, instead of one-off scripts like the Amiga import.

## Stretch goal: phone version

- [ ] Phone version as a web app you install from the browser (works offline). Planned approach:
  - Run the existing Python `core/` package and `roms_database.json` in the browser with Pyodide, so results match the desktop app exactly.
  - Build a phone layout for lookup (BackBit setting, warnings, pin diagram), checking a dump, comparing two files and exporting `sha.txt` through a save dialog.
  - Read dumps with the phone's file picker, including an SD card in a USB-C reader.
  - Host it free on GitHub Pages, deployed from CI next to the desktop releases. It works on Android and iPhone with no app store or signing.
  - A native Android app (PySide6) was considered and set aside: it means more work, a large app, a Linux-only build, and it covers Android only.
  - Wait until the desktop version is settled.

## Housekeeping

- [ ] The first commit (`579acf4`) has no co-author line. Fixing it means rewriting history and force-pushing `main`, so leave it unless that is wanted.
