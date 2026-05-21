import os
import json
import asyncio
import requests
import random
from html.parser import HTMLParser
from telegram import Bot, Update
from aiohttp import web

# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN  = os.environ["BOT_TOKEN"]
OWNER_ID   = int(os.environ["OWNER_CHAT_ID"])
RENDER_URL = os.environ.get("RENDER_URL", "")
DATA_FILE  = "customers.json"
NPC_URL    = "https://cskh.npc.com.vn/DichVuTTCSKH/DichVuTTCSKHNPC"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

# ── Proxy helpers ─────────────────────────────────────────────────────────────
def fetch_proxies() -> list:
    proxies = []
    try:
        r = requests.get(
            "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/countries/VN/data.json",
            timeout=10,
        )
        for p in r.json():
            proxies.append(f"http://{p['ip']}:{p['port']}")
    except Exception as e:
        print(f"[proxy] Lỗi: {e}")
    return proxies

def get_working_proxy(proxies: list) -> dict | None:
    random.shuffle(proxies)
    for proxy in proxies[:15]:
        try:
            r = requests.get("https://httpbin.org/ip",
                             proxies={"http": proxy, "https": proxy}, timeout=5)
            if r.status_code == 200:
                print(f"[proxy] Dùng: {proxy}")
                return {"http": proxy, "https": proxy}
        except Exception:
            continue
    print("[proxy] Không tìm được proxy.")
    return None

# ── HTML Parser ───────────────────────────────────────────────────────────────
class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_table = False
        self.rows, self.cur_row, self.cur_cell = [], [], []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "table":
            tid  = d.get("id", "").lower()
            tcls = d.get("class", "").lower()
            if any(k in tid + tcls for k in ("lich", "schedule", "grid", "tb")):
                self.in_table = True
        if self.in_table:
            if tag == "tr":        self.cur_row  = []
            if tag in ("td","th"): self.cur_cell = []

    def handle_endtag(self, tag):
        if self.in_table:
            if tag in ("td","th"):
                self.cur_row.append(" ".join(self.cur_cell).strip())
            if tag == "tr" and self.cur_row:
                self.rows.append(self.cur_row[:])
                self.cur_row = []
            if tag == "table":
                self.in_table = False

    def handle_data(self, data):
        if self.in_table:
            d = data.strip()
            if d: self.cur_cell.append(d)

def parse_html(html: str, ma_kh: str) -> str:
    p = TableParser()
    p.feed(html)
    if p.rows:
        header, data_rows = p.rows[0], p.rows[1:]
        if not data_rows:
            return f"✅ Mã KH *{ma_kh}*: Không có lịch cắt điện."
        lines = [f"⚡ *Lịch cắt điện – Mã KH: {ma_kh}*\n"]
        for row in data_rows:
            pairs = []
            for i, cell in enumerate(row):
                col = header[i] if i < len(header) else f"Cột {i+1}"
                if cell: pairs.append(f"{col}: {cell}")
            if pairs: lines.append("• " + " | ".join(pairs))
        return "\n".join(lines) if len(lines) > 1 else f"✅ Mã KH *{ma_kh}*: Không có lịch cắt điện."
    lower = html.lower()
    if "không có lịch" in lower: return f"✅ Mã KH *{ma_kh}*: Không có lịch cắt điện."
    if "không tìm thấy" in lower: return f"❌ Mã KH *{ma_kh}* không tìm thấy."
    return (f"⚠️ Mã KH *{ma_kh}*: Không đọc được bảng lịch.\n"
            "Kiểm tra thủ công: https://cskh.npc.com.vn/DichVuTTCSKH/DichVuTTCSKHNPC?index=7")

# ── NPC Scraper ───────────────────────────────────────────────────────────────
def fetch_lich_cat_dien(ma_kh: str) -> str:
    headers = {
        "User-Agent"     : random.choice(USER_AGENTS),
        "Accept"         : "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8",
        "Referer"        : "https://cskh.npc.com.vn/",
    }
    params = {"index": "7", "MaKhachHang": ma_kh}
    try:
        print(f"[npc] Thử direct cho {ma_kh}...")
        resp = requests.get(NPC_URL, params=params, headers=headers, timeout=15)
        resp.raise_for_status(); resp.encoding = "utf-8"
        print("[npc] Direct OK.")
        return parse_html(resp.text, ma_kh)
    except Exception as e1:
        print(f"[npc] Direct lỗi: {e1} → thử proxy...")
    proxy = get_working_proxy(fetch_proxies())
    try:
        resp = requests.get(NPC_URL, params=params, headers=headers, proxies=proxy, timeout=25)
        resp.raise_for_status(); resp.encoding = "utf-8"
        return parse_html(resp.text, ma_kh)
    except Exception as e2:
        return f"❌ Không kết nối được NPC:\n`{e2}`"

# ── Data helpers ──────────────────────────────────────────────────────────────
def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f: return json.load(f)
    return {}

def save_data(data: dict):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ── Xử lý lệnh Telegram ───────────────────────────────────────────────────────
async def handle_update(bot: Bot, update: Update):
    msg = update.message
    if not msg or not msg.text: return

    text    = msg.text.strip()
    chat_id = str(msg.chat.id)
    parts   = text.split()
    cmd     = parts[0].lower().split("@")[0]

    if cmd == "/start":
        await bot.send_message(chat_id=msg.chat.id,
            text=(
                "👋 Xin chào! Tôi là bot tra lịch cắt điện NPC.\n\n"
                "📌 *Các lệnh:*\n"
                "• /them `<mã_KH>` — Thêm mã khách hàng\n"
                "• /xoa `<mã_KH>` — Xoá mã khách hàng\n"
                "• /xem — Xem danh sách đang theo dõi\n"
                "• /tra `<mã_KH>` — Tra cứu ngay\n\n"
                "🕗 Bot tự động gửi lịch mỗi sáng 8h."
            ), parse_mode="Markdown")

    elif cmd == "/them":
        if len(parts) < 2:
            await bot.send_message(chat_id=msg.chat.id, text="❓ Dùng: /them <mã\\_KH>", parse_mode="Markdown"); return
        ma   = parts[1].strip().upper()
        data = load_data(); data.setdefault(chat_id, [])
        if ma not in data[chat_id]:
            data[chat_id].append(ma); save_data(data)
            await bot.send_message(chat_id=msg.chat.id, text=f"✅ Đã thêm: `{ma}`", parse_mode="Markdown")
        else:
            await bot.send_message(chat_id=msg.chat.id, text=f"⚠️ `{ma}` đã có rồi.", parse_mode="Markdown")

    elif cmd == "/xoa":
        if len(parts) < 2:
            await bot.send_message(chat_id=msg.chat.id, text="❓ Dùng: /xoa <mã\\_KH>", parse_mode="Markdown"); return
        ma   = parts[1].strip().upper()
        data = load_data()
        if ma in data.get(chat_id, []):
            data[chat_id].remove(ma); save_data(data)
            await bot.send_message(chat_id=msg.chat.id, text=f"🗑️ Đã xoá: `{ma}`", parse_mode="Markdown")
        else:
            await bot.send_message(chat_id=msg.chat.id, text=f"❌ Không tìm thấy `{ma}`.", parse_mode="Markdown")

    elif cmd == "/xem":
        ma_list = load_data().get(chat_id, [])
        text_out = ("📋 *Danh sách mã KH:*\n" + "\n".join(f"• `{m}`" for m in ma_list)
                    if ma_list else "Chưa có mã. Dùng /them để thêm.")
        await bot.send_message(chat_id=msg.chat.id, text=text_out, parse_mode="Markdown")

    elif cmd == "/tra":
        if len(parts) < 2:
            await bot.send_message(chat_id=msg.chat.id, text="❓ Dùng: /tra <mã\\_KH>", parse_mode="Markdown"); return
        ma = parts[1].strip().upper()
        await bot.send_message(chat_id=msg.chat.id, text=f"🔍 Đang tra `{ma}`...", parse_mode="Markdown")
        loop   = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, fetch_lich_cat_dien, ma)
        await bot.send_message(chat_id=msg.chat.id, text=result, parse_mode="Markdown")

# ── Daily send ────────────────────────────────────────────────────────────────
async def daily_send():
    bot = Bot(token=BOT_TOKEN)
    data = load_data()
    if not data: print("Không có mã KH."); return
    for chat_id, ma_list in data.items():
        for ma in ma_list:
            print(f"[daily] {ma} → {chat_id}")
            result = fetch_lich_cat_dien(ma)
            await bot.send_message(chat_id=int(chat_id), text=result, parse_mode="Markdown")
    print("✅ Xong.")

# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    port = int(os.environ.get("PORT", 10000))  # Render dùng 10000 mặc định
    bot  = Bot(token=BOT_TOKEN)

    async def handle_webhook(request):
        try:
            data   = await request.json()
            update = Update.de_json(data, bot)
            asyncio.create_task(handle_update(bot, update))
        except Exception as e:
            print(f"[webhook] Lỗi xử lý update: {e}")
        return web.Response(text="OK")

    async def handle_health(request):
        return web.Response(text="OK")

    web_app = web.Application()
    web_app.router.add_get("/",  handle_health)
    web_app.router.add_post(f"/webhook/{BOT_TOKEN}", handle_webhook)

    runner = web.AppRunner(web_app)
    await runner.setup()

    # ✅ FIX: Start server TRƯỚC, đảm bảo port bind xong
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"[server] Lắng nghe trên cổng {port}")

    # ✅ FIX: Chỉ đăng ký webhook SAU khi server đã lắng nghe
    webhook_url = f"{RENDER_URL}/webhook/{BOT_TOKEN}"
    await bot.set_webhook(url=webhook_url, drop_pending_updates=True)
    print(f"[webhook] Đã đăng ký: {webhook_url}")
    print("🤖 Bot đang chạy...")

    # Giữ chạy mãi
    await asyncio.Event().wait()

# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "daily":
        asyncio.run(daily_send())
    else:
        asyncio.run(main())
