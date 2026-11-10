"""Local, single-case evaluation workbench, using only the standard library."""

import argparse
import copy
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from cases import CASES, get_case
from case_spec import build_case_spec
from client import ArkClient, DEFAULT_MODEL, PROJECT_ROOT, load_local_env
from evaluate import evaluate
from report import render_report
from run import add_repeat_metrics, run_live, run_offline, summarize


WEB_ROOT = Path(__file__).resolve().parent / "web"
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/styles.css": ("styles.css", "text/css; charset=utf-8")}


class RunManager:
    def __init__(self, output_dir=None):
        self.output_dir = Path(output_dir) if output_dir else PROJECT_ROOT / "reports" / "seven_dimensions" / "workbench"
        self._lock = threading.Lock()
        self._jobs = {}
        self._active = None

    def config(self):
        load_local_env()
        key = os.getenv("ARK_API_KEY", "")
        return {"model": os.getenv("ARK_MODEL", DEFAULT_MODEL),
                "key_configured": bool(key and key != "replace_with_your_local_key")}

    @staticmethod
    def validate(payload):
        if not isinstance(payload, dict):
            raise ValueError("请求必须是JSON对象。")
        if set(payload) - {"case_id", "mode", "repeat", "judge"}:
            raise ValueError("存在不支持的运行参数。")
        try:
            case = get_case(payload.get("case_id"))
        except KeyError:
            raise ValueError("请选择有效的case。") from None
        mode = payload.get("mode", "live")
        repeat = payload.get("repeat", 1)
        judge = payload.get("judge", True)
        if mode not in ("live", "offline"):
            raise ValueError("mode必须是live或offline。")
        if type(repeat) is not int or not 1 <= repeat <= 3:
            raise ValueError("界面每次可重复运行1—3次。")
        if type(judge) is not bool:
            raise ValueError("judge必须是布尔值。")
        return case, mode, repeat, judge

    def start(self, payload):
        case, mode, repeat, judge = self.validate(payload)
        with self._lock:
            if self._active is not None:
                raise RuntimeError("已有case正在运行，请等待本次完成。")
            run_id = uuid.uuid4().hex
            # Keep only a bounded local session history; reports remain on disk.
            if len(self._jobs) >= 30:
                self._jobs.pop(next(iter(self._jobs)))
            self._jobs[run_id] = {
                "run_id": run_id, "status": "queued", "case_id": case["id"],
                "mode": mode, "repeat": repeat, "events": [], "results": [],
                "report_url": None, "error": None,
                "started_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                "elapsed_ms": 0, "_started": time.perf_counter(), "_report_path": None,
            }
            self._active = run_id
        worker = threading.Thread(target=self._work, args=(run_id, case, mode, repeat, judge), daemon=True)
        worker.start()
        return run_id

    def _emit(self, run_id, repeat_index, kind, data):
        with self._lock:
            job = self._jobs[run_id]
            job["events"].append({"type": kind, "repeat_index": repeat_index,
                                  "at_ms": round((time.perf_counter() - job["_started"]) * 1000),
                                  "data": copy.deepcopy(data)})

    def _work(self, run_id, case, mode, repeat, judge):
        client = None
        try:
            with self._lock:
                self._jobs[run_id]["status"] = "running"
            client = ArkClient() if mode == "live" else None
            results = []
            for index in range(repeat):
                if mode == "live":
                    record = run_live(case, client, 8, judge, index,
                                      on_event=lambda kind, data, i=index: self._emit(run_id, i, kind, data))
                else:
                    record = run_offline(case, index)
                    self._emit(run_id, index, "start", {"record": {
                        **record, "turns": [], "trace": [], "final_state": None}})
                    self._emit(run_id, index, "agent_end", {"record": record})
                record["dimensions"] = evaluate(record)
                results.append(record)
                add_repeat_metrics(results)
                self._emit(run_id, index, "evaluated", {"record": record})
                with self._lock:
                    self._jobs[run_id]["results"] = copy.deepcopy(results)
            now = datetime.now(timezone(timedelta(hours=8)))
            report = {"model": client.model if client else "offline_fixture",
                      "generated_at": now.isoformat(), "results": results, "summary": summarize(results)}
            self.output_dir.mkdir(parents=True, exist_ok=True)
            target = self.output_dir / (run_id + ".html")
            target.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            render_report(report, target)
            with self._lock:
                job = self._jobs[run_id]
                job.update(status="failed" if any(row["error"] for row in results) else "completed",
                           error=next((row["error"] for row in results if row["error"]), None),
                           results=copy.deepcopy(results), report_url=f"/reports/{run_id}.html",
                           _report_path=target)
        except Exception as exc:
            # Never return stack traces, request headers, or local settings to the browser.
            detail = client.safe_error(str(exc)) if client else "运行未完成，请检查本地模型配置或报告目录。"
            with self._lock:
                self._jobs[run_id].update(status="failed", error=detail)
        finally:
            with self._lock:
                self._jobs[run_id]["elapsed_ms"] = round(
                    (time.perf_counter() - self._jobs[run_id]["_started"]) * 1000)
                self._active = None

    def snapshot(self, run_id):
        with self._lock:
            job = self._jobs[run_id]
            result = {key: copy.deepcopy(value) for key, value in job.items() if not key.startswith("_")}
            if result["status"] in ("queued", "running"):
                result["elapsed_ms"] = round((time.perf_counter() - job["_started"]) * 1000)
            return result

    def report_path(self, run_id):
        with self._lock:
            return self._jobs.get(run_id, {}).get("_report_path")


class WorkbenchHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # The workbench is local; avoid logging user conversations or request bodies.
        return

    def _send(self, status, body, content_type="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def _local_request(self):
        try:
            host = urlsplit("http://" + self.headers.get("Host", ""))
            valid = (host.hostname in ("127.0.0.1", "localhost")
                     and host.port == self.server.server_port and not host.username
                     and not host.password and host.path == "" and not host.query and not host.fragment)
            origin = self.headers.get("Origin")
            if origin:
                source = urlsplit(origin)
                valid = valid and (source.scheme == "http" and source.hostname == host.hostname
                                   and source.port == self.server.server_port and not source.username
                                   and not source.password and source.path == ""
                                   and not source.query and not source.fragment)
            if valid:
                return True
        except ValueError:
            pass
        self._send(403, {"error": "仅接受从当前本地工作台发出的请求。"})
        return False

    def do_GET(self):
        if not self._local_request():
            return
        path = unquote(urlsplit(self.path).path)
        if path in ASSETS:
            filename, mime = ASSETS[path]
            target = WEB_ROOT / filename
            if target.is_file():
                self._send(200, target.read_bytes(), mime)
            else:
                self._send(404, {"error": "前端文件未找到。"})
        elif path == "/api/config":
            self._send(200, self.server.manager.config())
        elif path == "/api/cases":
            self._send(200, {"cases": [{key: case[key] for key in (
                "id", "title", "description", "scenario", "turns")} for case in CASES]})
        elif re.fullmatch(r"/api/cases/[a-z0-9_]+/spec", path):
            try:
                case = get_case(path.split("/")[3])
            except KeyError:
                self._send(404, {"error": "用例不存在。"})
                return
            # Display rules to the human viewer; never add them to Agent messages.
            self._send(200, build_case_spec(case))
        elif re.fullmatch(r"/api/runs/[a-f0-9]{32}", path):
            try:
                self._send(200, self.server.manager.snapshot(path.rsplit("/", 1)[1]))
            except KeyError:
                self._send(404, {"error": "运行记录不存在。"})
        elif re.fullmatch(r"/reports/[a-f0-9]{32}\.html", path):
            target = self.server.manager.report_path(path.rsplit("/", 1)[1][:-5])
            if target and target.is_file():
                self._send(200, target.read_bytes(), "text/html; charset=utf-8")
            else:
                self._send(404, {"error": "报告尚未生成。"})
        else:
            self._send(404, {"error": "页面不存在。"})

    def do_POST(self):
        if not self._local_request():
            return
        if urlsplit(self.path).path != "/api/runs":
            self._send(404, {"error": "接口不存在。"})
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            self._send(415, {"error": "请使用application/json。"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send(400, {"error": "请求长度无效。"})
            return
        if length < 1 or length > 16384:
            self._send(413 if length > 16384 else 400, {"error": "请求体为空或超出限制。"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            run_id = self.server.manager.start(payload)
            self._send(202, {"run_id": run_id})
        except (ValueError, UnicodeDecodeError) as exc:
            detail = str(exc) if not isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError)) else "JSON格式无效。"
            self._send(400, {"error": detail})
        except RuntimeError as exc:
            self._send(409, {"error": str(exc)})


def create_server(host="127.0.0.1", port=8765, manager=None):
    if host != "127.0.0.1":
        raise ValueError("工作台仅允许绑定127.0.0.1。")
    server = ThreadingHTTPServer((host, port), WorkbenchHandler)
    server.daemon_threads = True
    server.manager = manager or RunManager()
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    with create_server(port=args.port) as server:
        print(f"Local: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever(poll_interval=0.3)
        except KeyboardInterrupt:
            print("\n工作台已停止。", flush=True)


if __name__ == "__main__":
    main()
