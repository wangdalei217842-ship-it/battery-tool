"""Read Neware BTS .ndax cycling files used by BTS Client 8.1.

The reader follows the NDC layouts checked against this client's NdcReader
logs and against the step setpoints stored inside the same archives.
"""

from __future__ import annotations

import argparse
import csv
import json
import struct
import sys
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

# Raw current counts times this value are amperes.
# The same factor converts raw capacity counts to A·s and raw energy counts to W·s.
# Checked on this dataset: range 2 -> 0.102 A setpoints, range -1 -> 0.192 A and 0.900 A.
CURRENT_SCALE_A = {
    -100000000: 10.0,
    -200000: 1e-2,
    -100000: 1e-2,
    -60000: 1e-2,
    -50000: 1e-2,
    -30000: 1e-2,
    -20000: 1e-2,
    -12000: 1e-2,
    -10000: 1e-2,
    -6000: 1e-2,
    -5000: 1e-2,
    -3000: 1e-2,
    -2000: 1e-2,
    -1000: 1e-2,
    -500: 1e-3,
    -100: 1e-3,
    -50: 1e-4,
    -25: 1e-4,
    -20: 1e-4,
    -10: 1e-4,
    -5: 1e-5,
    -2: 1e-5,
    -1: 1e-5,
    0: 0.0,
    1: 1e-4,
    2: 1e-4,
    5: 1e-4,
    10: 1e-3,
    20: 1e-3,
    50: 1e-3,
    100: 1e-2,
    200: 1e-2,
    250: 1e-2,
    500: 1e-2,
    1000: 1e-1,
    6000: 1e-1,
    10000: 1e-1,
    12000: 1e-1,
    20000: 1e-1,
    30000: 1e-1,
    40000: 1e-1,
    50000: 1e-1,
    60000: 1e-1,
    100000: 1e-1,
    200000: 1e-1,
}

STATUS_NAME = {
    1: "恒流充电",
    2: "恒流放电",
    3: "恒压充电",
    4: "搁置",
    5: "循环",
    6: "结束",
    7: "恒流恒压充电",
    8: "恒功率放电",
    9: "恒功率充电",
    10: "恒阻放电",
    13: "暂停",
    16: "脉冲",
    17: "模拟工步",
    19: "恒压放电",
    20: "恒流恒压放电",
    21: "控制",
    22: "开路电压",
    26: "恒功率恒压放电",
    27: "恒功率恒压充电",
}

COLUMNS = [
    "index",
    "cycle",
    "step",
    "status",
    "status_name",
    "step_time_s",
    "test_time_s",
    "voltage_V",
    "current_A",
    "current_mA",
    "charge_capacity_mAh",
    "discharge_capacity_mAh",
    "charge_energy_mWh",
    "discharge_energy_mWh",
    "timestamp",
]


def read_ndax(path: str | Path) -> tuple[dict, list[dict]]:
    path = Path(path)
    with zipfile.ZipFile(path) as archive:
        if "data.ndc" not in archive.namelist():
            raise ValueError(f"{path.name} 里没有 data.ndc，不是新威 ndax 包")
        data = archive.read("data.ndc")
        version = data[2]
        meta = _metadata(archive, path, version)
        if version == 2 and data[0] == 1:
            rows = _read_v2(data)
        elif version in (11, 17) and data[0] == 1:
            rows = _read_paged(
                data,
                version,
                archive.read("data_runInfo.ndc"),
                archive.read("data_step.ndc") if "data_step.ndc" in archive.namelist() else b"",
            )
        else:
            raise ValueError(
                f"{path.name} 的 data.ndc 类型 {data[0]} 版本 {version} 还不能读取"
            )
    meta["rows"] = len(rows)
    if rows:
        meta["start"] = rows[0]["timestamp"]
        meta["end"] = next((row["timestamp"] for row in reversed(rows) if row["timestamp"]), "")
        voltages = [row["voltage_V"] for row in rows]
        currents = [row["current_A"] for row in rows]
        meta["voltage_min_V"] = min(voltages)
        meta["voltage_max_V"] = max(voltages)
        meta["current_min_A"] = min(currents)
        meta["current_max_A"] = max(currents)
    return meta, rows


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key, "")) for key in COLUMNS})


def summarize_cycles(rows: list[dict]) -> list[dict]:
    """Sum the final capacity of each charge or discharge step inside a cycle."""
    totals: dict[int, dict] = defaultdict(
        lambda: {
            "charge_mAh": 0.0,
            "discharge_mAh": 0.0,
            "charge_mWh": 0.0,
            "discharge_mWh": 0.0,
            "charge_end_voltage_V": "",
            "discharge_end_voltage_V": "",
        }
    )
    for row in _finished_steps(rows):
        _commit_step(totals, row)
    return [{"cycle": cycle, **totals[cycle]} for cycle in sorted(totals)]


def summarize_steps(rows: list[dict]) -> list[dict]:
    """One row per finished step. Capacity is the last point of that step, not a sum across steps."""
    finished = []
    for row in _finished_steps(rows):
        status = row["status"]
        if status in (1, 7, 9, 27):
            capacity = row["charge_capacity_mAh"]
            energy = row["charge_energy_mWh"]
        elif status in (2, 8, 10, 19, 20, 26):
            capacity = row["discharge_capacity_mAh"]
            energy = row["discharge_energy_mWh"]
        else:
            capacity = 0.0
            energy = 0.0
        finished.append(
            {
                "cycle": row["cycle"],
                "step": row["step"],
                "status": status,
                "status_name": row["status_name"],
                "step_time_s": row["step_time_s"],
                "capacity_mAh": capacity,
                "energy_mWh": energy,
                "end_voltage_V": row["voltage_V"],
                "end_timestamp": row["timestamp"],
            }
        )
    return finished


def _finished_steps(rows: list[dict]):
    current_cycle = None
    current_step = None
    last = None
    for row in rows:
        cycle = row["cycle"]
        step = row["step"]
        if cycle == "" or step == "":
            continue
        if current_step is not None and (cycle != current_cycle or step != current_step):
            yield last
        current_cycle = cycle
        current_step = step
        last = row
    if last is not None and last["cycle"] != "" and last["step"] != "":
        yield last


def _write_step_summary(steps: list[dict], path: Path) -> None:
    fields = [
        "cycle",
        "step",
        "status",
        "status_name",
        "step_time_s",
        "capacity_mAh",
        "energy_mWh",
        "end_voltage_V",
        "end_timestamp",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in steps:
            out = dict(item)
            for key in ("step_time_s", "capacity_mAh", "energy_mWh", "end_voltage_V"):
                if isinstance(out[key], float):
                    out[key] = f"{out[key]:.6f}"
            writer.writerow(out)


def write_summary(summary: list[dict], path: Path, nominal_mAh: float | None) -> None:
    fields = [
        "cycle",
        "charge_mAh",
        "discharge_mAh",
        "charge_mWh",
        "discharge_mWh",
        "charge_end_voltage_V",
        "discharge_end_voltage_V",
    ]
    if nominal_mAh:
        fields.append("discharge_retention_pct")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in summary:
            out = dict(item)
            for key in ("charge_mAh", "discharge_mAh", "charge_mWh", "discharge_mWh"):
                out[key] = f"{item[key]:.6f}"
            if nominal_mAh:
                out["discharge_retention_pct"] = f"{100 * item['discharge_mAh'] / nominal_mAh:.4f}"
            if item["charge_end_voltage_V"] != "":
                out["charge_end_voltage_V"] = f"{item['charge_end_voltage_V']:.6f}"
            if item["discharge_end_voltage_V"] != "":
                out["discharge_end_voltage_V"] = f"{item['discharge_end_voltage_V']:.6f}"
            writer.writerow(out)


def _commit_step(totals: dict[int, dict], row: dict) -> None:
    cycle = row["cycle"]
    bucket = totals[cycle]
    status = row["status"]
    if status in (1, 7, 9, 27):
        bucket["charge_mAh"] += float(row["charge_capacity_mAh"] or 0)
        bucket["charge_mWh"] += float(row["charge_energy_mWh"] or 0)
        bucket["charge_end_voltage_V"] = row["voltage_V"]
    elif status in (2, 8, 10, 19, 20, 26):
        bucket["discharge_mAh"] += float(row["discharge_capacity_mAh"] or 0)
        bucket["discharge_mWh"] += float(row["discharge_energy_mWh"] or 0)
        bucket["discharge_end_voltage_V"] = row["voltage_V"]


def _read_v2(data: bytes) -> list[dict]:
    rows = []
    start = None
    position = 512
    while position + 512 <= len(data):
        page = data[position : position + 512]
        kind, count = struct.unpack_from("<HH", page, 0)
        position += 512
        if kind != 2 or count == 0:
            continue
        count = min(count, 5)
        for index in range(count):
            record = page[5 + index * 94 : 5 + (index + 1) * 94]
            if len(record) < 94 or record[0] != 0x55:
                continue
            row, stamp = _parse_v2_record(record)
            if start is None and stamp is not None:
                start = stamp
            if stamp is not None and start is not None:
                row["test_time_s"] = (stamp - start).total_seconds()
            rows.append(row)
    if not rows:
        raise ValueError("版本 2 的 data.ndc 里没有读到 0x55 记录")
    return rows


def _parse_v2_record(record: bytes) -> tuple[dict, datetime | None]:
    index, cycle, step, status = struct.unpack_from("<IIBB", record, 8)
    step_ms, voltage, current = struct.unpack_from("<Qii", record, 23)
    charge_c, discharge_c, charge_e, discharge_e = struct.unpack_from("<qqqq", record, 43)
    year, month, day, hour, minute, second = struct.unpack_from("<HBBBBB", record, 75)
    current_range = struct.unpack_from("<i", record, 82)[0]
    try:
        scale = CURRENT_SCALE_A[current_range]
    except KeyError as exc:
        raise ValueError(f"未知电流量程代码 {current_range}，已停止以免把电流换算错") from exc
    stamp = _wall_time(year, month, day, hour, minute, second)
    row = _blank_row()
    row.update(
        {
            "index": index,
            "cycle": cycle + 1,
            "step": step,
            "status": status,
            "status_name": STATUS_NAME.get(status, str(status)),
            "step_time_s": step_ms / 1000,
            "voltage_V": voltage / 10000,
            "current_A": current * scale,
            "current_mA": current * scale * 1000,
            "charge_capacity_mAh": charge_c * scale * 1000 / 3600,
            "discharge_capacity_mAh": discharge_c * scale * 1000 / 3600,
            "charge_energy_mWh": charge_e * scale * 1000 / 3600,
            "discharge_energy_mWh": discharge_e * scale * 1000 / 3600,
            "timestamp": stamp.strftime("%Y-%m-%d %H:%M:%S") if stamp else "",
        }
    )
    return row, stamp


def _read_paged(data: bytes, version: int, run_info: bytes, step_blob: bytes) -> list[dict]:
    samples = list(_iter_float_pairs(data))
    steps = _step_rows(step_blob, 100 if version >= 16 else 37)
    known = []
    for record in _iter_fixed_records(run_info, 100 if version >= 16 else 47):
        if record["index"] <= 0:
            continue
        _attach_step(record, steps)
        if version == 17:
            _scale_v17_runinfo(record)
        else:
            _scale_v11_runinfo(record)
        known.append(record)
    known.sort(key=lambda item: item["index"])
    rows = []
    for index, voltage, current in samples:
        row = _blank_row()
        row["index"] = index
        row["voltage_V"] = voltage / 10000 if version == 11 else voltage
        amperes = current if version == 11 else current * 1000
        row["current_A"] = amperes
        row["current_mA"] = amperes * 1000
        rows.append(row)
    _merge_runinfo(rows, known)
    stamped = [row for row in rows if row["timestamp"]]
    if stamped:
        start = datetime.strptime(stamped[0]["timestamp"], "%Y-%m-%d %H:%M:%S.%f")
        for row in stamped:
            stamp = datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S.%f")
            row["test_time_s"] = (stamp - start).total_seconds()
            row["timestamp"] = stamp.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    return rows


def _iter_float_pairs(data: bytes):
    index = 0
    position = 4096
    while position + 4096 <= len(data):
        page = data[position : position + 4096]
        position += 4096
        kind, count = struct.unpack_from("<HH", page, 0)
        if kind != 2 or count == 0:
            continue
        for offset in range(count):
            voltage, current = struct.unpack_from("<ff", page, 132 + offset * 8)
            index += 1
            yield index, voltage, current


def _iter_fixed_records(data: bytes, record_length: int):
    position = 4096
    while position + 4096 <= len(data):
        page = data[position : position + 4096]
        position += 4096
        kind, count = struct.unpack_from("<HH", page, 0)
        if count == 0:
            continue
        for offset in range(count):
            raw = page[132 + offset * record_length : 132 + (offset + 1) * record_length]
            if len(raw) < 47:
                continue
            yield _parse_runinfo(raw)


def _parse_runinfo(raw: bytes) -> dict:
    step_ms = struct.unpack_from("<i", raw, 0)[0]
    charge_c, discharge_c, charge_e, discharge_e = struct.unpack_from("<ffff", raw, 5)
    interval_ms = struct.unpack_from("<i", raw, 29)[0]
    seconds = struct.unpack_from("<i", raw, 33)[0]
    step_id = struct.unpack_from("<i", raw, 37)[0]
    index = struct.unpack_from("<i", raw, 41)[0]
    msec = struct.unpack_from("<h", raw, 45)[0]
    stamp = None
    if 1_500_000_000 < seconds < 2_200_000_000:
        stamp = datetime.fromtimestamp(seconds) + timedelta(milliseconds=msec)
    return {
        "index": index,
        "step_id": step_id,
        "step_ms": step_ms,
        "interval_ms": interval_ms,
        "charge_raw": charge_c,
        "discharge_raw": discharge_c,
        "charge_energy_raw": charge_e,
        "discharge_energy_raw": discharge_e,
        "stamp": stamp,
    }


def _step_rows(data: bytes, record_length: int) -> list[dict]:
    """Read every step slot. The page count field can be shorter than the packed slots."""
    if not data:
        return []
    rows = []
    position = 4096
    while position + 4096 <= len(data):
        page = data[position : position + 4096]
        position += 4096
        payload = page[132:-5]
        for offset in range(0, len(payload) - record_length + 1, record_length):
            raw = payload[offset : offset + record_length]
            cycle, program = struct.unpack_from("<ii", raw, 0)
            status = raw[24] if len(raw) > 24 else 0
            rows.append({"cycle": cycle, "step": program, "status": status})
    return rows


def _attach_step(record: dict, steps: list[dict]) -> None:
    step_id = record["step_id"]
    if step_id <= 0 or step_id > len(steps):
        record["cycle"] = ""
        record["program_step"] = ""
        record["status"] = ""
        return
    chosen = steps[step_id - 1]
    record["cycle"] = chosen["cycle"] + 1 if chosen["step"] else ""
    record["program_step"] = chosen["step"] if chosen["step"] else ""
    record["status"] = chosen["status"] if chosen["step"] else ""


def _scale_v11_runinfo(record: dict) -> None:
    # Version 11 accumulators are A·s and W·s. Current in data.ndc is already amperes.
    record["charge_capacity_mAh"] = record["charge_raw"] * 1000 / 3600
    record["discharge_capacity_mAh"] = record["discharge_raw"] * 1000 / 3600
    record["charge_energy_mWh"] = record["charge_energy_raw"] * 1000 / 3600
    record["discharge_energy_mWh"] = record["discharge_energy_raw"] * 1000 / 3600


def _scale_v17_runinfo(record: dict) -> None:
    # Version 17 stores current, capacity and energy at 1/1000 of A, Ah and Wh.
    # Voltage stays in volts. Multiplying by 1000 matches the step setpoint and V*I*t.
    record["charge_capacity_mAh"] = record["charge_raw"] * 1_000_000
    record["discharge_capacity_mAh"] = record["discharge_raw"] * 1_000_000
    record["charge_energy_mWh"] = record["charge_energy_raw"] * 1_000_000
    record["discharge_energy_mWh"] = record["discharge_energy_raw"] * 1_000_000


def _merge_runinfo(rows: list[dict], known: list[dict]) -> None:
    by_index = {item["index"]: item for item in known}
    for row in rows:
        item = by_index.get(row["index"])
        if item is not None:
            _apply_known(row, item)
    _interpolate(rows, known)


def _apply_known(row: dict, item: dict) -> None:
    row["cycle"] = item["cycle"]
    row["step"] = item["program_step"]
    row["status"] = item["status"]
    row["status_name"] = STATUS_NAME.get(item["status"], "")
    row["step_time_s"] = item["step_ms"] / 1000
    row["charge_capacity_mAh"] = item["charge_capacity_mAh"]
    row["discharge_capacity_mAh"] = item["discharge_capacity_mAh"]
    row["charge_energy_mWh"] = item["charge_energy_mWh"]
    row["discharge_energy_mWh"] = item["discharge_energy_mWh"]
    if item["stamp"] is not None:
        row["timestamp"] = item["stamp"].strftime("%Y-%m-%d %H:%M:%S.%f")


def _interpolate(rows: list[dict], known: list[dict]) -> None:
    indexed = {row["index"]: row for row in rows}
    ordered = [item for item in known if item["index"] in indexed]
    for left, right in zip(ordered, ordered[1:]):
        gap = right["index"] - left["index"]
        if gap <= 1:
            continue
        same_step = left["step_id"] == right["step_id"] and left["step_id"] > 0
        for index in range(left["index"] + 1, right["index"]):
            row = indexed.get(index)
            if row is None or row["step"] != "":
                continue
            fraction = (index - left["index"]) / gap
            if same_step:
                _blend(row, left, right, fraction)
            else:
                _extend(row, left, index - left["index"])
    if ordered:
        last = ordered[-1]
        for row in rows:
            if row["index"] > last["index"] and row["step"] == "":
                _extend(row, last, row["index"] - last["index"])


def _blend(row: dict, left: dict, right: dict, fraction: float) -> None:
    def mix(key: str) -> float:
        return left[key] + (right[key] - left[key]) * fraction

    row["cycle"] = left["cycle"]
    row["step"] = left["program_step"]
    row["status"] = left["status"]
    row["status_name"] = STATUS_NAME.get(left["status"], "")
    row["step_time_s"] = mix("step_ms") / 1000
    row["charge_capacity_mAh"] = mix("charge_capacity_mAh")
    row["discharge_capacity_mAh"] = mix("discharge_capacity_mAh")
    row["charge_energy_mWh"] = mix("charge_energy_mWh")
    row["discharge_energy_mWh"] = mix("discharge_energy_mWh")
    if left["stamp"] is not None and right["stamp"] is not None:
        delta = (right["stamp"] - left["stamp"]) * fraction
        row["timestamp"] = (left["stamp"] + delta).strftime("%Y-%m-%d %H:%M:%S.%f")


def _extend(row: dict, anchor: dict, steps: int) -> None:
    interval = anchor["interval_ms"] if anchor["interval_ms"] > 0 else 0
    elapsed_s = steps * interval / 1000
    row["cycle"] = anchor["cycle"]
    row["step"] = anchor["program_step"]
    row["status"] = anchor["status"]
    row["status_name"] = STATUS_NAME.get(anchor["status"], "")
    row["step_time_s"] = anchor["step_ms"] / 1000 + elapsed_s
    coulombs = abs(row["current_A"]) * elapsed_s
    joules = abs(row["voltage_V"] * row["current_A"]) * elapsed_s
    if row["current_A"] >= 0:
        row["charge_capacity_mAh"] = anchor["charge_capacity_mAh"] + coulombs * 1000 / 3600
        row["charge_energy_mWh"] = anchor["charge_energy_mWh"] + joules * 1000 / 3600
        row["discharge_capacity_mAh"] = anchor["discharge_capacity_mAh"]
        row["discharge_energy_mWh"] = anchor["discharge_energy_mWh"]
    else:
        row["discharge_capacity_mAh"] = anchor["discharge_capacity_mAh"] + coulombs * 1000 / 3600
        row["discharge_energy_mWh"] = anchor["discharge_energy_mWh"] + joules * 1000 / 3600
        row["charge_capacity_mAh"] = anchor["charge_capacity_mAh"]
        row["charge_energy_mWh"] = anchor["charge_energy_mWh"]
    if anchor["stamp"] is not None and interval:
        row["timestamp"] = (anchor["stamp"] + timedelta(milliseconds=steps * interval)).strftime(
            "%Y-%m-%d %H:%M:%S.%f"
        )


def _metadata(archive: zipfile.ZipFile, path: Path, version: int) -> dict:
    meta = {"file": str(path), "ndc_version": version, "nominal_mAh": None, "setpoints_A": []}
    test = _xml(archive, "TestInfo.xml")
    if test is not None:
        node = test.find(".//TestInfo")
        if node is not None:
            meta.update(
                {
                    "device": node.attrib.get("DevID", ""),
                    "unit": node.attrib.get("UnitID", ""),
                    "channel": node.attrib.get("ChlID", ""),
                    "start_recorded": node.attrib.get("StartTime", ""),
                    "step_file": node.attrib.get("StepName", ""),
                    "server": node.attrib.get("SvrIP", ""),
                }
            )
    version_info = _xml(archive, "VersionInfo.xml")
    if version_info is not None:
        node = version_info.find(".//ZwjVersion")
        if node is not None:
            meta["server_version"] = node.attrib.get("SvrVer", "")
            meta["client_version"] = node.attrib.get("CurrClientVer", "")
    step = _xml(archive, "Step.xml")
    if step is not None:
        capacity = step.find(".//Head_Info/SCQ")
        if capacity is not None and capacity.attrib.get("Value"):
            meta["nominal_mAh"] = float(capacity.attrib["Value"])
        for node in step.findall(".//Limit/Main/Curr"):
            try:
                meta["setpoints_A"].append(float(node.attrib["Value"]))
            except (KeyError, ValueError):
                continue
        meta["setpoints_A"] = sorted(set(meta["setpoints_A"]))
    return meta


def _xml(archive: zipfile.ZipFile, name: str):
    if name not in archive.namelist():
        return None
    text = archive.read(name).decode("gb18030", errors="replace")
    return ET.fromstring(text)


def _wall_time(year: int, month: int, day: int, hour: int, minute: int, second: int):
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def _blank_row() -> dict:
    return {key: "" for key in COLUMNS}


def _csv_value(value):
    if isinstance(value, float):
        return f"{value:.8g}"
    return value


def convert_file(path: Path, output: Path | None) -> dict:
    meta, rows = read_ndax(path)
    if output is None:
        output = Path(r"D:\vibe\BTSDA逆向\out") / f"{path.stem}.csv"
    elif output.is_dir() or output.suffix == "":
        output.mkdir(parents=True, exist_ok=True)
        output = output / f"{path.stem}.csv"
    write_csv(rows, output)
    summary = summarize_cycles(rows)
    summary_path = output.with_name(output.stem + "_循环汇总.csv")
    write_summary(summary, summary_path, meta.get("nominal_mAh"))
    step_path = output.with_name(output.stem + "_工步汇总.csv")
    _write_step_summary(summarize_steps(rows), step_path)
    meta["csv"] = str(output)
    meta["summary"] = str(summary_path)
    meta["steps"] = str(step_path)
    meta["cycles"] = len(summary)
    output.with_suffix(".meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return meta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="读取新威 BTS 的 ndax 循环数据并导出 CSV")
    parser.add_argument("path", type=Path, help="一个 .ndax 文件，或包含这类文件的目录")
    parser.add_argument("-o", "--output", type=Path, help="输出 CSV 文件或目录")
    args = parser.parse_args(argv)
    targets = sorted(args.path.rglob("*.ndax")) if args.path.is_dir() else [args.path]
    if not targets:
        print(f"没有找到 ndax：{args.path}", file=sys.stderr)
        return 1
    for target in targets:
        meta = convert_file(target, args.output if len(targets) == 1 else (args.output or Path(r"D:\vibe\BTSDA逆向\out")))
        print(
            f"{target.name}  版本 {meta['ndc_version']}  {meta['rows']} 行  "
            f"电压 {meta.get('voltage_min_V', 0):.4f}–{meta.get('voltage_max_V', 0):.4f} V  "
            f"电流 {meta.get('current_min_A', 0):.4f}–{meta.get('current_max_A', 0):.4f} A  "
            f"循环 {meta['cycles']}"
        )
        print(f"  CSV {meta['csv']}")
        print(f"  工步 {meta['steps']}")
        print(f"  循环 {meta['summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
