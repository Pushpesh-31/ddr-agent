"""Parse Volve DDR XMLs into the per-well JSON cache contract from CLAUDE.md.

Run once at build time:
    python scripts/parse_ddrs.py --input data/raw/ --output data/parsed/

Filename convention from the attilanagy1986/volve-app mirror:
    <well_parts>_<YYYY>_<MM>_<DD>.xml
Examples:
    15_9_F_14_2007_12_04.xml   -> well "15/9-F-14"
    15_9_19_BT2_1997_12_15.xml -> well "15/9-19 BT2"
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from lxml import etree

NS = {"w": "http://www.witsml.org/schemas/1series"}
MISSING = -999.99
DATE_RE = re.compile(r"_(\d{4})_(\d{2})_(\d{2})\.xml$")


def well_id_from_filename(name: str) -> str:
    """`15_9_F_14_2007_12_04.xml` -> `15/9-F-14`. `15_9_19_BT2_2007_12_04.xml` -> `15/9-19 BT2`."""
    stem = DATE_RE.sub("", name)
    parts = stem.split("_")
    if len(parts) < 3:
        return stem.replace("_", "/")
    block = f"{parts[0]}/{parts[1]}"  # "15/9"
    rest = parts[2:]
    # Wells with letter codes are joined with "-" or " " depending on prefix.
    # Convention used here: if the third part is a single alphabetic letter, treat the
    # whole tail as F-style ("F-14", "F-1-A"); otherwise it's a numeric well possibly
    # with a sidetrack suffix ("19 BT2").
    if rest[0].isalpha() and len(rest[0]) == 1:
        return f"{block}-" + "-".join(rest)
    if rest[0].isdigit():
        suffix = " ".join(rest[1:]) if len(rest) > 1 else ""
        return f"{block}-{rest[0]}" + (f" {suffix}" if suffix else "")
    return f"{block}-" + "-".join(rest)


def text(el, path: str) -> str | None:
    found = el.find(path, NS)
    if found is None or found.text is None:
        return None
    s = found.text.strip()
    return s if s else None


def num(el, path: str) -> float | None:
    s = text(el, path)
    if s is None:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    if v == MISSING or v == int(MISSING):
        return None
    return v


def parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    # Strip TZ; we only need the date.
    if len(s) >= 10:
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            try:
                return datetime.fromisoformat(s[:19])
            except ValueError:
                return None
    return None


def hours_between(start: str | None, end: str | None) -> float | None:
    a, b = parse_dt(start), parse_dt(end)
    if a is None or b is None:
        return None
    return round((b - a).total_seconds() / 3600.0, 2)


def well_id_from_alias(report) -> str | None:
    """Prefer wellboreAlias (NPD code) — it distinguishes sidetracks (BT2, ST2, T2 etc.)
    from their parent wellbore. wellAlias collapses sidetracks into the parent well,
    which would merge their DDRs incorrectly."""
    for tag in ("w:wellboreAlias", "w:wellAlias"):
        for alias in report.findall(tag, NS):
            sys_el = alias.find("w:namingSystem", NS)
            name_el = alias.find("w:name", NS)
            if sys_el is not None and name_el is not None and name_el.text:
                if sys_el.text and "NPD code" in sys_el.text:
                    return name_el.text.strip()
    return None


@dataclass
class DailyReport:
    day: int  # filled in post-process
    date: str
    report_no: int | None
    depth_md_end_m: float | None
    depth_tvd_end_m: float | None
    activities: list[dict]
    mud: dict
    remarks_combined: str | None
    days_behind: float | None
    days_ahead: float | None


def parse_one(xml_path: Path) -> tuple[str, DailyReport, dict] | None:
    try:
        tree = etree.parse(str(xml_path))
    except etree.XMLSyntaxError as e:
        print(f"  skip {xml_path.name}: {e}")
        return None
    root = tree.getroot()
    report = root.find("w:drillReport", NS)
    if report is None:
        return None

    well_id = well_id_from_alias(report) or well_id_from_filename(xml_path.name)

    status = report.find("w:statusInfo", NS)
    wb_info = report.find("w:wellboreInfo", NS)

    dtim = text(status, "w:dTim") if status is not None else None
    dt = parse_dt(dtim)
    date_str = dt.date().isoformat() if dt else DATE_RE.search(xml_path.name).group(0)[1:11].replace("_", "-")

    activities: list[dict] = []
    for a in report.findall("w:activity", NS):
        activities.append({
            "code": text(a, "w:proprietaryCode"),
            "phase": text(a, "w:phase"),
            "state": text(a, "w:state"),
            "state_detail": text(a, "w:stateDetailActivity"),
            "hours": hours_between(text(a, "w:dTimStart"), text(a, "w:dTimEnd")),
            "depth_md_m": num(a, "w:md"),
            "remarks": text(a, "w:comments"),
        })

    fluids = report.findall("w:fluid", NS)
    mud: dict = {"density_sg": None, "viscosity_cp": None, "type": None, "pv_mPas": None, "yp_Pa": None}
    if fluids:
        # Take the last sample by dTim (latest in the day).
        def fluid_dt(f):
            d = parse_dt(text(f, "w:dTim"))
            return d or datetime.min
        last = max(fluids, key=fluid_dt)
        mud["density_sg"] = num(last, "w:density")
        mud["viscosity_cp"] = num(last, "w:visFunnel")
        mud["pv_mPas"] = num(last, "w:pv")
        mud["yp_Pa"] = num(last, "w:yp")
        mud["type"] = text(last, "w:type")

    sum24 = text(status, "w:sum24Hr") if status is not None else None

    daily = DailyReport(
        day=0,  # set later
        date=date_str,
        report_no=int(num(status, "w:reportNo") or 0) if status is not None else None,
        depth_md_end_m=num(status, "w:md") if status is not None else None,
        depth_tvd_end_m=num(status, "w:tvd") if status is not None else None,
        activities=activities,
        mud=mud,
        remarks_combined=sum24,
        days_behind=num(wb_info, "w:daysBehind") if wb_info is not None else None,
        days_ahead=num(wb_info, "w:daysAhead") if wb_info is not None else None,
    )

    # Well-level metadata candidates (we'll merge across all of a well's reports).
    meta = {}
    if wb_info is not None:
        meta["spud_date"] = parse_dt(text(wb_info, "w:dTimSpud")).date().isoformat() if text(wb_info, "w:dTimSpud") else None
        meta["drill_complete_date"] = text(wb_info, "w:dateDrillComplete")
        meta["operator"] = text(wb_info, "w:operator")
        meta["drill_contractor"] = text(wb_info, "w:drillContractor")
        rig_alias = wb_info.find("w:rigAlias/w:name", NS)
        meta["rig_name"] = rig_alias.text.strip() if rig_alias is not None and rig_alias.text else None
    return well_id, daily, meta


def aggregate_well(well_id: str, dailies: list[DailyReport], metas: list[dict]) -> dict:
    dailies.sort(key=lambda d: d.date)
    # Day numbering relative to the well's first report (the dataset is gappy; this is more honest
    # than using calendar days from spud).
    first_date = datetime.fromisoformat(dailies[0].date).date()
    last_date = datetime.fromisoformat(dailies[-1].date).date()
    for d in dailies:
        d.day = (datetime.fromisoformat(d.date).date() - first_date).days + 1

    calendar_span_days = (last_date - first_date).days + 1
    # Operational days = number of DDRs filed. More meaningful than calendar span for wells
    # with multiple drilling campaigns separated by long gaps.
    total_days = len(dailies)

    # Planned days: pick the most reliable late-well report and back out the delta.
    # (delta = days_behind - days_ahead at that point; planned = total - delta).
    last_known_delta = None
    for d in reversed(dailies):
        if d.days_behind is not None or d.days_ahead is not None:
            last_known_delta = (d.days_behind or 0) - (d.days_ahead or 0)
            break
    planned_days = round(total_days - last_known_delta, 1) if last_known_delta is not None else None

    # Merge metas — first non-None wins.
    merged = {}
    for key in ("spud_date", "drill_complete_date", "operator", "drill_contractor", "rig_name"):
        for m in metas:
            if m.get(key):
                merged[key] = m[key]
                break
        merged.setdefault(key, None)

    td_md = max((d.depth_md_end_m or 0) for d in dailies)
    td_tvd = max((d.depth_tvd_end_m or 0) for d in dailies)

    return {
        "well_id": well_id,
        "metadata": {
            **merged,
            "n_reports": len(dailies),
            "first_report_date": first_date.isoformat(),
            "last_report_date": last_date.isoformat(),
            "calendar_span_days": calendar_span_days,
            "total_days": total_days,
            "planned_days": planned_days,
            "td_md_m": td_md or None,
            "td_tvd_m": td_tvd or None,
        },
        "daily_reports": [
            {
                "day": d.day,
                "date": d.date,
                "report_no": d.report_no,
                "depth_md_end_m": d.depth_md_end_m,
                "depth_tvd_end_m": d.depth_tvd_end_m,
                "activities": d.activities,
                "mud": d.mud,
                "remarks_combined": d.remarks_combined,
                "days_behind": d.days_behind,
                "days_ahead": d.days_ahead,
            }
            for d in dailies
        ],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="data/raw", help="Folder of WITSML XML files")
    ap.add_argument("--output", default="data/parsed", help="Folder to write per-well JSON files")
    args = ap.parse_args()

    in_dir = Path(args.input)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    by_well: dict[str, list[DailyReport]] = defaultdict(list)
    metas: dict[str, list[dict]] = defaultdict(list)

    xml_files = sorted(in_dir.glob("*.xml"))
    print(f"Parsing {len(xml_files)} files from {in_dir}/")
    skipped = 0
    for f in xml_files:
        result = parse_one(f)
        if result is None:
            skipped += 1
            continue
        wid, daily, meta = result
        by_well[wid].append(daily)
        metas[wid].append(meta)

    print(f"Parsed {len(xml_files) - skipped} files into {len(by_well)} wells (skipped {skipped})")

    # Sanitize well_id for filename (replace / with _).
    for wid, dailies in sorted(by_well.items()):
        record = aggregate_well(wid, dailies, metas[wid])
        safe = wid.replace("/", "_").replace(" ", "_")
        out = out_dir / f"{safe}.json"
        out.write_text(json.dumps(record, indent=2, ensure_ascii=False))
        print(f"  {wid:25s} -> {out.name}  ({record['metadata']['n_reports']} reports, "
              f"TD {record['metadata']['td_md_m']} m MD, "
              f"actual {record['metadata']['total_days']} d / planned {record['metadata']['planned_days']} d)")


if __name__ == "__main__":
    main()
