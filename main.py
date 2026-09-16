import os
import asyncio
import logging
from datetime import datetime
import aiohttp
from telegram import Bot
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram import Update
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz

# تنظیمات لاگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# خواندن تنظیمات از Environment
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "")
SCAN_HOUR = int(os.getenv("SCAN_HOUR", "9"))  # ساعت ارسال روزانه
TIMEZONE = os.getenv("TIMEZONE", "Asia/Tehran")

COINGECKO_BASE = "https://api.coingecko.com/api/v3"


async def fetch_coins():
    """دریافت لیست 250 ارز برتر از CoinGecko"""
    headers = {}
    if COINGECKO_API_KEY:
        headers["x-cg-demo-api-key"] = COINGECKO_API_KEY
    
    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": 250,
        "page": 1,
        "price_change_percentage": "1h,24h,7d"
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                f"{COINGECKO_BASE}/coins/markets",
                params=params,
                headers=headers,
                timeout=30
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    logger.error(f"CoinGecko error: {resp.status}")
                    return []
        except Exception as e:
            logger.error(f"Fetch error: {e}")
            return []


def analyze_coin(coin):
    """تحلیل ساده هر ارز و محاسبه امتیاز"""
    try:
        price = coin.get("current_price", 0)
        mcap = coin.get("market_cap", 0)
        volume = coin.get("total_volume", 0)
        change_1h = coin.get("price_change_percentage_1h_in_currency") or 0
        change_24h = coin.get("price_change_percentage_24h_in_currency") or 0
        change_7d = coin.get("price_change_percentage_7d_in_currency") or 0
        
        # حذف استیبل‌کوین‌ها
        symbol = coin.get("symbol", "").upper()
        stables = ["USDT", "USDC", "DAI", "BUSD", "TUSD", "USDD", "FDUSD", "PYUSD", "USDE"]
        if symbol in stables:
            return None
        
        # حذف ارزهای با نقدشوندگی پایین
        if mcap < 50_000_000 or volume < 5_000_000:
            return None
        
        vol_mcap = (volume / mcap) if mcap > 0 else 0
        
        # محاسبه امتیاز (Evidence Score)
        score = 0
        evidence = []
        warnings = []
        
        # نشانه 1: افزایش حجم به مارکت‌کپ
        if vol_mcap > 0.15:
            score += 1
            evidence.append("✓ Volume/MC بالا")
        
        # نشانه 2: رشد ملایم 24 ساعته (نه پامپ)
        if 2 < change_24h < 15:
            score += 1
            evidence.append("✓ رشد کنترل‌شده 24h")
        elif change_24h >= 25:
            warnings.append("⚠ رشد شدید (احتمال FOMO)")
        
        # نشانه 3: مومنتوم 1 ساعته مثبت
        if 0.5 < change_1h < 5:
            score += 1
            evidence.append("✓ مومنتوم 1h مثبت")
        
        # نشانه 4: رشد 7 روزه معقول (Accumulation)
        if -5 < change_7d < 10:
            score += 1
            evidence.append("✓ در محدوده تراکم")
        
        # نشانه 5: حجم بالا
        if volume > mcap * 0.2:
            score += 1
            evidence.append("✓ حجم معاملات فعال")
        
        # تعیین State
        if score >= 4 and change_24h < 15:
            state = "🟢 ACCUMULATION / EARLY MOVE"
        elif score >= 3 and change_24h > 5:
            state = "🟡 EXPANSION"
        elif change_24h > 20 and vol_mcap > 0.3:
            state = "🔴 DISTRIBUTION RISK"
        elif score >= 2:
            state = "🔵 DEVELOPING"
        else:
            state = "⚪ NEUTRAL"
        
        return {
            "symbol": symbol,
            "name": coin.get("name"),
            "price": price,
            "mcap": mcap,
            "volume": volume,
            "vol_mcap": vol_mcap,
            "change_1h": change_1h,
            "change_24h": change_24h,
            "change_7d": change_7d,
            "score": score,
            "state": state,
            "evidence": evidence,
            "warnings": warnings
        }
    except Exception as e:
        logger.error(f"Analyze error: {e}")
        return None


async def scan_market():
    """اسکن کامل بازار و تولید گزارش"""
    logger.info("شروع اسکن بازار...")
    coins = await fetch_coins()
    
    if not coins:
        return "❌ خطا در دریافت داده‌ها. لطفاً بعداً امتحان کنید."
    
    results = []
    for coin in coins:
        analysis = analyze_coin(coin)
        if analysis and analysis["score"] >= 3:
            results.append(analysis)
    
    # مرتب‌سازی بر اساس امتیاز
    results.sort(key=lambda x: x["score"], reverse=True)
    top = results[:10]
    
    if not top:
        return "📊 امروز کاندید قوی پیدا نشد.\nNO STRONG CANDIDATES TODAY"
    
    # ساخت گزارش
    date_str = datetime.now(pytz.timezone(TIMEZONE)).strftime("%Y-%m-%d %H:%M")
    report = f"📊 *DAILY CRYPTO MONEY FLOW*\n"
    report += f"🕐 {date_str}\n"
    report += f"━━━━━━━━━━━━━━━━━━\n\n"
    
    for i, coin in enumerate(top, 1):
        report += f"*{i}. {coin['symbol']}* - {coin['name']}\n"
        report += f"State: {coin['state']}\n"
        report += f"Score: *{coin['score']}/5*\n"
        report += f"💰 Price: ${coin['price']:,.4f}\n"
        report += f"📈 24h: {coin['change_24h']:+.2f}% | 7d: {coin['change_7d']:+.2f}%\n"
        report += f"📊 Vol/MC: {coin['vol_mcap']:.3f}\n"
        
        if coin['evidence']:
            report += "شواهد مثبت:\n"
            for e in coin['evidence']:
                report += f"  {e}\n"
        
        if coin['warnings']:
            report += "هشدارها:\n"
            for w in coin['warnings']:
                report += f"  {w}\n"
        
        report += "\n"
    
    report += "━━━━━━━━━━━━━━━━━━\n"
    report += "⚠ *این گزارش صرفاً تحلیل داده است*\n"
    report += "*توصیه سرمایه‌گذاری نیست*"
    
    return report


async def send_daily_report():
    """ارسال گزارش روزانه"""
    try:
        report = await scan_market()
        bot = Bot(token=TELEGRAM_TOKEN)
        # تلگرام محدودیت 4096 کاراکتر داره
        if len(report) > 4000:
            for i in range(0, len(report), 4000):
                await bot.send_message(
                    chat_id=CHAT_ID,
                    text=report[i:i+4000],
                    parse_mode="Markdown"
                )
        else:
            await bot.send_message(
                chat_id=CHAT_ID,
                text=report,
                parse_mode="Markdown"
            )
        logger.info("گزارش روزانه ارسال شد")
    except Exception as e:
        logger.error(f"Send error: {e}")


# دستورات ربات
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 سلام! ربات Crypto Money Flow Scanner فعال است.\n\n"
        "دستورات:\n"
        "/scan - اسکن فوری بازار\n"
        "/daily - گزارش روزانه\n"
        "/status - وضعیت ربات\n"
        "/help - راهنما"
    )


async def scan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 در حال اسکن بازار... چند لحظه صبر کنید")
    report = await scan_market()
    if len(report) > 4000:
        for i in range(0, len(report), 4000):
            await update.message.reply_text(report[i:i+4000], parse_mode="Markdown")
    else:
        await update.message.reply_text(report, parse_mode="Markdown")


async def daily_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await scan_cmd(update, context)


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
    await update.message.reply_text(
        f"✅ ربات فعال است\n"
        f"🕐 زمان فعلی: {now}\n"
        f"⏰ ارسال روزانه: ساعت {SCAN_HOUR}:00\n"
        f"🌍 Timezone: {TIMEZONE}"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_cmd(update, context)


async def post_init(app):
    """راه‌اندازی زمان‌بند بعد از استارت ربات"""
    scheduler = AsyncIOScheduler(timezone=pytz.timezone(TIMEZONE))
    scheduler.add_job(send_daily_report, 'cron', hour=SCAN_HOUR, minute=0)
    scheduler.start()
    logger.info(f"زمان‌بند فعال شد - ارسال روزانه ساعت {SCAN_HOUR}:00")
    
    # ارسال پیام استارت
    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        await bot.send_message(
            chat_id=CHAT_ID,
            text=f"🚀 ربات آنلاین شد!\n⏰ گزارش روزانه ساعت {SCAN_HOUR}:00 ارسال می‌شود"
        )
    except Exception as e:
        logger.error(f"Start message error: {e}")


def main():
    if not TELEGRAM_TOKEN or not CHAT_ID:
        logger.error("❌ TELEGRAM_TOKEN یا CHAT_ID تنظیم نشده!")
        return
    
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("scan", scan_cmd))
    app.add_handler(CommandHandler("daily", daily_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    
    logger.info("🚀 ربات شروع به کار کرد...")
    app.run_polling()


if __name__ == "__main__":
    main()
