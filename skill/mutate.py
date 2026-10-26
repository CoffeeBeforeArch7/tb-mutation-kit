#!/usr/bin/env python3
"""tb-mutation：给被测 RTL 造变异体（每个只改一处），逐个跑同一条仿真命令，看 testbench 能不能抓到。

    python3 mutate.py --rtl rtl/fifo.sv --cmd "make sim" --pass-regex "TEST PASSED"

流程：把项目复制到临时目录 → 原始设计先跑一遍，必须通过 → 每个变异体改一处、跑一遍、改回来 →
结果分四类：killed（testbench 报错）、survived（没发现）、timeout（跑不完，算抓到）、
stillborn（编译不过，不算分）→ 在 --out 目录写 report.md 和 report.json。
分数 = (killed + timeout) / (killed + survived + timeout)。
"""
import argparse
import json
import os
import queue
import random
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

DECL = re.compile(r"\s*(input|output|inout|reg|wire|logic|bit|integer|genvar)\b")
PARAM = re.compile(r"\s*(parameter|localparam)\b")
STILLBORN = r"syntax error|Error-\[[A-Z]+\]|%Error[-:]|Unable to bind|I give up"


@dataclass
class Mutant:
    mid: int
    op: str
    file: str
    line: int
    start: int
    end: int
    repl: str
    before: str
    after: str
    status: str = ""
    seconds: float = 0.0
    log_tail: str = ""


def mask(text):
    """注释和字符串换成空格，长度不变，正则在它上面找到的位置和原文一一对应"""
    out, i, n = list(text), 0, len(text)
    while i < n:
        two = text[i:i + 2]
        if two == "//":
            j = text.find("\n", i)
            j = n if j < 0 else j
        elif two == "/*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
        elif text[i] == '"':
            j = i + 1
            while j < n and text[j] not in '"\n':
                j += 2 if text[j] == "\\" else 1
            j = min(j + 1, n)
        else:
            i += 1
            continue
        for k in range(i, j):
            out[k] = text[k] if text[k] == "\n" else " "
        i = j
    return "".join(out)


def paren_end(s, i):
    """s[i] 是 '('，返回和它配对的 ')' 的位置"""
    depth = 0
    for k in range(i, len(s)):
        depth += {"(": 1, ")": -1}.get(s[k], 0)
        if depth == 0:
            return k
    return len(s)


def if_conditions(m):
    """if / while 条件括号里面的范围"""
    spans = []
    for hit in re.finditer(r"\b(?:if|while)\s*\(", m):
        lp = hit.end() - 1
        spans.append((lp + 1, paren_end(m, lp)))
    return spans


def expr_spans(m):
    """条件括号，加上 assign 语句等号右边：这些地方的 <= 是比较，不是非阻塞赋值"""
    spans = if_conditions(m)
    for hit in re.finditer(r"\b(?:assign|wire|logic|bit)\b[^;=]*=", m):
        semi = m.find(";", hit.end())
        spans.append((hit.end(), len(m) if semi < 0 else semi))
    return spans


def line_of(text, pos):
    s = text.rfind("\n", 0, pos) + 1
    e = text.find("\n", pos)
    return s, len(text) if e < 0 else e


def skip_site(m, pos):
    """参数行、声明的等号左边、for 循环头、[a:b] 位宽范围里的不改：改了多半是编译错误或者等价"""
    s, e = line_of(m, pos)
    line = m[s:e]
    eq = line.find("=")
    if PARAM.match(line) or re.search(r"\bfor\s*\(", line):
        return True
    if DECL.match(line) and (eq < 0 or pos - s < eq):
        return True
    lb, rb = m.rfind("[", s, pos), m.find("]", pos, e)
    return lb >= 0 and rb >= 0 and ":" in m[lb:rb] and "]" not in m[lb:pos]


def inc_literal(lit):
    """常数加一（超出位宽就减一），保持原来的写法；有 x / z 的不改"""
    hit = re.fullmatch(r"(\d+)?'([sS]?)([bBoOdDhH])([0-9a-fA-F_]+)", lit)
    if not hit:
        return str(int(lit) + 1)
    width, sign, base, digits = hit.groups()
    radix = {"b": 2, "o": 8, "d": 10, "h": 16}[base.lower()]
    val = int(digits.replace("_", ""), radix)
    new = val + 1 if not width or val + 1 < (1 << int(width)) else val - 1
    fmt = {2: "b", 8: "o", 10: "d", 16: "x"}[radix]
    return f"{width or ''}'{sign}{base}{format(new, fmt)}"


# 名字, 正则, 替换, 只在条件 / assign 右边找
OPS = [
    ("EQ_NE", r"(?<![=!<>])==(?!=)", "!=", False),
    ("NE_EQ", r"(?<![=!])!=(?!=)", "==", False),
    ("AND_OR", r"&&", "||", False),
    ("OR_AND", r"\|\|", "&&", False),
    ("LT_LE", r"(?<![<])<(?![<=])", "<=", True),
    ("LE_LT", r"(?<![<])<=", "<", True),
    ("GT_GE", r"(?<![>=-])>(?![>=])", ">=", True),
    ("GE_GT", r"(?<![>])>=", ">", True),
    ("EDGE", r"\bposedge\b", "negedge", False),
    ("EDGE", r"\bnegedge\b", "posedge", False),
    ("NOT_DEL", r"!(?!=)(?=\s*[\w\(])", "", False),
    ("PLUS_MINUS", r"(?<=[\w\)\]])\s*\+\s*(?=[\w\(])", " - ", False),
    ("MINUS_PLUS", r"(?<=[\w\)\]])\s*-\s*(?=[\w\(])", " + ", False),
]
LITERAL = r"(?:\d+)?'[sS]?[bBoOdDhH][0-9a-fA-F_]+|(?<!['\w])\d+(?![\w'])"


def sites(path, text):
    """一个文件里所有能改的地方，每个返回 (算子, 起点, 终点, 替换)"""
    m, found = mask(text), []
    exprs = expr_spans(m)
    inside = lambda p: any(a <= p < b for a, b in exprs)  # noqa: E731
    for name, pat, repl, cond_only in OPS:
        for hit in re.finditer(pat, m):
            if (cond_only and not inside(hit.start())) or skip_site(m, hit.start()):
                continue
            found.append((name, hit.start(), hit.end(), repl))
    for hit in re.finditer(LITERAL, m):
        if inside(hit.start()) and not skip_site(m, hit.start()):
            found.append(("CONST_INC", hit.start(), hit.end(), inc_literal(hit.group())))
    for hit in re.finditer(r"\[(\d+)\]", m):
        if not skip_site(m, hit.start()):
            n = int(hit.group(1))
            found.append(("INDEX", hit.start(), hit.end(), f"[{n - 1 if n else 1}]"))
    for a, b in if_conditions(m):
        found.append(("COND_NEG", a, b, "!(" + text[a:b] + ")"))
    return found


def build_mutants(project, rtl_files, max_n, seed):
    """按算子轮流抽，同一种改法不会占满名额"""
    by_op = {}
    for rel in rtl_files:
        text = (project / rel).read_text(encoding="utf-8", errors="replace")
        for op, a, b, repl in sites(rel, text):
            by_op.setdefault(op, []).append((rel, text, a, b, repl))
    rng = random.Random(seed)
    for lst in by_op.values():
        rng.shuffle(lst)
    picked, ops = [], sorted(by_op)
    while len(picked) < max_n and any(by_op[o] for o in ops):
        for op in ops:
            if by_op[op] and len(picked) < max_n:
                picked.append((op,) + by_op[op].pop())
    mutants = []
    for mid, (op, rel, text, a, b, repl) in enumerate(picked, 1):
        s, e = line_of(text, a)
        after = text[s:a] + repl + text[b:e]
        mutants.append(Mutant(mid, op, rel, text.count("\n", 0, a) + 1, a, b, repl,
                              text[s:e].strip(), after.strip()))
    return mutants


def run_cmd(cmd, cwd, timeout):
    """跑一条命令；超时就把整个进程组杀掉，免得仿真器留在后台"""
    t0 = time.time()
    p = subprocess.Popen(cmd, shell=True, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, errors="replace", start_new_session=True)
    try:
        out, _ = p.communicate(timeout=timeout)
        return p.returncode, out or "", time.time() - t0, False
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        out, _ = p.communicate()
        return -1, out or "", time.time() - t0, True


def judge(args, cwd):
    """返回 pass / fail / timeout / stillborn，以及日志尾巴和用时"""
    spent = 0.0
    if args.build:
        rc, out, dt, to = run_cmd(args.build, cwd, args.timeout)
        spent += dt
        if to or rc != 0:
            return "stillborn", out[-1500:], spent
    rc, out, dt, to = run_cmd(args.cmd, cwd, args.timeout)
    spent += dt
    if to:
        return "timeout", out[-1500:], spent
    if not args.build and re.search(args.stillborn_regex, out):
        return "stillborn", out[-1500:], spent
    ok = rc == 0 and (not args.pass_regex or re.search(args.pass_regex, out))
    ok = ok and not (args.fail_regex and re.search(args.fail_regex, out))
    return ("pass" if ok else "fail"), out[-1500:], spent


def run_one(args, workers, mut):
    work = workers.get()
    target = work / mut.file
    original = target.read_text(encoding="utf-8", errors="replace")
    try:
        target.write_text(original[:mut.start] + mut.repl + original[mut.end:], encoding="utf-8")
        status, tail, mut.seconds = judge(args, work)
        mut.status = {"pass": "survived", "fail": "killed"}.get(status, status)
        mut.log_tail = tail if mut.status == "survived" else tail[-400:]
    finally:
        target.write_text(original, encoding="utf-8")
        workers.put(work)
    print(f"  #{mut.mid:<3} {mut.op:<10} {mut.file}:{mut.line:<5} {mut.status}", flush=True)
    return mut


def write_report(out, args, mutants, base_seconds):
    out.mkdir(parents=True, exist_ok=True)
    cnt = {k: sum(m.status == k for m in mutants) for k in ("killed", "survived", "timeout", "stillborn")}
    scored = cnt["killed"] + cnt["survived"] + cnt["timeout"]
    score = (cnt["killed"] + cnt["timeout"]) / scored if scored else 0.0
    md = ["# tb-mutation 报告", "",
          f"- 仿真命令：`{args.cmd}`" + (f"（编译：`{args.build}`）" if args.build else ""),
          f"- 原始设计跑一遍用时：{base_seconds:.1f} 秒",
          f"- 变异体 {len(mutants)} 个：killed {cnt['killed']}，survived {cnt['survived']}，"
          f"timeout {cnt['timeout']}，stillborn {cnt['stillborn']}（编译不过，不算分）",
          f"- 分数：{cnt['killed'] + cnt['timeout']} / {scored} = **{score:.0%}**", "",
          "## 没抓到的变异体（survived）", ""]
    survived = [m for m in mutants if m.status == "survived"]
    md += ["| # | 算子 | 位置 | 原来 | 改成 |", "| --- | --- | --- | --- | --- |"] if survived else ["无。"]
    for m in survived:
        md.append(f"| {m.mid} | {m.op} | {m.file}:{m.line} | `{m.before}` | `{m.after}` |")
    md += ["", "## 全部结果", "", "| # | 算子 | 位置 | 结果 | 用时 |", "| --- | --- | --- | --- | --- |"]
    md += [f"| {m.mid} | {m.op} | {m.file}:{m.line} | {m.status} | {m.seconds:.1f}s |" for m in mutants]
    (out / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    data = {"cmd": args.cmd, "build": args.build, "score": score, "counts": cnt,
            "mutants": [asdict(m) for m in mutants]}
    (out / "report.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return score, cnt


def parse_args(argv):
    ap = argparse.ArgumentParser(description="给 RTL 造变异体，检查 testbench 能不能抓到")
    ap.add_argument("--project", default=".", help="项目根目录，命令在它的副本里跑（默认当前目录）")
    ap.add_argument("--rtl", action="append", required=True, help="要变异的 RTL 文件（相对项目根目录，可重复）")
    ap.add_argument("--cmd", required=True, help="跑仿真的命令，在项目副本的根目录执行")
    ap.add_argument("--build", default="", help="单独的编译命令；失败就判 stillborn")
    ap.add_argument("--pass-regex", default="", help="日志里出现它才算通过")
    ap.add_argument("--fail-regex", default="", help="日志里出现它就算失败")
    ap.add_argument("--stillborn-regex", default=STILLBORN, help="没有 --build 时，用它认出编译错误")
    ap.add_argument("--timeout", type=float, default=300, help="每次运行的超时秒数")
    ap.add_argument("--max", type=int, default=40, help="最多跑几个变异体")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("-j", "--jobs", type=int, default=1, help="并行份数（每份一个项目副本）")
    ap.add_argument("--exclude", action="append", default=[], help="复制项目时跳过的目录或文件模式")
    ap.add_argument("--out", default="mutation_report", help="报告目录")
    ap.add_argument("--list", action="store_true", help="只列出会造哪些变异体，不跑仿真")
    ap.add_argument("--keep", action="store_true", help="保留项目副本，方便排查")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    project = Path(args.project).resolve()
    mutants = build_mutants(project, args.rtl, args.max, args.seed)
    print(f"造了 {len(mutants)} 个变异体", flush=True)
    if args.list:
        for m in mutants:
            print(f"  #{m.mid:<3} {m.op:<10} {m.file}:{m.line:<5} {m.before}  →  {m.after}")
        return 0
    tmp = Path(tempfile.mkdtemp(prefix="tbmut_"))
    ignore = shutil.ignore_patterns(".git", *args.exclude)
    copies = [tmp / f"w{i}" for i in range(max(1, args.jobs))]
    for c in copies:
        shutil.copytree(project, c, ignore=ignore, symlinks=True)
    try:
        status, tail, base = judge(args, copies[0])
        if status != "pass":
            print(f"原始设计没有通过（{status}），先修好 testbench 或命令：\n{tail}", file=sys.stderr)
            return 2
        print(f"原始设计通过，用时 {base:.1f} 秒；开始跑变异体", flush=True)
        workers = queue.Queue()
        for c in copies:
            workers.put(c)
        with ThreadPoolExecutor(max_workers=len(copies)) as pool:
            done = list(pool.map(lambda mu: run_one(args, workers, mu), mutants))
        score, cnt = write_report(Path(args.out), args, done, base)
        print(f"分数 {score:.0%}（killed {cnt['killed']}，survived {cnt['survived']}，"
              f"timeout {cnt['timeout']}，stillborn {cnt['stillborn']}），报告在 {Path(args.out).resolve()}")
        return 0
    finally:
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
