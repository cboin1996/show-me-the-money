"""Local HTTP server for the interactive dashboard."""

import json
import tempfile
import threading
from collections import Counter, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .categorizer import KEYWORD_SUGGESTIONS, categorize_batch
from .dashboard import (
    CATEGORY_COLORS,
    _compute_summary,
    _txns_to_json,
    generate_server_html,
)
from .db import Database
from .importer import import_file
from .models import CategoryDB, Transaction, TxnType


def compute_anomalies(
    transactions: list[Transaction], threshold: float = 2.0
) -> list[dict]:
    expenses = [
        t for t in transactions if t.txn_type == TxnType.EXPENSE and not t.is_deleted
    ]
    cat_amounts: dict[str, list[float]] = defaultdict(list)
    for t in expenses:
        cat = t.category or "Uncategorized"
        cat_amounts[cat].append(t.amount)

    cat_avg = {cat: sum(amts) / len(amts) for cat, amts in cat_amounts.items()}

    anomalies = []
    for t in expenses:
        cat = t.category or "Uncategorized"
        avg = cat_avg.get(cat, 0)
        if len(cat_amounts[cat]) < 3:
            continue
        if avg > 0 and t.amount > threshold * avg:
            anomalies.append(
                {
                    "uuid": t.uuid,
                    "date": t.date.isoformat(),
                    "store": t.store_normalized or t.store_raw,
                    "amount": round(t.amount, 2),
                    "category": cat,
                    "category_avg": round(avg, 2),
                    "multiplier": round(t.amount / avg, 1),
                }
            )

    anomalies.sort(key=lambda a: -a["multiplier"])
    return anomalies[:50]


def compute_uncategorized(db: Database) -> list[dict]:
    txns = db.get_expenses()
    uncategorized = [t for t in txns if not t.category or t.category == "Uncategorized"]

    groups: dict[str, dict] = {}
    for t in uncategorized:
        key = t.store_normalized or t.store_raw
        if key not in groups:
            groups[key] = {"store": key, "count": 0, "total_spend": 0.0, "uuids": []}
        groups[key]["count"] += 1
        groups[key]["total_spend"] += t.amount
        groups[key]["uuids"].append(t.uuid)

    result = sorted(groups.values(), key=lambda g: -g["total_spend"])
    for g in result:
        g["total_spend"] = round(g["total_spend"], 2)
    return result


def compute_suggestions(db: Database) -> list[dict]:
    txns = db.get_expenses()
    unclassified = [t for t in txns if not t.category]
    if not unclassified:
        return []

    store_amounts: dict[str, float] = {}
    store_counts: Counter = Counter()
    for t in unclassified:
        key = t.store_normalized or t.store_raw
        store_amounts[key] = store_amounts.get(key, 0) + t.amount
        store_counts[key] += 1

    suggestions = []
    for store in store_amounts:
        for category, keywords in KEYWORD_SUGGESTIONS.items():
            if any(kw in store for kw in keywords):
                suggestions.append(
                    {
                        "store": store,
                        "category": category,
                        "amount": round(store_amounts[store], 2),
                        "count": store_counts[store],
                    }
                )
                break

    suggestions.sort(key=lambda s: -s["amount"])
    return suggestions


def _parse_multipart(content_type: str, body: bytes) -> tuple[str, bytes]:
    boundary = ""
    for part in content_type.split(";"):
        part = part.strip()
        if part.startswith("boundary="):
            boundary = part[9:].strip('"')
            break

    if not boundary:
        raise ValueError("No boundary in Content-Type")

    delimiter = f"--{boundary}".encode()
    parts = body.split(delimiter)

    for part in parts[1:]:
        if part.strip() == b"--" or part.strip() == b"":
            continue
        header_end = part.find(b"\r\n\r\n")
        if header_end == -1:
            continue
        headers = part[:header_end].decode("utf-8", errors="replace")
        file_data = part[header_end + 4 :]
        if file_data.endswith(b"\r\n"):
            file_data = file_data[:-2]

        filename = ""
        for line in headers.split("\r\n"):
            if "filename=" in line:
                start = line.index('filename="') + 10
                end = line.index('"', start)
                filename = line[start:end]
                break

        if filename:
            return filename, file_data

    raise ValueError("No file found in multipart body")


class Handler(BaseHTTPRequestHandler):
    db: Database
    csv_dir: Path

    def log_message(self, format, *args):
        pass

    def _json_response(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status, message):
        self._json_response({"error": message}, status)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        body = self.rfile.read(length)
        return json.loads(body)

    def _read_file_upload(self) -> tuple[str, bytes]:
        content_type = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        return _parse_multipart(content_type, body)

    def _extract_uuid(self, path: str, prefix: str) -> str:
        rest = path[len(prefix) :]
        return rest.split("/")[0]

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
            "Access-Control-Allow-Methods", "GET,POST,PATCH,DELETE,OPTIONS"
        )
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/":
            html = generate_server_html()
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/transactions":
            txns = self.db.get_all_transactions()
            self._json_response({"transactions": _txns_to_json(txns)})
        elif path == "/api/transactions/deleted":
            txns = self.db.get_all_transactions(include_deleted=True)
            deleted = [t for t in txns if t.is_deleted]
            self._json_response({"transactions": _txns_to_json(deleted)})
        elif path == "/api/stats":
            self._json_response({"stats": self.db.get_stats()})
        elif path == "/api/summary":
            txns = self.db.get_all_transactions()
            summary = _compute_summary(txns)
            self._json_response({"summary": summary})
        elif path == "/api/budgets":
            params = parse_qs(parsed.query)
            month = params.get("month", [None])[0]
            self._json_response({"budgets": self.db.get_budgets(month)})
        elif path == "/api/history":
            self._json_response({"history": self.db.get_import_history()})
        elif path == "/api/rules":
            self._json_response({"rules": self.db.get_category_rules()})
        elif path == "/api/store-pairs":
            self._json_response({"store_pairs": self.db.get_store_pairs()})
        elif path == "/api/suggest":
            self._json_response({"suggestions": compute_suggestions(self.db)})
        elif path == "/api/anomalies":
            txns = self.db.get_all_transactions()
            self._json_response({"anomalies": compute_anomalies(txns)})
        elif path == "/api/uncategorized":
            self._json_response({"merchants": compute_uncategorized(self.db)})
        elif path == "/api/categories":
            cat_db = self.db.load_category_db()
            self._json_response({"categories": cat_db.categories})
        else:
            self._error(404, "Not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/api/import":
            self._handle_import(preview=False)
        elif path == "/api/import/preview":
            self._handle_import(preview=True)
        elif path == "/api/rules":
            data = self._read_json_body()
            pattern = data.get("pattern", "")
            category = data.get("category", "")
            match_type = data.get("match_type", "exact")
            if not pattern or not category:
                self._error(400, "pattern and category required")
                return
            self.db.add_category_rule(pattern, category, match_type)
            self._json_response({"ok": True})
        elif path == "/api/store-pairs":
            data = self._read_json_body()
            raw = data.get("raw_name", "")
            norm = data.get("normalized_name", "")
            if not raw or not norm:
                self._error(400, "raw_name and normalized_name required")
                return
            self.db.add_store_pair(raw, norm)
            self._json_response({"ok": True})
        elif path == "/api/budgets":
            data = self._read_json_body()
            month = data.get("month", "")
            category = data.get("category", "")
            amount = data.get("amount", 0)
            if not month or not category:
                self._error(400, "month and category required")
                return
            self.db.set_budget(month, category, float(amount))
            self._json_response({"ok": True})
        elif path == "/api/budgets/copy":
            data = self._read_json_body()
            from_month = data.get("from_month", "")
            to_month = data.get("to_month", "")
            if not from_month or not to_month:
                self._error(400, "from_month and to_month required")
                return
            count = self.db.copy_budget(from_month, to_month)
            self._json_response({"ok": True, "count": count})
        elif path == "/api/suggest/apply":
            data = self._read_json_body()
            suggestions = data.get("suggestions", [])
            for s in suggestions:
                self.db.add_category_rule(s["store"], s["category"], "exact")
            cat_db = self.db.load_category_db()
            updated = self.db.recategorize_all(cat_db)
            self._json_response(
                {"ok": True, "rules_added": len(suggestions), "recategorized": updated}
            )
        elif path == "/api/recategorize":
            cat_db = self.db.load_category_db()
            updated = self.db.recategorize_all(cat_db)
            self._json_response({"ok": True, "updated": updated})
        elif path.startswith("/api/transactions/") and path.endswith("/restore"):
            uuid = self._extract_uuid(path, "/api/transactions/")
            uuid = uuid.rstrip("/")
            if self.db.restore(uuid):
                self._json_response({"ok": True})
            else:
                self._error(404, "Transaction not found")
        else:
            self._error(404, "Not found")

    def do_PATCH(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path.startswith("/api/transactions/") and "/category" in path:
            uuid = self._extract_uuid(path, "/api/transactions/")
            if uuid.endswith("/category"):
                uuid = uuid[:-9]
            data = self._read_json_body()
            category = data.get("category", "")
            if not category:
                self._error(400, "category required")
                return
            self.db.update_category(uuid, category)
            self._json_response({"ok": True})
        else:
            self._error(404, "Not found")

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path.startswith("/api/transactions/"):
            uuid = self._extract_uuid(path, "/api/transactions/")
            if self.db.soft_delete(uuid):
                self._json_response({"ok": True})
            else:
                self._error(404, "Transaction not found")
        else:
            self._error(404, "Not found")

    def _handle_import(self, preview: bool):
        try:
            filename, file_data = self._read_file_upload()
        except (ValueError, KeyError) as e:
            self._error(400, f"File upload error: {e}")
            return

        with tempfile.NamedTemporaryFile(
            suffix=".csv", delete=False, dir=str(self.csv_dir)
        ) as f:
            f.write(file_data)
            tmp_path = Path(f.name)

        try:
            if preview:
                from .adapters import detect_and_parse

                txns = detect_and_parse(tmp_path)
                expenses = [t for t in txns if t.txn_type == TxnType.EXPENSE]
                income = [t for t in txns if t.txn_type == TxnType.INCOME]
                cat_db = self.db.load_category_db()
                classified, unclassified = categorize_batch(expenses, cat_db)
                dates = [t.date.isoformat() for t in txns] if txns else []
                self._json_response(
                    {
                        "preview": {
                            "filename": filename,
                            "parsed": len(txns),
                            "expenses": len(expenses),
                            "income": len(income),
                            "classified": len(classified),
                            "unclassified": len(unclassified),
                            "date_range": [min(dates), max(dates)] if dates else [],
                            "transactions": _txns_to_json(txns[:50]),
                        }
                    }
                )
            else:
                cat_db = self.db.load_category_db()
                result = import_file(self.db, tmp_path, cat_db)
                self._json_response({"result": result})
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def run_server(
    db_path: str, host: str = "127.0.0.1", port: int = 8000, csv_dir: str = "data/new"
):
    db = Database(db_path)
    db.initialize()
    Handler.db = db
    csv_path = Path(csv_dir)
    csv_path.mkdir(parents=True, exist_ok=True)
    Handler.csv_dir = csv_path

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Dashboard: http://{host}:{port}")
    print("Press Ctrl+C to stop")
    try:
        import webbrowser

        webbrowser.open(f"http://{host}:{port}")
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        db.close()
        server.server_close()
