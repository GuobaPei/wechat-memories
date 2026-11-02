#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 events.json 和聊天导出做成一个单文件回忆页面。只用标准库，不联网。

用法：
  python3 build_page.py --messages messages.json --events events.json \\
      --me 小鱼 --them 阿木 --out page.html
  加 --check 只核对，不写文件。

核对没过就不写页面，退出码 1：
  1. 每句原话按说话人和事件日期（前后各放宽 1 天）在导出里找得到；
  2. 文案规则：第三人称、1 到 2 句、最多 4 句原话；
  3. 页面里没有长数字、wxid、密码、验证码、外链，也没有 --ban-file 里列的词。
"""
import argparse
import datetime as dt
import html
import json
import re
import sys
from collections import Counter
from pathlib import Path

KINDS = {"开心": "happy", "吵架和难过": "hard", "大事": "big"}
WEEK = "一二三四五六日"

LIGHT = {
    "bg": "#EDEDED", "ink": "#191919", "ink-2": "#454545", "ink-3": "#5F5F5F",
    "hair": "#D2D2D2", "axis": "#ABABAB", "wash": "rgba(0,0,0,.06)",
    "stamp-bg": "#DADADA", "stamp-ink": "#555555",
    "me": "#95EC69", "me-ink": "#10190A", "them": "#FFFFFF", "them-ink": "#191919",
    "avatar": "#D0D0D0", "avatar-ink": "#3A3A3A",
    "bar": "#0A8F4C", "bar-hover": "#077A40",
    "tip-bg": "#191919", "tip-ink": "#FFFFFF",
    "press-bg": "#191919", "press-ink": "#FFFFFF",
    "k-happy": "#C8730A", "k-hard": "#2F6FDE", "k-big": "#B5338A",
    "focus": "#0B6E3B",
}
DARK = {
    "bg": "#111111", "ink": "#E8E8E8", "ink-2": "#C2C2C2", "ink-3": "#A0A0A0",
    "hair": "#2A2A2A", "axis": "#4D4D4D", "wash": "rgba(255,255,255,.07)",
    "stamp-bg": "#272727", "stamp-ink": "#ABABAB",
    "me": "#256340", "me-ink": "#EEF7F1", "them": "#2C2C2C", "them-ink": "#E8E8E8",
    "avatar": "#333333", "avatar-ink": "#CFCFCF",
    "bar": "#2FA76A", "bar-hover": "#45BC80",
    "tip-bg": "#E8E8E8", "tip-ink": "#111111",
    "press-bg": "#E8E8E8", "press-ink": "#111111",
    "k-happy": "#C98420", "k-hard": "#4F8AE0", "k-big": "#C956A3",
    "focus": "#7BD3A2",
}


# ---------- 读数据 ----------

def when(m, tz):
    t = float(m["ts"])
    return dt.datetime.fromtimestamp(t / 1000 if t > 1e11 else t, tz)  # 有的导出用毫秒


def kind_of(m):
    return int(m.get("msg_type", 1))


def text_of(m):
    return m["content"] if isinstance(m.get("content"), str) else ""


def body(m):
    """消息正文。引用消息只取回复部分，不算被引用的内容。"""
    c = text_of(m)
    if kind_of(m) == 49:
        hit = re.match(r"^\[引用 .*?\] ↳svrid:\d+ @\d{4}-\d{2}-\d{2} (.*)$", c, re.S)
        if hit:
            return hit.group(1)
    return c


def d(s):
    return dt.date.fromisoformat(s)


def compute_stats(raw, tz, span, count_word):
    seen = Counter(when(m, tz).strftime("%Y-%m") for m in raw)
    first, last = when(raw[0], tz).date(), when(raw[-1], tz).date()
    months, y, m = {}, first.year, first.month
    while (y, m) <= (last.year, last.month):  # 没聊天的月份也留着，沉默也是记录
        months[f"{y}-{m:02d}"] = seen.get(f"{y}-{m:02d}", 0)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    a, b = (d(span[0]), d(span[1])) if span else (first, last)
    return {
        "total": len(raw),
        "days": (b - a).days + 1,
        "stickers": sum(1 for x in raw if kind_of(x) == 47),
        "word_msgs": sum(1 for x in raw if kind_of(x) == 1 and count_word in text_of(x)) if count_word else 0,
        "months": months,
        "first": first,
        "last": last,
    }


# ---------- 核对 ----------

def check_quotes(events, raw, who, tz):
    index = {}
    for m in raw:
        if kind_of(m) in (1, 49):
            index.setdefault((when(m, tz).date(), bool(m["is_self"])), []).append(body(m))
    fails, partial, total = [], [], 0
    for e in events:
        start = d(e["date"]) - dt.timedelta(days=1)
        stop = d(e.get("end") or e["date"]) + dt.timedelta(days=1)
        days = [start + dt.timedelta(days=i) for i in range((stop - start).days + 1)]
        for q in e.get("quotes", []):
            total += 1
            pool = [t for day in days for t in index.get((day, who.get(q["who"])), [])]
            if any(t.strip() == q["text"] for t in pool):
                continue
            if any(q["text"] in t for t in pool):
                partial.append((e["date"], q["who"], q["text"]))
            else:
                fails.append((e["date"], e["title"], q["who"], q["text"]))
    return total, fails, partial


def outside_quotes(s):
    return re.sub(r"「[^」]*」", "", s)


def check_rules(events, who):
    problems, last = [], ""
    for e in events:
        tag = f'{e["date"]} {e["title"]}'
        if e["kind"] not in KINDS:
            problems.append(f"{tag}: kind 不对")
        if e["date"] < last:
            problems.append(f"{tag}: 日期没按顺序")
        last = e["date"]
        if e.get("end") and e["end"] < e["date"]:
            problems.append(f"{tag}: end 早于 date")
        if len(e.get("quotes", [])) > 4:
            problems.append(f"{tag}: 原话超过 4 句")
        for q in e.get("quotes", []):
            if q["who"] not in who:
                problems.append(f"{tag}: who 不对")
        plain = outside_quotes(e["title"] + "。" + e["text"])
        for name in who:
            plain = plain.replace(name, "")
        hit = re.search(r"[你她他我咱]", plain)
        if hit:
            problems.append(f"{tag}: 描述里有人称代词“{hit.group()}”（“自我介绍”这类词也算，换个说法）")
        if not 1 <= len(re.findall(r"[。！？]", outside_quotes(e["text"]))) <= 2:
            problems.append(f"{tag}: 描述要 1 到 2 句")
    return problems


def scan(text, raw, allowed, banned):
    lowered = text.lower()
    shown = set()  # 系统消息里的显示名常常是真名或备注
    for m in raw:
        if kind_of(m) == 10000:
            hit = re.search(r'"([^"]+)" 撤回', text_of(m))
            if hit:
                shown.add(hit.group(1))
    return {
        "连续7位以上数字": len(re.findall(r"\d{7,}", text)),
        "wxid": lowered.count("wxid"),
        "密码": text.count("密码") + lowered.count("password"),
        "验证码": text.count("验证码"),
        "账号": text.count("账号"),
        "邮箱": len(re.findall(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", text, re.I)),
        "外链": len(re.findall(r"https?:|//[a-z0-9.-]+\.[a-z]{2,}", text, re.I)),
        "外部资源": len(re.findall(r"\bsrc\s*=|url\(|@import", text, re.I)),
        "--ban-file 里的词": sum(w.lower() in lowered for w in banned),
        "系统消息里的显示名": sum(n in text for n in shown - allowed),
    }


# ---------- 页面 ----------

def esc(s):
    return html.escape(s, quote=True)


def num(n):
    return f"{n:,}"


def month_label(key):
    y, m = key.split("-")
    return f"{y} 年 {int(m)} 月"


def stamp(e):
    a = d(e["date"])
    if not e.get("end"):
        return f"{a.month} 月 {a.day} 日 周{WEEK[a.weekday()]}"
    b = d(e["end"])
    if a.year != b.year:
        return f"{a.year} 年 {a.month} 月 {a.day} 日至 {b.year} 年 {b.month} 月 {b.day} 日"
    if a.month != b.month:
        return f"{a.month} 月 {a.day} 日至 {b.month} 月 {b.day} 日"
    return f"{a.month} 月 {a.day} 日至 {b.day} 日"


def render_event(e, who):
    kind = KINDS[e["kind"]]
    bubbles = []
    for q in e.get("quotes", []):
        side = "me" if who[q["who"]] else "them"
        bubbles.append(
            f'<li class="b {side}"><span class="av" aria-hidden="true">{esc(q["who"][-1])}</span>'
            f'<p class="bubble"><span class="sr">{esc(q["who"])}：</span>{esc(q["text"])}</p></li>'
        )
    chat = f'<ul class="chat">{"".join(bubbles)}</ul>' if bubbles else ""
    return (
        f'<article class="ev" data-kind="{kind}">'
        f'<p class="stamp"><time datetime="{e["date"]}">{stamp(e)}</time></p>'
        f'<header class="ev-head"><span class="tag"><i class="dot" aria-hidden="true"></i>{e["kind"]}</span>'
        f'<h3>{esc(e["title"])}</h3></header>'
        f'<p class="ev-text">{esc(e["text"])}</p>{chat}</article>'
    )


def render_chart(stats):
    months = stats["months"]
    keys = list(months)
    top = max(months.values())
    peak = max(keys, key=lambda k: months[k])
    bars = []
    for i, k in enumerate(keys):
        pct = months[k] / top * 100
        side = "l" if i < len(keys) / 2 else "r"
        tip = f"{month_label(k)} · {num(months[k])} 条"
        peak_label = f'<span class="peak">{num(months[k])}</span>' if k == peak else ""
        bars.append(
            f'<li><a class="hit {side}" href="#m-{k}" data-month="{k}" style="--h:{pct:.2f}%" '
            f'aria-label="{esc(tip)}，跳到这个月" data-tip="{esc(tip)}">'
            f'<span class="bar"></span>{peak_label}</a></li>'
        )
    unit = next(u for e in range(len(str(top))) for u in (10 ** e, 2 * 10 ** e, 5 * 10 ** e) if top / u < 5)
    grid = "".join(
        f'<div class="grid" style="bottom:{v / top * 100:.2f}%" aria-hidden="true"><span>{num(v)}</span></div>'
        for v in (unit, 2 * unit) if v < top
    )
    n, span = len(keys), min(6, len(keys))
    ticks = []
    for i, k in enumerate(keys):
        y, m = k.split("-")
        label = f"{y}.{int(m)}"
        if i == 0:
            ticks.append(f'<span style="grid-column:1 / span {span}">{label}</span>')
        elif i == n - 1:
            ticks.append(f'<span class="end" style="grid-column:{i + 2 - span} / span {span}">{label}</span>')
        elif m == "01":
            ticks.append(f'<span style="grid-column:{i + 1} / span {span}">{label}</span>')
    rows = "".join(
        f'<tr><td><a href="#m-{k}" data-month="{k}">{month_label(k)}</a></td><td>{num(v)}</td></tr>'
        for k, v in months.items()
    )
    return f"""<section class="chart" aria-labelledby="chart-title">
<h2 id="chart-title">每月消息量</h2>
<div class="plot-wrap" style="--cols:{n}">
<div class="plot">{grid}<ol class="bars">{"".join(bars)}</ol></div>
<div class="xaxis" aria-hidden="true">{"".join(ticks)}</div>
</div>
<details class="data"><summary>每月数据表</summary>
<table><thead><tr><th scope="col">月份</th><th scope="col">消息数</th></tr></thead>
<tbody>{rows}</tbody>
<tfoot><tr><th scope="row">合计</th><td>{num(stats["total"])}</td></tr></tfoot></table>
</details>
</section>"""


def tokens(tok):
    return ";".join(f"--{k}:{v}" for k, v in tok.items())


CSS = """
:root{/*LIGHT*/;color-scheme:light;
--sans:"PingFang SC","Hiragino Sans GB","Noto Sans CJK SC","Microsoft YaHei",system-ui,sans-serif;
--serif:"Songti SC","Noto Serif CJK SC","Source Han Serif SC",STSong,SimSun,serif}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {/*DARK*/; color-scheme: dark} }
:root[data-theme="dark"] {/*DARK*/; color-scheme: dark}
*{box-sizing:border-box}
[hidden]{display:none!important}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);font-size:15px;line-height:1.6;overflow-wrap:anywhere}
.wrap{box-sizing:content-box;max-width:720px;margin-left:auto;margin-right:auto;padding-left:max(16px, env(safe-area-inset-left, 0px));padding-right:max(16px, env(safe-area-inset-right, 0px))}
.sr{position:absolute;width:1px;height:1px;margin:-1px;padding:0;overflow:hidden;clip-path:inset(50%);white-space:nowrap;border:0}
a{color:inherit}
:focus-visible{outline:2px solid var(--focus);outline-offset:2px;border-radius:2px}

.hero{padding-top:36px;padding-bottom:12px}
h1{font-family:var(--serif);font-size:34px;line-height:1.25;font-weight:600;letter-spacing:.04em;margin:0}
.range{margin:6px 0 26px;color:var(--ink-3);font-size:14px}
.overview{display:grid;gap:24px}
.stats{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px 16px;margin:0}
.stats div{display:flex;flex-direction:column-reverse;justify-content:flex-end;min-width:0}
.stats dt{font-size:13px;line-height:1.4;color:var(--ink-3)}
.stats dd{margin:0;font-size:26px;line-height:1.2;font-weight:600}
.chart h2{font-size:13px;line-height:1.4;font-weight:400;color:var(--ink-3);margin:0}
.plot-wrap{max-width:max(calc(var(--cols) * 26px),180px)}
.plot{position:relative;height:132px;margin-top:22px;border-bottom:1px solid var(--axis)}
.grid{position:absolute;left:0;right:0;border-top:1px solid var(--hair)}
.grid span{position:absolute;right:0;bottom:1px;font-size:11px;line-height:1.2;color:var(--ink-3);font-variant-numeric:tabular-nums}
.bars{position:relative;list-style:none;margin:0;padding:0;height:100%;display:grid;grid-template-columns:repeat(var(--cols),minmax(0,1fr))}
.bars li{height:100%;min-width:0}
.hit{position:relative;display:flex;align-items:flex-end;height:100%;padding:0 1px;text-decoration:none}
.bar{display:block;width:100%;height:var(--h);background:var(--bar);border-radius:4px 4px 0 0}
.peak{position:absolute;left:1px;bottom:calc(var(--h) + 3px);font-size:12px;line-height:1.2;color:var(--ink-2);white-space:nowrap;font-variant-numeric:tabular-nums}
.hit:hover{background:var(--wash)}
.hit:hover .bar,.hit:focus-visible .bar{background:var(--bar-hover)}
.hit::after{content:attr(data-tip);display:none;position:absolute;z-index:3;bottom:calc(var(--h) + 8px);padding:4px 8px;border-radius:4px;background:var(--tip-bg);color:var(--tip-ink);font-size:12px;line-height:1.4;white-space:nowrap;pointer-events:none;font-variant-numeric:tabular-nums}
.hit.l::after{left:0}
.hit.r::after{right:0}
.hit:hover::after,.hit:focus-visible::after{display:block}
.xaxis{display:grid;grid-template-columns:repeat(var(--cols),minmax(0,1fr));margin-top:5px;font-size:11px;line-height:1.3;color:var(--ink-3);font-variant-numeric:tabular-nums}
.xaxis span{grid-row:1;white-space:nowrap}
.xaxis .end{text-align:right}
.data{margin-top:12px;font-size:13px;color:var(--ink-2)}
.data summary{cursor:pointer;width:max-content;max-width:100%;padding:2px 0}
.data table{border-collapse:collapse;margin-top:8px;width:100%;max-width:320px;font-variant-numeric:tabular-nums}
.data th,.data td{padding:4px 0;border-bottom:1px solid var(--hair);text-align:left;font-weight:400}
.data thead th{color:var(--ink-3)}
.data td:last-child,.data thead th:last-child{text-align:right}
.data tfoot th,.data tfoot td{font-weight:600;border-bottom:0}

.filters{display:none;position:sticky;top:0;z-index:10;background:var(--bg);margin-top:20px;border-bottom:1px solid var(--bg)}
.js .filters{display:block}
.filters.stuck{border-bottom-color:var(--hair)}
.filter-row{display:flex;flex-wrap:wrap;gap:6px;padding-top:max(10px, env(safe-area-inset-top, 0px));padding-bottom:10px}
.f{appearance:none;font:inherit;font-size:14px;line-height:1.2;min-height:36px;padding:6px 10px;display:inline-flex;align-items:center;gap:5px;color:var(--ink);background:var(--bg);border:1px solid var(--axis);border-radius:6px;cursor:pointer;white-space:nowrap}
.f .n{color:var(--ink-3);font-variant-numeric:tabular-nums}
.f:hover{background:var(--wash)}
.f[aria-pressed="true"]{background:var(--press-bg);border-color:var(--press-bg);color:var(--press-ink)}
.f[aria-pressed="true"] .n{color:inherit}

.dot{display:inline-block;flex:none;width:8px;height:8px;border-radius:2px;background:var(--k)}
[data-kind="happy"]{--k:var(--k-happy)}
[data-kind="hard"]{--k:var(--k-hard)}
[data-kind="big"]{--k:var(--k-big)}

.month{scroll-margin-top:64px}
.month-title{display:flex;justify-content:space-between;align-items:baseline;gap:12px;font-family:var(--serif);font-size:24px;line-height:1.3;font-weight:600;letter-spacing:.02em;margin:40px 0 24px}
.month-title small{font-family:var(--sans);font-size:13px;font-weight:400;letter-spacing:0;color:var(--ink-3);white-space:nowrap}
.ev{margin:0 0 40px}
.stamp{margin:0 0 14px;text-align:center;line-height:1}
.stamp time{display:inline-block;padding:5px 8px;border-radius:4px;background:var(--stamp-bg);color:var(--stamp-ink);font-size:12px;line-height:1.25}
.ev-head{display:flex;flex-wrap:wrap;align-items:center;gap:2px 8px;margin:0 0 4px}
.ev-head h3{margin:0;font-size:16px;line-height:1.45;font-weight:600}
.tag{display:inline-flex;align-items:center;gap:5px;font-size:12px;line-height:1.45;color:var(--ink-2);white-space:nowrap}
.ev-text{margin:0 0 12px;color:var(--ink-2);line-height:1.7}
.chat{list-style:none;margin:0;padding:0}
.b{display:flex;align-items:flex-start;gap:9px;margin:10px 0 0}
.b.me{flex-direction:row-reverse}
.av{flex:none;width:34px;height:34px;display:flex;align-items:center;justify-content:center;border-radius:4px;background:var(--avatar);color:var(--avatar-ink);font-size:14px;line-height:1}
.bubble{position:relative;margin:0;max-width:min(calc(100% - 92px),500px);padding:7px 11px;border-radius:5px;font-size:15px;line-height:1.55}
.them .bubble{background:var(--them);color:var(--them-ink)}
.me .bubble{background:var(--me);color:var(--me-ink)}
.bubble::before{content:"";position:absolute;top:11px;width:0;height:0;border:6px solid transparent}
.them .bubble::before{left:-11px;border-right-color:var(--them)}
.me .bubble::before{right:-11px;border-left-color:var(--me)}
.foot{margin-top:8px;padding-top:16px;padding-bottom:max(36px, env(safe-area-inset-bottom, 0px));color:var(--ink-3);font-size:13px;line-height:1.6}
.foot p{margin:0;border-top:1px solid var(--hair);padding-top:14px}

@media (min-width:760px){
.overview{grid-template-columns:150px minmax(0,1fr);gap:40px}
.stats{grid-template-columns:minmax(0,1fr);gap:12px;align-content:start;padding-top:2px}
.stats dd{font-size:24px}
}
@media (max-width:519px){
.month{scroll-margin-top:104px}
.filter-row{gap:4px}
.f{font-size:13px;padding:6px 7px}
.bubble{max-width:calc(100% - 60px)}
}
@media (prefers-reduced-motion: no-preference){
html{scroll-behavior:smooth}
.f,.hit{transition:background-color .12s ease}
}
@media (forced-colors: active){
.bar,.dot{forced-color-adjust:none;background:CanvasText}
.f[aria-pressed="true"]{outline:2px solid CanvasText}
}
"""

JS = """
(function(){
var btns=[].slice.call(document.querySelectorAll('.f'));
var evs=[].slice.call(document.querySelectorAll('.ev'));
var months=[].slice.call(document.querySelectorAll('.month'));
var bar=document.getElementById('filters');
var mark=document.getElementById('filter-mark');
var status=document.getElementById('filter-status');
function apply(kind,moveUp){
  var shown=0;
  btns.forEach(function(b){b.setAttribute('aria-pressed',b.getAttribute('data-kind')===kind?'true':'false')});
  evs.forEach(function(e){var on=kind==='all'||e.getAttribute('data-kind')===kind;e.hidden=!on;if(on)shown++});
  months.forEach(function(m){m.hidden=!m.querySelector('.ev:not([hidden])')});
  status.textContent='显示 '+shown+' 条';
  if(moveUp&&mark.getBoundingClientRect().top<0){mark.scrollIntoView()}
}
btns.forEach(function(b){b.addEventListener('click',function(){apply(b.getAttribute('data-kind'),true)})});
[].slice.call(document.querySelectorAll('a[data-month]')).forEach(function(a){
  a.addEventListener('click',function(){
    var t=document.getElementById('m-'+a.getAttribute('data-month'));
    if(t&&t.hidden){apply('all',false)}
  });
});
if('IntersectionObserver' in window){
  new IntersectionObserver(function(en){bar.classList.toggle('stuck',!en[0].isIntersecting)}).observe(mark);
}
})();
"""


def render_page(events, stats, who, title, span_label, count_word):
    counts = Counter(e["kind"] for e in events)
    buttons = [f'<button type="button" class="f" data-kind="all" aria-pressed="true">全部 <span class="n">{len(events)}</span></button>']
    for label, kind in KINDS.items():
        buttons.append(
            f'<button type="button" class="f" data-kind="{kind}" aria-pressed="false">'
            f'<i class="dot" aria-hidden="true"></i>{label} <span class="n">{counts[label]}</span></button>'
        )
    sections = []
    for key in sorted({e["date"][:7] for e in events} | set(stats["months"])):
        group = [e for e in events if e["date"][:7] == key]
        if not group:
            continue
        body_html = "".join(render_event(e, who) for e in group)
        sections.append(
            f'<section class="month" id="m-{key}" aria-label="{month_label(key)}">'
            f'<h2 class="month-title">{month_label(key)}<small>{num(stats["months"].get(key, 0))} 条消息</small></h2>{body_html}</section>'
        )
    first, last = stats["first"], stats["last"]
    css = CSS.replace("/*LIGHT*/", tokens(LIGHT)).replace("/*DARK*/", tokens(DARK))
    stat_items = [
        (num(stats["total"]), "条消息"),
        (num(stats["days"]), span_label),
        (num(stats["stickers"]), "个表情包"),
    ]
    if count_word:
        stat_items.append((num(stats["word_msgs"]), f"条文字消息里有“{esc(count_word)}”"))
    stats_html = "".join(f"<div><dt>{b}</dt><dd>{a}</dd></div>" for a, b in stat_items)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'">
<meta name="color-scheme" content="light dark">
<meta name="referrer" content="no-referrer">
<title>{esc(title)}</title>
<link rel="icon" href="data:,">
<script>document.documentElement.className='js'</script>
<style>{css}</style>
</head>
<body>
<header class="hero wrap">
<h1>{esc(title)}</h1>
<p class="range">{first.year} 年 {first.month} 月 {first.day} 日到 {last.year} 年 {last.month} 月 {last.day} 日</p>
<div class="overview">
<dl class="stats">{stats_html}</dl>
{render_chart(stats)}
</div>
</header>
<main>
<div id="filter-mark"></div>
<div class="filters" id="filters">
<div class="filter-row wrap" role="group" aria-label="按类型筛选">{"".join(buttons)}</div>
<p class="sr" id="filter-status" role="status" aria-live="polite"></p>
</div>
<div class="wrap">
{"".join(sections)}
</div>
</main>
<footer class="foot wrap"><p>根据聊天文字整理，通话、见面、图片、语音和撤回的消息看不到；页面只存在本地。</p></footer>
<script>{JS}</script>
</body>
</html>
"""


def main(argv=None):
    p = argparse.ArgumentParser(description="把聊天导出和事件整理成单文件回忆页面")
    p.add_argument("--messages", required=True, help="聊天导出 JSON（只读 ts、content、is_self、msg_type）")
    p.add_argument("--events", required=True, help="事件 JSON，格式见 SKILL.md")
    p.add_argument("--me", required=True, help="页面上“自己”的称呼，对应 is_self=true")
    p.add_argument("--them", required=True, help="页面上另一个人的称呼")
    p.add_argument("--out", help="输出的 HTML 路径")
    p.add_argument("--title", help="页面标题，默认用两个称呼")
    p.add_argument("--tz-hours", type=float, default=8, help="两个人当时所在地的 UTC 偏移，默认 8（别用电脑当前时区）")
    p.add_argument("--span", nargs=2, metavar=("START", "END"), help="统计天数的起止日期，比如在一起的那段时间")
    p.add_argument("--span-label", help="天数的说明文字")
    p.add_argument("--count-word", help="顶部多统计一个词，比如 晚安")
    p.add_argument("--ban-file", help="本机文本文件，一行一个不许出现的词（真名、房间号等）")
    p.add_argument("--allow", nargs="*", default=[], help="系统消息里出现、但允许上页面的显示名")
    p.add_argument("--check", action="store_true", help="只核对，不写文件")
    a = p.parse_args(argv)
    if not a.check and not a.out:
        p.error("需要 --out，或者加 --check")

    tz = dt.timezone(dt.timedelta(hours=a.tz_hours))
    who = {a.me: True, a.them: False}
    raw = json.load(open(a.messages, encoding="utf-8"))
    raw.sort(key=lambda m: float(m["ts"]))
    events = json.load(open(a.events, encoding="utf-8"))
    banned = Path(a.ban_file).read_text(encoding="utf-8").split("\n") if a.ban_file else []
    banned = [w.strip() for w in banned if w.strip()]

    stats = compute_stats(raw, tz, a.span, a.count_word)
    total, fails, partial = check_quotes(events, raw, who, tz)
    rules = check_rules(events, who)
    counts = Counter(e["kind"] for e in events)
    print(f"事件 {len(events)} 件：" + "，".join(f"{k} {counts[k]}" for k in KINDS))
    print(f"原话 {total} 句，找不到 {len(fails)}，只是长消息片段 {len(partial)}")
    for f in fails:
        print("  找不到：", *f)
    print(f"文案问题 {len(rules)}")
    for r in rules:
        print("  ", r)
    event_months = set()
    for e in events:
        start, stop = d(e["date"]), d(e.get("end") or e["date"])
        event_months |= {(start + dt.timedelta(days=i)).strftime("%Y-%m") for i in range((stop - start).days + 1)}
    gaps = [k for k, v in stats["months"].items() if v and k not in event_months]
    if gaps:
        print("提醒：这些月份有聊天但没有事件（只有寒暄的话不用硬凑）：", " ".join(gaps))

    span_label = a.span_label or ("天在一起（首尾都算）" if a.span else "天（第一条到最后一条）")
    page = render_page(events, stats, who, a.title or f"{a.me}和{a.them}", span_label, a.count_word)
    hits = {k: v for k, v in scan(page, raw, set(who) | set(a.allow), banned).items() if v}
    print("页面扫描：", hits or "干净")
    if fails or rules or hits:
        print("核对没过，不写页面")
        return 1
    if not a.check:
        Path(a.out).write_text(page, encoding="utf-8")
        print("已写出", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
