"""
Sync H-1 data to Google Sheets (DATABASE + HISTORY).
"""
from __future__ import annotations

import datetime as dt
from typing import Dict, List, Tuple

import pandas as pd
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

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
    "StatusUpdate",
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


def _ensure_sheet(service, spreadsheet_id: str, sheet_name: str):
    try:
        meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    except HttpError:
        return
    sheets = meta.get("sheets", [])
    titles = {s["properties"]["title"] for s in sheets}
    if sheet_name in titles:
        return
    body = {"requests": [{"addSheet": {"properties": {"title": sheet_name}}}]}
    service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=body).execute()


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




def _status_update_for_priority(priority: str, aging: int) -> str:
    prio = str(priority).strip().upper()
    if prio == "P1":
        return "NeedClose"
    if prio == "P2":
        return "NeedClose" if aging > 2 else "Open"
    return "Open"

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

    if "statusupdate12feb" in df.columns and "StatusUpdate" not in df.columns:
        df["StatusUpdate"] = df["statusupdate12feb"]
    for col in GLOBAL_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    return df[GLOBAL_COLUMNS]


def build_daily_records(df_daily: pd.DataFrame, today: dt.date) -> List[Dict[str, str]]:
    records: List[Dict[str, str]] = []
    for _, row in df_daily.iterrows():
        priority = str(row.get("Priority", "")).strip().upper()
        if priority not in ("P1", "P2"):
            continue
        date_raw = str(row.get("DATE", "")).strip()
        ticket_date = _parse_date_to_yyyymmdd(date_raw)
        ticket_id = row.get("TiketID") or f"{row.get('SITEID', '')}{ticket_date}"
        status_update = "NeedClose" if priority == "P1" else "Open"
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
            "Priority": priority,
            "Suspect": str(row.get("Suspect", "")).strip(),
            "TiketID": str(ticket_id).strip(),
            "Update12feb": today.strftime("%Y%m%d"),
            "StatusUpdate": status_update,
            "DateOpen": ticket_date,
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

    # Map SITEID to last closed info for ReOpen detection
    closed_map: Dict[str, Dict[str, str]] = {}
    if "SITEID" in df_out.columns:
        for idx, row in df_out.iterrows():
            status = str(row.get("Status", "")).lower()
            if status not in ("closed", "clear"):
                continue
            site_id = str(row.get("SITEID", "")).strip()
            if not site_id:
                continue
            close_date = _parse_date_to_yyyymmdd(row.get("Updatetanggal", ""))
            if not close_date:
                continue
            try:
                close_dt = dt.datetime.strptime(close_date, "%Y%m%d").date()
            except ValueError:
                continue
            prev = closed_map.get(site_id)
            if prev is None or close_dt > prev["close_dt"]:
                closed_map[site_id] = {
                    "close_dt": close_dt,
                    "row_idx": idx,
                    "ticket_id": str(row.get("TiketID", "")),
                    "date_open": str(row.get("DateOpen", "")),
                }

    # Update existing open tickets: aging + status update (P2 need close after 2 days)
    for idx, row in df_out.iterrows():
        status = str(row.get("Status", "")).lower()
        if status != "open":
            continue
        date_open = _parse_date_to_yyyymmdd(row.get("DateOpen", ""))
        aging = _aging_days(date_open, today)
        priority = row.get("Priority", "")
        df_out.at[idx, "Aging"] = str(aging)
        df_out.at[idx, "StatusUpdate"] = _status_update_for_priority(priority, aging)

    # Mark closed tickets as Clear if not reappearing for > 2 days
    for idx, row in df_out.iterrows():
        status = str(row.get("Status", "")).lower()
        if status != "closed":
            continue
        status_update = str(row.get("StatusUpdate", ""))
        if status_update == "Clear":
            continue
        close_date = _parse_date_to_yyyymmdd(row.get("Updatetanggal", ""))
        if not close_date:
            continue
        try:
            close_dt = dt.datetime.strptime(close_date, "%Y%m%d").date()
        except ValueError:
            continue
        if (today - close_dt).days > 2:
            df_out.at[idx, "StatusUpdate"] = "Clear"

    for rec in daily_records:
        tid = rec.get("TiketID", "")
        if not tid:
            continue
        site_id = str(rec.get("SITEID", "")).strip()
        priority = rec.get("Priority", "")

        if tid in index:
            i = index[tid]
            existing = df_out.loc[i].to_dict()
            date_open = existing.get("DateOpen") or rec.get("DateOpen")
            date_open_norm = _parse_date_to_yyyymmdd(date_open)
            if not date_open_norm:
                date_open_norm = _parse_date_to_yyyymmdd(rec.get("DateOpen"))
            status = existing.get("Status") or "Open"
            status_update = _status_update_for_priority(priority, _aging_days(date_open_norm, today))
            if str(status).lower() in ("closed", "clear"):
                status_update = "ReOpen"
            rec["DateOpen"] = date_open
            rec["Aging"] = str(_aging_days(date_open_norm, today))
            rec["Status"] = "Open"
            rec["StatusUpdate"] = status_update
            rec["Updatetanggal"] = today.strftime("%Y%m%d")
            df_out.loc[i, GLOBAL_COLUMNS] = [rec.get(c, "") for c in GLOBAL_COLUMNS]
        else:
            # ReOpen by SITEID within 2 days after close
            if site_id and site_id in closed_map:
                closed_info = closed_map[site_id]
                delta_days = (today - closed_info["close_dt"]).days
                if delta_days <= 2:
                    i = closed_info["row_idx"]
                    existing = df_out.loc[i].to_dict()
                    date_open = existing.get("DateOpen") or rec.get("DateOpen")
                    date_open_norm = _parse_date_to_yyyymmdd(date_open)
                    rec["TiketID"] = closed_info["ticket_id"]
                    rec["DateOpen"] = date_open
                    rec["Aging"] = str(_aging_days(date_open_norm, today))
                    rec["Status"] = "Open"
                    rec["StatusUpdate"] = "ReOpen"
                    rec["Updatetanggal"] = today.strftime("%Y%m%d")
                    df_out.loc[i, GLOBAL_COLUMNS] = [rec.get(c, "") for c in GLOBAL_COLUMNS]
                    history_records.append(rec)
                    continue

            # New ticket ID (> 2 days after close or first time)
            date_open_norm = _parse_date_to_yyyymmdd(rec.get("DateOpen", ""))
            rec["Aging"] = str(_aging_days(date_open_norm, today))
            rec["StatusUpdate"] = _status_update_for_priority(priority, _aging_days(date_open_norm, today))
            rec["Updatetanggal"] = today.strftime("%Y%m%d")
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


def append_user_activity(
    credentials_path: str,
    spreadsheet_id: str,
    tab_activity: str,
    row: List[str],
):
    """Append a user activity row to activity sheet."""
    service = _build_service(credentials_path)
    _ensure_sheet(service, spreadsheet_id, tab_activity)
    values = _read_sheet(service, spreadsheet_id, tab_activity)
    if not values:
        header = ["telegram_id", "telegram_name", "telegram_username", "access_at", "message"]
        _write_sheet(service, spreadsheet_id, tab_activity, [header])
    _append_rows(service, spreadsheet_id, tab_activity, [row])


def close_ticket(
    credentials_path: str,
    spreadsheet_id: str,
    tab_database: str,
    tab_updatedaily: str,
    ticket_id: str,
    closed_by: str,
    note: str,
) -> Tuple[bool, str]:
    """Set ticket to Closed and append to UPDATEDAILY."""
    service = _build_service(credentials_path)
    values = _read_sheet(service, spreadsheet_id, tab_database)
    df_db = _to_df(values)

    if df_db.empty or "TiketID" not in df_db.columns:
        return False, "Database kosong atau kolom TiketID tidak ditemukan."

    ticket_id = str(ticket_id).strip()
    match = df_db.index[df_db["TiketID"].astype(str) == ticket_id].tolist()
    if not match:
        return False, f"TiketID {ticket_id} tidak ditemukan."

    row_idx = match[0]
    today = dt.datetime.now().strftime("%Y%m%d")

    df_db.at[row_idx, "Status"] = "Closed"
    df_db.at[row_idx, "StatusUpdate"] = "Closed"
    df_db.at[row_idx, "Updatetanggal"] = today
    df_db.at[row_idx, "closedby"] = closed_by
    if note:
        df_db.at[row_idx, "Note"] = note

    rows = [GLOBAL_COLUMNS] + df_db.fillna("").astype(str).values.tolist()
    _clear_sheet(service, spreadsheet_id, tab_database)
    _write_sheet(service, spreadsheet_id, tab_database, rows)

    updated_row = [df_db.at[row_idx, col] if col in df_db.columns else "" for col in GLOBAL_COLUMNS]
    _append_rows(service, spreadsheet_id, tab_updatedaily, [updated_row])

    return True, "Tiket berhasil ditutup."


def write_source_sheet(
    credentials_path: str,
    spreadsheet_id: str,
    tab_name: str,
    df_source: pd.DataFrame,
):
    """Replace source sheet content with provided DataFrame."""
    service = _build_service(credentials_path)
    _ensure_sheet(service, spreadsheet_id, tab_name)
    rows = [list(df_source.columns)] + df_source.fillna("").astype(str).values.tolist()
    _clear_sheet(service, spreadsheet_id, tab_name)
    _write_sheet(service, spreadsheet_id, tab_name, rows)
