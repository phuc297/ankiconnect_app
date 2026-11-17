import tkinter as tk
from tkinter import messagebox, ttk

import requests

ANKI_URL = "http://127.0.0.1:8765"


def invoke(action, **params):
    """Gửi yêu cầu đến AnkiConnect API."""

    payload = {"action": action, "version": 6, "params": params}

    try:
        response = requests.post(ANKI_URL, json=payload, timeout=10)

        response.raise_for_status()
        data = response.json()

        if data["error"] is not None:
            raise Exception(data["error"])

        return data["result"]

    except requests.exceptions.ConnectionError:
        print("Không thể kết nối Anki. Hãy mở Anki!")
        return None

    except Exception as e:
        print(f"Lỗi: {e}")
        return None


def get_decks():
    result = invoke("deckNames")
    return result


def get_due_cards(deck):
    card_ids = invoke("findCards", query=f"deck:{deck} prop:due=0")
    card_infos = invoke("cardsInfo", cards=card_ids) or []

    words = []

    for card in card_infos:
        words.append(card["fields"]["Front"]["value"])

    return words


def get_all_due_cards(deck):
    card_ids = invoke("findCards", query=f"deck:{deck} is:due")
    card_infos = invoke("cardsInfo", cards=card_ids) or []

    words = []

    for card in card_infos:
        words.append(card["fields"]["Front"]["value"])

    return words


class Application:
    def __init__(self, root):
        self.root = root
        root.title("Main")
        root.geometry("550x550")

        # Configure root layout
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        # Top controls
        self.top_frame = ttk.Frame(root)
        self.top_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=10)

        self.top_frame.columnconfigure(1, weight=1)

        self.decks = get_decks()
        self.deck_cbx = ttk.Combobox(self.top_frame, values=self.decks)
        self.deck_cbx.grid(row=0, column=0, sticky="ew", padx=5)
        if self.decks:
            self.deck_cbx.current(0)

        self.due_btn = ttk.Button(
            self.top_frame, text="get today due", command=self.on_due_btn
        )
        self.due_btn.grid(row=0, column=1, padx=5)

        self.all_due_btn = ttk.Button(
            self.top_frame, text="get all due", command=self.on_all_due_btn
        )
        self.all_due_btn.grid(row=0, column=2, padx=5)

        self.reload_btn = ttk.Button(
            self.top_frame, text="Load", command=self.on_reload_btn
        )
        self.reload_btn.grid(row=0, column=3, padx=5)

        # Text container
        self.text_frame = ttk.Frame(root)
        self.text_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=5)

        self.text_frame.columnconfigure(0, weight=1)
        self.text_frame.rowconfigure(0, weight=1)

        # TextBox
        self.result_text = tk.Text(self.text_frame, wrap="word")
        self.result_text.grid(row=0, column=0, sticky="nsew")

        # Scrollbar
        scrollbar = ttk.Scrollbar(self.text_frame, command=self.result_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")

        self.result_text.configure(yscrollcommand=scrollbar.set)

        # Bottom controls
        self.bottom_frame = ttk.Frame(root)
        self.bottom_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=10)

        self.copy_btn = ttk.Button(
            self.bottom_frame, text="Copy", command=self.on_copy_btn
        )
        self.copy_btn.pack(side="left")

    def on_due_btn(self):
        selected_deck = self.deck_cbx.get()
        if not selected_deck:
            messagebox.showwarning("Warning", "Vui lòng chọn deck!")
            return
        due_cards = get_due_cards(selected_deck)
        result = "\n".join(due_cards)
        self.change_text_frame(result)

    def on_all_due_btn(self):
        selected_deck = self.deck_cbx.get()
        if not selected_deck:
            messagebox.showwarning("Warning", "Vui lòng chọn deck!")
            return
        due_cards = get_all_due_cards(selected_deck)
        result = "\n".join(due_cards)
        self.change_text_frame(result)

    def on_copy_btn(self):
        text = self.result_text.get("1.0", "end-1c")
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    def on_reload_btn(self):
        self.decks = get_decks()
        self.deck_cbx["values"] = self.decks
        if not self.decks:
            messagebox.showwarning("Warning", "Không thể kết nối Anki. Hãy mở Anki!")
            return
        self.deck_cbx.current(0)

    def change_text_frame(self, text):
        self.result_text.delete("1.0", tk.END)
        self.result_text.insert(tk.END, text)


if __name__ == "__main__":
    root = tk.Tk()
    app = Application(root)
    root.mainloop()
