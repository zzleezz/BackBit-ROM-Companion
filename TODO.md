# To-do

Open items for BackBit ROM Companion. Each one needs a fact checked on a real BackBit Chip Tester Pro V2 before the code can change.

## Needs a check on the tester

- [ ] **Kickstart byte order.** Dump one known Kickstart chip with the `27400` setting and load it in Tab 2 (Inspector).
  - If it shows **BYTE-SWAPPED MATCH**, the BackBit saves the other byte order. Store the swapped hashes in the database so the tester's `sha.txt` matches directly.
  - Also check whether a 256 KB Kickstart 1.x chip reads as a doubled 512 KB image.
- [ ] **CD32 support.** Does the tester have a 42-pin `27800` (1 MB) setting? If it does, add the CD32 Kickstart/extended ROM from MAME.
- [ ] **Missing mask ROM settings.** Check whether the tester's menu has `23256`, which the A1000 boot ROMs and some older entries use. Also check `2316`, `2332`, `2364`, `23512` and `231024`.
  - Add each missing setting to `NO_NATIVE_SETTING` in `core/database.py`, mapped to the matching 27-series setting (one line each, like `23128 -> 27128`).
- [ ] **CDTV extended ROMs.** Confirm the chip package of 391008-01 (U34) and 391009-01 (U35), then add them. They are left out for now.

## Testing and release

- [ ] Run the macOS and Linux builds on real machines. CI only smoke-tests them; only Windows has been tried by hand.
- [ ] Code-sign the builds to remove the Windows SmartScreen and macOS Gatekeeper warnings. This needs a signing certificate and an Apple Developer ID.

## Housekeeping

- [ ] The first commit (`579acf4`) has no co-author line. Fixing it means rewriting history and force-pushing `main`, so leave it unless that is wanted.
