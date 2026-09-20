#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ScreenScape Telegram Bot with Redis Cache
==========================================
Search TMDB, reply with tap-to-play ScreenScape links.
Uses Upstash Redis to cache searches and avoid rate limits.
"""

import os
import json
import logging
import requests
from dotenv import load_dotenv
from upstash_redis import Redis
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)

# ---------- Load Environment ----------
load_dotenv()

BOT_TOKEN     = os.getenv("BOT_TOKEN")
TMDB_TOKEN    = os.getenv("TMDB_TOKEN")
UPSTASH_URL   = os.getenv("UPSTASH_URL")
UPSTASH_TOKEN = os.getenv("UPSTASH_TOKEN")

SCREENSCAPE = "https://screenscape.me/embed"
CACHE_TTL = 600  # 10 minutes

# ---------- Logging ----------
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)

# ---------- Redis Client ----------
redis = Redis(url=UPSTASH_URL, token=UPSTASH_TOKEN)

# ---------- TMDB (Bearer Auth) ----------
TMDB_HEADERS = {
    "Authorization": f"Bearer {TMDB_TOKEN}",
    "Accept": "application/json",
}

def tmdb_get(path, **params):
    r = requests.get(
        f"https://api.themoviedb.org/3{path}",
        headers=TMDB_HEADERS, params=params, timeout=15
    )
    r.raise_for_status()
    return r.json()

# ---------- Cached TMDB Search ----------
def tmdb_search_cached(query, kind="multi"):
    """Search TMDB with Redis caching."""
    cache_key = f"tmdb:search:{kind}:{query.lower().strip()}"

    cached = redis.get(cache_key)
    if cached:
        log.info(f"Cache HIT: '{query}'")
        return json.loads(cached)

    log.info(f"Cache MISS: '{query}' — calling TMDB")
    results = tmdb_get(f"/search/{kind}", query=query).get("results", [])

    redis.set(cache_key, json.dumps(results), ex=CACHE_TTL)
    return results

def tmdb_details(tmdb_id, kind):
    return tmdb_get(f"/{kind}/{tmdb_id}")

def tmdb_season(tmdb_id, season):
    return tmdb_get(f"/tv/{tmdb_id}/season/{season}")

# ---------- ScreenScape URL Builder ----------
def ss_url(tmdb=None, imdb=None, kind="movie",
           season=None, episode=None, lang=None):
    p = []
    if tmdb: p.append(f"tmdb={tmdb}")
    if imdb: p.append(f"imdb={imdb}")
    p.append(f"type={kind}")
    if season  is not None: p.append(f"s={season}")
    if episode is not None: p.append(f"e={episode}")
    if lang: p.append(f"lan={lang}")
    return f"{SCREENSCAPE}?{'&'.join(p)}"

# ---------- /start ----------
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 *ScreenScape Stream Bot*\n\n"
        "Type a title to search, or use:\n"
        "`/movie <title>` — movie search\n"
        "`/tv <title>` — TV search\n"
        "`/id <tmdb_id>` — play by TMDB ID\n"
        "`/s <tmdb_id> <season> <episode>` — specific episode\n\n"
        "Example: `/movie Iron Man`",
        parse_mode=ParseMode.MARKDOWN
    )

# ---------- /movie ----------
async def cmd_movie(update, ctx):
    q = " ".join(ctx.args)
    if not q:
        return await update.message.reply_text(
            "Usage: `/movie Iron Man`", parse_mode=ParseMode.MARKDOWN)
    await _search(update, q, "movie")

# ---------- /tv ----------
async def cmd_tv(update, ctx):
    q = " ".join(ctx.args)
    if not q:
        return await update.message.reply_text(
            "Usage: `/tv Breaking Bad`", parse_mode=ParseMode.MARKDOWN)
    await _search(update, q, "tv")

# ---------- /id ----------
async def cmd_id(update, ctx):
    if not ctx.args:
        return await update.message.reply_text("Usage: `/id 10195`")
    try:
        tid = int(ctx.args[0])
    except ValueError:
        return await update.message.reply_text("TMDB ID must be numeric.")
    url = ss_url(tmdb=tid, kind="movie")
    await update.message.reply_text(
        f"🎬 *TMDB {tid}*\n[▶️ Tap to Play]({url})",
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True
    )

# ---------- /s ----------
async def cmd_season(update, ctx):
    if len(ctx.args) < 3:
        return await update.message.reply_text(
            "Usage: `/s <tmdb_id> <season> <episode>`\n"
            "Example: `/s 1396 1 1`")
    try:
        tid, s, e = map(int, ctx.args[:3])
    except ValueError:
        return await update.message.reply_text("All three must be numbers.")
    url = ss_url(tmdb=tid, kind="tv", season=s, episode=e)
    await update.message.reply_text(
        f"🎬 *TMDB {tid} — S{s:02d}E{e:02d}*\n[▶️ Tap to Play]({url})",
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True
    )

# ---------- Generic Text Handler ----------
async def on_text(update, ctx):
    q = update.message.text.strip()
    if q:
        await _search(update, q, "multi")

# ---------- Search ----------
async def _search(update, query, kind="multi"):
    try:
        results = tmdb_search_cached(query, kind=kind)
    except Exception as e:
        return await update.message.reply_text(f"TMDB error: {e}")

    buttons = []
    for r in results[:8]:
        media = r.get("media_type") or kind
        if media not in ("movie", "tv"):
            continue
        name = r.get("title") or r.get("name") or "Unknown"
        year = (r.get("release_date") or r.get("first_air_date") or "")[:4]
        label = f"{name} ({year})" if year else name
        cb = f"pick|{media}|{r['id']}|0|0"
        buttons.append([InlineKeyboardButton(label, callback_data=cb)])

    if not buttons:
        return await update.message.reply_text("No playable results.")

    await update.message.reply_text(
        f"Results for *{query}*:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=ParseMode.MARKDOWN
    )

# ---------- Button Clicks ----------
async def on_button(update, ctx):
    q = update.callback_query
    await q.answer()
    if q.data == "noop":
        return

    try:
        _, media, tid, s, e = q.data.split("|")
        tid, s, e = int(tid), int(s), int(e)
    except Exception:
        return await q.edit_message_text("Bad button data.")

    # Movie
    if media == "movie":
        url = ss_url(tmdb=tid, kind="movie")
        title = tmdb_details(tid, "movie").get("title", f"TMDB {tid}")
        await _show_play(q, title, url)

    # TV Picker / Episode
    elif media == "tv":
        if s == 0 or e == 0:
            info = tmdb_details(tid, "tv")
            title = info.get("name", f"TMDB {tid}")
            rows = []
            for season in info.get("seasons", []):
                sn = season["season_number"]
                if sn == 0: continue
                rows.append([InlineKeyboardButton(
                    f"Season {sn}",
                    callback_data=f"season|{tid}|{sn}"
                )])
            rows.append([InlineKeyboardButton(
                "▶️ Play S01E01",
                callback_data=f"pick|tv|{tid}|1|1"
            )])
            await q.edit_message_text(
                f"*{title}* — pick a season:",
                reply_markup=InlineKeyboardMarkup(rows),
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            url = ss_url(tmdb=tid, kind="tv", season=s, episode=e)
            title = tmdb_details(tid, "tv").get("name", f"TMDB {tid}")
            await _show_play(q, f"{title} — S{s:02d}E{e:02d}", url, tid, s, e)

    # Season Episode List
    elif media == "season":
        sn = s
        try:
            data = tmdb_season(tid, sn)
        except Exception as ex:
            return await q.edit_message_text(f"Error: {ex}")
        title = tmdb_details(tid, "tv").get("name", f"TMDB {tid}")
        rows = []
        for ep in data.get("episodes", []):
            en = ep["episode_number"]
            name = (ep.get("name") or "")[:35]
            rows.append([InlineKeyboardButton(
                f"E{en:02d} — {name}",
                callback_data=f"pick|tv|{tid}|{sn}|{en}"
            )])
        await q.edit_message_text(
            f"*{title}* — Season {sn}:",
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode=ParseMode.MARKDOWN
        )

# ---------- Shared Play Message ----------
async def _show_play(q, title, url, tid=None, s=None, e=None):
    text = f"🎬 *{title}*\n\n[▶️ Tap to Play]({url})"
    rows = []
    if tid and s and e:
        rows.append([
            InlineKeyboardButton(
                "⬅️ Prev",
                callback_data=f"pick|tv|{tid}|{s}|{max(1, e-1)}"
            ),
            InlineKeyboardButton(
                "➡️ Next",
                callback_data=f"pick|tv|{tid}|{s}|{e+1}"
            ),
        ])
    await q.edit_message_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(rows) if rows else None,
        disable_web_page_preview=True
    )

# ---------- Main ----------
def main():
    # Validate config
    if not BOT_TOKEN or "PASTE" in BOT_TOKEN:
        raise SystemExit("❌ BOT_TOKEN missing in .env")
    if not TMDB_TOKEN or "PASTE" in TMDB_TOKEN:
        raise SystemExit("❌ TMDB_TOKEN missing in .env")
    if not UPSTASH_URL or "PASTE" in UPSTASH_URL:
        raise SystemExit("❌ UPSTASH_URL missing in .env")
    if not UPSTASH_TOKEN or "PASTE" in UPSTASH_TOKEN:
        raise SystemExit("❌ UPSTASH_TOKEN missing in .env")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("movie", cmd_movie))
    app.add_handler(CommandHandler("tv",    cmd_tv))
    app.add_handler(CommandHandler("id",    cmd_id))
    app.add_handler(CommandHandler("s",     cmd_season))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, on_text))

    log.info("Bot starting (polling)…")
    app.run_polling()

if __name__ == "__main__":
    main()
