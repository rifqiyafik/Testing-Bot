"""
Module untuk formatting pesan Telegram
"""
from typing import Dict, List
from datetime import datetime


class MessageFormatter:
    @staticmethod
    def _normalize_key(value: str) -> str:
        return "".join(ch for ch in value.lower() if ch.isalnum())

    @staticmethod
    def _get_value(ticket: Dict, keys: List[str], default: str = "N/A"):
        key_map = {MessageFormatter._normalize_key(str(k)): k for k in ticket.keys()}
        for key in keys:
            actual = key_map.get(MessageFormatter._normalize_key(key))
            if actual is None:
                continue
            value = ticket.get(actual)
            if value is None:
                continue
            value_str = str(value).strip()
            if value_str == "" or value_str.lower() == "nan":
                continue
            return value
        return default

    @staticmethod
    def _parse_date_to_yyyymmdd(value) -> str:
        if value is None:
            return ""
        if isinstance(value, datetime):
            return value.strftime("%Y%m%d")
        value_str = str(value).strip()
        if value_str == "":
            return ""
        for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y"):
            try:
                return datetime.strptime(value_str, fmt).strftime("%Y%m%d")
            except ValueError:
                continue
        digits = "".join(ch for ch in value_str if ch.isdigit())
        if len(digits) == 8:
            return digits
        return ""

    @staticmethod
    def format_alarm_message(region: str, open_count: int, need_close_count: int,
                             nop_summary: str, tickets: List[Dict]) -> str:
        """
        Bot NOVLI V1.0
        24-06-2025 08:51
        Update_FEGE_Alarm OPEN
        """
        current_time = datetime.now().strftime("%d-%m-%Y %H:%M")

        message = "<b>Bot NOVLI V1.0</b>\n"
        message += f"<i>{current_time}</i>\n"
        message += "<b>Update_FEGE_Alarm OPEN</b>\n\n"
        message += "<blockquote><i>Bot NOVLI V1.0:</i>\n"
        message += "[<i>Region:</i> <a href='#tiket_open'>#tiket_open</a>/<a href='#tiket_Need_close'>#tiket_Need_close</a>]\n"
        message += f"<b>SUMBAGUT</b> : {open_count} Site / {need_close_count} Site\n\n"
        message += "[<i>NOP:</i> <a href='#tiket_open'>#tiket_open</a>/<a href='#tiket_Need_close'>#tiket_Need_close</a>]\n"
        message += nop_summary
        message += "</blockquote>"

        return message

    @staticmethod
    def format_ticket_list(tickets: List[Dict]) -> str:
        """
        TiketID|Prio|Aging|BW|TrafMax|StatusUpdate|Status
        RAP40020250211|P2|132|800|203.42|NeedClose|Open
        """
        if not tickets:
            return "Tidak ada tiket"

        message = "<b>TiketID|Prio|Aging|BW|TrafMax|StatusUpdate|Status</b>\n"

        for ticket in tickets:
            ticket_id = MessageFormatter._get_value(ticket, ["TiketID", "TicketID"], default="")
            if ticket_id == "":
                site_id = MessageFormatter._get_value(ticket, ["SiteID", "SITEID", "Site Id"], default="")
                date_value = MessageFormatter._get_value(ticket, ["Date", "DATE", "Tanggal"], default="")
                date_part = MessageFormatter._parse_date_to_yyyymmdd(date_value)
                if site_id and date_part:
                    ticket_id = f"{site_id}{date_part}"
                else:
                    ticket_id = "N/A"

            prio = MessageFormatter._get_value(ticket, ["Prio", "Priority"])
            aging = MessageFormatter._get_value(ticket, ["Aging", "Count of >0.9"])
            bw = MessageFormatter._get_value(ticket, ["BW", "Bw"])
            traf_max = MessageFormatter._get_value(ticket, ["TrafMax", "Traf Max", "Max Ethernet Port Daily"])
            status_update = MessageFormatter._get_value(
                ticket,
                ["StatusUpdate", "StatusUpdate", "Status Update"],
                default="Open",
            )
            status = MessageFormatter._get_value(ticket, ["Status"], default="Open")

            message += f"<code>{ticket_id}</code>|{prio}|{aging}|{bw}|{traf_max}|{status_update}|{status}\n"

        return message

    @staticmethod
    def format_ticket_detail(ticket: Dict) -> str:
        """
        Format detail tiket untuk edit
        Format:
        tiketid : RAP39520250407
        status : closed
        updatetanggal : 24062025
        closedby : hilmifaww
        """
        if not ticket:
            return "Tiket tidak ditemukan"

        ticket_id = ticket.get("TiketID", ticket.get("TicketID", "N/A"))
        status = ticket.get("Status", "N/A")
        update_date = ticket.get("Updatetanggal", ticket.get("UpdateTanggal", "N/A"))
        closed_by = ticket.get("closedby", ticket.get("ClosedBy", "N/A"))
        status_update = ticket.get("StatusUpdate", ticket.get("StatusUpdate", "N/A"))
        note = ticket.get("Note", "N/A")
        date_open = ticket.get("DateOpen", "N/A")
        aging = ticket.get("Aging", "N/A")

        message = "<b>Detail Tiket</b>\n\n"
        message += f"<b>tiketid</b> : <code>{ticket_id}</code>\n"
        message += f"<b>status</b> : {status}\n"
        message += f"<b>statusupdate</b> : {status_update}\n"
        message += f"<b>dateopen</b> : {date_open}\n"
        message += f"<b>aging</b> : {aging}\n"
        message += f"<b>updatetanggal</b> : {update_date}\n"
        message += f"<b>closedby</b> : {closed_by}\n"
        message += f"<b>note</b> : {note}\n"

        return message


    @staticmethod
    def format_history_rows(rows: List[Dict], title: str) -> str:
        if not rows:
            return "Tidak ada data history"

        lines = [f"<b>{title}</b>"]
        for row in rows:
            ticket_id = MessageFormatter._get_value(row, ["TiketID", "TicketID"], default="N/A")
            site_id = MessageFormatter._get_value(row, ["SITEID", "SiteID"], default="N/A")
            date_open = MessageFormatter._get_value(row, ["DateOpen", "DATE"], default="N/A")
            update_date = MessageFormatter._get_value(row, ["Updatetanggal", "UpdateTanggal"], default="N/A")
            status = MessageFormatter._get_value(row, ["Status"], default="N/A")
            status_update = MessageFormatter._get_value(row, ["StatusUpdate"], default="N/A")
            priority = MessageFormatter._get_value(row, ["Priority", "Prio"], default="N/A")
            suspect = MessageFormatter._get_value(row, ["Suspect"], default="N/A")
            note = MessageFormatter._get_value(row, ["Note"], default="-")
            closed_by = MessageFormatter._get_value(row, ["closedby", "ClosedBy"], default="-")
            lines.append(
                "<blockquote>"
                f"TiketID: <code>{ticket_id}</code>\n"
                f"SITEID: {site_id}\n"
                f"Priority: {priority}\n"
                f"Status: {status}\n"
                f"StatusUpdate: {status_update}\n"
                f"DateOpen: {date_open}\n"
                f"UpdateTanggal: {update_date}\n"
                f"ClosedBy: {closed_by}\n"
                f"Note: {note}\n"
                f"Suspect: {suspect}"
                "</blockquote>"
            )
        return "\n".join(lines)

    @staticmethod
    def format_help_message() -> str:
        """Format pesan bantuan"""
        message = "<b>Bot NOVLI V1.0 - Panduan Penggunaan</b>\n\n"
        message += "<b>Command yang tersedia:</b>\n\n"
        message += "/start - Mulai bot + tampilkan menu\n"
        message += "/summary - Tampilkan ringkasan tiket\n"
        message += "/help - Tampilkan panduan ini\n"
        message += "/info - Statistik data\n"
        message += "/alarm - Tampilkan alarm update tiket\n"
        message += "/list - Tampilkan daftar tiket\n"
        message += "/p1 - Tampilkan tiket prioritas P1\n"
        message += "/p2 - Tampilkan tiket prioritas P2\n"
        message += "/ticket [ID] - Tampilkan detail tiket\n"
        message += "/sync - Sinkronisasi data ke database\n"
        message += "/import - Import data dari file (staging replika)\n"
        message += "/close [ID] [NOTE] - Tutup tiket\n"
        message += "/columns - Lihat nama kolom di sheet\n\n"
        message += "<b>Info Penting:</b>\n"
        message += "- Data diambil dari NOVLI Global\n"
        message += "- Hanya tiket dengan status Open yang ditampilkan\n"
        message += "- Gunakan /sync untuk update data\n\n"
        message += "<i>Data diambil dari Google Sheets secara otomatis</i>"

        return message
