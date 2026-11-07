# Third-party components and licensing

SPDX-License-Identifier: GPL-3.0-or-later

All bundled or fetched dependencies are compatible with **GPL-3.0-or-later**.
No GPL-2.0-only, proprietary, ad-supported, subscription-gated or
membership-gated component is used, linked or distributed.

| Component | Version / pin | License | How it is used |
|---|---|---|---|
| GNU LibreDWG | pinned in `third_party/libredwg/PIN.json` | GPL-3.0-or-later | DWG/DXF read + DWG write kernel, invoked as a separate executable |
| PySide6 (Qt for Python) | `>=6.8,<7` | LGPL-3.0-only / GPL-3.0-only (dual) or commercial | Desktop UI, `QPainter` screen rendering, `QPdfWriter` PDF device |
| Python standard library | >=3.10 | PSF-2.0 | Everything else |

## LibreDWG corresponding source

Because LibreDWG is GPL-3.0-or-later, any binary distribution of IloveCAD must
be accompanied by the **Corresponding Source** of the exact LibreDWG build
used. `scripts/fetch_libredwg.py` downloads both the prebuilt Windows tools and
the exact matching source tarball, verifies their SHA-256 digests, and stores
them under `third_party/libredwg/`. Release packages are produced by
`scripts/make_source_bundle.py`, which copies that source tarball alongside
IloveCAD's own complete source tree.

The upstream project: <https://www.gnu.org/software/libredwg/>
Upstream license statement: "either version 3 of the License, or (at your
option) any later version".

## Why R2004 and not a newer DWG version

LibreDWG gained **R2004 (AC1018) write support in 0.14 (2026-06-27)**; earlier
releases (e.g. 0.13.3, which most distro packages still ship) only write
reliably up to R2000. IloveCAD therefore pins a nightly build that contains the
0.14.1 security fixes and hard-locks its writer to AC1018. See `README.md`.

## Deliberately excluded

* ODA File Converter / Teigha / AutoCAD / any proprietary DWG converter.
  Users may optionally configure their own external converter path, but no
  such binary is shipped, downloaded by default, or required.
* `@mlightcad/libredwg-web` (WASM) is GPL-3.0-only, not or-later. It is not used.
* Any GPL-2.0-only library, which would be incompatible with GPL-3.0.
