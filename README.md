<h1 align="center">FastCollageForWin</h1>

<p align="center">
  Desktop collage editor for Windows: a free canvas, generated photo grids and
  automatic canvas fitting.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/platform-Windows-0078D6?logo=windows&logoColor=white" alt="Platform: Windows">
  <img src="https://img.shields.io/badge/GUI-PySide6%20%7C%20Qt%206-41CD52?logo=qt&logoColor=white" alt="GUI: PySide6, Qt 6">
  <img src="https://img.shields.io/badge/build-PyInstaller-FFD43B?logo=python&logoColor=black" alt="Build: PyInstaller">
  <img src="https://img.shields.io/badge/i18n-EN%20%7C%20RU%20%7C%20ES%20%7C%20ZH%20%7C%20AR-2E86C1" alt="Interface languages">
  <img src="https://img.shields.io/badge/version-2.5.0-757575" alt="Version 2.5.0">
</p>

<p align="center">
  <b>English</b> ·
  <a href="README.ru.md">Русский</a> ·
  <a href="README.es.md">Español</a> ·
  <a href="README.zh.md">中文</a> ·
  <a href="README.ar.md">العربية</a>
</p>

<p align="center">
  <img src="https://github.com/user-attachments/assets/2d1062fd-11d9-440e-8e38-3b4127bf624f" alt="FastCollageForWin">
</p>

---

## Contents

- [What it does](#what-it-does)
- [Features](#features)
- [Requirements](#requirements)
- [Install and run](#install-and-run)
- [Build a standalone executable](#build-a-standalone-executable)
- [Quick start](#quick-start)
- [Fit canvas to photos](#fit-canvas-to-photos)
- [Keyboard and mouse](#keyboard-and-mouse)
- [Export](#export)
- [Projects, settings and logs](#projects-settings-and-logs)
- [Repository layout](#repository-layout)
- [Limits](#limits)
- [Contributing](#contributing)
- [License](#license)

## What it does

FastCollageForWin turns a pile of photos into one image. You add pictures to the
left-hand panel, place them on the canvas by hand or into a generated grid,
adjust each photo (zoom, pan, rotate, mirror), and export the result as a single
PNG or JPEG.

It is a desktop application written in Python with PySide6 (Qt 6) and shipped as
a single windowed Windows executable built by PyInstaller.

There are two ways to work:

- **Free collage** - every photo is an independent item you move, scale and
  rotate anywhere on the canvas.
- **Random collage** - the app generates a grid of slots; a photo dropped into a
  slot fills it, and the borders between slots can be dragged to give one photo
  more room and another less.

**Who it is for:**

- anyone who needs a clean collage fast, without opening a full photo editor;
- designers and photographers preparing layouts and previews;
- people making photo books, posters, thumbnails or social media posts.

## Features

- Two collage modes: free placement and generated grids with draggable slot
  borders.
- **Fit canvas to photos** (`Ctrl+Shift+A`): the app computes a canvas size and
  a layout from the photos you selected, in two modes - exact aspect ratios, or
  with a crop budget and a ranked list of alternative grids.
- Per-photo editing: zoom, pan, free rotation, horizontal and vertical mirror,
  layer order.
- Collage style: spacing between photos, corner radius, background colour or a
  fully transparent background.
- 25 canvas presets grouped into screen, social and print sizes (A5-A3 and photo
  formats at 300 dpi), plus any custom size from 100 to 10000 px.
- Export to PNG or JPEG at 10-400 % scale with a JPEG quality slider, a file
  size estimate before saving, and tile-based rendering that keeps memory flat
  on very large canvases.
- Projects (`.fcproj`, JSON inside) store **relative image paths**, so a moved
  project folder still opens; missing files can be relinked on open.
- Undo and redo, 100 steps by default.
- Images load in a background thread pool with a cancellable progress dialog.
- Image panel with drag and drop from Explorer, thumbnails, and a shortcut that
  sends a photo from the canvas back to the panel.
- Clipboard support: copy, paste and duplicate items.
- Five interface languages - English, Russian, Spanish, Chinese and Arabic -
  with a right-to-left layout for Arabic.
- Shortcuts work on any keyboard layout: the mode keys are read by physical
  scan code, not by the letter the layout produces.
- Rotating log file, so a silent failure always leaves a trace.
- In a grid every photo is clipped to its own slot, so photos never overlap and
  the grid tiles the whole canvas - the only gaps are the spacing you set.
- Free collage puts no cap on the number of photos; memory is the only limit.
- The image panel is a dock: **View -> Preview panel** hides or shows it, it can
  be floated and cleared in one click, and its state is restored on the next
  start. **File -> Load to panel** fills the panel without placing anything on
  the canvas.

## Requirements

- **Windows 10 or 11.** The application is Windows-first: the log location and
  the layout-independent shortcuts assume Windows. Everything else is plain
  Python and Qt.
- **Python 3.9+**
- **PySide6** (Qt 6) - the only runtime dependency.

## Install and run

```powershell
git clone <repository-url>
cd fastcollageforwin
python -m venv .venv
.venv\Scripts\activate
pip install PySide6
python main.py
```

There is no `requirements.txt` in the repository yet - `pip install PySide6` is
the entire dependency list.

## Build a standalone executable

```powershell
pip install pyinstaller
pyinstaller main.spec
```

The spec file builds a windowed single-file executable (`console=False`) and
bundles the `assets/` folder, so the toolbar icons survive packaging. The result
is `dist/main.exe` - rename it as you like.

The version in the window title comes from `core/version.py`. If a fresh build
still shows the old version, PyInstaller picked up stale files: fix the build,
not the code.

## Quick start

1. Start the app. The **New collage** dialog asks for the mode (free or random),
   the number of photos for a grid, and the canvas size - a preset or a custom
   width and height.
2. Add photos: **File -> Add image** (`Ctrl+O`), or drag files from Explorer into
   the image panel on the left.
3. Drag a thumbnail from the panel onto the canvas, or onto a grid slot.
4. Adjust a photo: hold `Z` and scroll to zoom the content, hold `C` and drag to
   move it inside its slot, hold `R` and drag to rotate. Press `X` to send the
   selected photo back to the panel.
5. Tune the look in **Canvas -> Spacing and background...**: gaps between
   photos, corner radius, background colour or transparency.
6. Export with **File -> Export** (`Ctrl+E`): choose PNG or JPEG, a scale and a
   quality, then save.

## Fit canvas to photos

**Canvas -> Fit canvas to photos** (`Ctrl+Shift+A`) reads the photos in the image
panel and proposes a canvas size together with a layout, instead of making you
guess a size first. Size rounding is selectable: exact, to 10 px, to 100 px, or
an automatic round number.

Two fitting modes:

- **Exact proportions (no cropping)** - every photo keeps its aspect ratio and
  nothing is cut off. One best layout is proposed.
- **More options (crop up to N %)** - each photo may lose up to N % of a side, so
  its proportions can be pulled to a convenient target. That unlocks dozens of
  different grids, ranked from best to worst; the **Another layout** button walks
  through them and the counter shows "Option 3 of 12".

The crop budget and the number of variants live in
**Settings -> Canvas fitting...** (0-50 % and 2-48, defaults 20 % and 12). They
apply to the second mode only; the first mode ignores them.

A bigger crop budget is not automatically better: past roughly 35 % different
photos get pulled to the same target proportions and the layouts start to repeat,
so the number of distinct grids goes down again. 20-35 % is the sweet spot.

## Keyboard and mouse

### Files

| Keys | Action |
| --- | --- |
| `Ctrl+O` | Add image |
| `Ctrl+Shift+O` | Open project |
| `Ctrl+S` / `Ctrl+Shift+S` | Save project / Save as |
| `Ctrl+E` | Export |

### Editing

| Keys | Action |
| --- | --- |
| `Ctrl+Z` / `Ctrl+Y` | Undo / redo |
| `Delete` | Delete the selected item |
| `Ctrl+C` / `Ctrl+V` / `Ctrl+D` | Copy / paste / duplicate |
| `Ctrl+]` / `Ctrl+[` | Bring to front / send to back |

### Canvas and view

| Keys | Action |
| --- | --- |
| `Ctrl+Shift+C` | Canvas size |
| `Ctrl+Shift+A` | Fit canvas to photos |
| `Ctrl+M` | Zoom |
| `Ctrl` + wheel | Zoom the view |
| middle button + drag | Pan the canvas without limits |
| wheel, arrow buttons | Pan within one canvas size on each side |
| `F1` | Shortcut reference |

### Photo

| Keys | Action |
| --- | --- |
| `Ctrl+Shift+H` / `Ctrl+Shift+V` | Mirror horizontally / vertically |
| `Ctrl+Shift+L` / `Ctrl+Shift+R` | Rotate left / right |
| `Shift` + wheel | Rotate freely |

### Modes (while the key is held)

| Keys | Action |
| --- | --- |
| `Z` + wheel | Zoom the content inside a slot |
| `Z` + drag | Pan the zoomed content |
| `C` + drag, `Alt` + drag | Move the photo inside its slot |
| `R` + drag | Rotate the photo |
| `X` | Return the selected photo to the image panel |

## Export

**File -> Export** (`Ctrl+E`) writes one PNG or JPEG file.

- Scale presets: 10, 25, 50, 75, 100, 150, 200, 300 and 400 %.
- JPEG quality slider, 92 by default.
- The dialog shows the resulting pixel size and an estimated file size before
  you save.
- Guard rails: a side above 32000 px or an estimate above 2 GB is refused, and
  anything above 512 MB is confirmed with a warning.
- Rendering runs in horizontal bands of 2048 px, so a huge canvas never needs a
  full-height bitmap in memory.
- A transparent background survives in PNG; JPEG has no transparency and is
  flattened.
- At 100 % the exported image matches the canvas size in pixels exactly, so the
  file is ready to print or upload as is.

## Projects, settings and logs

- Projects are saved as `.fcproj` (JSON inside; plain `.json` is still accepted
  for files from older builds).
- Image paths are stored **relative** to the project file, so moving the whole
  folder keeps the collage intact. Absolute paths are kept as a fallback.
- If images are missing when a project opens, the app offers to relink them by
  pointing at their new folder.
- The last 8 projects are listed in **File -> Recent projects**.
- Settings are stored with `QSettings` (Windows registry): language, window
  geometry, undo limit, export scale and quality, canvas fitting parameters and
  the folders you used last.
- The log file is `%LOCALAPPDATA%\FastCollageForWin\fastcollage.log`, rotated at
  2 MB with 5 backups. The build is windowed, so the log is the only place where
  an unexpected error shows up - the error dialog prints its path.

## Repository layout

```text
main.py            entry point, logging, crash dialog
main.spec          PyInstaller build spec
i18n.py            every interface string, five languages
core/              settings, presets, project I/O, image cache,
                   background loading, canvas fitting, keymap
canvas/            scene, photo items, slots, grid generator
ui/                main window, dialogs, image panel
undo/              undo and redo commands
assets/icons/      toolbar icons
```

## Limits

- Canvas side: 100-10000 px.
- Grid generation in the start dialog: 1-100 photos.
- Canvas fitting explores the full layout tree up to 20 photos; above that it
  switches to a simplified search - still instant, but with fewer variants.
- Export: side up to 32000 px, estimated file up to 2 GB.
- Input formats: PNG, JPG, JPEG, BMP, WEBP.
- There is no automated test suite in the repository yet.

## Contributing

- Keep `i18n.py` in sync: every key must exist in all five languages, otherwise
  the interface silently falls back to English or Russian.
- `python -m compileall .` is the cheapest smoke test. PySide6 dialogs need a
  display, so most checks are still manual.
- Release history and the technical details behind each change are in
  [CHANGELOG.md](CHANGELOG.md).

## License

No license file yet - until one is added, all rights are reserved by the author.
If you publish this repository, add a `LICENSE` file and name it here.
