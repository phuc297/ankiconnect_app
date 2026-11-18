"""Anki Helper: lấy danh sách từ và thêm card vào Anki thông qua AnkiConnect."""

import copy
import json
import queue
import re
import threading
import tkinter as tk
from html import unescape
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import requests

DEFAULT_URL = "http://127.0.0.1:8765"
CONFIG_PATH = Path(__file__).with_name("config.json")

ALL_DECKS = "(Tất cả deck)"
KEEP_JSON_DECK = "(theo JSON)"

# Tên hiển thị -> điều kiện tìm kiếm của Anki
MODES = {
    "Đến hạn hôm nay": "prop:due=0",
    "Tất cả đã đến hạn": "is:due",
    "Thẻ mới": "is:new",
    "Đang học": "is:learn",
    "Tất cả thẻ": "",
}

SAMPLE_JSON = """{
  "action": "addNotes",
  "version": 6,
  "params": {
    "notes": [
      {
        "deckName": "english temp",
        "modelName": "Basic",
        "fields": {
          "Front": "peek",
          "IPA": "/piːk/",
          "Meaning": "(v.) to look quickly or secretly at something",
          "Back": "She tried to <b><u>peek</u></b> through the curtains."
        },
        "tags": ["Word"],
        "options": {
          "allowDuplicate": false
        }
      }
    ]
  }
}"""


# --------------------------------------------------------------------------
# AnkiConnect
# --------------------------------------------------------------------------


class AnkiError(Exception):
    """Lỗi do Anki/AnkiConnect trả về."""


class AnkiConnectionError(AnkiError):
    """Không kết nối được tới Anki."""


class AnkiClient:
    def __init__(self, url=DEFAULT_URL):
        self.url = url

    def request(self, action, **params):
        payload = {"action": action, "version": 6, "params": params}

        try:
            response = requests.post(self.url, json=payload, timeout=60)
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.ConnectionError as exc:
            raise AnkiConnectionError(
                "Không thể kết nối Anki. Hãy mở Anki và cài add-on AnkiConnect!"
            ) from exc
        except (requests.RequestException, ValueError) as exc:
            raise AnkiError(f"Lỗi giao tiếp với Anki: {exc}") from exc

        if data.get("error") is not None:
            raise AnkiError(str(data["error"]))

        return data.get("result")

    def load_cards(self, deck, extra_query=""):
        """Trả về danh sách thông tin card theo deck và điều kiện tìm kiếm."""

        parts = []
        if deck and deck != ALL_DECKS:
            parts.append(f'"deck:{escape_search(deck)}"')
        if extra_query:
            parts.append(extra_query)

        query = " ".join(parts) or "deck:*"
        card_ids = self.request("findCards", query=query)
        if not card_ids:
            return []
        return self.request("cardsInfo", cards=card_ids)

    def add_notes(self, notes):
        """Trả về danh sách id; phần tử None nghĩa là note không được thêm."""

        return self.request("addNotes", notes=notes)


# --------------------------------------------------------------------------
# Hàm tiện ích
# --------------------------------------------------------------------------


def escape_search(text):
    """Escape ký tự đặc biệt trong cú pháp tìm kiếm của Anki."""

    return re.sub(r'([\\"*_])', r"\\\1", text)


def strip_html(text):
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def load_json(raw):
    """Đọc JSON, chấp nhận cả khi được bọc trong ```json ... ```."""

    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE)
    return json.loads(raw)


def parse_notes(raw, deck_override=None, allow_duplicate=None):
    """Chuyển chuỗi JSON thành danh sách note hợp lệ.

    Chấp nhận các dạng:
    - Payload đầy đủ: {"action": "addNotes", "params": {"notes": [...]}}
    - {"notes": [...]}
    - Danh sách note: [{...}, {...}]
    - Một note đơn: {"deckName": ..., "modelName": ..., "fields": {...}}
    """

    data = load_json(raw)

    if isinstance(data, dict):
        if "params" in data:
            action = data.get("action", "addNotes")
            if action != "addNotes":
                raise ValueError(f"Chỉ hỗ trợ action 'addNotes' (nhận được '{action}').")
            data = data["params"]
            if not isinstance(data, dict):
                raise ValueError("'params' phải là một đối tượng JSON.")
        data = data["notes"] if "notes" in data else [data]

    if not isinstance(data, list) or not data:
        raise ValueError("Không tìm thấy note nào trong JSON.")

    notes = []
    for index, original in enumerate(data, start=1):
        if not isinstance(original, dict):
            raise ValueError(f"Note #{index} không hợp lệ.")

        note = copy.deepcopy(original)
        if deck_override:
            note["deckName"] = deck_override

        for key in ("deckName", "modelName", "fields"):
            if not note.get(key):
                raise ValueError(f"Note #{index} thiếu trường '{key}'.")

        if not isinstance(note["fields"], dict):
            raise ValueError(f"Note #{index}: 'fields' phải là một đối tượng JSON.")
        note["fields"] = {
            str(name): "" if value is None else str(value)
            for name, value in note["fields"].items()
        }

        tags = note.get("tags", [])
        if isinstance(tags, str):
            tags = tags.replace(",", " ").split()
        note["tags"] = [str(tag) for tag in tags]

        if allow_duplicate is not None:
            options = note.get("options")
            if not isinstance(options, dict):
                options = {}
            options["allowDuplicate"] = allow_duplicate
            note["options"] = options

        notes.append(note)

    return notes


def note_label(note, limit=40):
    """Nhãn ngắn gọn cho một note (dựa trên trường đầu tiên)."""

    fields = note.get("fields", {})
    first = next(iter(fields.values()), "")
    label = strip_html(first) or "(trống)"
    return label if len(label) <= limit else label[: limit - 1] + "…"


def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(config):
    try:
        CONFIG_PATH.write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


# --------------------------------------------------------------------------
# Giao diện
# --------------------------------------------------------------------------


class Application:
    def __init__(self, root):
        self.root = root
        self.config = load_config()
        self.client = AnkiClient(self.config.get("url", DEFAULT_URL))

        self.decks = []
        self.models = []
        self.cards = []  # mỗi phần tử: {tên trường: giá trị}
        self.form_fields = {}  # tên trường -> tk.Text
        self.buttons = []
        self.busy = 0
        self.jobs = queue.Queue()

        root.title("Anki Helper")
        root.geometry("800x680")
        root.minsize(680, 520)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        self.build_header()

        self.notebook = ttk.Notebook(root)
        self.notebook.grid(row=1, column=0, sticky="nsew", padx=5)

        self.list_tab = ttk.Frame(self.notebook)
        self.add_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.list_tab, text="Danh sách")
        self.notebook.add(self.add_tab, text="Thêm card")

        self.build_list_tab(self.list_tab)
        self.build_add_tab(self.add_tab)

        self.status_var = tk.StringVar(value="Sẵn sàng")
        ttk.Label(root, textvariable=self.status_var, anchor="w", padding=(10, 4)).grid(
            row=2, column=0, sticky="ew"
        )

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.poll_jobs()
        self.refresh()

    # ----- Xây dựng giao diện ---------------------------------------------

    def build_header(self):
        header = ttk.Frame(self.root)
        header.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 5))
        header.columnconfigure(1, weight=1)

        ttk.Label(header, text="AnkiConnect:").grid(row=0, column=0, padx=(0, 5))

        self.url_var = tk.StringVar(value=self.client.url)
        url_entry = ttk.Entry(header, textvariable=self.url_var)
        url_entry.grid(row=0, column=1, sticky="ew")
        url_entry.bind("<Return>", lambda _e: self.refresh())

        self.reconnect_btn = ttk.Button(header, text="Kết nối lại", command=self.refresh)
        self.reconnect_btn.grid(row=0, column=2, padx=5)
        self.buttons.append(self.reconnect_btn)

        self.conn_label = ttk.Label(header, text="● Chưa kết nối", foreground="#c62828")
        self.conn_label.grid(row=0, column=3, padx=(5, 0))

    def build_list_tab(self, tab):
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(2, weight=1)

        # Hàng điều khiển chính
        controls = ttk.Frame(tab)
        controls.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 5))
        controls.columnconfigure(0, weight=1)

        self.list_deck_var = tk.StringVar()
        self.list_deck_cbx = ttk.Combobox(
            controls, textvariable=self.list_deck_var, state="readonly"
        )
        self.list_deck_cbx.grid(row=0, column=0, sticky="ew", padx=(0, 5))

        self.mode_var = tk.StringVar(value=self.config.get("mode", "Đến hạn hôm nay"))
        if self.mode_var.get() not in MODES:
            self.mode_var.set("Đến hạn hôm nay")
        mode_cbx = ttk.Combobox(
            controls,
            textvariable=self.mode_var,
            values=list(MODES),
            state="readonly",
            width=20,
        )
        mode_cbx.grid(row=0, column=1, padx=5)

        self.load_btn = ttk.Button(controls, text="Tải", command=self.on_load)
        self.load_btn.grid(row=0, column=2, padx=(5, 0))
        self.buttons.append(self.load_btn)

        # Hàng tùy chọn hiển thị
        options = ttk.Frame(tab)
        options.grid(row=1, column=0, sticky="ew", padx=10, pady=5)
        options.columnconfigure(5, weight=1)

        ttk.Label(options, text="Trường:").grid(row=0, column=0, padx=(0, 5))
        self.field_var = tk.StringVar(value=self.config.get("field", "Front"))
        self.field_cbx = ttk.Combobox(
            options, textvariable=self.field_var, state="readonly", width=14
        )
        self.field_cbx.grid(row=0, column=1)
        self.field_cbx.bind("<<ComboboxSelected>>", lambda _e: self.render_list())

        self.strip_var = tk.BooleanVar(value=self.config.get("strip_html", True))
        ttk.Checkbutton(
            options, text="Bỏ HTML", variable=self.strip_var, command=self.render_list
        ).grid(row=0, column=2, padx=10)

        self.dedupe_var = tk.BooleanVar(value=self.config.get("dedupe", True))
        ttk.Checkbutton(
            options, text="Bỏ trùng", variable=self.dedupe_var, command=self.render_list
        ).grid(row=0, column=3)

        ttk.Label(options, text="Lọc:").grid(row=0, column=4, padx=(15, 5))
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self.render_list())
        ttk.Entry(options, textvariable=self.filter_var).grid(row=0, column=5, sticky="ew")

        # Khung kết quả
        text_frame = ttk.Frame(tab)
        text_frame.grid(row=2, column=0, sticky="nsew", padx=10, pady=5)
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)

        self.result_text = tk.Text(text_frame, wrap="word")
        self.result_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(text_frame, command=self.result_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.result_text.configure(yscrollcommand=scrollbar.set)

        # Hàng dưới
        bottom = ttk.Frame(tab)
        bottom.grid(row=3, column=0, sticky="ew", padx=10, pady=(5, 10))

        ttk.Button(bottom, text="Copy", command=self.on_copy).pack(side="left")
        ttk.Button(bottom, text="Lưu file...", command=self.on_save).pack(side="left", padx=5)
        self.count_label = ttk.Label(bottom, text="0 mục")
        self.count_label.pack(side="right")

    def build_add_tab(self, tab):
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)

        self.add_notebook = ttk.Notebook(tab)
        self.add_notebook.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 5))

        json_tab = ttk.Frame(self.add_notebook)
        form_tab = ttk.Frame(self.add_notebook)
        self.add_notebook.add(json_tab, text="JSON")
        self.add_notebook.add(form_tab, text="Nhập tay")

        self.build_json_tab(json_tab)
        self.build_form_tab(form_tab)

        # Nhật ký kết quả
        log_frame = ttk.LabelFrame(tab, text="Kết quả")
        log_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        log_frame.columnconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, height=6, wrap="word", state="disabled")
        self.log_text.grid(row=0, column=0, sticky="ew")
        log_scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scroll.set)

    def build_json_tab(self, tab):
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        options = ttk.Frame(tab)
        options.grid(row=0, column=0, sticky="ew", padx=5, pady=5)
        options.columnconfigure(1, weight=1)

        ttk.Label(options, text="Deck:").grid(row=0, column=0, padx=(0, 5))
        self.json_deck_var = tk.StringVar(value=KEEP_JSON_DECK)
        self.json_deck_cbx = ttk.Combobox(
            options,
            textvariable=self.json_deck_var,
            values=[KEEP_JSON_DECK],
            state="readonly",
        )
        self.json_deck_cbx.grid(row=0, column=1, sticky="ew")

        self.json_dup_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            options, text="Cho phép trùng (ghi đè JSON)", variable=self.json_dup_var
        ).grid(row=0, column=2, padx=(10, 0))

        json_frame = ttk.Frame(tab)
        json_frame.grid(row=1, column=0, sticky="nsew", padx=5)
        json_frame.columnconfigure(0, weight=1)
        json_frame.rowconfigure(0, weight=1)

        self.json_text = tk.Text(json_frame, wrap="none", undo=True, height=10)
        self.json_text.grid(row=0, column=0, sticky="nsew")
        self.json_text.bind("<Control-Return>", lambda _e: self.on_add_json() or "break")

        scroll_y = ttk.Scrollbar(json_frame, command=self.json_text.yview)
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x = ttk.Scrollbar(json_frame, orient="horizontal", command=self.json_text.xview)
        scroll_x.grid(row=1, column=0, sticky="ew")
        self.json_text.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)

        buttons = ttk.Frame(tab)
        buttons.grid(row=2, column=0, sticky="ew", padx=5, pady=5)

        self.add_json_btn = ttk.Button(buttons, text="Thêm (Ctrl+Enter)", command=self.on_add_json)
        self.add_json_btn.pack(side="left")
        self.buttons.append(self.add_json_btn)

        self.check_btn = ttk.Button(buttons, text="Kiểm tra trùng", command=self.on_check_json)
        self.check_btn.pack(side="left", padx=5)
        self.buttons.append(self.check_btn)

        ttk.Button(buttons, text="Định dạng", command=self.on_format_json).pack(side="left")
        ttk.Button(buttons, text="Dán", command=self.on_paste_json).pack(side="left", padx=5)
        ttk.Button(buttons, text="Chèn mẫu", command=self.on_sample_json).pack(side="left")
        ttk.Button(buttons, text="Xóa", command=self.on_clear_json).pack(side="left", padx=5)

    def build_form_tab(self, tab):
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(2, weight=1)

        ttk.Label(tab, text="Deck:").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.form_deck_var = tk.StringVar()
        self.form_deck_cbx = ttk.Combobox(tab, textvariable=self.form_deck_var)
        self.form_deck_cbx.grid(row=0, column=1, sticky="ew", padx=5, pady=5)

        ttk.Label(tab, text="Model:").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        self.form_model_var = tk.StringVar()
        self.form_model_cbx = ttk.Combobox(
            tab, textvariable=self.form_model_var, state="readonly"
        )
        self.form_model_cbx.grid(row=1, column=1, sticky="ew", padx=5, pady=5)
        self.form_model_cbx.bind("<<ComboboxSelected>>", lambda _e: self.load_model_fields())

        # Các trường được tạo động theo model
        self.form_fields_frame = ttk.Frame(tab)
        self.form_fields_frame.grid(row=2, column=0, columnspan=2, sticky="nsew", padx=5)
        self.form_fields_frame.columnconfigure(1, weight=1)

        ttk.Label(tab, text="Tags:").grid(row=3, column=0, sticky="w", padx=5, pady=5)
        self.form_tags_var = tk.StringVar(value=self.config.get("form_tags", "Word"))
        ttk.Entry(tab, textvariable=self.form_tags_var).grid(
            row=3, column=1, sticky="ew", padx=5, pady=5
        )

        bottom = ttk.Frame(tab)
        bottom.grid(row=4, column=0, columnspan=2, sticky="ew", padx=5, pady=5)

        self.form_add_btn = ttk.Button(bottom, text="Thêm", command=self.on_add_form)
        self.form_add_btn.pack(side="left")
        self.buttons.append(self.form_add_btn)

        ttk.Button(bottom, text="Xóa form", command=self.clear_form).pack(side="left", padx=5)

        self.form_dup_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bottom, text="Cho phép trùng", variable=self.form_dup_var).pack(
            side="left", padx=10
        )

    # ----- Chạy tác vụ nền ------------------------------------------------

    def poll_jobs(self):
        self.root.after(50, self.poll_jobs)
        while True:
            try:
                job = self.jobs.get_nowait()
            except queue.Empty:
                break
            job()

    def run(self, func, on_ok, on_err=None, status=None):
        """Chạy func ở luồng nền để giao diện không bị đứng."""

        if status:
            self.set_status(status)
        self.set_busy(True)

        def worker():
            try:
                result = func()
            except Exception as exc:  # noqa: BLE001
                self.jobs.put(lambda exc=exc: self.finish(on_err or self.show_error, exc))
            else:
                self.jobs.put(lambda: self.finish(on_ok, result))

        threading.Thread(target=worker, daemon=True).start()

    def finish(self, callback, argument):
        self.set_busy(False)
        callback(argument)

    def set_busy(self, busy):
        self.busy += 1 if busy else -1
        state = ["disabled"] if self.busy > 0 else ["!disabled"]
        for button in self.buttons:
            button.state(state)

    def set_status(self, text):
        self.status_var.set(text)

    def set_connected(self, connected, version=None):
        if connected:
            self.conn_label.config(text=f"● Đã kết nối (v{version})", foreground="#1a7f37")
        else:
            self.conn_label.config(text="● Chưa kết nối", foreground="#c62828")

    def show_error(self, exc):
        if isinstance(exc, AnkiConnectionError):
            self.set_connected(False)
        self.set_status(f"Lỗi: {exc}")
        messagebox.showerror("Lỗi", str(exc))

    # ----- Kết nối và nạp dữ liệu -----------------------------------------

    def refresh(self):
        self.client.url = self.url_var.get().strip() or DEFAULT_URL

        def work():
            return {
                "version": self.client.request("version"),
                "decks": sorted(self.client.request("deckNames")),
                "models": sorted(self.client.request("modelNames")),
            }

        self.run(work, self.on_refreshed, self.on_refresh_failed, "Đang kết nối Anki...")

    def on_refreshed(self, data):
        self.set_connected(True, data["version"])
        self.decks = data["decks"]
        self.models = data["models"]
        self.update_deck_widgets()
        self.set_status(f"Đã tải {len(self.decks)} deck, {len(self.models)} model")

    def on_refresh_failed(self, exc):
        self.set_connected(False)
        self.set_status(str(exc))

    @staticmethod
    def pick(current, values, *preferred):
        """Chọn giá trị hợp lệ: giữ giá trị hiện tại, nếu không thì lấy ưu tiên đầu tiên có trong danh sách."""

        for candidate in (current, *preferred):
            if candidate and candidate in values:
                return candidate
        return values[0] if values else ""

    def update_deck_widgets(self):
        list_values = [ALL_DECKS] + self.decks
        self.list_deck_cbx["values"] = list_values
        self.list_deck_var.set(
            self.pick(self.list_deck_var.get(), list_values, self.config.get("deck"), ALL_DECKS)
        )

        json_values = [KEEP_JSON_DECK] + self.decks
        self.json_deck_cbx["values"] = json_values
        self.json_deck_var.set(self.pick(self.json_deck_var.get(), json_values, KEEP_JSON_DECK))

        self.form_deck_cbx["values"] = self.decks
        if not self.form_deck_var.get():
            self.form_deck_var.set(self.pick("", self.decks, self.config.get("form_deck")))

        self.form_model_cbx["values"] = self.models
        previous = self.form_model_var.get()
        model = self.pick(previous, self.models, self.config.get("form_model"), "Basic")
        self.form_model_var.set(model)
        if model != previous or not self.form_fields:
            self.load_model_fields()

    # ----- Tab Danh sách --------------------------------------------------

    def on_load(self):
        deck = self.list_deck_var.get()
        if not deck:
            messagebox.showwarning("Warning", "Vui lòng chọn deck!")
            return

        query = MODES.get(self.mode_var.get(), "")
        self.run(
            lambda: self.client.load_cards(deck, query),
            self.on_cards_loaded,
            status="Đang tải card...",
        )

    def on_cards_loaded(self, cards):
        self.cards = []
        field_names = []
        for card in cards:
            fields = sorted(card["fields"].items(), key=lambda item: item[1]["order"])
            self.cards.append({name: info["value"] for name, info in fields})
            for name, _info in fields:
                if name not in field_names:
                    field_names.append(name)

        self.field_cbx["values"] = field_names
        self.field_var.set(self.pick(self.field_var.get(), field_names, "Front"))

        self.render_list()
        self.set_status(f"Đã tải {len(self.cards)} card")

    def render_list(self):
        field = self.field_var.get()
        strip = self.strip_var.get()
        dedupe = self.dedupe_var.get()
        keyword = self.filter_var.get().strip().lower()

        items = []
        seen = set()
        for fields in self.cards:
            value = fields.get(field)
            if value is None:
                continue
            value = strip_html(value) if strip else value.strip()
            if not value:
                continue
            if dedupe:
                if value in seen:
                    continue
                seen.add(value)
            if keyword and keyword not in value.lower():
                continue
            items.append(value)

        self.result_text.delete("1.0", tk.END)
        self.result_text.insert(tk.END, "\n".join(items))
        self.count_label.config(text=f"{len(items)} mục")

    def on_copy(self):
        text = self.result_text.get("1.0", "end-1c")
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        lines = len(text.splitlines()) if text else 0
        self.set_status(f"Đã sao chép {lines} dòng")

    def on_save(self):
        text = self.result_text.get("1.0", "end-1c")
        if not text:
            messagebox.showwarning("Warning", "Không có nội dung để lưu!")
            return

        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return

        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("Lỗi", f"Không thể lưu file: {exc}")
            return
        self.set_status(f"Đã lưu {path}")

    # ----- Tab Thêm card: JSON --------------------------------------------

    def read_json_notes(self):
        """Đọc và kiểm tra JSON trong ô nhập. Trả về None nếu có lỗi."""

        raw = self.json_text.get("1.0", "end-1c").strip()
        if not raw:
            messagebox.showwarning("Warning", "Vui lòng nhập JSON!")
            return None

        deck = self.json_deck_var.get()
        deck_override = None if deck in ("", KEEP_JSON_DECK) else deck
        allow_duplicate = True if self.json_dup_var.get() else None

        try:
            return parse_notes(raw, deck_override, allow_duplicate)
        except json.JSONDecodeError as exc:
            messagebox.showerror(
                "JSON không hợp lệ", f"{exc.msg} (dòng {exc.lineno}, cột {exc.colno})"
            )
        except ValueError as exc:
            messagebox.showerror("Dữ liệu không hợp lệ", str(exc))
        return None

    def on_add_json(self):
        notes = self.read_json_notes()
        if notes:
            self.submit_notes(notes, on_success=self.on_clear_json)

    def on_check_json(self):
        notes = self.read_json_notes()
        if not notes:
            return

        def ok(results):
            self.clear_log()
            for note, can_add in zip(notes, results):
                mark = "✓ có thể thêm" if can_add else "✗ trùng hoặc không hợp lệ"
                self.log(f"{mark}: {note_label(note)}")
            usable = sum(1 for r in results if r)
            self.set_status(f"Kiểm tra xong: {usable}/{len(results)} card có thể thêm")

        self.run(
            lambda: self.client.request("canAddNotes", notes=notes),
            ok,
            status="Đang kiểm tra...",
        )

    def on_format_json(self):
        raw = self.json_text.get("1.0", "end-1c").strip()
        if not raw:
            return
        try:
            data = load_json(raw)
        except json.JSONDecodeError as exc:
            messagebox.showerror(
                "JSON không hợp lệ", f"{exc.msg} (dòng {exc.lineno}, cột {exc.colno})"
            )
            return
        self.json_text.delete("1.0", tk.END)
        self.json_text.insert(tk.END, json.dumps(data, ensure_ascii=False, indent=2))

    def on_paste_json(self):
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            return
        self.json_text.delete("1.0", tk.END)
        self.json_text.insert(tk.END, text)

    def on_sample_json(self):
        self.json_text.delete("1.0", tk.END)
        self.json_text.insert(tk.END, SAMPLE_JSON)

    def on_clear_json(self):
        self.json_text.delete("1.0", tk.END)

    # ----- Tab Thêm card: Nhập tay ----------------------------------------

    def load_model_fields(self):
        model = self.form_model_var.get()
        if not model:
            return

        def ok(names):
            if model == self.form_model_var.get():
                self.build_form_fields(names)

        self.run(
            lambda: self.client.request("modelFieldNames", modelName=model),
            ok,
            self.on_refresh_failed,
            "Đang tải các trường của model...",
        )

    def build_form_fields(self, names):
        for child in self.form_fields_frame.winfo_children():
            child.destroy()
        self.form_fields = {}

        for row, name in enumerate(names):
            ttk.Label(self.form_fields_frame, text=f"{name}:").grid(
                row=row, column=0, sticky="nw", padx=(0, 5), pady=3
            )
            text = tk.Text(self.form_fields_frame, height=2, wrap="word", undo=True)
            text.grid(row=row, column=1, sticky="ew", pady=3)
            self.form_fields[name] = text

    def on_add_form(self):
        deck = self.form_deck_var.get().strip()
        model = self.form_model_var.get()
        if not deck or not model:
            messagebox.showwarning("Warning", "Vui lòng chọn deck và model!")
            return
        if not self.form_fields:
            messagebox.showwarning("Warning", "Chưa tải được các trường của model.")
            return

        fields = {
            name: widget.get("1.0", "end-1c").strip()
            for name, widget in self.form_fields.items()
        }
        first = next(iter(fields))
        if not fields[first]:
            messagebox.showwarning("Warning", f"Trường '{first}' không được để trống!")
            return

        note = {
            "deckName": deck,
            "modelName": model,
            "fields": fields,
            "tags": self.form_tags_var.get().replace(",", " ").split(),
            "options": {"allowDuplicate": bool(self.form_dup_var.get())},
        }
        self.submit_notes([note], on_success=self.clear_form)

    def clear_form(self):
        for widget in self.form_fields.values():
            widget.delete("1.0", tk.END)
        if self.form_fields:
            next(iter(self.form_fields.values())).focus_set()

    # ----- Gửi note tới Anki ----------------------------------------------

    def submit_notes(self, notes, on_success=None):
        create = []
        if self.decks:
            create = sorted({note["deckName"] for note in notes} - set(self.decks))
        if create:
            names = "\n".join(f"- {name}" for name in create)
            if not messagebox.askyesno(
                "Deck chưa tồn tại", f"Các deck sau chưa tồn tại:\n{names}\n\nTạo mới?"
            ):
                return

        def work():
            for deck in create:
                self.client.request("createDeck", deck=deck)
            return self.client.add_notes(notes)

        def ok(results):
            self.on_notes_added(notes, results, bool(create), on_success)

        self.run(work, ok, status="Đang thêm card...")

    def on_notes_added(self, notes, results, created_decks, on_success):
        self.clear_log()
        failed = 0
        for note, result in zip(notes, results):
            if result is None:
                failed += 1
                self.log(f"✗ {note_label(note)} — không thêm được (có thể bị trùng)")
            else:
                self.log(f"✓ {note_label(note)}")

        added = len(results) - failed
        message = f"Đã thêm {added}/{len(results)} card."
        self.set_status(message)

        if created_decks:
            self.refresh()
        if failed:
            messagebox.showwarning("Kết quả", f"{message}\n{failed} card không được thêm (có thể bị trùng).")
        elif on_success:
            on_success()

    # ----- Nhật ký --------------------------------------------------------

    def log(self, line):
        self.log_text.config(state="normal")
        self.log_text.insert(tk.END, line + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state="disabled")

    def clear_log(self):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.config(state="disabled")

    # ----- Thoát ----------------------------------------------------------

    def on_close(self):
        self.config.update(
            {
                "url": self.url_var.get().strip() or DEFAULT_URL,
                "deck": self.list_deck_var.get(),
                "mode": self.mode_var.get(),
                "field": self.field_var.get(),
                "strip_html": self.strip_var.get(),
                "dedupe": self.dedupe_var.get(),
                "form_deck": self.form_deck_var.get(),
                "form_model": self.form_model_var.get(),
                "form_tags": self.form_tags_var.get(),
            }
        )
        save_config(self.config)
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = Application(root)
    root.mainloop()
