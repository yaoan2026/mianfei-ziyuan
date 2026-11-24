# 免費資源雷達

每 3 小時自動蒐集指定主題的免費課程、蝦皮／3C 優惠與禮券，整理成網頁，新項目推播到 Discord／Telegram。

| 分類 | 來源 | 篩選 |
|---|---|---|
| 課程 | DiscUdemy（Udemy 100% OFF 優惠碼） | 標題含 AI／股票投資／日文；英日中語言 |
| 課程 | Google 新聞（中文免費課程、講座） | 標題含「免費」+ 課程類字眼 + 主題 |
| 優惠 | PTT 省錢板、Google 新聞 | 蝦皮或 3C 相關 |
| 禮券 | PTT e-coupon 板 | 轉售／贈送，排除已售出 |

要改追蹤主題，修改 `fetch.py` 開頭的 `COURSE_TOPICS`、`SHOPEE`、`TECH` 關鍵字。

推播規則：課程與優惠的新項目全推；禮券只推標題有「免費／贈送」的。

## 設定推播（擇一或都設）
到 repo 的 **Settings → Secrets and variables → Actions → New repository secret** 新增：

- `DISCORD_WEBHOOK`：Discord 頻道設定 → 整合 → Webhook → 複製網址
- `TELEGRAM_TOKEN` 與 `TELEGRAM_CHAT_ID`：向 @BotFather 建立 bot 取得 token

沒設定就只更新網頁，不推播。

## 本機執行
```
python fetch.py
python -m http.server
```
