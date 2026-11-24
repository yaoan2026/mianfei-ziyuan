"""免費資源雷達：抓取各來源 → data.json，新項目推播到 Discord / Telegram。
只用 Python 標準函式庫。環境變數：
  DISCORD_WEBHOOK   Discord 頻道 webhook 網址（可選）
  TELEGRAM_TOKEN    Telegram bot token（可選，需搭配 TELEGRAM_CHAT_ID）
  TELEGRAM_CHAT_ID
"""
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent
DATA = ROOT / "data.json"
SEEN = ROOT / "seen.json"
KEEP_DAYS = {"課程": 5}  # Udemy 優惠碼通常幾天內就失效
KEEP_DEFAULT = 14
UA = "Mozilla/5.0 (compatible; mianfei-ziyuan/1.0)"
TW = timezone(timedelta(hours=8))
FREE_WORDS = re.compile(r"免費|0元|\$0\b|白拿|贈送|送\d|限免|100% ?OFF|free", re.I)
ATOM = "{http://www.w3.org/2005/Atom}"

# ---------- 主題關鍵字（要調整追蹤主題改這裡） ----------

COURSE_TOPICS = {
    # 用 (?<![A-Za-z]) 而非 \b，因為中文字也算 \w，「AI課程」會配不到 \bAI\b
    "AI": r"(?<![A-Za-z])A\.?I(?![A-Za-z])|ChatGPT|GPT|Claude|Gemini|Copilot|LLM|Generative|Prompt"
          r"|Machine Learning|Deep Learning|Neural|Agentic|Midjourney|Stable Diffusion|n8n"
          r"|(?<![A-Za-z])RAG(?![A-Za-z])|LangChain|人工智慧|生成式",
    "股票": r"Stock|Trading|Trader|Invest|(?<![A-Za-z])Options(?![A-Za-z])|Dividend|Candlestick"
            r"|Technical Analysis|Portfolio|Day Trad|Swing Trad|Forex|(?<![A-Za-z])ETF|Wall Street"
            r"|Financial Market|股票|台股|美股|投資|理財|存股",
    "日文": r"Japanese|JLPT|Hiragana|Katakana|Kanji|Nihongo|日本語|日文|日語|五十音",
}
COURSE_RE = {k: re.compile(v, re.I) for k, v in COURSE_TOPICS.items()}

LANGS = {"English", "Japanese", "Chinese", "Chinese (Traditional)", "Chinese (Simplified)"}

SHOPEE = re.compile(r"蝦皮|Shopee", re.I)
TECH = re.compile(
    r"3C|iPhone|iPad|MacBook|Mac mini|AirPods|Apple Watch|蘋果|三星|Samsung|Galaxy|小米|Xiaomi|Pixel|OPPO|vivo"
    r"|筆電|筆記型電腦|耳機|螢幕|顯示器|平板|手機|SSD|硬碟|記憶卡|隨身碟|行動電源|充電器|鍵盤|滑鼠"
    r"|Switch|PS5|相機|智慧手錶|藍牙|喇叭|路由器|電視|投影機|顯卡|主機板|Sony|ASUS|華碩|Acer|宏碁"
    r"|羅技|Logitech|Anker|Dyson|掃地機|吸塵器|電競|燦坤|全國電子|順發|原價屋|STUDIO A|德誼", re.I)
DEAL = re.compile(r"優惠|特價|折|促銷|下殺|回饋|免運|懶人包|限時|開搶|搶購|現省|\d+元|快閃|超殺|破盤|史低|最低價")


def deal_tag(title):
    if SHOPEE.search(title):
        return "蝦皮"
    if TECH.search(title):
        return "3C"
    return None


# ---------- 工具 ----------

def get(url):
    if "news.google.com" in url:
        time.sleep(4)  # 同一次執行會連 Google 新聞 3 次，間隔開來避免 429
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read()


def iso(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TW).isoformat(timespec="minutes")


def item(source, category, title, url, published=None, expires=None, extra="", tag=""):
    return {
        "id": hashlib.sha1(url.encode()).hexdigest()[:16],
        "source": source,
        "category": category,
        "tag": tag or "",
        "title": html.unescape(title).strip(),
        "url": url,
        "published": iso(published),
        "expires": iso(expires),
        "extra": extra or "",
    }


# ---------- 各來源 ----------

def src_discudemy():
    """DiscUdemy 列表頁（RSS 只有 20 筆不夠），只留 AI／股票／日文主題、英日中語言。"""
    out = []
    for page in range(1, 11):
        doc = get(f"https://www.discudemy.com/all/{page}").decode("utf-8", "replace")
        for card in doc.split('class="card"')[1:]:
            m = re.search(r'class="card-header" href="([^"]+)">\s*([^<]+?)\s*</a>', card)
            if not m:
                continue
            url, title = m.groups()
            # 只看標題（描述常順帶提到 AI，會誤判）
            topics = [k for k, r in COURSE_RE.items() if r.search(title)]
            lang = re.search(r'disc-fee label">([^<]+)<', card)
            if not topics or (lang and lang.group(1).strip() not in LANGS):
                continue
            title = re.sub(r"^\[(100% OFF|Free)\]\s*", "", html.unescape(title), flags=re.I)
            extra = " · ".join(x for x in ["Udemy 100% OFF", lang.group(1).strip() if lang else ""] if x)
            out.append(item("Udemy", "課程", title, url, extra=extra, tag="/".join(topics)))
    return out


def ptt(board, category, keep, tag=lambda t: ""):
    """PTT 看板 Atom。只取標題/連結/時間，不保留內文（內含推文者 ID 與 IP）。"""
    root = ET.fromstring(get(f"https://www.ptt.cc/atom/{board}.xml"))
    out = []
    for e in root.findall(f"{ATOM}entry"):
        title = e.findtext(f"{ATOM}title") or ""
        if title.startswith("Re:") or not keep(title):
            continue
        link = e.find(f"{ATOM}link").get("href")
        pub = datetime.fromisoformat(e.findtext(f"{ATOM}published"))
        out.append(item(f"PTT {board}", category, title, link, pub, tag=tag(title)))
    return out


def src_lifeismoney():
    # 省錢板只留蝦皮與 3C 相關的情報
    return ptt("Lifeismoney", "優惠",
               lambda t: re.match(r"\[(情報|限時|分享)\]", t) and deal_tag(t), deal_tag)


def src_ecoupon():
    # e-coupon 板以網友轉售／贈送禮券為主，過濾掉已售出的
    return ptt("e-coupon", "禮券", lambda t: re.match(r"\[(交易|贈送|免費|情報)\]", t)
               and not re.search(r"已售|已出|已贈|售完", t))


def google_news(query):
    q = urllib.parse.quote(f"{query} when:2d")
    root = ET.fromstring(get(f"https://news.google.com/rss/search?q={q}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"))
    out = []
    for i in root.iter("item"):
        title = i.findtext("title") or ""
        media = i.findtext("source") or ""
        if media and title.endswith(" - " + media):
            title = title[: -len(media) - 3]
        tag = deal_tag(title)
        if not tag or not DEAL.search(title):
            continue
        out.append(item("新聞", "優惠", title, i.findtext("link"),
                        parsedate_to_datetime(i.findtext("pubDate")), extra=media, tag=tag))
    return out


def src_news_courses():
    """中文免費課程／講座（新聞與活動平台），標題要同時有「免費」、課程類字眼與主題。"""
    q = urllib.parse.quote("免費 (課程 OR 講座 OR 工作坊 OR 教學) (AI OR 股票 OR 台股 OR 投資 OR 理財 OR 日文 OR 日語) when:7d")
    root = ET.fromstring(get(f"https://news.google.com/rss/search?q={q}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"))
    out = []
    for i in root.iter("item"):
        title = i.findtext("title") or ""
        media = i.findtext("source") or ""
        if media and title.endswith(" - " + media):
            title = title[: -len(media) - 3]
        topics = [k for k, r in COURSE_RE.items() if r.search(title)]
        if not topics or "免費" not in title or not re.search(r"課|講座|工作坊|培訓|教室|研習|報名", title):
            continue
        out.append(item("中文課程/講座", "課程", title, i.findtext("link"),
                        parsedate_to_datetime(i.findtext("pubDate")), extra=media, tag="/".join(topics)))
    return out


def src_news_shopee():
    return google_news("蝦皮 優惠 OR 折扣 OR 免運 OR 折價券")


def src_news_3c():
    return google_news("3C 特價 OR 優惠 OR 下殺 OR 促銷")


SOURCES = {
    "Udemy 課程": src_discudemy,
    "中文課程/講座": src_news_courses,
    "PTT 省錢板": src_lifeismoney,
    "新聞：蝦皮": src_news_shopee,
    "新聞：3C": src_news_3c,
    "PTT 禮券板": src_ecoupon,
}


# ---------- 推播 ----------

def post_json(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": UA})
    urllib.request.urlopen(req, timeout=20).read()


def notify(new_items):
    # 課程（已篩主題）與蝦皮/3C 優惠全推；禮券只推免費的
    hits = [i for i in new_items if i["category"] in ("課程", "優惠") or FREE_WORDS.search(i["title"])]
    if not hits:
        print("沒有需要推播的新項目")
        return
    lines = [f"🎁 免費資源雷達：{len(hits)} 筆新資源"]
    for i in hits[:25]:
        label = f"{i['category']}·{i['tag']}" if i["tag"] else i["category"]
        lines.append(f"【{label}】{i['title']}\n{i['url']}")
    if len(hits) > 25:
        lines.append(f"…還有 {len(hits) - 25} 筆，請看網頁")
    text = "\n\n".join(lines)

    hook = os.environ.get("DISCORD_WEBHOOK")
    if hook:
        chunk = ""  # Discord 單則上限 2000 字，切段送出
        for part in text.split("\n\n"):
            if len(chunk) + len(part) + 2 > 1900:
                post_json(hook, {"content": chunk, "flags": 4})  # flags 4 = 不展開連結預覽
                chunk = ""
            chunk += part + "\n\n"
        if chunk.strip():
            post_json(hook, {"content": chunk, "flags": 4})
        print(f"已推播 Discord：{len(hits)} 筆")
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat:
        for start in range(0, len(text), 3900):
            post_json(f"https://api.telegram.org/bot{token}/sendMessage",
                      {"chat_id": chat, "text": text[start:start + 3900], "disable_web_page_preview": True})
        print(f"已推播 Telegram：{len(hits)} 筆")
    if not hook and not (token and chat):
        print(f"未設定推播管道，略過（本來要推 {len(hits)} 筆）")


# ---------- 主流程 ----------

def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def main():
    old = load(DATA, {"items": [], "status": {}})
    seen = load(SEEN, None)
    first_run = seen is None
    seen = set(seen or [])
    now = datetime.now(TW)

    items, status, failed = [], {}, set()
    for name, fn in SOURCES.items():
        try:
            got = fn()
            for i in got:
                i["src"] = name
            items += got
            status[name] = {"ok": True, "count": len(got)}
            print(f"✓ {name}: {len(got)}")
        except Exception as e:  # 單一來源壞掉不影響其他，沿用上次資料
            failed.add(name)
            status[name] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}
            print(f"✗ {name}: {e}（沿用舊資料）")

    # 沒有日期的來源（Udemy 列表頁）用第一次看到的時間
    old_pub = {i["id"]: i["published"] for i in old["items"]}
    for i in items:
        if not i["published"]:
            i["published"] = old_pub.get(i["id"]) or now.isoformat(timespec="minutes")

    # 來源清單都很短（PTT 20 篇、課程只抓前幾頁），舊資料累積保留，靠過期規則淘汰。
    # 只保留目前還在 SOURCES 裡的來源（移除的來源不會殘留）。
    merged = {i["id"]: i for i in old["items"] if i.get("src") in SOURCES}
    merged.update({i["id"]: i for i in items})
    keep = []
    for i in merged.values():
        if i["expires"] and datetime.fromisoformat(i["expires"]) < now:
            continue
        days = KEEP_DAYS.get(i["category"], KEEP_DEFAULT)
        if not i["expires"] and datetime.fromisoformat(i["published"]) < now - timedelta(days=days):
            continue
        keep.append(i)
    keep.sort(key=lambda i: i["published"] or "", reverse=True)

    new = [i for i in keep if i["id"] not in seen]
    if first_run:
        print(f"首次執行：記錄 {len(new)} 筆為已讀，不推播")
    else:
        print(f"新項目 {len(new)} 筆")
        notify(new)

    SEEN.write_text(json.dumps(sorted(i["id"] for i in keep)), encoding="utf-8")
    DATA.write_text(json.dumps({"updated": now.isoformat(timespec="minutes"), "status": status,
                                "items": keep}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"共 {len(keep)} 筆 → data.json")


if __name__ == "__main__":
    main()
