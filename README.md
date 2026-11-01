# BackBit ROM Companion

A cross-platform workbench companion for the **BackBit Chip Tester Pro V2**, built for people who restore vintage computers.
It tells you which tester setting to use and where the chip goes in the ZIF socket. It also identifies ROM dumps and
diagnoses failing chips by comparing them against a known-good image. Finally, it builds the `sha.txt` file that makes
the tester show a ROM's name on its 16x2 character LCD.

Runs on **Windows**, **macOS** (Intel and Apple Silicon) and **Linux**, either from source (Python + PyQt6) or as a
standalone build with no Python required.

---

## Features

| Tab | What it does |
|-----|--------------|
| **1 · Machine → BackBit Lookup** | Pick a machine and ROM to see the exact BackBit menu setting (e.g. `Menu: ROM -> 24-Pin -> 2364`; the BackBit lists ROM tests by pin count, then chip number), the motherboard socket, part numbers, size, SHA-1/CRC32, pin-compatibility and polarity warnings, a suggested modern replacement, and a drawing of where the chip sits in the 48-pin ZIF. Chips are top-justified (chip pin 1 = ZIF pin 1, notch up), the bottom rows stay empty, and the lever is drawn down the left side. Includes a **Database Manager**: Add / Edit / Delete entries. |
| **2 · ROM File Inspector & Identifier** | Drop in a dump to get SHA-1/MD5/CRC32, database identification (whole chip *or* individual banks of doubled, padded or combined images), repeat/mirror detection, and a data-bit health check (stuck bits, bridged data lines, address-line mirroring). If the file is unrecognised, **Register in Database…** opens the editor with the size, SHA-1 and CRC32 already filled in. |
| **3 · Smart ROM Compare & Diagnostics** | Compares a ripped dump (A) with a known-good reference (B). The verdict banner shows *Identical*, *Size mismatch / content match* (doubled, quadrupled, padded) or *Different*. Automated fault diagnosis covers stuck data bits, AND/OR-shorted data lines, stuck address lines, small patches, EPROM bit-rot and unrelated files. A side-by-side hex diff shows 16 bytes per row, with highlighted bytes, per-bit detail and a *Show only rows with differences* filter. |
| **4 · BackBit sha.txt Generator** | Builds a staging list from the built-in database, imported sha.txt files, dropped ROM files or an existing sha.txt (duplicates are removed by SHA-1). Labels are editable, with a live counter against the LCD's 16-character line, and a preview updates as you edit. The output follows the BackBit manual exactly: `sha1sum` format, i.e. a lower-case hash, **two spaces**, then the label (e.g. `b376885ac8452b6cbf9ced81b1080bfd570d9b91  ZEROES`). The file is saved only through a native **Save As…** dialog. Nothing is ever written to an SD card automatically. |

Other features:

- **Database sharing**: *File → Export User Database Additions…* writes your added and edited ROMs to a `.json` pack. *File → Import Database JSON…* merges a pack (or a full `roms_database.json`) from someone else. Before writing anything it shows a dry-run summary: new, identical, duplicate SHA-1, conflicting and invalid entries.
- **sha.txt import**: *File → Import BackBit sha.txt…* adds community hash lists to identification.
- **Dark / Light theme**: switch instantly with the **☀ Light / 🌙 Dark** button in the top-right corner of the tab bar, *View → Switch to Light/Dark Mode* (**Ctrl+T**) or *View → Theme*. No restart is needed, and everything is restyled, including the ZIF drawing, hex-diff highlights, verdict banners and label counters. The choice is saved in `settings.json`; dark is the default.
- **Help → About** shows the version, database statistics and the active user-data folder, with an *Open User Data Folder* button.

## Supported machines (built-in database)

39 machines, 172 ROM entries. Hashes are transcribed from MAME driver sources. Combined images are verified from
their MAME bank hashes.

| Manufacturer | Machines |
|---|---|
| Sinclair / Amstrad | ZX80, ZX81, ZX Spectrum 48K, 128K (Toastrack), +2 (grey), +2A / +2B / +3, QL |
| Commodore | C64 / 64C (incl. 251913-01), 1541 (long board), VIC-20, C16 / Plus/4, C128, PET 2001 (BASIC 1), 2001-N / 3000 (BASIC 2), 4016 / 4032 (BASIC 4), 8032 |
| Amstrad | CPC 464, CPC 664, CPC 6128, 464 Plus / 6128 Plus |
| Atari | 400 / 800, 800XL, 65XE / 130XE |
| Acorn | BBC Micro Model B, BBC Master 128, Electron, Atom, Archimedes A300 / A400 / A3000 |
| Apple | Apple II / II+, Apple IIe (unenhanced and enhanced) |
| Tandy / Radio Shack | TRS-80 Model I, Model III, Color Computer 1 / 2 |
| Others | Dragon 32 / 64, Camputers Lynx 48K / 96K / 128K, Oric-1 / Atmos, Memotech MTX500 / 512, Jupiter Ace, MSX1 (Toshiba HX-10, Sony HB-75P) |

Entries carry warnings where a dump or a socket is uncertain, for example MAME `BAD_DUMP` flags on the Atari 400/800
OS and the QL MG ROMs, and MAME's "needs verification" note on the Apple II Integer BASIC 341-0001…0003.

---

## Running from source

Requires **Python 3.10 or newer** and **PyQt6 6.5+**.

### Windows

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

### macOS (Intel or Apple Silicon)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

### Linux

PyQt6 wheels bundle Qt itself, but the X11/Wayland platform plugin needs a few system libraries.
On Debian / Ubuntu:

```bash
sudo apt install python3-venv libxcb-cursor0 libxkbcommon-x11-0 libegl1
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

On Fedora, the equivalent packages are `xcb-util-cursor`, `libxkbcommon-x11` and `mesa-libEGL`.

You can pass a ROM file on the command line to open it straight in Tab 2: `python main.py dump.bin`.

## Where your data is stored

The built-in database (`data/roms_database.json`) is read-only when the app is packaged. Your own data is kept in an
OS-native folder:

| OS | User data folder |
|---|---|
| Windows | `%APPDATA%\BackBitROMCompanion` |
| macOS | `~/Library/Application Support/BackBitROMCompanion` |
| Linux | `$XDG_DATA_HOME/BackBitROMCompanion` (default `~/.local/share/BackBitROMCompanion`) |

It contains:

- `user_database.json`: your Database Manager additions, edits and deletions. These are re-applied on top of the built-in database at every start, so they survive app updates.
- `imported_sha.json`: hashes imported from sha.txt files.
- `settings.json`: preferences (currently the theme: `"dark"` or `"light"`).

When running **from source**, Database Manager edits are *also* written into `data/roms_database.json` (a checkbox in
the editor, on by default) so you can commit them back to the project.

### Portable mode (USB stick on the repair bench)

Create an empty file named **`portable.mode`**, or a folder named **`portable_data`**, next to the executable. On
macOS, put it next to the `.app` bundle; from source, put it next to `main.py`. All user data then lives in
`portable_data/` beside the program, and nothing is written to the host computer.

`BACKBIT_DATA_DIR=/some/folder` overrides both, which is useful for shared bench machines and for tests.

---

## Building standalone executables

```bash
pip install -r requirements.txt -r requirements-dev.txt
python build.py                 # one-folder build (fast start-up)  -> dist/BackBitROMCompanion/  (.app on macOS)
python build.py --onefile       # single executable                -> dist/onefile/BackBitROMCompanion(.exe)
python build.py --archive       # also create dist/BackBitROMCompanion-v<version>-<os>-<arch>.zip / .tar.gz
python build.py --clean         # wipe build/ and dist/ first
python build.py --dry-run       # show the PyInstaller command only
```

`build.py` runs PyInstaller for the **current** OS; PyInstaller cannot cross-compile. It bundles
`data/roms_database.json` and `assets/` and sets the platform icon (`.ico` on Windows, `.icns` on macOS). Icons are
generated from code with `python scripts/generate_icons.py`, which `build.py` runs automatically if they are missing.

Notes:

- **macOS**: one-folder mode produces `BackBitROMCompanion.app`. The builds are not code-signed or notarised, so on first launch right-click the app and choose **Open**, or run `xattr -dr com.apple.quarantine BackBitROMCompanion.app`. PyInstaller deprecates `--onefile` for windowed macOS apps, so use the default `.app`.
- **Windows**: unsigned executables may trigger a SmartScreen prompt (*More info → Run anyway*).
- **Linux**: build on the oldest distribution you want to support, because the binary needs a glibc at least as new as the build machine's. The archive is a `.tar.gz` so the executable bit is kept.

### GitHub Actions releases

`.github/workflows/build.yml` builds Windows x64, macOS arm64, macOS x64 (Intel) and Linux x64 packages. It runs:

- on a **`v*` tag push** (e.g. `git tag v1.0.0 && git push origin v1.0.0`), which also publishes a GitHub Release with every archive attached;
- or manually via **Actions → Build standalone packages → Run workflow**, which leaves the archives as workflow artifacts.

Each job runs the unit tests, builds, and then starts the frozen app headless in a smoke-test mode. The job fails
unless the bundled database and icon load.

---

## Tests

```bash
python -m unittest discover -s tests -v
```

The suite covers the database (integrity, combined-hash CRC consistency, Database Manager overlay, pack export/import),
the analyzer and compare/diagnosis engine, sha.txt generation, cross-platform path resolution (including portable
mode and frozen-bundle resolution), offscreen GUI tests of all four tabs and the About dialog, and theme tests
(live switching of every tab, settings persistence, WCAG contrast of both palettes, wide-body ZIF geometry).
Set `BACKBIT_SCREENSHOTS=<folder>` to save screenshots of each tab.

## Project layout

```
main.py                 entry point, main window, menus
core/                   Qt-free logic
  paths.py              bundled resources vs. user data, portable mode
  database.py           ROM database, user overlay, ROM packs, sha.txt import
  analyzer.py           hashing, health checks, bank detection, compare + fault diagnosis
  shatxt.py             sha.txt generation
  zif.py                48-pin ZIF placement maths
  settings.py           settings.json (theme preference)
ui/                     PyQt6 tabs, dialogs and custom widgets
  theme.py              Dark/Light palettes, global stylesheet, live theme switching
data/roms_database.json built-in ROM database
assets/                 generated application icons
scripts/generate_icons.py
build.py                PyInstaller packaging
tests/                  unittest suite
```

## Disclaimer

Hashes identify ROM images; this project ships **no ROM content**. Always confirm pinouts and chip-select polarity
against the actual board before fitting a replacement chip. BackBit and the Chip Tester Pro are products of their
respective owner; this is an independent community tool.
