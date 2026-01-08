"""
Module untuk membaca dan memproses data dari Google Sheets
"""
import io
import logging
import urllib.parse
import urllib.request
import pandas as pd
from typing import Dict, List, Tuple, Optional
from datetime import datetime, timedelta
from sheet_sync import read_database_df

logger = logging.getLogger("novli_bot")


class SheetReader:
    def __init__(
        self,
        sheet_url: Optional[str],
        global_sheet_id: Optional[str] = None,
        global_tab: Optional[str] = None,
        credentials_path: Optional[str] = None,
    ):
        self.sheet_url = sheet_url
        self.global_sheet_id = global_sheet_id
        self.global_tab = global_tab
        self.credentials_path = credentials_path
        self.df = None
        self.df_raw = None  # Simpan raw data
        self.last_load_time = None
        self.cache_duration = 300  # Cache selama 5 menit (dalam detik)
        self.last_date_filter_target = None
        self.last_date_filter_found = None

    def load_data(self, force_reload: bool = False, filter_h1: bool = True) -> pd.DataFrame:
        """
        Membaca data dari Google Sheets dengan caching dan filtering
        
        Args:
            force_reload: Paksa reload data meskipun ada cache
            filter_h1: Filter data untuk H-1 (kemarin) saja
        """
        # Cek apakah perlu reload berdasarkan cache
        if not force_reload and self.df is not None and self.last_load_time:
            time_elapsed = (datetime.now() - self.last_load_time).total_seconds()
            if time_elapsed < self.cache_duration:
                logger.info("Using cache (expires in %ss)", int(self.cache_duration - time_elapsed))
                self.df = self._ensure_derived_columns(self.df)
                return self.df

        try:
            import time
            start_time = time.monotonic()
            if self.global_sheet_id and self.credentials_path and self.global_tab:
                logger.info("Loading NOVLI Global: sheet_id=%s tab=%s", self.global_sheet_id, self.global_tab)
                self.df_raw = read_database_df(
                    self.credentials_path,
                    self.global_sheet_id,
                    self.global_tab,
                )
            else:
                logger.info("Loading source Google Sheet")
                csv_url = self._build_csv_url(self.sheet_url)
                self.df_raw = self._read_csv_url(csv_url)
            load_seconds = time.monotonic() - start_time
            logger.info("Load time: %.2fs", load_seconds)
            logger.info("Rows loaded: %s", len(self.df_raw))

            # Filter dan clean data
            self.df = self._filter_and_clean_data(self.df_raw, filter_h1=filter_h1)

            self.last_load_time = datetime.now()
            logger.info("Filtered rows: %s", len(self.df))

            return self.df
        except Exception as e:
            print(f"? Error membaca Google Sheets: {e}")
            return pd.DataFrame()

    def _build_csv_url(self, sheet_url: str) -> str:
        """Normalize Google Sheets URL into CSV export URL."""
        if not sheet_url:
            return sheet_url
        if "docs.google.com/spreadsheets/d/" not in sheet_url:
            return sheet_url
        if "export?format=csv" in sheet_url:
            return sheet_url

        parsed = urllib.parse.urlparse(sheet_url)
        path_parts = parsed.path.split("/")
        try:
            doc_index = path_parts.index("d") + 1
            sheet_id = path_parts[doc_index]
        except (ValueError, IndexError):
            return sheet_url

        gid = None
        query = urllib.parse.parse_qs(parsed.query)
        if "gid" in query and query["gid"]:
            gid = query["gid"][0]
        if not gid and parsed.fragment:
            frag = urllib.parse.parse_qs(parsed.fragment)
            if "gid" in frag and frag["gid"]:
                gid = frag["gid"][0]

        csv_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
        if gid:
            csv_url += f"&gid={gid}"
        return csv_url

    def _read_csv_url(self, url: str) -> pd.DataFrame:
        """Read CSV from URL, with HTML guard."""
        # Add cache busting to avoid stale CSV responses
        if "docs.google.com/spreadsheets/d/" in url and "export?format=csv" in url:
            cache_bust = int(datetime.now().timestamp())
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}cachebust={cache_bust}"
        try:
            with urllib.request.urlopen(url) as response:
                data = response.read()
        except Exception as e:
            raise RuntimeError(f"Gagal mengakses URL CSV: {e}") from e

        text = data.decode("utf-8", errors="replace")
        head = text[:200].lower()
        if "<!doctype html" in head or "<html" in head:
            raise RuntimeError(
                "URL tidak mengembalikan CSV. Pastikan link memakai export?format=csv "
                "atau publish sheet ke web."
            )
        try:
            return pd.read_csv(io.StringIO(text))
        except Exception:
            # Fallback for inconsistent rows
            return pd.read_csv(
                io.StringIO(text),
                engine="python",
                on_bad_lines="skip",
            )
    
    def _filter_and_clean_data(self, df: pd.DataFrame, filter_h1: bool = True) -> pd.DataFrame:
        """
        Filter dan clean data:
        1. Remove entries dengan TiketID = N/A atau kosong
        2. Filter Transport Type hanya 'FO TSEL' atau kosong
        3. Filter hanya P1 dan P2
        4. Filter untuk H-1 (kemarin) jika diminta
        """
        if df.empty:
            return df
        
        df_filtered = df.copy()
        
        # 1. Filter: Hapus data dengan TiketID = N/A atau kosong
        if 'TiketID' in df_filtered.columns:
            df_filtered = df_filtered[
                (df_filtered['TiketID'].notna()) & 
                (df_filtered['TiketID'] != 'N/A') &
                (df_filtered['TiketID'].astype(str).str.strip() != '')
            ]
            print(f"   ├─ Setelah filter TiketID valid: {len(df_filtered)} baris")
        
        # 2. Filter: Buang Transport Type 'FO TSEL' atau kosong
        transport_col = None
        for col in df_filtered.columns:
            normalized = "".join(ch for ch in col.lower() if ch.isalnum())
            if normalized == "transporttype":
                transport_col = col
                break
        if transport_col:
            transport_series = df_filtered[transport_col].astype(str).str.strip()
            df_filtered = df_filtered[
                (df_filtered[transport_col].notna()) &
                (transport_series != "") &
                (transport_series.str.upper() != "FO TSEL")
            ]
            print(f"   - Setelah buang Transport Type (FO TSEL/blank): {len(df_filtered)} baris")

        # 3. Filter: Hanya ambil P1 dan P2
        prio_col = None
        if 'Prio' in df_filtered.columns:
            prio_col = 'Prio'
        elif 'Priority' in df_filtered.columns:
            prio_col = 'Priority'
        if prio_col:
            df_filtered = df_filtered[
                df_filtered[prio_col].isin(['P1', 'P2'])
            ]
            print(f"   - Setelah filter P1/P2: {len(df_filtered)} baris")

        # 4. Filter: Data H-1 (kemarin)
        if filter_h1:
            df_filtered = self._filter_by_date(df_filtered, days_ago=1)

        df_filtered = self._ensure_derived_columns(df_filtered)

        return df_filtered
    
    def _filter_by_date(self, df: pd.DataFrame, days_ago: int = 1) -> pd.DataFrame:
        """
        Filter data untuk H-1 (kemarin) atau beberapa hari sebelumnya
        Cari kolom tanggal dan filter berdasarkan days_ago
        """
        if df.empty:
            return df
        
        # Cari kolom tanggal (biasanya bernama 'Date', 'Tanggal', 'CreatedDate', dll)
        date_columns = []
        for col in df.columns:
            col_lower = col.lower()
            if any(keyword in col_lower for keyword in ['date', 'tanggal', 'created', 'update']):
                date_columns.append(col)
        
        if not date_columns:
            print("   ⚠️ Tidak ada kolom tanggal ditemukan, skip filter H-1")
            self.last_date_filter_target = None
            self.last_date_filter_found = None
            return df

        target_date = datetime.now().date() - timedelta(days=days_ago)
        self.last_date_filter_target = target_date
        best_col = None
        best_count = -1
        best_series = None

        for col in date_columns:
            try:
                series = pd.to_datetime(df[col], errors='coerce', dayfirst=False)
                count = int((series.dt.date == target_date).sum())
                if count > best_count:
                    best_count = count
                    best_col = col
                    best_series = series
            except Exception:
                continue

        if best_col is None:
            print("   ⚠️ Tidak ada kolom tanggal yang valid, skip filter H-1")
            self.last_date_filter_found = None
            return df

        print(f"   ├─ Menggunakan kolom tanggal: '{best_col}'")

        if best_count <= 0 or best_series is None:
            print(f"   ⚠️ Tidak ada data H-{days_ago} ({target_date}) pada kolom tanggal")
            self.last_date_filter_found = False
            return df.iloc[0:0].copy()

        df_target = df[best_series.dt.date == target_date]
        print(f"   ├─ Setelah filter H-{days_ago} ({target_date}): {len(df_target)} baris")
        self.last_date_filter_found = True
        return df_target

    def filter_by_days_ago(self, days_ago: int) -> pd.DataFrame:
        """Filter ulang data raw berdasarkan days_ago tanpa reload dari URL."""
        if self.df_raw is None or self.df_raw.empty:
            return pd.DataFrame()
        base = self._filter_and_clean_data(self.df_raw, filter_h1=False)
        df_filtered = self._filter_by_date(base, days_ago=days_ago)
        self.df = self._ensure_derived_columns(df_filtered)
        self.last_load_time = datetime.now()
        return self.df

    def _ensure_derived_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df

        df_out = df
        needs_copy = False

        def get_column_by_normalized(name: str) -> Optional[str]:
            target = "".join(ch for ch in name.lower() if ch.isalnum())
            for col in df_out.columns:
                normalized = "".join(ch for ch in col.lower() if ch.isalnum())
                if normalized == target:
                    return col
            return None

        prio_col = get_column_by_normalized("prio")
        if prio_col is None:
            priority_col = get_column_by_normalized("priority")
            if priority_col:
                if not needs_copy:
                    df_out = df_out.copy()
                    needs_copy = True
                df_out["Prio"] = df_out[priority_col]

        if "Aging" not in df_out.columns:
            count_col = get_column_by_normalized("countof09")
            if count_col:
                if not needs_copy:
                    df_out = df_out.copy()
                    needs_copy = True
                df_out["Aging"] = df_out[count_col]

        if "TrafMax" not in df_out.columns:
            traf_col = get_column_by_normalized("maxethernetportdaily")
            if traf_col:
                if not needs_copy:
                    df_out = df_out.copy()
                    needs_copy = True
                df_out["TrafMax"] = df_out[traf_col]

        if "NeedClose" not in df_out.columns:
            suspect_col = get_column_by_normalized("suspect")
            if suspect_col:
                if not needs_copy:
                    df_out = df_out.copy()
                    needs_copy = True
                df_out["NeedClose"] = df_out[suspect_col]

        if "Status" not in df_out.columns:
            if not needs_copy:
                df_out = df_out.copy()
                needs_copy = True
            df_out["Status"] = "Open"

        if "TiketID" not in df_out.columns:
            site_col = get_column_by_normalized("siteid")
            date_col = get_column_by_normalized("date")
            if site_col and date_col:
                if not needs_copy:
                    df_out = df_out.copy()
                    needs_copy = True
                site_series = df_out[site_col].astype(str).str.strip()
                date_series = pd.to_datetime(df_out[date_col], errors="coerce", dayfirst=False)
                date_str = date_series.dt.strftime("%Y%m%d").fillna("")
                ticket_id = (site_series + date_str).where((site_series != "") & (date_str != ""))
                df_out["TiketID"] = ticket_id

        return df_out
    
    def get_tickets_by_nop(self) -> Dict[str, List[Dict]]:
        """Mengelompokkan tiket berdasarkan NOP (region)"""
        if self.df is None or self.df.empty:
            return {}

        nop_col = None
        for col in self.df.columns:
            normalized = "".join(ch for ch in col.lower() if ch.isalnum())
            if normalized == "nop":
                nop_col = col
                break
        
        grouped = {}
        
        for _, row in self.df.iterrows():
            ticket_data = row.to_dict()
            raw_nop = ticket_data.get(nop_col, ticket_data.get('NOP', 'Unknown'))
            if pd.isna(raw_nop) or str(raw_nop).strip() == "":
                nop = "Unknown"
            else:
                nop = str(raw_nop).strip()
            
            if nop not in grouped:
                grouped[nop] = []
            grouped[nop].append(ticket_data)
        
        return grouped
    
    def get_summary_stats(self) -> Tuple[int, int]:
        """
        Mendapatkan statistik tiket
        - open_count: Total tiket P1 + P2 (semua tiket yang valid)
        - need_close_count: Total tiket P1
        """
        if self.df is None or self.df.empty:
            return 0, 0

        # Total tiket open = semua tiket P1 dan P2 yang sudah difilter
        open_count = len(self.df)

        # Need close = tiket prioritas P1
        if 'Prio' in self.df.columns:
            need_close_count = len(self.df[self.df['Prio'] == 'P1'])
        elif 'Priority' in self.df.columns:
            need_close_count = len(self.df[self.df['Priority'] == 'P1'])
        else:
            need_close_count = 0

        return open_count, need_close_count

    def get_tickets_by_priority(self, priority: str) -> pd.DataFrame:
        """Filter tiket berdasarkan prioritas (P1 atau P2)"""
        if self.df is None or self.df.empty:
            return pd.DataFrame()
        
        if 'Prio' in self.df.columns:
            return self.df[self.df['Prio'] == priority]
        elif 'Priority' in self.df.columns:
            return self.df[self.df['Priority'] == priority]
        
        return pd.DataFrame()
    
    def get_ticket_by_id(self, ticket_id: str) -> Dict:
        """Mendapatkan detail tiket berdasarkan ID"""
        if self.df is None or self.df.empty:
            return {}
        
        # Cari di kolom TiketID
        if 'TiketID' in self.df.columns:
            ticket = self.df[self.df['TiketID'] == ticket_id]
            if not ticket.empty:
                return ticket.iloc[0].to_dict()
        
        # Fallback: cari kolom lain yang mengandung 'ID'
        for col in self.df.columns:
            if 'id' in col.lower():
                ticket = self.df[self.df[col] == ticket_id]
                if not ticket.empty:
                    return ticket.iloc[0].to_dict()
        
        return {}
    
    def format_region_summary(self) -> str:
        """
        Format summary berdasarkan region/NOP
        Format: NOP : X Site / Y Site
        X = Total tiket di NOP tersebut (P1 + P2)
        Y = Total tiket P1 di NOP tersebut
        """
        if self.df is None or self.df.empty:
            return "Tidak ada data"

        summary = []
        grouped = self.get_tickets_by_nop()

        for nop, tickets in sorted(grouped.items(), key=lambda item: item[0].lower()):
            # Total tiket di NOP ini
            total_count = len(tickets)

            # Need close = tiket P1
            need_close_count = sum(
                1 for t in tickets
                if str(t.get('Prio', t.get('Priority', ''))).upper() == 'P1'
            )

            summary.append(f"{nop} : {total_count} Site / {need_close_count} Site")

        return "\n".join(summary)

    def get_column_names(self) -> List[str]:
        """Mendapatkan nama-nama kolom di sheet"""
        if self.df is None or self.df.empty:
            return []
        return list(self.df.columns)
    
    def get_data_info(self) -> Dict:
        """Mendapatkan informasi statistik data"""
        info = {
            'total_raw': len(self.df_raw) if self.df_raw is not None else 0,
            'total_filtered': len(self.df) if self.df is not None else 0,
            'last_update': self.last_load_time.strftime('%Y-%m-%d %H:%M:%S') if self.last_load_time else 'Never',
            'cache_valid': False
        }
        
        if self.last_load_time:
            time_elapsed = (datetime.now() - self.last_load_time).total_seconds()
            info['cache_valid'] = time_elapsed < self.cache_duration
            info['cache_expires_in'] = max(0, int(self.cache_duration - time_elapsed))
        
        return info
