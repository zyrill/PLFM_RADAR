#!/usr/bin/env python3
"""Build a consolidated, costed BOM for an AERIS-10 build.

Reads the per-board BOM exports under
``4_Schematics and Boards Layout/4_7_Production Files`` and merges them into a
single CSV with a (blank) unit-price column ready to be filled in by hand or by
a distributor API (Mouser/Digikey/LCSC).

Board multiplicities reflect one full system:

    AERIS-10X (Extended, default): Main + FreqSynth + Power + 16x PA
    AERIS-10N (Nexus):             Main + FreqSynth + Power + Patch antenna

The PA board carries the 10 W QPA2962 GaN amplifier and is populated 16x on the
Extended variant -- the dominant cost driver. Note the source BOMs are EAGLE
exports with empty price columns, so unit prices are NOT present in the repo and
must be supplied; ``Unit_Price_USD`` is emitted blank and ``Line_Total_USD`` is
computed from whatever is filled in.

Usage:
    python3 build_costed_bom.py                 # Extended -> costed_bom.csv
    python3 build_costed_bom.py --variant nexus
    python3 build_costed_bom.py --prices prices.csv   # MPN,unit_price overrides
    python3 build_costed_bom.py -o /tmp/bom.csv
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import os
import re
import zipfile
from dataclasses import dataclass

# Production-files root, relative to repo root.
PROD = os.path.join(
    "4_Schematics and Boards Layout", "4_7_Production Files",
)

# Columns we keep from each source BOM, looked up by header name (case-insensitive).
WANT = ["Qty", "Value", "Device", "Package", "Parts", "Description"]
# Preference order for the manufacturer part number column.
MPN_COLS = ["MANUFACTURER_PART_NUMBER", "MPN", "MP", "Device"]


@dataclass
class BomLine:
    board: str
    boards: int
    qty_per_board: int
    value: str
    mpn: str
    package: str
    description: str

    @property
    def total_qty(self) -> int:
        return self.qty_per_board * self.boards


def _pick(header: list[str], names: list[str]) -> int | None:
    low = [h.strip().upper() for h in header]
    for n in names:
        if n.upper() in low:
            return low.index(n.upper())
    return None


def _clean(s: str) -> str:
    return (s or "").strip()


def read_csv_bom(path: str, board: str, boards: int) -> list[BomLine]:
    with open(path, encoding="latin1", newline="") as f:
        rows = list(csv.reader(f, delimiter=";"))
    header = rows[0]
    idx = {w: _pick(header, [w]) for w in WANT}
    mpn_i = _pick(header, MPN_COLS)
    out: list[BomLine] = []
    for r in rows[1:]:
        if not any(_clean(c) for c in r):
            continue
        qty_raw = _clean(r[idx["Qty"]]) if idx["Qty"] is not None else ""
        try:
            qty = int(float(qty_raw))
        except ValueError:
            qty = 0
        mpn = _clean(r[mpn_i]) if mpn_i is not None and mpn_i < len(r) else ""
        out.append(BomLine(
            board=board, boards=boards, qty_per_board=qty,
            value=_clean(r[idx["Value"]]) if idx["Value"] is not None else "",
            mpn=mpn or (_clean(r[idx["Device"]]) if idx["Device"] is not None else ""),
            package=_clean(r[idx["Package"]]) if idx["Package"] is not None else "",
            description=_clean(r[idx["Description"]]) if idx["Description"] is not None else "",
        ))
    return out


def _xlsx_rows(path: str) -> list[list[str]]:
    """Minimal xlsx reader: returns sheet1 as a list of string rows."""
    z = zipfile.ZipFile(path)
    shared: list[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        xml = z.read("xl/sharedStrings.xml").decode("utf-8", "replace")
        # Each <si> may hold multiple <t> runs; join them.
        shared.extend(
            "".join(re.findall(r"<t[^>]*>(.*?)</t>", si, re.S))
            for si in re.findall(r"<si>(.*?)</si>", xml, re.S)
        )
    sheet = z.read("xl/worksheets/sheet1.xml").decode("utf-8", "replace")

    def col_index(ref: str) -> int:
        """Convert a cell ref like 'C5' to a zero-based column index."""
        letters = re.match(r"[A-Z]+", ref or "")
        if not letters:
            return 0
        n = 0
        for ch in letters.group(0):
            n = n * 26 + (ord(ch) - ord("A") + 1)
        return n - 1

    rows: list[list[str]] = []
    for row in re.findall(r"<row[^>]*>(.*?)</row>", sheet, re.S):
        cells: dict[int, str] = {}
        for c in re.finditer(r"<c\b([^>]*)>(.*?)</c>", row, re.S):
            attrs, body = c.group(1), c.group(2)
            ref_m = re.search(r'r="([A-Z]+\d+)"', attrs)
            ci = col_index(ref_m.group(1)) if ref_m else len(cells)
            ctype = re.search(r't="([^"]+)"', attrs)
            t = ctype.group(1) if ctype else None
            if t == "inlineStr":
                val = "".join(re.findall(r"<t[^>]*>(.*?)</t>", body, re.S))
            else:
                vm = re.search(r"<v>(.*?)</v>", body, re.S)
                v = vm.group(1) if vm else ""
                val = shared[int(v)] if (t == "s" and v.isdigit()) else v
            cells[ci] = val
        if cells:
            width = max(cells) + 1
            rows.append([cells.get(i, "") for i in range(width)])
    return rows


def read_xlsx_bom(path: str, board: str, boards: int) -> list[BomLine]:
    rows = _xlsx_rows(path)
    rows = [r for r in rows if any(_clean(c) for c in r)]
    header = rows[0]
    idx = {w: _pick(header, [w]) for w in WANT}
    mpn_i = _pick(header, MPN_COLS)

    def cell(r: list[str], i: int | None) -> str:
        return _clean(r[i]) if i is not None and i < len(r) else ""

    out: list[BomLine] = []
    for r in rows[1:]:
        try:
            qty = int(float(cell(r, idx["Qty"])))
        except ValueError:
            qty = 0
        out.append(BomLine(
            board=board, boards=boards, qty_per_board=qty,
            value=cell(r, idx["Value"]),
            mpn=cell(r, mpn_i) or cell(r, idx["Device"]),
            package=cell(r, idx["Package"]),
            description=cell(r, idx["Description"]),
        ))
    return out


# Board set per variant: (label, sub-path, file, reader, boards-per-system)
VARIANTS = {
    "extended": [
        ("Main", "Gerber_Main_Board/RADAR_Main_Board.csv", "csv", 1),
        ("FreqSynth", "Gerber_freq_synth/Clocks_Freq_Synth_board.csv", "csv", 1),
        ("Power", "Gerber_PowerBoard/PowerBoard.csv", "csv", 1),
        ("PA", "Gerber_PA/BOM_PA.xlsx", "xlsx", 16),
    ],
    "nexus": [
        ("Main", "Gerber_Main_Board/RADAR_Main_Board.csv", "csv", 1),
        ("FreqSynth", "Gerber_freq_synth/Clocks_Freq_Synth_board.csv", "csv", 1),
        ("Power", "Gerber_PowerBoard/PowerBoard.csv", "csv", 1),
        ("PatchAntenna", "Gerber_Patch_Antenna/BOM_Patch_Antenna.xlsx", "xlsx", 1),
    ],
}


def load_prices(path: str | None) -> dict[str, float]:
    if not path:
        return {}
    prices: dict[str, float] = {}
    with open(path, newline="") as f:
        for row in csv.reader(f):
            if len(row) >= 2 and row[0].strip():
                with contextlib.suppress(ValueError):
                    prices[row[0].strip().upper()] = float(row[1])
    return prices


def main() -> None:
    ap = argparse.ArgumentParser(description="Build a consolidated costed BOM")
    ap.add_argument("--variant", choices=sorted(VARIANTS), default="extended")
    ap.add_argument("--root", default=".", help="repo root (default: cwd)")
    ap.add_argument("-o", "--output", default="8_Utils/costed_bom.csv")
    ap.add_argument("--prices", help="optional CSV of MPN,unit_price overrides")
    args = ap.parse_args()

    prices = load_prices(args.prices)
    lines: list[BomLine] = []
    for label, rel, kind, boards in VARIANTS[args.variant]:
        path = os.path.join(args.root, PROD, rel)
        if not os.path.exists(path):
            print(f"  WARNING: missing {path} -- skipped")
            continue
        reader = read_csv_bom if kind == "csv" else read_xlsx_bom
        got = reader(path, label, boards)
        lines += got
        print(f"  {label:13s} x{boards:<2d}: {len(got):3d} lines from {os.path.basename(path)}")

    out_path = args.output
    if not os.path.isabs(out_path):
        out_path = os.path.join(args.root, out_path)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    total = 0.0
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "Board", "Qty_per_board", "Boards", "Total_Qty",
            "Value", "MPN", "Package", "Description",
            "Unit_Price_USD", "Line_Total_USD",
        ])
        for ln in lines:
            unit = prices.get(ln.mpn.strip().upper(), "")
            line_total = (unit * ln.total_qty) if isinstance(unit, float) else ""
            if isinstance(line_total, float):
                total += line_total
            w.writerow([
                ln.board, ln.qty_per_board, ln.boards, ln.total_qty,
                ln.value, ln.mpn, ln.package, ln.description,
                f"{unit:.4f}" if isinstance(unit, float) else "",
                f"{line_total:.2f}" if isinstance(line_total, float) else "",
            ])

    n_parts = sum(ln.total_qty for ln in lines)
    print(f"\nVariant: {args.variant}")
    print(f"BOM lines: {len(lines)}   Total placed parts: {n_parts}")
    if prices:
        print(f"Priced subtotal: ${total:,.2f} ({len(prices)} MPNs priced)")
    else:
        print("No --prices supplied: Unit_Price_USD left blank for you to fill in.")
    print(f"Written: {out_path}")


if __name__ == "__main__":
    main()
