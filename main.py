"""
Bot Telegram untuk mengelola tiket NOVLI V1.0
Data diambil dari Google Sheets
"""
import os
import asyncio
import tempfile
import pandas as pd
from typing import List, Tuple
from datetime import datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo
import logging
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BotCommand,
    MenuButtonCommands,
)
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.constants import ParseMode
from dotenv import load_dotenv
from sheet_reader import SheetReader
from message_formatter import MessageFormatter
from sheet_sync import (
    sync_to_global,
    close_ticket,
    append_user_activity,
    read_database_df,
    write_source_sheet,
)

# Load environment variables
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
SHEET_URL = os.getenv("GOOGLE_SHEET_URL")
GLOBAL_SHEET_ID = os.getenv("GLOBAL_SHEET_ID")
GLOBAL_SHEET_TAB_DATABASE = os.getenv("GLOBAL_SHEET_TAB_DATABASE", "DATABASE")
GLOBAL_SHEET_TAB_HISTORY = os.getenv("GLOBAL_SHEET_TAB_HISTORY", "HISTORY")
GLOBAL_SHEET_TAB_UPDATEDAILY = os.getenv("GLOBAL_SHEET_TAB_UPDATEDAILY", "UPDATEDAILY")
GLOBAL_SHEET_TAB_ACTIVITY = os.getenv("GLOBAL_SHEET_TAB_ACTIVITY", "USER_ACTIVITY")
SOURCE_SHEET_ID = os.getenv("SOURCE_SHEET_ID")
SOURCE_SHEET_TAB = os.getenv("SOURCE_SHEET_TAB")
SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
SYNC_TIMEZONE = os.getenv("SYNC_TIMEZONE", "Asia/Jakarta")

REQUIRED_SOURCE_COLUMNS = [
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
]

# Initialize sheet reader
source_reader = SheetReader(SHEET_URL)
# Data untuk tampilan bot diambil dari NOVLI Global
display_reader = SheetReader(
    None,
    global_sheet_id=GLOBAL_SHEET_ID,
    global_tab=GLOBAL_SHEET_TAB_DATABASE,
    credentials_path=SERVICE_ACCOUNT_FILE,
)
logger = logging.getLogger("novli_bot")

def setup_logging():
    log_dir = os.path.join(os.getcwd(), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "bot.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )

async def run_sync_job():
    """Sync H-1 data to global spreadsheet and local backup."""
    if not SERVICE_ACCOUNT_FILE or not GLOBAL_SHEET_ID:
        return "Missing GLOBAL_SHEET_ID or GOOGLE_SERVICE_ACCOUNT_FILE"

    # Reload source data before syncing
    source_reader.load_data(force_reload=True, filter_h1=False)

    # Filter H-1; fallback to H-2 if empty
    df = source_reader.filter_by_days_ago(1)
    if df is None or df.empty:
        df = source_reader.filter_by_days_ago(2)
    if df is None or df.empty:
        return "No data found for H-1/H-2"

    result = await _run_sync_with_df(df)
    return result


async def daily_sync_loop():
    """Run sync every day at 08:00 WIB."""
    tz = ZoneInfo(SYNC_TIMEZONE)
    while True:
        now = datetime.now(tz)
        target = now.replace(hour=8, minute=0, second=0, microsecond=0)
        if now >= target:
            target = target + timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        await run_sync_job()


async def scheduled_sync(context: ContextTypes.DEFAULT_TYPE):
    """JobQueue callback for daily sync."""
    await run_sync_job()

# Inline keyboard menu utama (tampil di dalam chat)
def get_main_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton("Summary", callback_data="summary"),
            InlineKeyboardButton("List", callback_data="list"),
            InlineKeyboardButton("Alarm", callback_data="alarm"),
        ],
        [
            InlineKeyboardButton("P1", callback_data="p1"),
            InlineKeyboardButton("P2", callback_data="p2"),
            InlineKeyboardButton("Ticket Detail", callback_data="ticket"),
        ],
        [
            InlineKeyboardButton("Info", callback_data="info"),
            InlineKeyboardButton("Sync", callback_data="sync"),
            InlineKeyboardButton("Columns", callback_data="columns"),
        ],
        [InlineKeyboardButton("Close", callback_data="close")],
        [InlineKeyboardButton("Import", callback_data="import")],
        [InlineKeyboardButton("Help", callback_data="help")],
    ]
    return InlineKeyboardMarkup(buttons)





def _get_message_from_update(update: Update):
    if update.message:
        return update.message
    if update.callback_query and update.callback_query.message:
        return update.callback_query.message
    return None


async def _edit_or_send(update: Update, text: str, **kwargs):
    message = _get_message_from_update(update)
    if update.callback_query and message:
        try:
            return await message.edit_text(text, **kwargs)
        except Exception:
            return await message.reply_text(text, **kwargs)
    if message:
        return await message.reply_text(text, **kwargs)
    return None

async def send_reply(update: Update, text: str, **kwargs):
    message = update.message
    if message is None and update.callback_query:
        message = update.callback_query.message
    if message is None:
        return None
    return await message.reply_text(text, **kwargs)

async def log_user_activity(update: Update, message: str):
    if not SERVICE_ACCOUNT_FILE or not GLOBAL_SHEET_ID:
        return
    user = update.effective_user
    if not user:
        return
    access_at = datetime.now(ZoneInfo(SYNC_TIMEZONE)).strftime("%d/%m/%Y %H:%M")
    row = [
        str(user.id),
        user.full_name or "",
        user.username or "",
        access_at,
        message,
    ]
    try:
        await asyncio.to_thread(
            append_user_activity,
            SERVICE_ACCOUNT_FILE,
            GLOBAL_SHEET_ID,
            GLOBAL_SHEET_TAB_ACTIVITY,
            row,
        )
    except Exception:
        logger.exception("Failed to log user activity")


def get_open_df():
    """Load data from NOVLI Global and return only open tickets."""
    display_reader.load_data(force_reload=False, filter_h1=False)
    df = display_reader.df
    if df is None or df.empty:
        return df
    if 'Status' in df.columns:
        return df[df['Status'].astype(str).str.lower() == 'open']
    return df


def _normalize_col_name(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def _validate_import_headers(df: pd.DataFrame) -> Tuple[bool, List[str]]:
    if df is None or df.empty:
        return False, REQUIRED_SOURCE_COLUMNS
    normalized = {_normalize_col_name(col) for col in df.columns}
    missing = []
    for col in REQUIRED_SOURCE_COLUMNS:
        if _normalize_col_name(col) not in normalized:
            missing.append(col)
    return len(missing) == 0, missing


def _filter_import_df(df_raw: pd.DataFrame) -> pd.DataFrame:
    base = source_reader._filter_and_clean_data(df_raw, filter_h1=False)
    df_h1 = source_reader._filter_by_date(base, days_ago=1)
    if df_h1 is not None and not df_h1.empty:
        return df_h1
    df_h2 = source_reader._filter_by_date(base, days_ago=2)
    return df_h2


def _write_backup(backup_df: pd.DataFrame):
    backup_dir = os.path.join(os.getcwd(), "backup")
    os.makedirs(backup_dir, exist_ok=True)
    backup_date = datetime.now().strftime("%Y%m%d")
    backup_path = os.path.join(backup_dir, f"backup_{backup_date}.csv")
    suffix = datetime.now(ZoneInfo(SYNC_TIMEZONE)).strftime("%d%b%y").lower()
    backup_col = f"StatusUpdate{suffix}"
    if "StatusUpdate" in backup_df.columns:
        backup_df = backup_df.rename(columns={"StatusUpdate": backup_col})
    backup_df.to_csv(backup_path, index=False)


async def _run_sync_with_df(df_filtered: pd.DataFrame) -> str:
    if df_filtered is None or df_filtered.empty:
        return "No data found for H-1/H-2"
    try:
        sync_to_global(
            SERVICE_ACCOUNT_FILE,
            GLOBAL_SHEET_ID,
            GLOBAL_SHEET_TAB_DATABASE,
            GLOBAL_SHEET_TAB_HISTORY,
            df_filtered,
        )
    except Exception as exc:
        logger.exception("Sync failed: %s", exc)
        return f"Sync failed: {exc}"

    backup_df = df_filtered
    if GLOBAL_SHEET_ID and SERVICE_ACCOUNT_FILE:
        try:
            backup_df = read_database_df(
                SERVICE_ACCOUNT_FILE,
                GLOBAL_SHEET_ID,
                GLOBAL_SHEET_TAB_DATABASE,
            )
        except Exception:
            logger.exception("Failed to read database for backup; falling back to source data")

    _write_backup(backup_df)
    logger.info("Sync finished: %s rows", len(df_filtered))
    return f"Synced {len(df_filtered)} rows"


def _parse_close_payload(message: str):
    payload = {}
    for line in message.splitlines():
        line = line.strip()
        if not line:
            continue
        if ':' in line:
            key, value = line.split(':', 1)
        elif '=' in line:
            key, value = line.split('=', 1)
        else:
            continue
        key = key.strip().lower().replace(' ', '')
        payload[key] = value.strip()
    ticket_id = payload.get('ticketid') or payload.get('tiketid')
    closed_by = payload.get('closedby') or payload.get('closed_by')
    note = payload.get('note') or payload.get('catatan')
    return ticket_id, closed_by, note




def _get_history_df():
    if not SERVICE_ACCOUNT_FILE or not GLOBAL_SHEET_ID:
        return None
    try:
        return read_database_df(
            SERVICE_ACCOUNT_FILE,
            GLOBAL_SHEET_ID,
            GLOBAL_SHEET_TAB_HISTORY,
        )
    except Exception:
        logger.exception("Failed to read history sheet")
        return None


def _start_history(update: Update, context: ContextTypes.DEFAULT_TYPE, rows, title: str):
    context.chat_data["history_rows"] = rows
    context.chat_data["history_title"] = title
    context.chat_data["history_offset"] = 0


async def _send_history_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = context.chat_data.get("history_rows") or []
    title = context.chat_data.get("history_title", "History")
    offset = int(context.chat_data.get("history_offset", 0))
    total = len(rows)
    if total == 0:
        await _edit_or_send(update, "Tidak ada data history.")
        return

    page_size = 10
    chunk = rows[offset:offset + page_size]
    body = MessageFormatter.format_history_rows(chunk, title=title)
    end_idx = min(offset + page_size, total)
    header = f"""<i>Total: {total} data</i>
<i>Menampilkan {offset + 1}-{end_idx}</i>

"""

    buttons = []
    nav_row = []
    if offset > 0:
        nav_row.append(InlineKeyboardButton("Sebelumnya", callback_data="history_prev"))
    if end_idx < total:
        nav_row.append(InlineKeyboardButton("Lanjut", callback_data="history_next"))
    if nav_row:
        buttons.append(nav_row)
    buttons.append([InlineKeyboardButton("Back", callback_data="menu")])
    keyboard = InlineKeyboardMarkup(buttons)

    await _edit_or_send(update, header + body, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def history_by_site(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await log_user_activity(update, update.message.text)
    if not context.args:
        await send_reply(update, "Gunakan format: /history [SITEID]")
        return
    site_id = context.args[0].strip().upper()
    df_hist = _get_history_df()
    if df_hist is None or df_hist.empty:
        await send_reply(update, "History belum tersedia.")
        return
    if "SITEID" not in df_hist.columns:
        await send_reply(update, "Kolom SITEID tidak ditemukan di History.")
        return
    rows = df_hist[df_hist["SITEID"].astype(str).str.upper() == site_id]
    if rows.empty:
        await send_reply(update, f"Tidak ada history untuk SITEID {site_id}.")
        return
    _start_history(update, context, rows.to_dict("records"), title=f"History SITEID: {site_id}")
    await _send_history_page(update, context)


async def history_by_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await log_user_activity(update, update.message.text)
    if not context.args:
        await send_reply(update, "Gunakan format: /historyid [TICKET_ID]")
        return
    ticket_id = context.args[0].strip()
    df_hist = _get_history_df()
    if df_hist is None or df_hist.empty:
        await send_reply(update, "History belum tersedia.")
        return
    if "TiketID" not in df_hist.columns:
        await send_reply(update, "Kolom TiketID tidak ditemukan di History.")
        return
    rows = df_hist[df_hist["TiketID"].astype(str) == ticket_id]
    if rows.empty:
        await send_reply(update, f"Tidak ada history untuk TiketID {ticket_id}.")
        return
    _start_history(update, context, rows.to_dict("records"), title=f"History TiketID: {ticket_id}")
    await _send_history_page(update, context)


async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline menu button taps."""
    query = update.callback_query
    if not query:
        return
    await query.answer()
    action = query.data
    if query.message:
        await query.edit_message_reply_markup(reply_markup=None)
    await log_user_activity(update, f"button:{action}")

    if action == 'summary':
        await show_summary(update, context)
    elif action == 'list':
        await prompt_list_menu(update, context)
    elif action == 'list_all':
        await list_tickets_all(update, context)
    elif action.startswith('list_code:'):
        code = action.split(':', 1)[1]
        code_map = {
            'ACH': 'ACEH',
            'BJI': 'BINJAI',
            'MDN': 'MEDAN',
            'PMS': 'PEMATANG SIANTAR',
            'PSP': 'PADANG SIDEMPUAN',
            'RAP': 'RANTAU PRAPAT',
        }
        nop_name = code_map.get(code)
        if not nop_name:
            await send_reply(update, 'Pilihan tidak valid.')
            return
        await list_tickets_by_nop(update, context, nop_name)
    elif action == 'list_next':
        tickets = context.chat_data.get('list_tickets') or []
        if not tickets:
            await send_reply(update, 'Tidak ada data untuk dilanjutkan.')
            return
        offset = int(context.chat_data.get('list_offset', 0)) + 20
        if offset >= len(tickets):
            await send_reply(update, 'Tidak ada data lagi.')
            context.chat_data.pop('list_tickets', None)
            context.chat_data.pop('list_title', None)
            context.chat_data.pop('list_offset', None)
            return
        context.chat_data['list_offset'] = offset
        await _send_list_page(update, context)
    elif action == 'list_stop':
        context.chat_data.pop('list_tickets', None)
        context.chat_data.pop('list_title', None)
        context.chat_data.pop('list_offset', None)
        await send_reply(update, 'Baik, daftar dihentikan.')
    elif action == 'p1':
        await p1_tickets(update, context)
    elif action == 'p2':
        await p2_tickets(update, context)
    elif action == 'ticket':
        await send_reply(update, 'Kirim format: /ticket [TICKET_ID]')
    elif action == 'info':
        await info_command(update, context)
    elif action == 'sync':
        await sync_command(update, context)
    elif action == 'close':
        await close_command(update, context)
    elif action == 'columns':
        await show_columns(update, context)
    elif action == 'alarm':
        await alarm(update, context)
    elif action == 'close_back':
        await handle_close_back(update, context)
    elif action == 'close_submit':
        await handle_close_submit(update, context)
    elif action == 'close_view_yes':
        await handle_close_view_yes(update, context)
    elif action == 'close_view_no':
        await handle_close_view_no(update, context)
    elif action == 'close_view_ticket':
        await handle_close_view_ticket(update, context)
    elif action == 'close_view_history':
        await handle_close_view_history(update, context)
    elif action == 'import':
        await import_command(update, context)
    elif action == 'import_cancel':
        context.user_data.pop("awaiting_import", None)
        await menu_command(update, context)
    elif action == 'import_sync_yes':
        df_pending = context.user_data.pop("import_pending_df", None)
        if df_pending is None or df_pending.empty:
            await _edit_or_send(update, "Tidak ada data import yang bisa disinkronkan.")
        else:
            await _edit_or_send(update, "Memulai sinkronisasi data...")
            result = await _run_sync_with_df(df_pending)
            if result.startswith("Synced"):
                display_reader.load_data(force_reload=True, filter_h1=False)
            await _edit_or_send(update, f"Sinkronisasi selesai: {result}", reply_markup=get_main_keyboard())
    elif action == 'import_sync_no':
        context.user_data.pop("import_pending_df", None)
        await _edit_or_send(update, "Import disimpan di replika. Sync dibatalkan.", reply_markup=get_main_keyboard())
    elif action == 'menu':
        await menu_command(update, context)
    elif action == 'help':
        await help_command(update, context)

# Fungsi untuk command /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk command /start dengan daftar fitur"""
    if update.message:
        await log_user_activity(update, update.message.text)
    help_text = """<b>Selamat datang di Bot NOVLI V1.0!</b>

<b>Apa yang ingin kamu lihat -.-</b>
/summary - Ringkasan tiket (Open)
/list - Daftar tiket (Open)
/p1 - Tiket prioritas P1 (Open)
/p2 - Tiket prioritas P2 (Open)
/ticket [ID] - Detail tiket
/history [SITEID] - Riwayat per site
/historyid [ID] - Riwayat per tiket
/info - Statistik data
/alarm - Alarm ringkasan
/sync - Sinkronisasi ke database
/import - Import data dari file (staging replika)
/close - Tutup tiket
/columns - Lihat nama kolom
/help - Panduan
/menu - Tampilkan tombol menu

<i>Gunakan tombol di bawah untuk menjalankan perintah.</i>"""
    await _edit_or_send(
        update,
        help_text,
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard(),
    )

# Fungsi untuk menampilkan summary tiket
async def show_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk menampilkan summary tiket"""
    if update.message:
        await log_user_activity(update, update.message.text)
    df_open = get_open_df()
    info = display_reader.get_data_info()

    if df_open is None or df_open.empty:
        await send_reply(update, '? Belum ada data. Jalankan /sync untuk memperbarui data.')
        return

    prio_col = 'Prio' if 'Prio' in df_open.columns else 'Priority'
    p1_count = len(df_open[df_open[prio_col] == 'P1']) if prio_col in df_open.columns else 0
    p2_count = len(df_open[df_open[prio_col] == 'P2']) if prio_col in df_open.columns else 0
    open_count = len(df_open)
    need_close_count = p1_count

    nop_summary = []
    if 'NOP' in df_open.columns:
        grouped = df_open.groupby('NOP')
        for nop, group in sorted(grouped, key=lambda x: str(x[0])):
            total_count = len(group)
            p1_count_nop = len(group[group[prio_col] == 'P1']) if prio_col in group.columns else 0
            nop_summary.append(f"{nop} : {total_count} Site / {p1_count_nop} Site")
    nop_summary = '\n'.join(nop_summary) if nop_summary else 'Tidak ada data'
    nop_lines = nop_summary.split('\n')

    if len(nop_lines) > 15:
        nop_display = '\n'.join(nop_lines[:15])
        nop_display += f"\n<i>... dan {len(nop_lines) - 15} region lainnya</i>"
    else:
        nop_display = nop_summary

    from datetime import datetime
    today = datetime.now().date()

    message = (
        f"<b>SUMMARY TIKET NOVLI V1.0</b>\n"
        f"<b>Tanggal: {today.strftime('%d-%m-%Y')}</b>\n\n"
        f"<b>Total Data (Open):</b>\n"
        f"  Total Open: {open_count} tiket\n\n"
        f"<b>Prioritas (Open):</b>\n"
        f"  P1: {p1_count} tiket\n"
        f"  P2: {p2_count} tiket\n\n"
        f"<b>Status:</b>\n"
        f"  Tiket Open: {open_count} tiket\n"
        f"  Need Close (P1): {need_close_count} tiket\n\n"
        f"<b>Breakdown per NOP (Open):</b>\n"
        f"<i>(Format: Total / Need Close)</i>\n"
        f"{nop_display}\n\n"
        f"<i>Ketik /help untuk command lainnya</i>"
    )

    await _edit_or_send(update, message, parse_mode=ParseMode.HTML)


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await log_user_activity(update, update.message.text)
    await _edit_or_send(
        update,
        "Menu ditampilkan. Pilih salah satu tombol.",
        reply_markup=get_main_keyboard(),
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk command /help"""
    if update.message:
        await log_user_activity(update, update.message.text)
    help_text = MessageFormatter.format_help_message()
    await send_reply(update, 
        help_text,
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard(),
    )

# Fungsi untuk command /info
async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk menampilkan info statistik data"""
    if update.message:
        await log_user_activity(update, update.message.text)
    info = display_reader.get_data_info()
    df_open = get_open_df()

    if df_open is None or df_open.empty:
        await send_reply(update, '? Belum ada data. Jalankan /sync untuk memperbarui data.')
        return

    prio_col = 'Prio' if 'Prio' in df_open.columns else 'Priority'
    p1_count = len(df_open[df_open[prio_col] == 'P1']) if prio_col in df_open.columns else 0
    p2_count = len(df_open[df_open[prio_col] == 'P2']) if prio_col in df_open.columns else 0
    open_count = len(df_open)
    need_close_count = p1_count

    message = (
        f"<b>Statistik Data Bot NOVLI V1.0</b>\n\n"
        f"<b>Data Source:</b>\n"
        f"   Total di NOVLI Global: {info['total_raw']:,} baris\n"
        f"   Total Open: {open_count} baris\n\n"
        f"<b>Prioritas (Open):</b>\n"
        f"   P1: {p1_count} tiket\n"
        f"   P2: {p2_count} tiket\n\n"
        f"<b>Status:</b>\n"
        f"   Open: {open_count} tiket\n"
        f"   Need Close: {need_close_count} tiket\n\n"
        f"<b>Cache Info:</b>\n"
        f"   Last update: {info['last_update']}\n"
        f"   Cache valid: {'Ya' if info['cache_valid'] else 'Tidak'}\n"
        f"   Expires in: {info['cache_expires_in']}s\n\n"
        f"<i>Jalankan /sync untuk memperbarui data</i>"
    )

    await _edit_or_send(update, message, parse_mode=ParseMode.HTML)

async def sync_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk menjalankan sinkronisasi ke spreadsheet global"""
    if update.message:
        await log_user_activity(update, update.message.text)
    await send_reply(update, "Memulai sinkronisasi data...")
    result = await run_sync_job()
    if result.startswith("Missing"):
        await send_reply(update, "Konfigurasi sync belum lengkap.")
    elif result.startswith("No data"):
        await send_reply(update, "Tidak ada data H-1/H-2 untuk disinkronkan.")
    else:
        await send_reply(update, f"Sinkronisasi selesai: {result}")


async def import_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mulai proses import data dari file."""
    if update.message:
        await log_user_activity(update, update.message.text)
    if not SERVICE_ACCOUNT_FILE or not SOURCE_SHEET_ID or not SOURCE_SHEET_TAB:
        await _edit_or_send(
            update,
            "Konfigurasi import belum lengkap. Pastikan `SOURCE_SHEET_ID`, "
            "`SOURCE_SHEET_TAB`, dan `GOOGLE_SERVICE_ACCOUNT_FILE` sudah diisi.",
        )
        return
    context.user_data["awaiting_import"] = True
    prompt = (
        "Silakan kirim file <b>Excel/CSV</b> sesuai format replika.\n"
        "File akan divalidasi sebelum sync.\n\n"
        "<i>Format header wajib sama dengan sumber replika.</i>"
    )
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Back", callback_data="import_cancel")]]
    )
    await _edit_or_send(update, prompt, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def import_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle upload file untuk import."""
    if not context.user_data.get("awaiting_import"):
        return
    if update.message:
        await log_user_activity(update, update.message.text or "[file]")
    document = update.message.document
    if not document:
        await send_reply(update, "File tidak ditemukan. Silakan kirim ulang.")
        return

    filename = document.file_name or ""
    suffix = os.path.splitext(filename)[1].lower()
    if suffix not in (".xlsx", ".xls", ".csv"):
        await send_reply(update, "Format file harus .xlsx, .xls, atau .csv.")
        return

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
            tmp_path = tmp_file.name

        file_obj = await context.bot.get_file(document.file_id)
        await file_obj.download_to_drive(tmp_path)

        if suffix == ".csv":
            df_raw = pd.read_csv(tmp_path)
        else:
            df_raw = pd.read_excel(tmp_path)

        ok, missing = _validate_import_headers(df_raw)
        if not ok:
            missing_list = ", ".join(missing)
            await send_reply(
                update,
                f"Header tidak sesuai. Kolom yang kurang: {missing_list}\n"
                "Silakan kirim file dengan header yang benar.",
            )
            return

        write_source_sheet(
            SERVICE_ACCOUNT_FILE,
            SOURCE_SHEET_ID,
            SOURCE_SHEET_TAB,
            df_raw,
        )

        df_filtered = _filter_import_df(df_raw)
        if df_filtered is None or df_filtered.empty:
            await send_reply(update, "Tidak ada data H-1/H-2 yang valid di file.")
            context.user_data.pop("awaiting_import", None)
            return

        context.user_data["awaiting_import"] = False
        context.user_data["import_pending_df"] = df_filtered

        buttons = [
            [InlineKeyboardButton("Lanjut Sync", callback_data="import_sync_yes")],
            [InlineKeyboardButton("Batal", callback_data="import_sync_no")],
        ]
        await _edit_or_send(
            update,
            "Data valid dan sudah disimpan ke replika.\nLanjut sync ke NOVLI Global?",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    except Exception as exc:
        logger.exception("Import failed: %s", exc)
        await send_reply(update, f"Gagal import: {exc}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

# Fungsi untuk command /close

def _get_close_state(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.user_data.setdefault("close_flow", {})


async def _show_close_prompt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    show_back: bool = True,
    extra_buttons=None,
):
    buttons = []
    if extra_buttons:
        buttons.extend(extra_buttons)
    if show_back:
        buttons.append([InlineKeyboardButton("Back", callback_data="close_back")])
    keyboard = InlineKeyboardMarkup(buttons) if buttons else None

    state = _get_close_state(context)
    if update.callback_query and update.callback_query.message:
        await _edit_or_send(update, text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        state["prompt_message_id"] = update.callback_query.message.message_id
        state["chat_id"] = update.callback_query.message.chat_id
        return

    message_id = state.get("prompt_message_id")
    chat_id = state.get("chat_id")
    if message_id and chat_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
            return
        except Exception:
            pass

    sent = await send_reply(update, text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    if sent:
        state["prompt_message_id"] = sent.message_id
        state["chat_id"] = sent.chat_id


async def close_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk menutup tiket"""
    if update.message:
        await log_user_activity(update, update.message.text)
    state = _get_close_state(context)
    if state.get("step") and state.get("step") != "ticket":
        state.clear()
        await _show_close_prompt(
            update,
            context,
            "Proses sebelumnya dibatalkan.\nMasukkan <b>TiketID</b> yang ingin di close:",
            show_back=True,
        )
        state["step"] = "ticket"
        return
    state.clear()
    state["step"] = "ticket"
    await _show_close_prompt(
        update,
        context,
        "Masukkan <b>TiketID</b> yang ingin di close:",
        show_back=True,
    )


async def handle_close_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = _get_close_state(context)
    step = state.get("step")
    user_text = update.message.text.strip()
    if user_text.startswith("/"):
        await _show_close_prompt(update, context, "Gunakan input sesuai pertanyaan, bukan command.")
        return

    if step == "ticket":
        if not user_text:
            await _show_close_prompt(update, context, "TiketID tidak boleh kosong.")
            return
        ticket = display_reader.get_ticket_by_id(user_text)
        if not ticket:
            await _show_close_prompt(
                update,
                context,
                f"TiketID <code>{user_text}</code> tidak ditemukan. Coba lagi:",
            )
            return
        state["ticket_id"] = user_text
        state["ticket_data"] = ticket
        state["step"] = "note"
        await _show_close_prompt(update, context, "Masukkan <b>pesan/catatan</b> penutupan:")
        return

    if step == "note":
        if not user_text:
            await _show_close_prompt(update, context, "Catatan tidak boleh kosong. Coba lagi:")
            return
        state["note"] = user_text
        state["step"] = "name"
        await _show_close_prompt(update, context, "Masukkan <b>nama asli</b> kamu:")
        return

    if step == "name":
        if not user_text:
            await _show_close_prompt(update, context, "Nama tidak boleh kosong. Coba lagi:")
            return
        state["closed_by"] = user_text
        state["step"] = "confirm"

        summary = f"""<b>Konfirmasi penutupan tiket</b>

TiketID: <code>{state.get('ticket_id')}</code>
Catatan: {state.get('note')}
Nama: {state.get('closed_by')}

Lanjutkan?"""
        buttons = [[InlineKeyboardButton("Lanjut", callback_data="close_submit")]]
        await _show_close_prompt(update, context, summary, show_back=True, extra_buttons=buttons)
        return


async def handle_close_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = _get_close_state(context)
    step = state.get("step")
    if step == "ticket":
        await menu_command(update, context)
        return
    if step == "note":
        state["step"] = "ticket"
        await _show_close_prompt(update, context, "Masukkan <b>TiketID</b> yang ingin di close:")
        return
    if step == "name":
        state["step"] = "note"
        await _show_close_prompt(update, context, "Masukkan <b>pesan/catatan</b> penutupan:")
        return
    if step == "confirm":
        state["step"] = "name"
        await _show_close_prompt(update, context, "Masukkan <b>nama asli</b> kamu:")
        return
    await menu_command(update, context)


async def handle_close_submit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = _get_close_state(context)
    ticket_id = state.get("ticket_id")
    closed_by = state.get("closed_by")
    note = state.get("note")
    if not ticket_id or not closed_by or not note:
        await _show_close_prompt(update, context, "Data belum lengkap. Silakan isi kembali.")
        state["step"] = "ticket"
        return

    ok, message = close_ticket(
        SERVICE_ACCOUNT_FILE,
        GLOBAL_SHEET_ID,
        GLOBAL_SHEET_TAB_DATABASE,
        GLOBAL_SHEET_TAB_UPDATEDAILY,
        ticket_id,
        closed_by,
        note,
    )
    if ok:
        display_reader.load_data(force_reload=True, filter_h1=False)

    state["step"] = "done"
    state["last_ticket_id"] = ticket_id
    text = (
        f"{message}\n"
        "Terima kasih atas kerjasama mu. Ingin melihat status tiket?"
    )
    buttons = [
        [InlineKeyboardButton("Ya", callback_data="close_view_yes")],
        [InlineKeyboardButton("Tidak", callback_data="close_view_no")],
    ]
    await _show_close_prompt(update, context, text, show_back=True, extra_buttons=buttons)


async def handle_close_view_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buttons = [
        [InlineKeyboardButton("Tiket", callback_data="close_view_ticket")],
        [InlineKeyboardButton("History", callback_data="close_view_history")],
        [InlineKeyboardButton("Back", callback_data="menu")],
    ]
    await _show_close_prompt(
        update,
        context,
        "Pilih data yang ingin ditampilkan:",
        show_back=False,
        extra_buttons=buttons,
    )


async def handle_close_view_no(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await menu_command(update, context)


async def handle_close_view_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = _get_close_state(context)
    ticket_id = state.get("last_ticket_id")
    if not ticket_id:
        await _show_close_prompt(update, context, "Tiket tidak ditemukan.")
        return
    ticket = display_reader.get_ticket_by_id(ticket_id)
    if not ticket:
        await _show_close_prompt(update, context, "Detail tiket tidak ditemukan.")
        return
    message = MessageFormatter.format_ticket_detail(ticket)
    buttons = [[InlineKeyboardButton("Back", callback_data="menu")]]
    await _show_close_prompt(update, context, message, show_back=False, extra_buttons=buttons)


async def handle_close_view_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = _get_close_state(context)
    ticket_id = state.get("last_ticket_id")
    if not ticket_id:
        await _show_close_prompt(update, context, "History tidak ditemukan.")
        return
    df_hist = _get_history_df()
    if df_hist is None or df_hist.empty:
        await _show_close_prompt(update, context, "History belum tersedia.")
        return
    if "TiketID" not in df_hist.columns:
        await _show_close_prompt(update, context, "Kolom TiketID tidak ditemukan di History.")
        return
    rows = df_hist[df_hist["TiketID"].astype(str) == ticket_id]
    if rows.empty:
        await _show_close_prompt(update, context, f"Tidak ada history untuk TiketID {ticket_id}.")
        return
    _start_history(update, context, rows.to_dict("records"), title=f"History TiketID: {ticket_id}")
    await _send_history_page(update, context)


# Fungsi untuk command /alarm
async def alarm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk menampilkan alarm update"""
    if update.message:
        await log_user_activity(update, update.message.text)
    df_open = get_open_df()
    if df_open is None or df_open.empty:
        await send_reply(update, '? Belum ada data. Jalankan /sync untuk memperbarui data.')
        return

    prio_col = 'Prio' if 'Prio' in df_open.columns else 'Priority'
    open_count = len(df_open)
    need_close_count = len(df_open[df_open[prio_col] == 'P1']) if prio_col in df_open.columns else 0

    nop_summary = []
    if 'NOP' in df_open.columns:
        grouped = df_open.groupby('NOP')
        for nop, group in sorted(grouped, key=lambda x: str(x[0])):
            total_count = len(group)
            p1_count_nop = len(group[group[prio_col] == 'P1']) if prio_col in group.columns else 0
            nop_summary.append(f"{nop} : {total_count} Site / {p1_count_nop} Site")
    nop_summary = '\n'.join(nop_summary) if nop_summary else 'Tidak ada data'

    message = MessageFormatter.format_alarm_message(
        region='SUMBAGUT',
        open_count=open_count,
        need_close_count=need_close_count,
        nop_summary=nop_summary,
        tickets=[],
    )

    await _edit_or_send(update, message, parse_mode=ParseMode.HTML)

# Fungsi untuk command /list
# Fungsi untuk command /list
async def list_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk menampilkan pilihan NOP"""
    if update.message:
        await log_user_activity(update, update.message.text)
    await prompt_list_menu(update, context)

async def prompt_list_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tampilkan pilihan NOP sebelum menampilkan list."""
    df_open = get_open_df()
    if df_open is None or df_open.empty:
        await send_reply(update, 'Tidak ada data tiket. Jalankan /sync untuk memperbarui data.')
        return

    nop_map = [
        ('ACH', 'ACEH'),
        ('BJI', 'BINJAI'),
        ('MDN', 'MEDAN'),
        ('PMS', 'PEMATANG SIANTAR'),
        ('PSP', 'PADANG SIDEMPUAN'),
        ('RAP', 'RANTAU PRAPAT'),
    ]

    lines = ['Kamu mau pilih NOVLI tampilkan yang mana?']
    for code, name in nop_map:
        lines.append(f"{code} => {name}")
    lines.append('ALL => Semua NOP')

    rows = []
    for i, (code, _name) in enumerate(nop_map):
        button = InlineKeyboardButton(code, callback_data=f'list_code:{code}')
        if i % 2 == 0:
            rows.append([button])
        else:
            rows[-1].append(button)
    rows.append([InlineKeyboardButton('ALL', callback_data='list_all')])
    rows.append([InlineKeyboardButton('Back', callback_data='menu')])
    keyboard = InlineKeyboardMarkup(rows)

    await send_reply(
        update,
        "\n".join(lines),
        reply_markup=keyboard,
    )

async def _start_list(update: Update, context: ContextTypes.DEFAULT_TYPE, tickets, title: str):
    context.chat_data["list_tickets"] = tickets
    context.chat_data["list_title"] = title
    context.chat_data["list_offset"] = 0
    await _send_list_page(update, context)

async def _send_list_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tickets = context.chat_data.get("list_tickets") or []
    title = context.chat_data.get("list_title", "Daftar Tiket")
    offset = int(context.chat_data.get("list_offset", 0))
    total = len(tickets)

    if total == 0:
        await send_reply(update, "Tidak ada data untuk ditampilkan.")
        return

    chunk = tickets[offset:offset + 20]
    message = MessageFormatter.format_ticket_list(chunk)
    end_idx = min(offset + 20, total)
    header = (
        f"<b>{title}</b>\n"
        f"<i>Total: {total} tiket</i>\n"
        f"<i>Menampilkan {offset + 1}-{end_idx}</i>\n\n"
    )

    buttons = []
    if end_idx < total:
        buttons.append([InlineKeyboardButton("Tampilkan data selanjutnya", callback_data="list_next")])
    buttons.append([InlineKeyboardButton("Berhenti", callback_data="list_stop")])
    buttons.append([InlineKeyboardButton("Back", callback_data="menu")])
    keyboard = InlineKeyboardMarkup(buttons)

    await _edit_or_send(update, header + message, parse_mode=ParseMode.HTML, reply_markup=keyboard)

async def list_tickets_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk menampilkan semua tiket"""
    df_open = get_open_df()
    if df_open is None or df_open.empty:
        await send_reply(update, 'Tidak ada data tiket. Jalankan /sync untuk memperbarui data.')
        return

    tickets = df_open.to_dict('records')
    await _start_list(update, context, tickets, 'Daftar Tiket (Status: Open)')

async def list_tickets_by_nop(update: Update, context: ContextTypes.DEFAULT_TYPE, nop_name: str):
    """Handler untuk menampilkan tiket per NOP"""
    df_open = get_open_df()
    if df_open is None or df_open.empty:
        await send_reply(update, 'Tidak ada data tiket. Jalankan /sync untuk memperbarui data.')
        return

    if 'NOP' in df_open.columns:
        df_open = df_open[df_open['NOP'] == nop_name]
    tickets = df_open.to_dict('records')
    if not tickets:
        await send_reply(update, f'Tidak ada tiket untuk NOP {nop_name}.')
        return

    await _start_list(update, context, tickets, f'Daftar Tiket (NOP: {nop_name}, Status: Open)')

# Fungsi untuk command /p1
async def p1_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk menampilkan tiket prioritas P1"""
    if update.message:
        await log_user_activity(update, update.message.text)
    df_open = get_open_df()
    if df_open is None or df_open.empty:
        await send_reply(update, 'Tidak ada tiket P1 saat ini.')
        return

    prio_col = 'Prio' if 'Prio' in df_open.columns else 'Priority'
    df_p1 = df_open[df_open[prio_col] == 'P1'] if prio_col in df_open.columns else df_open.iloc[0:0]
    if df_p1.empty:
        await send_reply(update, 'Tidak ada tiket P1 saat ini.')
        return

    tickets = df_p1.to_dict('records')

    chunk_size = 20
    header = f"<b>Tiket Prioritas P1 (Open)</b>\n<i>Total: {len(tickets)} tiket</i>\n\n"
    for i in range(0, len(tickets), chunk_size):
        chunk = tickets[i:i + chunk_size]
        message = MessageFormatter.format_ticket_list(chunk)
        prefix = header if i == 0 else '<i>Lanjutan...</i>\n\n'
        await send_reply(update, prefix + message, parse_mode=ParseMode.HTML)

async def p2_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk menampilkan tiket prioritas P2"""
    if update.message:
        await log_user_activity(update, update.message.text)
    df_open = get_open_df()
    if df_open is None or df_open.empty:
        await send_reply(update, 'Tidak ada tiket P2 saat ini.')
        return

    prio_col = 'Prio' if 'Prio' in df_open.columns else 'Priority'
    df_p2 = df_open[df_open[prio_col] == 'P2'] if prio_col in df_open.columns else df_open.iloc[0:0]
    if df_p2.empty:
        await send_reply(update, 'Tidak ada tiket P2 saat ini.')
        return

    tickets = df_p2.to_dict('records')

    chunk_size = 20
    header = f"<b>Tiket Prioritas P2 (Open)</b>\n<i>Total: {len(tickets)} tiket</i>\n\n"
    for i in range(0, len(tickets), chunk_size):
        chunk = tickets[i:i + chunk_size]
        message = MessageFormatter.format_ticket_list(chunk)
        prefix = header if i == 0 else '<i>Lanjutan...</i>\n\n'
        await send_reply(update, prefix + message, parse_mode=ParseMode.HTML)

async def ticket_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk menampilkan detail tiket berdasarkan ID"""
    if update.message:
        await log_user_activity(update, update.message.text)
    if not context.args:
        await send_reply(
            update,
            "Gunakan format: /ticket [TICKET_ID]\n"
            "Contoh: /ticket RAP39520260407",
        )
        return

    ticket_id = context.args[0]
    ticket = display_reader.get_ticket_by_id(ticket_id)

    if not ticket:
        await send_reply(
            update,
            f"Tiket dengan ID <code>{ticket_id}</code> tidak ditemukan.",
            parse_mode=ParseMode.HTML,
        )
        return

    message = MessageFormatter.format_ticket_detail(ticket)
    await _edit_or_send(update, message, parse_mode=ParseMode.HTML)

# Fungsi untuk command /columns

async def show_columns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk menampilkan nama kolom di Google Sheets"""
    if update.message:
        await log_user_activity(update, update.message.text)
    columns = display_reader.get_column_names()
    
    if not columns:
        await send_reply(update, "❌ Tidak dapat membaca kolom. Jalankan /sync untuk memperbarui data.")
        return
    
    message = "<b>📋 Kolom di Google Sheets:</b>\n\n"
    for i, col in enumerate(columns, 1):
        message += f"{i}. <code>{col}</code>\n"
    
    await _edit_or_send(update, message, parse_mode=ParseMode.HTML)

# Fungsi untuk pesan biasa
async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk pesan biasa"""
    if update.message:
        await log_user_activity(update, update.message.text)
    close_step = context.user_data.get("close_flow", {}).get("step")
    if close_step in ("ticket", "note", "name"):
        await handle_close_text(update, context)
        return
    if context.user_data.get("awaiting_import"):
        await send_reply(update, "Silakan kirim file Excel/CSV untuk import.")
        return
    text_raw = update.message.text.strip()
    text = text_raw.lower()
    
    if "halo" in text or "hai" in text:
        await send_reply(update, "👋 Hai! Ketik /help untuk melihat command yang tersedia.")
    elif "tiket" in text:
        await send_reply(update, "Untuk melihat tiket, gunakan /list atau /p1 atau /p2")
    else:
        await send_reply(update, f"Anda mengirim: {update.message.text}\n\nKetik /help untuk bantuan.")

def main():
    """Fungsi utama untuk menjalankan bot"""
    setup_logging()
    # Buat aplikasi bot
    async def _post_init(application: Application):
        commands = [
            BotCommand("start", "Mulai bot"),
            BotCommand("summary", "Ringkasan tiket (Open)"),
            BotCommand("list", "Daftar tiket (Open)"),
            BotCommand("p1", "Tiket prioritas P1 (Open)"),
            BotCommand("p2", "Tiket prioritas P2 (Open)"),
            BotCommand("ticket", "Detail tiket"),
            BotCommand("history", "Riwayat per site"),
            BotCommand("historyid", "Riwayat per tiket"),
            BotCommand("info", "Statistik data"),
            BotCommand("alarm", "Alarm ringkasan"),
            BotCommand("sync", "Sinkronisasi ke database"),
            BotCommand("import", "Import data dari file"),
            BotCommand("close", "Tutup tiket"),
            BotCommand("columns", "Lihat nama kolom"),
            BotCommand("menu", "Tampilkan tombol menu"),
            BotCommand("help", "Panduan"),
        ]
        await application.bot.set_my_commands(commands)
        await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())

    app = Application.builder().token(TOKEN).post_init(_post_init).build()
    
    # Load data pertama kali dengan filter H-1
    print("🔄 Memuat data dari Google Sheets...")
    display_reader.load_data(filter_h1=False)  # Pastikan filter H-1 aktif
    
    # Tambahkan handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("summary", show_summary))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("info", info_command))
    app.add_handler(CommandHandler("sync", sync_command))
    app.add_handler(CommandHandler("import", import_command))
    app.add_handler(CommandHandler("close", close_command))
    app.add_handler(CommandHandler("alarm", alarm))
    app.add_handler(CommandHandler("list", list_tickets))
    app.add_handler(CommandHandler("p1", p1_tickets))
    app.add_handler(CommandHandler("p2", p2_tickets))
    app.add_handler(CommandHandler("ticket", ticket_detail))
    app.add_handler(CommandHandler("history", history_by_site))
    app.add_handler(CommandHandler("historyid", history_by_ticket))
    app.add_handler(CommandHandler("columns", show_columns))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CallbackQueryHandler(handle_menu))
    app.add_handler(MessageHandler(filters.Document.ALL, import_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    # Jalankan sinkronisasi harian jam 08:00 WIB
    if GLOBAL_SHEET_ID and SERVICE_ACCOUNT_FILE and app.job_queue:
        run_time = dt_time(hour=8, minute=0, tzinfo=ZoneInfo(SYNC_TIMEZONE))
        app.job_queue.run_daily(scheduled_sync, time=run_time, name="daily_sync")
    
    # Jalankan bot
    print("🤖 Bot NOVLI V1.0 sedang berjalan...")
    print("✅ Tekan Ctrl+C untuk menghentikan bot")
    app.run_polling()

if __name__ == "__main__":
    main()
