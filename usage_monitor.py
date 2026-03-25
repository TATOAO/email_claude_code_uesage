#!/usr/bin/env python3
"""Claude Code usage monitor: scan JSONL sessions, store in SQLite, email hourly dashboard."""

import glob
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Template

HOOKS_DIR = Path(__file__).parent
DB_PATH = HOOKS_DIR / "usage.db"
TEMPLATE_PATH = HOOKS_DIR / "usage_template.html"

DEFAULT_CONFIG = {
    "reset_weekday": "4",
    "reset_hour": "13",
    "reset_minute": "59",
    "timezone": "Asia/Shanghai",
    "weekly_output_quota": "6000000",
    "weekly_input_quota": "",
    "short_window_hours": "5",
    "short_output_quota": "",
    "to_email": "w_wt_t@126.com",
}

CST = timezone(timedelta(hours=8))


def get_db():
    db = sqlite3.connect(str(DB_PATH))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS hourly_usage (
            date       TEXT     NOT NULL,
            hour       INTEGER  NOT NULL,
            model      TEXT     NOT NULL,
            input_tokens           INTEGER DEFAULT 0,
            output_tokens          INTEGER DEFAULT 0,
            cache_read_tokens      INTEGER DEFAULT 0,
            cache_creation_tokens  INTEGER DEFAULT 0,
            request_count          INTEGER DEFAULT 0,
            PRIMARY KEY (date, hour, model)
        );
        CREATE TABLE IF NOT EXISTS scan_progress (
            file_path     TEXT PRIMARY KEY,
            last_offset   INTEGER DEFAULT 0,
            last_modified REAL    DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS config (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    for k, v in DEFAULT_CONFIG.items():
        db.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", (k, v))
    db.commit()
    return db


def get_config(db):
    rows = db.execute("SELECT key, value FROM config").fetchall()
    return {k: v for k, v in rows}


def parse_timestamp(ts_raw):
    if isinstance(ts_raw, str):
        return datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).astimezone(CST)
    elif isinstance(ts_raw, (int, float)):
        if ts_raw > 1e12:
            ts_raw = ts_raw / 1000
        return datetime.fromtimestamp(ts_raw, tz=CST)
    return None


def scan_jsonl_files(db):
    base = Path.home() / ".claude" / "projects"
    files = glob.glob(str(base / "*" / "*.jsonl"))

    progress = {}
    for row in db.execute("SELECT file_path, last_offset, last_modified FROM scan_progress").fetchall():
        progress[row[0]] = {"offset": row[1], "mtime": row[2]}

    stale = [fp for fp in progress if not os.path.exists(fp)]
    for fp in stale:
        db.execute("DELETE FROM scan_progress WHERE file_path = ?", (fp,))
        del progress[fp]

    batch = []

    for fpath in files:
        try:
            stat = os.stat(fpath)
            mtime = stat.st_mtime
            size = stat.st_size

            prev = progress.get(fpath)
            if prev and prev["mtime"] == mtime:
                continue

            offset = prev["offset"] if prev and prev["mtime"] != mtime else 0
            if offset > size:
                offset = 0

            with open(fpath, "r", encoding="utf-8") as f:
                f.seek(offset)
                new_data = f.read()
                new_offset = f.tell()

            for line in new_data.splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    msg = entry.get("message", {})
                    if msg.get("role") != "assistant" or "usage" not in msg:
                        continue

                    dt = parse_timestamp(entry.get("timestamp"))
                    if dt is None:
                        continue

                    usage = msg["usage"]
                    model = msg.get("model", "unknown")
                    batch.append((
                        dt.strftime("%Y-%m-%d"),
                        dt.hour,
                        model,
                        usage.get("input_tokens", 0),
                        usage.get("output_tokens", 0),
                        usage.get("cache_read_input_tokens", 0),
                        usage.get("cache_creation_input_tokens", 0),
                    ))
                except (json.JSONDecodeError, KeyError):
                    continue

            db.execute(
                "INSERT INTO scan_progress (file_path, last_offset, last_modified) "
                "VALUES (?, ?, ?) ON CONFLICT(file_path) DO UPDATE SET "
                "last_offset = excluded.last_offset, last_modified = excluded.last_modified",
                (fpath, new_offset, mtime),
            )
        except Exception:
            continue

    for date, hour, model, inp, out, cache_r, cache_c in batch:
        db.execute(
            "INSERT INTO hourly_usage (date, hour, model, input_tokens, output_tokens, "
            "cache_read_tokens, cache_creation_tokens, request_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1) "
            "ON CONFLICT(date, hour, model) DO UPDATE SET "
            "input_tokens = input_tokens + excluded.input_tokens, "
            "output_tokens = output_tokens + excluded.output_tokens, "
            "cache_read_tokens = cache_read_tokens + excluded.cache_read_tokens, "
            "cache_creation_tokens = cache_creation_tokens + excluded.cache_creation_tokens, "
            "request_count = request_count + excluded.request_count",
            (date, hour, model, inp, out, cache_r, cache_c),
        )

    db.commit()
    return len(batch)


def get_week_boundaries(config):
    now = datetime.now(CST)
    reset_weekday = int(config.get("reset_weekday", "4"))
    reset_hour = int(config.get("reset_hour", "13"))
    reset_minute = int(config.get("reset_minute", "59"))

    days_since_reset = (now.weekday() - reset_weekday) % 7
    reset_today = now.replace(hour=reset_hour, minute=reset_minute, second=0, microsecond=0)

    if days_since_reset == 0 and now < reset_today:
        days_since_reset = 7

    week_start = (now - timedelta(days=days_since_reset)).replace(
        hour=reset_hour, minute=reset_minute, second=0, microsecond=0
    )
    week_end = week_start + timedelta(days=7)
    return week_start, week_end


def fmt_tokens(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def query_week_data(db, week_start, week_end):
    start_date = week_start.strftime("%Y-%m-%d")
    end_date = week_end.strftime("%Y-%m-%d")
    rows = db.execute(
        "SELECT date, hour, SUM(input_tokens), SUM(output_tokens), "
        "SUM(cache_read_tokens), SUM(cache_creation_tokens), SUM(request_count) "
        "FROM hourly_usage WHERE date >= ? AND date <= ? "
        "GROUP BY date, hour ORDER BY date, hour",
        (start_date, end_date),
    ).fetchall()

    data = {}
    for date, hour, inp, out, cr, cc, req in rows:
        data[(date, hour)] = {
            "input": inp, "output": out, "cache_read": cr,
            "cache_create": cc, "requests": req,
        }
    return data


def build_template_context(db, config):
    now = datetime.now(CST)
    week_start, week_end = get_week_boundaries(config)
    week_data = query_week_data(db, week_start, week_end)

    today_str = now.strftime("%Y-%m-%d")
    current_hour = now.hour
    prev_hour = current_hour - 1 if current_hour > 0 else 23
    prev_date = today_str if current_hour > 0 else (now - timedelta(days=1)).strftime("%Y-%m-%d")

    cur = week_data.get((today_str, current_hour), {"input": 0, "output": 0, "requests": 0})
    prev = week_data.get((prev_date, prev_hour), {"input": 0, "output": 0, "requests": 0})

    def delta_pct(cur_val, prev_val):
        if prev_val == 0:
            return ("up", 100) if cur_val > 0 else ("same", 0)
        pct = round((cur_val - prev_val) / prev_val * 100)
        if pct > 0:
            return ("up", pct)
        elif pct < 0:
            return ("down", abs(pct))
        return ("same", 0)

    hour_summary = {
        "hour_label": f"{current_hour:02d}:00 - {(current_hour + 1) % 24:02d}:00",
        "output": cur.get("output", 0),
        "output_fmt": fmt_tokens(cur.get("output", 0)),
        "input": cur.get("input", 0),
        "input_fmt": fmt_tokens(cur.get("input", 0)),
        "requests": cur.get("requests", 0),
        "output_delta": delta_pct(cur.get("output", 0), prev.get("output", 0)),
        "input_delta": delta_pct(cur.get("input", 0), prev.get("input", 0)),
        "requests_delta": delta_pct(cur.get("requests", 0), prev.get("requests", 0)),
        "has_activity": cur.get("output", 0) > 0 or cur.get("requests", 0) > 0,
    }

    week_totals = {"output": 0, "input": 0, "cache_read": 0, "requests": 0}
    active_days = set()
    for (date, hour), vals in week_data.items():
        week_totals["output"] += vals["output"]
        week_totals["input"] += vals["input"]
        week_totals["cache_read"] += vals.get("cache_read", 0)
        week_totals["requests"] += vals["requests"]
        if vals["output"] > 0 or vals["requests"] > 0:
            active_days.add(date)

    elapsed_days = (now - week_start).days + 1
    total_days = 7

    weekly_output_quota = config.get("weekly_output_quota", "")
    quota_int = int(weekly_output_quota) if weekly_output_quota else 0

    week_overview = {
        "period_start": week_start.strftime("%m-%d %a %H:%M"),
        "period_end": week_end.strftime("%m-%d %a %H:%M"),
        "output_total": week_totals["output"],
        "output_total_fmt": fmt_tokens(week_totals["output"]),
        "input_total": week_totals["input"],
        "input_total_fmt": fmt_tokens(week_totals["input"]),
        "requests_total": week_totals["requests"],
        "active_days": len(active_days),
        "elapsed_days": min(elapsed_days, total_days),
        "total_days": total_days,
        "time_pct": round(min(elapsed_days, total_days) / total_days * 100),
        "has_quota": quota_int > 0,
        "quota_fmt": fmt_tokens(quota_int) if quota_int else "",
        "quota_pct": round(week_totals["output"] / quota_int * 100) if quota_int else 0,
    }

    short_hours = int(config.get("short_window_hours", "5"))
    short_output = 0
    for i in range(short_hours):
        t = now - timedelta(hours=i)
        d = t.strftime("%Y-%m-%d")
        h = t.hour
        vals = week_data.get((d, h), {"output": 0})
        short_output += vals["output"]

    short_quota_str = config.get("short_output_quota", "")
    short_quota_int = int(short_quota_str) if short_quota_str else 0
    week_overview["short_output"] = short_output
    week_overview["short_output_fmt"] = fmt_tokens(short_output)
    week_overview["short_hours"] = short_hours
    week_overview["has_short_quota"] = short_quota_int > 0
    week_overview["short_quota_fmt"] = fmt_tokens(short_quota_int) if short_quota_int else ""
    week_overview["short_quota_pct"] = round(short_output / short_quota_int * 100) if short_quota_int else 0

    max_output = 1
    for day_offset in range(7):
        day_dt = week_start + timedelta(days=day_offset)
        day_str = day_dt.strftime("%Y-%m-%d")
        for h in range(24):
            if day_offset == 0 and h < week_start.hour:
                continue
            vals = week_data.get((day_str, h), {"output": 0})
            if vals["output"] > max_output:
                max_output = vals["output"]

    heatmap = []
    for day_offset in range(7):
        day_dt = week_start + timedelta(days=day_offset)
        day_str = day_dt.strftime("%Y-%m-%d")
        day_label = day_dt.strftime("%a %d")
        is_today = day_str == today_str
        is_future = day_dt.date() > now.date()

        hours = []
        for h in range(24):
            vals = week_data.get((day_str, h), {"output": 0})
            out = vals["output"]
            if out == 0:
                level = 0
            else:
                ratio = out / max_output
                if ratio < 0.1:
                    level = 1
                elif ratio < 0.25:
                    level = 2
                elif ratio < 0.4:
                    level = 3
                elif ratio < 0.6:
                    level = 4
                elif ratio < 0.8:
                    level = 5
                else:
                    level = 6

            show_value = fmt_tokens(out) if level >= 4 else ""
            is_current = is_today and h <= current_hour
            hours.append({
                "level": level if not is_future else 0,
                "value": show_value if not is_future else "",
                "is_today_active": is_today and is_current and out > 0,
            })

        heatmap.append({
            "label": day_label,
            "is_today": is_today,
            "is_future": is_future,
            "hours": hours,
        })

    daily_stats = []
    max_daily_output = 1
    for day_offset in range(7):
        day_dt = week_start + timedelta(days=day_offset)
        day_str = day_dt.strftime("%Y-%m-%d")
        day_out = sum(v["output"] for (d, h), v in week_data.items() if d == day_str)
        if day_out > max_daily_output:
            max_daily_output = day_out

    for day_offset in range(7):
        day_dt = week_start + timedelta(days=day_offset)
        day_str = day_dt.strftime("%Y-%m-%d")
        is_today = day_str == today_str
        is_future = day_dt.date() > now.date()

        day_out = sum(v["output"] for (d, h), v in week_data.items() if d == day_str)
        day_inp = sum(v["input"] for (d, h), v in week_data.items() if d == day_str)
        day_req = sum(v["requests"] for (d, h), v in week_data.items() if d == day_str)

        daily_stats.append({
            "date": day_dt.strftime("%m-%d"),
            "weekday": day_dt.strftime("%a"),
            "output": day_out,
            "output_fmt": fmt_tokens(day_out),
            "input": day_inp,
            "input_fmt": fmt_tokens(day_inp),
            "requests": day_req,
            "bar_pct": round(day_out / max_daily_output * 100) if not is_future else 0,
            "is_today": is_today,
            "is_future": is_future,
        })

    return {
        "hour_summary": hour_summary,
        "week_overview": week_overview,
        "heatmap": heatmap,
        "daily_stats": daily_stats,
        "week_totals": {
            "output_fmt": fmt_tokens(week_totals["output"]),
            "input_fmt": fmt_tokens(week_totals["input"]),
            "requests": week_totals["requests"],
        },
        "generated_at": now.strftime("%Y-%m-%d %H:%M CST"),
        "next_reset": week_end.strftime("%m-%d %a %H:%M"),
    }


def should_send(db, config, hour_summary):
    now = datetime.now(CST)
    reset_weekday = int(config.get("reset_weekday", "4"))
    reset_hour = int(config.get("reset_hour", "13"))

    if now.weekday() == reset_weekday and reset_hour - 1 <= now.hour <= reset_hour:
        return True, True

    if hour_summary["has_activity"]:
        return True, False

    today_str = now.strftime("%Y-%m-%d")
    prev_hour = now.hour - 1 if now.hour > 0 else 23
    prev_date = today_str if now.hour > 0 else (now - timedelta(days=1)).strftime("%Y-%m-%d")
    prev_data = db.execute(
        "SELECT SUM(output_tokens), SUM(request_count) FROM hourly_usage "
        "WHERE date = ? AND hour = ?", (prev_date, prev_hour)
    ).fetchone()
    if prev_data and (prev_data[0] or prev_data[1]):
        return True, False

    return False, False


def send_email(to_email, subject, html_body):
    email = (
        f"Subject: {subject}\n"
        f"MIME-Version: 1.0\n"
        f"Content-Type: text/html; charset=utf-8\n"
        f"\n"
        f"{html_body}"
    )
    subprocess.run(
        ["msmtp", to_email],
        input=email.encode("utf-8"),
        timeout=15,
    )


def main():
    db = get_db()
    config = get_config(db)

    new_records = scan_jsonl_files(db)

    ctx = build_template_context(db, config)

    do_send, is_summary = should_send(db, config, ctx["hour_summary"])
    if not do_send:
        db.close()
        return

    try:
        tpl = Template(TEMPLATE_PATH.read_text("utf-8"))
        html = tpl.render(**ctx)
    except Exception as e:
        print(f"Template render failed: {e}", file=sys.stderr)
        db.close()
        return

    to_email = config.get("to_email", "w_wt_t@126.com")
    wo = ctx["week_overview"]
    if is_summary:
        subject = f"[周报] Claude Code 用量 - {wo['output_total_fmt']} output"
    else:
        subject = f"[用量] Claude Code - {ctx['hour_summary']['output_fmt']} / 本周 {wo['output_total_fmt']}"

    try:
        send_email(to_email, subject, html)
    except Exception as e:
        print(f"Email send failed: {e}", file=sys.stderr)

    db.close()


if __name__ == "__main__":
    main()
