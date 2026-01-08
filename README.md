# Bot Telegram NOVLI R01 🤖

Bot Telegram untuk mengelola dan memantau tiket dengan data dari Google Sheets.

## Features

✅ **Membaca data dari Google Sheets** - Data diambil langsung dan auto-update
✅ **Filter berdasarkan Prioritas** - Tampilkan tiket P1 atau P2
✅ **Alarm Update** - Notifikasi dengan breakdown regional
✅ **Detail Tiket** - Lihat informasi lengkap tiket
✅ **List Tiket** - Tampilkan semua tiket dengan format rapi

## Setup

### 0. Buat Environment Lokal

```bash
python -m venv env
```

Aktifkan environment:

```bash
# PowerShell (Windows)
.\env\Scripts\Activate.ps1
```

```bash
# CMD (Windows)
.\env\Scripts\activate.bat
```

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Konfigurasi Google Sheets

**PENTING:** Google Sheets harus bisa diakses secara public atau menggunakan service account.

#### Opsi A: Public Access (Recommended untuk testing)

1. Buka Google Sheets Anda
2. Klik **Share** > **Change to anyone with the link**
3. Pastikan akses minimal **Viewer**

#### Opsi B: Service Account (Untuk production)

1. Buat Service Account di Google Cloud Console
2. Download JSON credentials
3. Share Google Sheets ke email service account
4. Update `sheet_reader.py` untuk menggunakan credentials

### 3. Jalankan Bot

```bash
python main.py
```

## Commands

| Command             | Deskripsi                         |
| ------------------- | --------------------------------- |
| `/start`            | Mulai bot                         |
| `/help`             | Tampilkan panduan                 |
| `/alarm`            | Tampilkan alarm update tiket      |
| `/list`             | Tampilkan semua tiket             |
| `/p1`               | Tampilkan tiket prioritas P1      |
| `/p2`               | Tampilkan tiket prioritas P2      |
| `/ticket [ID]`      | Tampilkan detail tiket            |
| `/import`           | Import data dari file (Excel/CSV) |
| `/close`            | Tutup tiket                       |
| `/columns`          | Lihat nama kolom di sheet         |
| `/history [SITEID]` | Riwayat per site                  |
| `/historyid [ID]`   | Riwayat per tiket                 |
| `/menu`             | Tampilkan tombol menu             |

## File Structure

```
NOVLI/
main.py                  # Main bot application
sheet_reader.py          # Google Sheets integration
message_formatter.py     # Message formatting
requirements.txt         # Dependencies
.env                     # Environment variables
PROGRAM_SUMMARY.md       # Ringkasan fungsi program
```

## Environment Variables

Edit file `.env`:

```
TELEGRAM_TOKEN=your_telegram_bot_token
GOOGLE_SHEET_URL=https://docs.google.com/spreadsheets/.../export?format=csv&gid=...
GOOGLE_SERVICE_ACCOUNT_FILE=env/credentials.json
SOURCE_SHEET_ID=your_source_spreadsheet_id
SOURCE_SHEET_TAB=your_source_tab_name
GLOBAL_SHEET_ID=your_global_spreadsheet_id
GLOBAL_SHEET_TAB_DATABASE=DATABASE
GLOBAL_SHEET_TAB_HISTORY=HISTORY
GLOBAL_SHEET_TAB_UPDATEDAILY=UPDATEDAILY
SYNC_TIMEZONE=Asia/Jakarta
```

## Troubleshooting

### Error 401 Unauthorized

Jika mendapat error ini, berarti Google Sheets tidak bisa diakses. Solusi:

1. **Make sheet public:**
   - Buka Google Sheets
   - Klik Share > Anyone with the link > Viewer
2. **Atau gunakan Google Sheets API:**
   - Install: `pip install gspread oauth2client`
   - Setup service account credentials
   - Update `sheet_reader.py`

### Kolom Tidak Ditemukan

Gunakan command `/columns` untuk melihat nama kolom yang tersedia di Google Sheets Anda, lalu sesuaikan mapping di `sheet_reader.py`.

## Customization

### Sesuaikan Nama Kolom

Edit `sheet_reader.py` dan sesuaikan nama kolom dengan Google Sheets Anda:

```python
# Contoh di fungsi get_tickets_by_priority
if 'Prio' in self.df.columns:  # Ganti 'Prio' dengan nama kolom Anda
    return self.df[self.df['Prio'] == priority]
```

### Format Pesan

Edit `message_formatter.py` untuk customize format pesan yang dikirim bot.

## Support

Jika ada pertanyaan atau issue, silakan hubungi developer.

---

**Made with ❤️ for NOVLI R01**

## Menu di Chat

Gunakan /menu atau /start untuk menampilkan tombol menu di samping chat.
Tombol akan hilang setelah dipilih (one-time keyboard).

## Menu Button

Bot akan menampilkan tombol Menu di samping kolom chat melalui daftar command Telegram.
Jika belum muncul, kirim /start atau buka kembali chat bot.

## Import File

Gunakan `/import` lalu kirim file Excel/CSV dengan header yang sama seperti sheet replika.
Bot akan memvalidasi header sebelum menjalankan sync.
Data akan disimpan ke sheet replika terlebih dahulu, lalu bot akan meminta konfirmasi untuk sync ke NOVLI Global.
