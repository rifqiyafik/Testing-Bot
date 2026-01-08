# Ringkasan Program Bot NOVLI R01

Dokumen ini menjelaskan alur kerja dan fungsi utama di dalam program bot.

## Arsitektur Singkat
- `main.py`: entry point bot Telegram dan semua handler command/menu.
- `sheet_reader.py`: mengambil, memfilter, dan menyiapkan data dari Google Sheets.
- `message_formatter.py`: memformat pesan agar rapi dan konsisten.

## main.py

### 1) Inisialisasi
- Memuat environment (`TELEGRAM_TOKEN`, `GOOGLE_SHEET_URL`).
- Membuat instance `SheetReader` untuk akses data.
- Menyediakan reply keyboard (tombol di samping chat) melalui `InlineKeyboardMarkup`.

### 2) Inline Menu
- `get_main_keyboard()` membuat tombol seperti Summary, List, P1, P2, Refresh, dll.
- `handle_menu()` menangani klik tombol (callback data) dan memanggil handler yang sesuai.

### 3) Helper Reply
- `send_reply()` menyatukan cara reply dari command biasa dan callback button.

### 4) Command Utama
- `start()`: menampilkan daftar fitur + tombol menu.
- `show_summary()`: menampilkan ringkasan tiket H-1 (total, P1/P2, open/need close, NOP breakdown).
- `help_command()`: panduan penggunaan dan tombol menu.
- `import_command()` + `import_document()`: menerima file Excel/CSV, validasi header, simpan ke replika, lalu konfirmasi sync ke NOVLI Global.
- `refresh()`: meminta konfirmasi refresh (Ya/Tidak) sebelum reload data.
- `run_refresh()`: menjalankan reload dengan tombol batalkan (window 5 detik).
- `info_command()`: statistik data dan cache.
- `alarm()`: menampilkan alarm ringkasan (format blockquote).
- `list_tickets()`: menampilkan daftar tiket (dibatasi 20) dengan header/total.
- `p1_tickets()` dan `p2_tickets()`: menampilkan tiket P1/P2 per 20 baris untuk menghindari batas panjang pesan.
- `ticket_detail()`: detail tiket berdasarkan ID.
- `show_columns()`: menampilkan nama kolom dari sheet.
- `echo()`: balasan sederhana untuk pesan biasa.

### 5) Menjalankan Bot
- `main()` membuat application, memuat data awal, mendaftarkan handler, lalu `run_polling()`.

## sheet_reader.py

### 1) Ambil Data + Cache
- `load_data()` membaca CSV dari Google Sheets, menyimpan cache 5 menit, dan mencatat waktu load.

### 2) Filter dan Validasi
- `_filter_and_clean_data()`:
  - Validasi `TiketID` (tidak kosong).
  - Buang `Transport Type` yang `FO TSEL` atau kosong.
  - Filter prioritas hanya `P1`/`P2`.
  - Filter tanggal H-1 (kemarin).
  - Menambahkan kolom turunan melalui `_ensure_derived_columns()`.

### 3) Filter Tanggal
- `_filter_by_date()` memilih kolom tanggal yang paling banyak match H-1, parsing `MM/DD/YY`.

### 4) Kolom Turunan
- `_ensure_derived_columns()` membuat:
  - `TiketID` = `SITEID + DATE(YYYYMMDD)`
  - `Prio` dari `Priority`
  - `Aging` dari `Count of >0.9`
  - `TrafMax` dari `Max Ethernet Port Daily`
  - `NeedClose` dari `Suspect`
  - `Status` default `Open`

### 5) Statistik
- `get_summary_stats()`:
  - `open_count` = total P1 + P2
  - `need_close_count` = jumlah P1

### 6) Grouping
- `get_tickets_by_nop()` grouping per NOP (nilai kosong jadi `Unknown`).
- `format_region_summary()` membuat ringkasan per NOP (Total / P1).

### 7) Utilitas
- `get_tickets_by_priority()`, `get_ticket_by_id()`, `get_column_names()`, `get_data_info()`.

## message_formatter.py

### 1) Helper
- `_get_value()` dan `_parse_date_to_yyyymmdd()` untuk membaca nilai kolom fleksibel.

### 2) Formatter
- `format_alarm_message()` menyiapkan tampilan alarm.
- `format_ticket_list()` membuat tabel TiketID|Prio|Aging|BW|TrafMax|NeedClose|Status.
- `format_ticket_detail()` menampilkan detail tiket untuk edit.
- `format_help_message()` menampilkan panduan command.

## Catatan Operasional
- Cache data 5 menit, `/refresh` untuk reload manual.
- Jika tombol menu tidak responsif, pastikan handler `CallbackQueryHandler` aktif.
- Jika `Message is too long`, output sudah dipecah per 20 tiket di `/p1` dan `/p2`.


## Menu di chat

- Bot menampilkan Reply Keyboard via /start atau /menu.
- Tombol bersifat one-time dan hilang setelah dipilih.


## Tombol

- Menu utama menggunakan Reply Keyboard (tombol di samping chat).
- Tombol inline hanya dipakai untuk list/pagination, dan akan dihapus setelah ditekan.
