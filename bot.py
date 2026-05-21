import os
import json
import asyncio
import requests
import random
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

# ── Config ──────────────────────────────────────────────
BOT_TOKEN   = os.environ["BOT_TOKEN"]
OWNER_ID    = int(os.environ["OWNER_CHAT_ID"])
DATA_FILE   = "customers.json"
NPC_URL     = "https://cskh.npc.com.vn/DichVuTTCSKH/DichVuTTCSKHNPC"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]

# ── Proxy helpers ────────────────────────────────────────
def fetch_proxies() -> list[str]:
    """Cào proxy VN miễn phí từ proxifly."""
    proxies = []
    try:
        r = requests.get(
            "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/countries/VN/data.json",
            timeout=10
        )
        for p in r.json():
            proxies.append(f"http://{p['ip']}:{p['port']}")
    except Exception:
        pass
    return proxies

def get_working_proxy(proxies: list[str], test_url="https://httpbin.org/ip") -> dict | None:
    random.shuffle(proxies)
    for proxy in proxies[:10]:          # thử tối đa 10 proxy
        try:
            r = requests.get(test_url, proxies={"http": proxy, "https": proxy}, timeout=5)
            if r.status_code == 200:
                return {"http": proxy, "https": proxy}
        except Exception:
            continue
    return None

# ── NPC scraper ──────────────────────────────────────────
def fetch_lich_cat_dien(ma_kh: str) -> str:
    proxies_list = fetch_proxies()
    proxy        = get_working_proxy(proxies_list)

    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://cskh.npc.com.vn/",
    }

    params = {"index": "7", "MaKhachHang": ma_kh}

    try:
        resp = requests.get(
            NPC_URL,
            params=params,
            headers=headers,
            proxies=proxy,
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as e:
        return f"❌ Không thể kết nối trang NPC: {e}"

    # Parse HTML đơn giản — tìm bảng lịch cắt điện
    from html.parser import HTMLParser

    class TableParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.in_table = False
            self.rows, self.current_row, self.current_cell = [], [], []
            self.depth = 0

        def handle_starttag(self, tag, attrs):
            attrs_dict = dict(attrs)
            if tag == "table" and "lich" in attrs_dict.get("id", "").lower():
                self.in_table = True
            if self.in_table:
                if tag == "tr": self.current_row = []
                if tag in ("td", "th"): self.current_cell = []

        def handle_endtag(self, tag):
            if self.in_table:
                if tag in ("td", "th"):
                    self.current_row.append(" ".join(self.current_cell).strip())
                if tag == "tr" and self.current_row:
                    self.rows.append(self.current_row)
                if tag == "table": self.in_table = False

        def handle_data(self, data):
            if self.in_table: self.current_cell.append(data.strip())

    parser = TableParser()
    parser.feed(resp.text)

    if not parser.rows:
        # Fallback: tìm text chứa từ khoá
        if "không có lịch" in resp.text.lower():
            return "✅ Không có lịch cắt điện trong thời gian tới."
        return "⚠️ Không tìm thấy bảng lịch cắt điện. Trang có thể thay đổi cấu trúc."

    lines = [f"⚡ *Lịch cắt điện – Mã KH: {ma_kh}*\n"]
    for row in parser.rows:
        lines.append(" | ".join(row))
    return "\n".join(lines)

# ── Lưu/đọc danh sách khách hàng ─────────────────────────
def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_data(data: dict):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ── Telegram handlers ─────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Xin chào! Tôi là bot tra lịch cắt điện NPC.\n\n"
        "• /them <mã_KH>  — Thêm mã khách hàng\n"
        "• /xoa <mã_KH>   — Xoá mã khách hàng\n"
        "• /xem           — Xem danh sách mã đang theo dõi\n"
        "• /tra <mã_KH>   — Tra thủ công ngay bây giờ"
    )

async def cmd_them(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Dùng: /them <mã_KH>"); return
    ma = ctx.args[0].strip().upper()
    data = load_data()
    chat_id = str(update.effective_chat.id)
    data.setdefault(chat_id, [])
    if ma not in data[chat_id]:
        data[chat_id].append(ma)
        save_data(data)
        await update.message.reply_text(f"✅ Đã thêm mã KH: {ma}")
    else:
        await update.message.reply_text(f"⚠️ Mã {ma} đã có trong danh sách.")

async def cmd_xoa(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Dùng: /xoa <mã_KH>"); return
    ma = ctx.args[0].strip().upper()
    data = load_data()
    chat_id = str(update.effective_chat.id)
    if ma in data.get(chat_id, []):
        data[chat_id].remove(ma)
        save_data(data)
        await update.message.reply_text(f"🗑️ Đã xoá mã KH: {ma}")
    else:
        await update.message.reply_text(f"❌ Không tìm thấy mã {ma}.")

async def cmd_xem(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    chat_id = str(update.effective_chat.id)
    ma_list = data.get(chat_id, [])
    if ma_list:
        await update.message.reply_text("📋 Danh sách mã KH:\n" + "\n".join(f"• {m}" for m in ma_list))
    else:
        await update.message.reply_text("Chưa có mã KH nào. Dùng /them để thêm.")

async def cmd_tra(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Dùng: /tra <mã_KH>"); return
    ma = ctx.args[0].strip().upper()
    await update.message.reply_text("🔍 Đang tra cứu, vui lòng chờ...")
    result = fetch_lich_cat_dien(ma)
    await update.message.reply_text(result, parse_mode="Markdown")

# ── Chế độ gửi hàng ngày (gọi từ GitHub Actions) ──────────
async def daily_send():
    bot  = Bot(token=BOT_TOKEN)
    data = load_data()
    for chat_id, ma_list in data.items():
        for ma in ma_list:
            result = fetch_lich_cat_dien(ma)
            await bot.send_message(chat_id=int(chat_id), text=result, parse_mode="Markdown")
    print("✅ Đã gửi xong lịch cắt điện.")
    -----------------------------------------------

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *args): pass  # tắt log spam

def run_health():
    HTTPServer(("0.0.0.0", int(os.environ.get("PORT", 8080))), HealthHandler).serve_forever()

# ── Entry point ───────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "daily":
        asyncio.run(daily_send())
    else:
        # Chạy bot lắng nghe lệnh
        app = Application.builder().token(BOT_TOKEN).build()
        app.add_handler(CommandHandler("start", cmd_start))
        app.add_handler(CommandHandler("them",  cmd_them))
        app.add_handler(CommandHandler("xoa",   cmd_xoa))
        app.add_handler(CommandHandler("xem",   cmd_xem))
        app.add_handler(CommandHandler("tra",   cmd_tra))
        print("🤖 Bot đang chạy...")
        app.run_polling()
