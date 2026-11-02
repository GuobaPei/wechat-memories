#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把聊天导出压成按天排列的文字稿，分段存放，方便一段一段读完。

每段里插入随机校验码，读的人把校验码抄进笔记，就能确认真的读到了结尾。

用法：
  python3 make_transcript.py messages.json work/ [--tz-hours 8] [--chunk-chars 70000]
      写出 work/chunks/cNN.txt 和 work/codes.json
  python3 make_transcript.py --verify work/
      检查 work/notes/cNN.md 里有没有抄全校验码

文字稿格式：“我”是 is_self=true，“对”是另一个人，“系”是系统消息；
连续的消息用 ｜ 隔开，〔x〕是表情，「引我:…」是引用回复。
"""
import argparse
import collections
import datetime as dt
import glob
import json
import os
import re
import secrets
import sys

WK = "一二三四五六日"


def seconds(m):
    t = float(m["ts"])
    return t / 1000 if t > 1e11 else t  # 有的导出用毫秒


def kind_of(m):
    return int(m.get("msg_type", 1))


def text_of(m):
    return m["content"] if isinstance(m.get("content"), str) else ""


def build(messages, out, tz_hours, target):
    tz = dt.timezone(dt.timedelta(hours=tz_hours))
    msgs = sorted(json.load(open(messages, encoding="utf-8")), key=seconds)

    # 引用里的显示名是谁：拿被引用的文字去对之前的消息
    said = {}
    for m in msgs:
        if kind_of(m) == 1:
            said.setdefault(text_of(m), m["is_self"])
    votes = collections.defaultdict(collections.Counter)
    for m in msgs:
        x = re.match(r"\[引用 ([^:\]]*): (.*?)\]", text_of(m), re.S)
        if x and x.group(2) in said:
            votes[x.group(1)][said[x.group(2)]] += 1
    is_me = {k: v[True] > v[False] for k, v in votes.items()}

    def render(m):
        c = text_of(m).replace("\n", "⏎")
        c = re.sub(r"\[表情: ?([^\]]*)\]", r"〔\1〕", c)
        c = re.sub(r"\[表情#[0-9a-f]+\]", "〔〕", c)
        c = re.sub(r" ↳svrid:\d+ @\d{4}-\d\d-\d\d", "", c)

        def quote(x):
            who = "我" if is_me.get(x.group(1)) else "对"
            return f"「引{who}:{x.group(2)[:40]}」"

        c = re.sub(r"\[引用 ([^:\]]*): (.*?)\]", quote, c, count=1)
        c = re.sub(r"\[卡片 type=\d+: ", "[", c)
        return c.replace("[系统] ", "") if kind_of(m) == 10000 else c

    days, cur = [], None
    for m in msgs:
        t = dt.datetime.fromtimestamp(seconds(m), tz)
        d = t.strftime("%F")
        if not cur or cur[0] != d:
            cur = (d, t, [])
            days.append(cur)
        who = "系" if kind_of(m) == 10000 else ("我" if m["is_self"] else "对")
        lines = cur[2]
        text = render(m)
        if lines and lines[-1][1] == who and seconds(m) - lines[-1][3] <= 300 and len(lines[-1][2]) + len(text) < 300:
            lines[-1][2] += " ｜ " + text
            lines[-1][3] = seconds(m)
        else:
            lines.append([t, who, text, seconds(m)])

    def day_text(d, t0, lines):
        rows, last = [f"## {d} 周{WK[t0.weekday()]}"], None
        for t, who, text, _ in lines:
            text = re.sub(r"(〔[^〕]*〕)(?: ｜ \1)+", lambda x: x.group(1) + f"x{x.group(0).count('〔')}", text)
            stamp = ""
            if last is None or (t - last).total_seconds() >= 600:
                stamp, last = t.strftime("%H:%M "), t
            for i in range(0, len(text), 1500):  # 免得读文件的工具把长行截断
                rows.append(f"{stamp if i == 0 else ''}{who} {text[i:i + 1500]}")
        return rows

    chunks, buf, size = [], [], 0
    for d, t, lines in days:
        x = day_text(d, t, lines)
        n = sum(map(len, x))
        if buf and size + n > target:
            chunks.append(buf)
            buf, size = [], 0
        buf += x
        size += n
    chunks.append(buf)

    folder = os.path.join(out, "chunks")
    os.makedirs(folder, exist_ok=True)
    for f in glob.glob(os.path.join(folder, "c*.txt")):
        os.remove(f)
    codes, blob = {}, []
    for i, c in enumerate(chunks, 1):
        name = f"c{i:02d}"
        rows, cs = [], []
        for j, line in enumerate(c):
            if j and j % 500 == 0:
                cs.append(secrets.token_hex(3))
                rows.append(f"〈校验码 {cs[-1]}〉")
            rows.append(line)
        cs.append(secrets.token_hex(3))
        rows.append(f"〈校验码 {cs[-1]}〉本段结束")
        open(os.path.join(folder, name + ".txt"), "w", encoding="utf-8").write("\n".join(rows) + "\n")
        codes[name] = cs
        blob += rows
        heads = [line[3:13] for line in c if line.startswith("## ")]
        print(f"{name}.txt {heads[0]} → {heads[-1]}，{sum(map(len, rows))} 字，校验码 {len(cs)} 个")
    json.dump(codes, open(os.path.join(out, "codes.json"), "w", encoding="utf-8"))

    # 每条文字消息都要进文字稿，少了就是脚本有问题
    text = "\n".join(blob)
    missing = [m for m in msgs if kind_of(m) == 1 and len(text_of(m)) < 1400 and render(m) not in text]
    print(f"共 {len(msgs)} 条消息，{days[0][0]} 到 {days[-1][0]}，文字稿里缺 {len(missing)} 条")
    return 1 if missing else 0


def verify(work):
    codes = json.load(open(os.path.join(work, "codes.json"), encoding="utf-8"))
    bad = 0
    for name, cs in codes.items():
        path = os.path.join(work, "notes", name + ".md")
        notes = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
        lost = [c for c in cs if c not in notes]
        print(name, "通过" if not lost else f"缺 {len(lost)}/{len(cs)} 个校验码，这段要重读")
        bad += bool(lost)
    return 1 if bad else 0


def main(argv=None):
    p = argparse.ArgumentParser(description="把聊天导出压成分段文字稿")
    p.add_argument("paths", nargs="+", help="messages.json work/，或 --verify work/")
    p.add_argument("--verify", action="store_true")
    p.add_argument("--tz-hours", type=float, default=8, help="两个人当时所在地的 UTC 偏移，默认 8（别用电脑当前时区）")
    p.add_argument("--chunk-chars", type=int, default=70000, help="每段字数，约 10 万 token；读不完就调小")
    a = p.parse_args(argv)
    if a.verify:
        return verify(a.paths[0])
    if len(a.paths) != 2:
        p.error("需要 messages.json 和 work/ 两个路径")
    return build(a.paths[0], a.paths[1], a.tz_hours, a.chunk_chars)


if __name__ == "__main__":
    sys.exit(main())
