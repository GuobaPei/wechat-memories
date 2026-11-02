#!/usr/bin/env python3
"""用虚构示例跑一遍两个脚本。python3 tests/test_skill.py"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MESSAGES = ROOT / "examples" / "messages.json"
EVENTS = json.loads((ROOT / "examples" / "events.json").read_text(encoding="utf-8"))


def run(script, *args):
    return subprocess.run([sys.executable, str(ROOT / "scripts" / script), *map(str, args)], capture_output=True, text=True).returncode


def build(tmp, events, *extra):
    path = tmp / "events.json"
    path.write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
    out = tmp / "page.html"
    out.unlink(missing_ok=True)
    code = run("build_page.py", "--messages", MESSAGES, "--events", path, "--me", "小鱼", "--them", "阿木", "--out", out, *extra)
    return code, out


def changed(fn):
    events = json.loads(json.dumps(EVENTS))
    fn(events)
    return events


def main():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)

        # 文字稿：校验码没抄全就不通过，抄全才通过
        work = tmp / "work"
        assert run("make_transcript.py", MESSAGES, work) == 0
        assert run("make_transcript.py", "--verify", work) == 1
        codes = json.loads((work / "codes.json").read_text(encoding="utf-8"))
        (work / "notes").mkdir()
        for name, cs in codes.items():
            (work / "notes" / f"{name}.md").write_text(" ".join(cs), encoding="utf-8")
        assert run("make_transcript.py", "--verify", work) == 0

        # 示例能生成页面，页面不依赖外部资源
        code, out = build(tmp, EVENTS)
        assert code == 0 and out.exists()
        page = out.read_text(encoding="utf-8")
        assert "http://" not in page and "https://" not in page
        assert EVENTS[0]["title"] in page

        # 下面每一种错都不能写出页面
        def fake_quote(e):
            e[0]["quotes"][0]["text"] = "这句话从来没人说过"

        def swapped_speaker(e):
            q = e[0]["quotes"][0]
            q["who"] = "阿木" if q["who"] == "小鱼" else "小鱼"

        def wrong_date(e):
            e[0]["date"] = "2023-12-30"

        def phone_number(e):
            e[0]["text"] = "小鱼的电话是 13812345678。"

        def pronoun(e):
            e[0]["text"] = "她去爬山了。"

        for bad in (fake_quote, swapped_speaker, wrong_date, phone_number, pronoun):
            code, out = build(tmp, changed(bad))
            assert code == 1 and not out.exists(), bad.__name__

        # --ban-file 里的词不能上页面
        ban = tmp / "ban.txt"
        ban.write_text(EVENTS[0]["title"] + "\n", encoding="utf-8")
        code, out = build(tmp, EVENTS, "--ban-file", ban)
        assert code == 1 and not out.exists()

    print("通过")


if __name__ == "__main__":
    main()
