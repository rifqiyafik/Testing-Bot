"""
Sync H-1 data to Google Sheets (DATABASE + HISTORY).
"""
from __future__ import annotations

import datetime as dt
from typing import Dict, List, Tuple

import pandas as pd
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

GLOBAL_COLUMNS = [
    "VENDOR",
    "DATE",
    "SITEID",
    "Transport Type",
    "NOP",
    "Count of >0.9",
    "Util FEGE %",
    "Max Ethernet Port Daily",
    "BW",
    "Priority",
    "Suspect",
    "TiketID",
    "Update12feb",
    "statusupdate12feb",
    "DateOpen",
    "Aging",
    "Status",
    "Updatetanggal",
    "closedby",
    "Note",
    "CapSiteSimpul",
    "CapIntermediateLink",
    "OtherPelurusanDataBW",
]


def _build_service(credentials_path: str):
    creds = Credentials.from_service_account_file(
        credentials_path,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _read_sheet(service, spreadsheet_id: str, sheet_name: str) -> List[List[str]]:
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"{sheet_name}!A1:Z")
        .execute()
    )
    return result.get("values", [])


def _clear_sheet(service, spreadsheet_id: str, sheet_name: str):
    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A:Z",
        body={},
    ).execute()


def _write_sheet(service, spreadsheet_id: str, sheet_name: str, rows: List[List[str]]):
    body = {"values": rows}
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A1",
        valueInputOption="RAW",
        body=body,
    ).execute()


def _append_rows(service, spreadsheet_id: str, sheet_name: str, rows: List[List[str]]):
    body = {"values": rows}
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body=body,
    ).execute()


def _parse_date_to_yyyymmdd(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (dt.date, dt.datetime)):
        return value.strftime("%Y%m%d")
    value_str = str(value).strip()
    if value_str == "":
        return ""
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return dt.datetime.strptime(value_str, fmt).strftime("%Y%m%d")
        except ValueError:
            continue
    digits = "".join(ch for ch in value_str if ch.isdigit())
    if len(digits) == 8:
        return digits
    return ""


def _aging_days(date_open: str, today: dt.date) -> int:
    if not date_open:
        return 0
    try:
        open_date = dt.datetime.strptime(date_open, "%Y%m%d").date()
    except ValueError:
        return 0
    delta = (today - open_date).days
    return max(delta, 0)


def _to_df(values: List[List[str]]) -> pd.DataFrame:
    if not values:
        return pd.DataFrame(columns=GLOBAL_COLUMNS)
    header = values[0]
    rows = values[1:] if len(values) > 1 else []

    normalized = [str(h).strip() for h in header]
    records: List[Dict[str, str]] = []
    for row in rows:
        row_cells = list(row)
        if len(row_cells) < len(normalized):
            row_cells += [""] * (len(normalized) - len(row_cells))
        if len(row_cells) > len(normalized):
            row_cells = row_cells[: len(normalized)]
        record = {normalized[i]: row_cells[i] for i in range(len(normalized))}
        records.append(record)

    try:
        df = pd.DataFrame.from_records(records)
    except Exception:
        df = pd.DataFrame(records)
    for col in GLOBAL_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    return df[GLOBAL_COLUMNS]


def build_daily_records(df_daily: pd.DataFrame, today: dt.date) -> List[Dict[str, str]]:
    records: List[Dict[str, str]] = []
    for _, row in df_daily.iterrows():
        date_raw = str(row.get("DATE", "")).strip()
        ticket_date = _parse_date_to_yyyymmdd(date_raw)
        ticket_id = row.get("TiketID") or f"{row.get('SITEID', '')}{ticket_date}"
        record = {
            "VENDOR": str(row.get("VENDOR", "")).strip(),
            "DATE": date_raw,
            "SITEID": str(row.get("SITEID", "")).strip(),
            "Transport Type": str(row.get("Transport Type", "")).strip(),
            "NOP": str(row.get("NOP", "")).strip(),
            "Count of >0.9": str(row.get("Count of >0.9", "")).strip(),
            "Util FEGE %": str(row.get("Util FEGE %", "")).strip(),
            "Max Ethernet Port Daily": str(row.get("Max Ethernet Port Daily", "")).strip(),
            "BW": str(row.get("BW", "")).strip(),
            "Priority": str(row.get("Priority", "")).strip(),
            "Suspect": str(row.get("Suspect", "")).strip(),
            "TiketID": str(ticket_id).strip(),
            "Update12feb": today.strftime("%Y%m%d"),
            "statusupdate12feb": "Open",
            "DateOpen": date_raw,
            "Aging": "",
            "Status": "Open",
            "Updatetanggal": today.strftime("%Y%m%d"),
            "closedby": "",
            "Note": "",
            "CapSiteSimpul": "",
            "CapIntermediateLink": "",
            "OtherPelurusanDataBW": "",
        }
        records.append(record)
    return records


def upsert_database(
    df_db: pd.DataFrame,
    daily_records: List[Dict[str, str]],
    today: dt.date,
) -> Tuple[pd.DataFrame, List[Dict[str, str]]]:
    df_out = df_db.copy()
    history_records: List[Dict[str, str]] = []

    if "TiketID" not in df_out.columns:
        df_out["TiketID"] = ""
    df_out["TiketID"] = df_out["TiketID"].astype(str)
    index = {tid: i for i, tid in enumerate(df_out["TiketID"].tolist()) if tid}

    for rec in daily_records:
        tid = rec.get("TiketID", "")
        if not tid:
            continue
        if tid in index:
            i = index[tid]
            existing = df_out.loc[i].to_dict()
            date_open = existing.get("DateOpen") or rec.get("DateOpen")
            date_open_norm = _parse_date_to_yyyymmdd(date_open)
            if not date_open_norm:
                date_open_norm = _parse_date_to_yyyymmdd(rec.get("DateOpen"))
            status = existing.get("Status") or "Open"
            status_update = "Open"
            if str(status).lower() in ("closed", "clear"):
                status_update = "ReOpen"
            rec["DateOpen"] = date_open
            rec["Aging"] = str(_aging_days(date_open_norm, today))
            rec["Status"] = "Open"
            rec["statusupdate12feb"] = status_update
            df_out.loc[i, GLOBAL_COLUMNS] = [rec.get(c, "") for c in GLOBAL_COLUMNS]
        else:
            rec["Aging"] = str(_aging_days(_parse_date_to_yyyymmdd(rec.get("DateOpen", "")), today))
            df_out = pd.concat([df_out, pd.DataFrame([rec])], ignore_index=True)
            index[tid] = len(df_out) - 1
        history_records.append(rec)

    return df_out[GLOBAL_COLUMNS], history_records


def sync_to_global(
    credentials_path: str,
    spreadsheet_id: str,
    tab_database: str,
    tab_history: str,
    df_daily: pd.DataFrame,
):
    service = _build_service(credentials_path)
    values = _read_sheet(service, spreadsheet_id, tab_database)
    df_db = _to_df(values)

    today = dt.datetime.now().date()
    daily_records = build_daily_records(df_daily, today)
    df_updated, history_records = upsert_database(df_db, daily_records, today)

    rows = [GLOBAL_COLUMNS] + df_updated.fillna("").astype(str).values.tolist()
    _clear_sheet(service, spreadsheet_id, tab_database)
    _write_sheet(service, spreadsheet_id, tab_database, rows)

    if history_records:
        history_rows = [
            [rec.get(c, "") for c in GLOBAL_COLUMNS] for rec in history_records
        ]
        _append_rows(service, spreadsheet_id, tab_history, history_rows)


def read_database_df(credentials_path: str, spreadsheet_id: str, tab_database: str) -> pd.DataFrame:
    """Read DATABASE sheet into a DataFrame."""
    service = _build_service(credentials_path)
    values = _read_sheet(service, spreadsheet_id, tab_database)
    return _to_df(values)
