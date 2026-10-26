"""Local, offline Fujian CSP collection practice. Python 3.10+, no dependencies."""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import os
from pathlib import Path
import re
import secrets
import stat
import threading
import time
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
import uuid
import webbrowser

ROOT = Path(__file__).resolve().parent
CHINA = timezone(timedelta(hours=8))
SOURCE_URL = "https://noi.cn/gs/xw/fj/2026-10-15/940146.shtml"
MAX_SOURCE = 10 * 1024 * 1024


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def stamp(ts):
    return datetime.fromtimestamp(ts, CHINA).strftime("%Y-%m-%d %H:%M:%S")


def validate_settings(data, now=None):
    if not isinstance(data, dict):
        raise ValueError("设置格式错误。")
    problems = data.get("problems")
    if not isinstance(problems, list) or len(problems) != 4:
        raise ValueError("必须完整填写四道题目。")
    clean = []
    for i, problem in enumerate(problems, 1):
        if not isinstance(problem, dict):
            raise ValueError("题目格式错误。")
        title = str(problem.get("title", "")).strip()
        slug = str(problem.get("slug", "")).strip()
        if not title or len(title) > 100:
            raise ValueError(f"第 {i} 题名称不能为空，且不超过 100 字。")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,49}", slug):
            raise ValueError(f"第 {i} 题英文名须以小写字母开头，仅含小写字母、数字和下划线。")
        if slug in {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}:
            raise ValueError("题目英文名不能使用系统保留名称。")
        clean.append({"title": title, "slug": slug})
    if len({p["slug"] for p in clean}) != 4:
        raise ValueError("四道题的英文名不能重复。")
    try:
        dt = datetime.fromisoformat(str(data.get("deadline", "")))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CHINA)
        deadline = dt.timestamp()
    except (ValueError, OverflowError):
        raise ValueError("请填写有效的回收日期和时间。") from None
    if deadline <= (time.time() if now is None else now):
        raise ValueError("回收时间必须晚于当前时间。")
    return {"deadline": dt.astimezone(CHINA).isoformat(), "deadline_ts": deadline, "problems": clean}


def exact_child(parent, name):
    """Do not allow Windows case folding or junctions to silently accept bad paths."""
    child = next((p for p in parent.iterdir() if p.name == name), None)
    if child is None:
        raise ValueError(f"缺少 {name}（名称及大小写必须完全一致）")
    info = child.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError(f"{name} 是链接或联接目录，不予回收")
    return child


def decode_source(raw):
    for encoding in (("utf-8-sig", "gb18030") if raw.startswith(b"\xef\xbb\xbf") else ("utf-8", "gb18030")):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            pass
    return None, "无法识别编码"


def read_problem(workspace, candidate, problem, cutoff=None):
    slug = problem["slug"]
    result = {**problem, "path": f"{candidate}/{slug}/{slug}.cpp", "status": "missing", "bytes": None}
    try:
        folder = exact_child(Path(workspace), candidate)
        task_dir = exact_child(folder, slug)
        source = exact_child(task_dir, slug + ".cpp")
        before = source.stat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("目标不是普通源程序文件")
        if before.st_size > MAX_SOURCE:
            raise ValueError("超过本模拟器的单文件 10 MiB 上限")
        if cutoff is not None and before.st_mtime_ns > int(cutoff * 1e9):
            raise ValueError("文件在截止时间之后修改，未回收")
        with source.open("rb") as f:
            raw = f.read(MAX_SOURCE + 1)
            after = os.fstat(f.fileno())
        current = source.stat()
        if len(raw) > MAX_SOURCE:
            raise ValueError("超过本模拟器的单文件 10 MiB 上限")
        if (before.st_mtime_ns, before.st_size, before.st_ino) != (after.st_mtime_ns, after.st_size, after.st_ino) or (after.st_mtime_ns, after.st_size, after.st_ino) != (current.st_mtime_ns, current.st_size, current.st_ino):
            raise ValueError("读取期间文件发生变化，未回收；请核对回收记录")
        code, encoding = decode_source(raw)
        result.update(status="collected", bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                      raw=base64.b64encode(raw).decode("ascii"), code=code, encoding=encoding,
                      modified_at=stamp(before.st_mtime), message="已回收" if raw else "已回收空文件（0 字节）")
        if code is None:
            result["message"] = "已回收原始字节，但编码无法识别；请下载原文件核对"
    except (OSError, ValueError) as exc:
        result["message"] = str(exc)
    return result


def report_html(snapshot):
    esc = lambda x: html.escape(str(x), quote=True)
    cards = []
    for i, p in enumerate(snapshot["problems"], 1):
        ok = p["status"] == "collected"
        code = p.get("code")
        buttons = ""
        if ok:
            if code is not None:
                buttons += '<button class="copy" type="button">复制代码</button>'
            buttons += f'<a class="download" href="data:application/octet-stream;base64,{p["raw"]}" download="{esc(p["slug"])}.cpp">下载 .cpp</a>'
        cards.append(f'''<section class="report-card"><div class="report-card-head"><div><span class="eyebrow">PROBLEM {i:02}</span><h2>{esc(p['title'])} <small>{esc(p['slug'])}</small></h2></div><div class="actions">{buttons}</div></div>
<p class="{'success' if ok else 'warning'}">{esc(p['message'])}{' · ' + str(p['bytes']) + ' 字节 · ' + esc(p['encoding']) if ok else ''}</p>
<p class="file-path">{esc(p['path'])}</p>
{'<pre tabindex="0"><code>' + esc(code) + '</code></pre>' if code is not None else '<div class="empty-code">' + ('原始文件已保存，请下载查看。' if ok else '没有可复制的代码。只接受规定路径中的源程序。') + '</div>'}
{'<details><summary>查看 SHA-256 校验值</summary><code>' + p['sha256'] + '</code></details>' if ok else ''}</section>''')
    count = sum(p["status"] == "collected" for p in snapshot["problems"])
    css = (ROOT / "web" / "style.css").read_text(encoding="utf-8")
    script = """document.querySelectorAll('.copy').forEach(button => button.addEventListener('click', async () => {
const code = button.closest('section').querySelector('pre code').textContent;
try { if(navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(code);
else { const a=document.createElement('textarea'); a.value=code; document.body.append(a); a.select(); const ok=document.execCommand('copy'); a.remove(); if(!ok) throw Error(); }
button.textContent='已复制'; setTimeout(()=>button.textContent='复制代码',1800);
} catch { button.textContent='请选中代码后 Ctrl+C'; const r=document.createRange(); r.selectNodeContents(button.closest('section').querySelector('pre code')); const s=window.getSelection(); s.removeAllRanges(); s.addRange(r); }
}));"""
    late = '<div class="notice warning">回收发生延迟（可能是程序关闭或电脑休眠）。仅接收修改时间不晚于截止时间的文件；这不是精确截止时刻的完整快照，请核对记录。</div>' if snapshot["late"] else ''
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(snapshot['candidate'])} · 代码回收报告</title><style>{css}</style></head>
<body class="report-page"><main class="report-wrap"><header class="report-header"><div class="brand"><span class="brand-icon">F</span> 福建 CSP <span class="brand-sub">/ 回收报告</span></div><span class="pill">本地模拟 · 非官方客户端</span></header>
<span class="eyebrow">COLLECTION RECEIPT</span><h1>答卷已封存，<span class="muted">继续你的思考。</span></h1><p class="lead">逐题核对源程序与字节大小，复制代码到 OJ 自行提交。</p>
<div class="receipt-stats"><div><span>准考证号</span><strong>{esc(snapshot['candidate'])}</strong></div><div><span>规定回收时间 · 北京时间</span><strong>{stamp(snapshot['deadline_ts'])}</strong></div><div><span>实际读取时间 · 北京时间</span><strong>{stamp(snapshot['collected_at'])}</strong></div><div><span>回收源程序</span><strong>{count} / 4</strong></div></div>
{late}<div class="notice">本页展示已封存的原始代码，不会随工作文件夹变化。复制前请检查目标 OJ 是否要求标准输入输出；本工具不修改 freopen 等语句。</div>
{''.join(cards)}<footer>仅回收规定目录中的 .cpp 源程序 · 未进行编译、评测或评分 · <a href="{SOURCE_URL}">福建官方命名规则依据</a></footer>
</main><div role="status" aria-live="polite"></div><script>{script}</script></body></html>'''


class Exam:
    def __init__(self, data_dir, opener=webbrowser.open):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.data_dir / "state.json"
        self.lock = threading.RLock()
        self.opener = opener
        self.state = {"phase": "setup", "settings": None}
        if self.state_path.exists():
            # A damaged state must not silently reset an active exam.
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))

    def save(self):
        atomic_json(self.state_path, self.state)

    def public(self):
        with self.lock:
            result = dict(self.state)
            result["now"] = time.time()
            result["data_dir"] = str(self.data_dir)
            return result

    def configure(self, data):
        with self.lock:
            if self.state["phase"] != "setup":
                raise ValueError("本场已开始，不能修改 Settings。")
            self.state["settings"] = validate_settings(data)
            self.save()

    def start(self, data):
        with self.lock:
            if self.state["phase"] != "setup":
                raise ValueError("本场已开始。")
            if not self.state.get("settings"):
                raise ValueError("请先完成必填 Settings。")
            validate_settings(self.state["settings"])
            candidate = str(data.get("candidate", "")).strip()
            if not re.fullmatch(r"FJ-[A-Z0-9]{1,24}", candidate):
                raise ValueError("准考证号须为大写 FJ- 加数字或大写字母，例如 FJ-00055；使用英文半角减号。")
            workspace = Path(str(data.get("workspace", ""))).expanduser()
            if not str(data.get("workspace", "")).strip() or not workspace.is_absolute() or not workspace.is_dir():
                raise ValueError("请选择实际存在的工作文件夹（绝对路径）。")
            workspace = workspace.resolve()
            if workspace == self.data_dir or self.data_dir in workspace.parents or workspace == ROOT:
                raise ValueError("请选择独立的考生工作文件夹，不要使用程序或回收数据目录。")
            if not os.access(workspace, os.R_OK | os.W_OK):
                raise ValueError("工作文件夹需要读写权限。")
            self.state.update(phase="running", candidate=candidate, workspace=str(workspace),
                              session_id=datetime.now(CHINA).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8], started_at=time.time())
            self.save()

    def check(self, data):
        with self.lock:
            if self.state["phase"] == "setup":
                candidate, workspace = str(data.get("candidate", "")), str(data.get("workspace", ""))
                if not re.fullmatch(r"FJ-[A-Z0-9]{1,24}", candidate) or not Path(workspace).is_absolute() or not Path(workspace).is_dir():
                    raise ValueError("请先填写正确的准考证号和工作文件夹。")
            else:
                candidate, workspace = self.state["candidate"], self.state["workspace"]
            settings = self.state.get("settings")
            if not settings:
                raise ValueError("请先完成 Settings。")
            rows = [read_problem(workspace, candidate, p) for p in settings["problems"]]
            return [{k: v for k, v in p.items() if k not in ("raw", "code")} for p in rows]

    def collect_due(self, now=None):
        with self.lock:
            now = time.time() if now is None else now
            if self.state["phase"] not in ("running", "collecting"):
                return False
            settings = self.state["settings"]
            if now < settings["deadline_ts"]:
                return False
            folder = self.data_dir / "collections" / self.state["session_id"]
            snapshot_path = folder / "snapshot.json"
            try:
                # Save the phase BEFORE reading. On a crash, never silently reread a partial collection.
                recovering = self.state["phase"] == "collecting"
                self.state["phase"] = "collecting"
                self.save()
                if snapshot_path.exists():
                    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
                else:
                    if recovering:
                        raise ValueError("上次回收在快照落盘前中断，无法重建截止时刻答卷；请保留现场并核对原文件。")
                    snapshot = {"candidate": self.state["candidate"], "workspace": self.state["workspace"],
                                "deadline_ts": settings["deadline_ts"], "collected_at": now,
                                "late": now - settings["deadline_ts"] > 2,
                                "problems": [read_problem(self.state["workspace"], self.state["candidate"], p, settings["deadline_ts"]) for p in settings["problems"]]}
                    atomic_json(snapshot_path, snapshot)
                for p in snapshot["problems"]:
                    if p["status"] == "collected":
                        dest = folder / "sources" / p["path"]
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(base64.b64decode(p["raw"]))
                manifest = {**snapshot, "problems": [{k: v for k, v in p.items() if k not in ("raw", "code")} for p in snapshot["problems"]]}
                atomic_json(folder / "manifest.json", manifest)
                report = folder / "report.html"
                # Avoid Windows translating CRLF inside source code into CR-CR-LF.
                report.write_text(report_html(snapshot), encoding="utf-8", newline="")
                self.state.update(phase="collected", report=str(report), result=manifest, error=None)
                self.save()
            except Exception as exc:
                self.state.update(phase="error", error=f"回收未完成：{exc}", snapshot=str(snapshot_path))
                self.save()
                return False
        try:
            self.opener(report.as_uri())
        except Exception:
            pass  # Report is saved and can still be opened explicitly.
        return True

    def retry_export(self):
        with self.lock:
            if self.state["phase"] != "error" or not Path(self.state.get("snapshot", "")).is_file():
                raise ValueError("没有完整快照可重新导出。请保留原文件与错误信息。")
            self.state["phase"] = "collecting"
            self.save()
        self.collect_due()

    def reset(self):
        with self.lock:
            if self.state["phase"] not in ("collected", "error"):
                raise ValueError("请等待本场回收结束。")
            self.state = {"phase": "setup", "settings": self.state.get("settings")}
            self.save()


def list_folders(value=""):
    """Browse local directories without a GUI toolkit or browser upload permission."""
    folder = Path(value).expanduser() if value else Path.home()
    if not folder.is_absolute() or not folder.is_dir():
        raise ValueError("请输入存在的文件夹绝对路径。")
    folder = folder.resolve()
    children = []
    for child in folder.iterdir():
        try:
            if child.is_dir():
                children.append({"name": child.name, "path": str(child)})
        except OSError:
            continue
    children.sort(key=lambda p: p["name"].casefold())
    roots = [{"name": "用户目录", "path": str(Path.home())}]
    if os.name == "nt":
        import ctypes
        drives = ctypes.windll.kernel32.GetLogicalDrives()
        roots.extend({"name": f"{chr(65+i)}:", "path": f"{chr(65+i)}:\\"} for i in range(26) if drives & (1 << i))
    else:
        roots.append({"name": "文件系统", "path": "/"})
    return {"path": str(folder), "parent": str(folder.parent), "folders": children[:1000],
            "truncated": len(children) > 1000, "roots": roots}


def make_handler(exam, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, body, content_type="application/json; charset=utf-8", status=200):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False)
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(body)

        def trusted_host(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def do_GET(self):
            if not self.trusted_host():
                return self.send({"error": "无效的本地主机"}, status=403)
            path = urlparse(self.path).path
            files = {"/": ("index.html", "text/html; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8"), "/app.js": ("app.js", "application/javascript; charset=utf-8")}
            if path in files:
                name, mime = files[path]
                text = (ROOT / "web" / name).read_text(encoding="utf-8")
                return self.send(text.replace("__TOKEN__", token), mime)
            if path == "/api/state":
                return self.send(exam.public())
            if path == "/report" and exam.state.get("report"):
                return self.send(Path(exam.state["report"]).read_bytes(), "text/html; charset=utf-8")
            return self.send({"error": "未找到页面"}, status=404)

        def do_POST(self):
            origin = self.headers.get("Origin")
            expected_origin = f"http://127.0.0.1:{self.server.server_port}"
            if not self.trusted_host() or self.headers.get("X-CSRF-Token") != token or (origin and origin != expected_origin):
                return self.send({"error": "请求不是来自当前本地页面，请刷新后重试"}, status=403)
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 16384:
                    raise ValueError("请求大小无效。")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("请求格式错误。")
                path = urlparse(self.path).path
                if path == "/api/settings":
                    exam.configure(data)
                elif path == "/api/start":
                    exam.start(data)
                elif path == "/api/check":
                    return self.send({"problems": exam.check(data)})
                elif path == "/api/folders":
                    return self.send(list_folders(str(data.get("path", ""))))
                elif path == "/api/reset":
                    exam.reset()
                elif path == "/api/retry":
                    exam.retry_export()
                else:
                    return self.send({"error": "未知操作"}, status=404)
                return self.send(exam.public())
            except (ValueError, OSError) as exc:
                return self.send({"error": str(exc)}, status=400)
            except Exception as exc:
                return self.send({"error": f"操作失败：{exc}"}, status=500)
    return Handler


def main():
    parser = argparse.ArgumentParser(description="福建 CSP 考场回收模拟器")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--data-dir", type=Path, default=ROOT / ".csp-data")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    # Prevent two collectors from operating on the same persisted session.
    args.data_dir.mkdir(parents=True, exist_ok=True)
    lock_file = (args.data_dir / "instance.lock").open("a+b")
    lock_file.seek(0)
    if os.name == "nt":
        import msvcrt
        if (args.data_dir / "instance.lock").stat().st_size == 0:
            lock_file.write(b"0")
            lock_file.flush()
        lock_file.seek(0)
        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise SystemExit("This data directory is already in use. Keep the existing app running.")
    else:
        import fcntl
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    exam = Exam(args.data_dir, opener=(lambda _: None) if args.no_browser else webbrowser.open)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(exam, secrets.token_urlsafe(32)))
    stop = threading.Event()

    def ticker():
        while not stop.wait(0.2):
            try:
                exam.collect_due()
            except Exception as exc:
                print(f"Collection error: {exc}", flush=True)

    thread = threading.Thread(target=ticker, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"Fujian CSP simulator: {url}\nKeep this process running until collection finishes.", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        thread.join()
        server.server_close()
        lock_file.close()


if __name__ == "__main__":
    main()
