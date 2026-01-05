"""
Bot Telegram untuk mengelola tiket NOVLI V1.0
Data diambil dari Google Sheets
"""
import os
import asyncio
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.constants import ParseMode
from dotenv import load_dotenv
from sheet_reader import SheetReader
from message_formatter import MessageFormatter

# Load environment variables
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
SHEET_URL = os.getenv("GOOGLE_SHEET_URL")

# Initialize sheet reader
sheet_reader = SheetReader(SHEET_URL)

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
            InlineKeyboardButton("Refresh", callback_data="refresh"),
            InlineKeyboardButton("Columns", callback_data="columns"),
        ],
        [InlineKeyboardButton("Help", callback_data="help")],
    ]
    return InlineKeyboardMarkup(buttons)

async def send_reply(update: Update, text: str, **kwargs):
    message = update.message
    if message is None and update.callback_query:
        message = update.callback_query.message
    if message is None:
        return None
    return await message.reply_text(text, **kwargs)

async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline menu button taps."""
    query = update.callback_query
    if not query:
        return
    await query.answer()
    action = query.data

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
    elif action == 'refresh':
        await refresh(update, context)
    elif action == 'refresh_yes':
        await run_refresh(update, context)
    elif action == 'refresh_fallback_yes':
        await run_refresh_fallback(update, context)
    elif action == 'refresh_fallback_no':
        backup = context.chat_data.get('refresh_backup')
        if backup:
            sheet_reader.df, sheet_reader.df_raw, sheet_reader.last_load_time = backup
        context.chat_data['refresh_pending'] = False
        context.chat_data.pop('refresh_backup', None)
        await send_reply(update, 'Menggunakan data lama.')
    elif action == 'refresh_no':
        context.chat_data['refresh_pending'] = False
        context.chat_data['refresh_cancelled'] = True
        await send_reply(update, 'Refresh dibatalkan.')
    elif action == 'cancel_refresh':
        if not context.chat_data.get('refresh_pending'):
            await send_reply(update, 'Tidak ada refresh aktif untuk dibatalkan.')
            return
        context.chat_data['refresh_cancelled'] = True
        await send_reply(update, 'Refresh dibatalkan. Menggunakan data lama.')
    elif action == 'columns':
        await show_columns(update, context)
    elif action == 'alarm':
        await alarm(update, context)
    elif action == 'help':
        await help_command(update, context)

# Fungsi untuk command /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk command /start dengan daftar fitur"""
    help_text = (
        "<b>Selamat datang di Bot NOVLI V1.0!</b>\n\n"
        "<b>Apa yang ingin kamu lihat -.-</b>\n"
        "/summary - Ringkasan tiket H-1\n"
        "/list - Daftar tiket (H-1)\n"
        "/p1 - Tiket prioritas P1\n"
        "/p2 - Tiket prioritas P2\n"
        "/ticket [ID] - Detail tiket\n"
        "/info - Statistik data\n"
        "/alarm - Alarm ringkasan\n"
        "/refresh - Muat ulang data\n"
        "/columns - Lihat nama kolom\n"
        "/help - Panduan\n\n"
        "<i>Gunakan tombol di bawah untuk menjalankan perintah.</i>"
    )
    await send_reply(update, 
        help_text,
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard(),
    )

# Fungsi untuk menampilkan summary tiket
async def show_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk menampilkan summary tiket"""
    info = sheet_reader.get_data_info()
    
    if info['total_filtered'] == 0:
        await send_reply(update, "❌ Belum ada data. Gunakan /refresh untuk memuat data.")
        return
    
    # Get summary stats
    p1_count = len(sheet_reader.get_tickets_by_priority('P1'))
    p2_count = len(sheet_reader.get_tickets_by_priority('P2'))
    open_count, need_close_count = sheet_reader.get_summary_stats()
    
    # Get NOP breakdown (batasi agar tidak terlalu panjang)
    nop_summary = sheet_reader.format_region_summary()
    nop_lines = nop_summary.split('\n')
    
    # Batasi maksimal 15 region untuk avoid "text too long"
    if len(nop_lines) > 15:
        nop_display = '\n'.join(nop_lines[:15])
        nop_display += f"\n<i>... dan {len(nop_lines) - 15} region lainnya</i>"
    else:
        nop_display = nop_summary
    
    # Format summary dengan compact style
    from datetime import datetime, timedelta
    yesterday = datetime.now().date() - timedelta(days=1)
    
    message = (
        f"<b>📊 SUMMARY TIKET NOVLI V1.0</b>\n"
        f"<b>Tanggal: {yesterday.strftime('%d-%m-%Y')}</b>\n\n"
        f"<b>📦 Total Data:</b>\n"
        f"  Raw: {info['total_raw']:,} → Valid: {info['total_filtered']} tiket\n\n"
        f"<b>🎯 Prioritas:</b>\n"
        f"  🔴 P1: {p1_count} tiket\n"
        f"  🟡 P2: {p2_count} tiket\n\n"
        f"<b>📋 Status:</b>\n"
        f"  📊 Tiket Open: {open_count} tiket\n"
        f"  🔧 Need Close: {need_close_count} tiket\n\n"
        f"<b>🗺️ Breakdown per NOP:</b>\n"
        f"<i>(Format: Total / Need Close)</i>\n"
        f"{nop_display}\n\n"
        f"<i>Ketik /help untuk command lainnya</i>"
    )
    
    await send_reply(update, message, parse_mode=ParseMode.HTML)
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk command /help"""
    help_text = MessageFormatter.format_help_message()
    await send_reply(update, 
        help_text,
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard(),
    )

# Fungsi untuk command /refresh
async def refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk refresh data dari Google Sheets"""
    confirm_keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton('Ya', callback_data='refresh_yes'), InlineKeyboardButton('Tidak', callback_data='refresh_no')]]
    )
    await send_reply(
        update,
        'Apakah kamu yakin ingin me-refresh data sekarang?',
        reply_markup=confirm_keyboard,
    )

async def run_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cancel_keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton('Batalkan', callback_data='cancel_refresh')]]
    )
    context.chat_data['refresh_cancelled'] = False
    context.chat_data['refresh_pending'] = True
    context.chat_data['refresh_backup'] = (sheet_reader.df, sheet_reader.df_raw, sheet_reader.last_load_time)
    await send_reply(
        update,
        "Memuat ulang data dari Google Sheets...\n"
        "<i>Tekan Batalkan dalam 5 detik untuk membatalkan.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_keyboard,
    )

    await asyncio.sleep(5)
    if context.chat_data.get('refresh_cancelled'):
        context.chat_data['refresh_pending'] = False
        return

    df = sheet_reader.load_data(force_reload=True)

    if sheet_reader.last_date_filter_found is False and (df is None or df.empty):
        fallback_keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton('Ya', callback_data='refresh_fallback_yes'), InlineKeyboardButton('Tidak', callback_data='refresh_fallback_no')]]
        )
        await send_reply(
            update,
            'Data H-1 tidak ditemukan. Mau pakai data dua hari sebelumnya?',
            reply_markup=fallback_keyboard,
        )
        return

    if df is not None and not df.empty:
        info = sheet_reader.get_data_info()
        await send_reply(
            update,
            "<b>Data berhasil dimuat!</b>\n\n"
            f"Total data asli: {info['total_raw']:,} baris\n"
            f"Data valid (P1/P2): {info['total_filtered']:,} baris\n"
            f"Update: {info['last_update']}\n\n"
            f"<i>Cache akan expire dalam {info['cache_expires_in']}s</i>",
            parse_mode=ParseMode.HTML,
        )
    else:
        await send_reply(
            update,
            'Gagal memuat data. Pastikan Google Sheets dapat diakses.',
            parse_mode=ParseMode.HTML,
        )

    context.chat_data['refresh_pending'] = False
    context.chat_data.pop('refresh_backup', None)

async def run_refresh_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    df = sheet_reader.filter_by_days_ago(2)
    if df is None or df.empty:
        await send_reply(update, 'Data H-2 tidak ditemukan. Menggunakan data lama.')
        backup = context.chat_data.get('refresh_backup')
        if backup:
            sheet_reader.df, sheet_reader.df_raw, sheet_reader.last_load_time = backup
    else:
        info = sheet_reader.get_data_info()
        await send_reply(
            update,
            "<b>Data H-2 berhasil dimuat!</b>\n\n"
            f"Total data asli: {info['total_raw']:,} baris\n"
            f"Data valid (P1/P2): {info['total_filtered']:,} baris\n"
            f"Update: {info['last_update']}\n\n"
            f"<i>Cache akan expire dalam {info['cache_expires_in']}s</i>",
            parse_mode=ParseMode.HTML,
        )

    context.chat_data['refresh_pending'] = False
    context.chat_data.pop('refresh_backup', None)

# Fungsi untuk command /info
async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk menampilkan info statistik data"""
    info = sheet_reader.get_data_info()
    
    if info['total_filtered'] == 0:
        await send_reply(update, "❌ Belum ada data. Gunakan /refresh untuk memuat data.")
        return
    
    p1_count = len(sheet_reader.get_tickets_by_priority('P1'))
    p2_count = len(sheet_reader.get_tickets_by_priority('P2'))
    open_count, need_close_count = sheet_reader.get_summary_stats()
    
    message = (
        f"<b>📊 Statistik Data Bot NOVLI V1.0</b>\n\n"
        f"📦 <b>Data Source:</b>\n"
        f"   • Total di Google Sheets: {info['total_raw']:,} baris\n"
        f"   • Setelah filter valid: {info['total_filtered']:,} baris\n\n"
        f"🎯 <b>Prioritas:</b>\n"
        f"   • P1: {p1_count} tiket 🔴\n"
        f"   • P2: {p2_count} tiket 🟡\n\n"
        f"📋 <b>Status:</b>\n"
        f"   • Open: {open_count} tiket\n"
        f"   • Need Close: {need_close_count} tiket\n\n"
        f"🕐 <b>Cache Info:</b>\n"
        f"   • Last update: {info['last_update']}\n"
        f"   • Cache valid: {'✅ Ya' if info['cache_valid'] else '❌ Tidak'}\n"
        f"   • Expires in: {info['cache_expires_in']}s\n\n"
        f"<i>Gunakan /refresh untuk reload data dari Google Sheets</i>"
    )
    
    await send_reply(update, message, parse_mode=ParseMode.HTML)

# Fungsi untuk command /alarm
async def alarm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk menampilkan alarm update"""
    # Get summary stats
    open_count, need_close_count = sheet_reader.get_summary_stats()
    
    # Get NOP summary
    nop_summary = sheet_reader.format_region_summary()
    
    # Format message
    message = MessageFormatter.format_alarm_message(
        region="SUMBAGUT",
        open_count=open_count,
        need_close_count=need_close_count,
        nop_summary=nop_summary,
        tickets=[]
    )
    
    await send_reply(update, message, parse_mode=ParseMode.HTML)

# Fungsi untuk command /list
async def list_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk menampilkan pilihan NOP"""
    await prompt_list_menu(update, context)

async def prompt_list_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tampilkan pilihan NOP sebelum menampilkan list."""
    df = sheet_reader.df
    if df is None or df.empty:
        await send_reply(update, "Tidak ada data tiket. Gunakan /refresh untuk memuat data.")
        return

    nop_map = [
        ("ACH", "ACEH"),
        ("BJI", "BINJAI"),
        ("MDN", "MEDAN"),
        ("PMS", "PEMATANG SIANTAR"),
        ("PSP", "PADANG SIDEMPUAN"),
        ("RAP", "RANTAU PRAPAT"),
    ]

    lines = ["Kamu mau pilih NOVLI tampilkan yang mana?"]
    for code, name in nop_map:
        lines.append(f"{code} => {name}")
    lines.append("ALL => Semua NOP")

    rows = []
    for i, (code, _name) in enumerate(nop_map):
        button = InlineKeyboardButton(code, callback_data=f"list_code:{code}")
        if i % 2 == 0:
            rows.append([button])
        else:
            rows[-1].append(button)
    rows.append([InlineKeyboardButton("ALL", callback_data="list_all")])
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
    keyboard = InlineKeyboardMarkup(buttons)

    await send_reply(update, header + message, parse_mode=ParseMode.HTML, reply_markup=keyboard)

async def list_tickets_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk menampilkan semua tiket"""
    df = sheet_reader.df
    if df is None or df.empty:
        await send_reply(update, "Tidak ada data tiket. Gunakan /refresh untuk memuat data.")
        return

    tickets = df.to_dict("records")
    await _start_list(update, context, tickets, "Daftar Tiket H-1 (Data Valid)")

async def list_tickets_by_nop(update: Update, context: ContextTypes.DEFAULT_TYPE, nop_name: str):
    """Handler untuk menampilkan tiket per NOP"""
    grouped = sheet_reader.get_tickets_by_nop()
    tickets = grouped.get(nop_name, [])
    if not tickets:
        await send_reply(update, f"Tidak ada tiket untuk NOP {nop_name}.")
        return

    await _start_list(update, context, tickets, f"Daftar Tiket H-1 (NOP: {nop_name})")

# Fungsi untuk command /p1
async def p1_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk menampilkan tiket prioritas P1"""
    # Get P1 tickets
    df_p1 = sheet_reader.get_tickets_by_priority('P1')

    if df_p1.empty:
        await send_reply(update, "Tidak ada tiket P1 saat ini.")
        return

    # Convert to list of dicts
    tickets = df_p1.to_dict('records')

    # Format and send in chunks to avoid Telegram max length
    chunk_size = 20
    header = f"<b>Tiket Prioritas P1 H-1</b>\n<i>Total: {len(tickets)} tiket</i>\n\n"
    for i in range(0, len(tickets), chunk_size):
        chunk = tickets[i:i + chunk_size]
        message = MessageFormatter.format_ticket_list(chunk)
        prefix = header if i == 0 else "<i>Lanjutan...</i>\n\n"
        await send_reply(update, prefix + message, parse_mode=ParseMode.HTML)

# Fungsi untuk command /p2
async def p2_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk menampilkan tiket prioritas P2"""
    # Get P2 tickets
    df_p2 = sheet_reader.get_tickets_by_priority('P2')

    if df_p2.empty:
        await send_reply(update, "Tidak ada tiket P2 saat ini.")
        return

    # Convert to list of dicts
    tickets = df_p2.to_dict('records')

    # Format and send in chunks to avoid Telegram max length
    chunk_size = 20
    header = f"<b>Tiket Prioritas P2 H-1</b>\n<i>Total: {len(tickets)} tiket</i>\n\n"
    for i in range(0, len(tickets), chunk_size):
        chunk = tickets[i:i + chunk_size]
        message = MessageFormatter.format_ticket_list(chunk)
        prefix = header if i == 0 else "<i>Lanjutan...</i>\n\n"
        await send_reply(update, prefix + message, parse_mode=ParseMode.HTML)

# Fungsi untuk command /ticket
async def ticket_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk menampilkan detail tiket berdasarkan ID"""
    if not context.args:
        await send_reply(update, 
            "❌ Gunakan format: /ticket [TICKET_ID]\n"
            "Contoh: /ticket RAP39520260407"
        )
        return
    
    ticket_id = context.args[0]
    
    # Get ticket by ID
    ticket = sheet_reader.get_ticket_by_id(ticket_id)
    
    if not ticket:
        await send_reply(update, f"❌ Tiket dengan ID <code>{ticket_id}</code> tidak ditemukan.", parse_mode=ParseMode.HTML)
        return
    
    # Format and send
    message = MessageFormatter.format_ticket_detail(ticket)
    await send_reply(update, message, parse_mode=ParseMode.HTML)

# Fungsi untuk command /columns
async def show_columns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk menampilkan nama kolom di Google Sheets"""
    columns = sheet_reader.get_column_names()
    
    if not columns:
        await send_reply(update, "❌ Tidak dapat membaca kolom. Gunakan /refresh untuk memuat data.")
        return
    
    message = "<b>📋 Kolom di Google Sheets:</b>\n\n"
    for i, col in enumerate(columns, 1):
        message += f"{i}. <code>{col}</code>\n"
    
    await send_reply(update, message, parse_mode=ParseMode.HTML)

# Fungsi untuk pesan biasa
async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler untuk pesan biasa"""
    text = update.message.text.lower()
    
    if "halo" in text or "hai" in text:
        await send_reply(update, "👋 Hai! Ketik /help untuk melihat command yang tersedia.")
    elif "tiket" in text:
        await send_reply(update, "Untuk melihat tiket, gunakan /list atau /p1 atau /p2")
    else:
        await send_reply(update, f"Anda mengirim: {update.message.text}\n\nKetik /help untuk bantuan.")

def main():
    """Fungsi utama untuk menjalankan bot"""
    # Buat aplikasi bot
    app = Application.builder().token(TOKEN).build()
    
    # Load data pertama kali dengan filter H-1
    print("🔄 Memuat data dari Google Sheets...")
    sheet_reader.load_data(filter_h1=True)  # Pastikan filter H-1 aktif
    
    # Tambahkan handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("summary", show_summary))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("info", info_command))
    app.add_handler(CommandHandler("refresh", refresh))
    app.add_handler(CommandHandler("alarm", alarm))
    app.add_handler(CommandHandler("list", list_tickets))
    app.add_handler(CommandHandler("p1", p1_tickets))
    app.add_handler(CommandHandler("p2", p2_tickets))
    app.add_handler(CommandHandler("ticket", ticket_detail))
    app.add_handler(CommandHandler("columns", show_columns))
    app.add_handler(CallbackQueryHandler(handle_menu))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    
    # Jalankan bot
    print("🤖 Bot NOVLI V1.0 sedang berjalan...")
    print("✅ Tekan Ctrl+C untuk menghentikan bot")
    app.run_polling()

if __name__ == "__main__":
    main()
