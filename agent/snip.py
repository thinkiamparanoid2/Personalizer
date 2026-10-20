"""2.2 Screen snip -> code / LaTeX / translation.

Hotkey (default Alt+S) opens the Windows snipping overlay; the captured image is sent to Gemini Vision
and the result lands on the clipboard. Reusing the built-in snipper avoids DPI/multi-monitor bugs
of a custom overlay.
"""
import io
import os
import re
import threading
import time

from . import config
from .util import error_box, gemini_generate, notify, set_clipboard_text

PROMPTS = {
    "code": "Extract the source code in this image exactly, with correct indentation. "
            "Output only the code: no Markdown fences, no commentary.",
    "latex": "Convert the mathematics in this image to LaTeX. Output only the LaTeX source "
             "(no $ delimiters, no Markdown fences, no commentary).",
    "translate": "Translate all text in this image into English. Then give a brief grammar breakdown "
                 "of the key phrases (word order, cases, verb forms). Plain text, no Markdown.",
}


def _wait_for_clipboard_image(timeout=60):
    import win32clipboard
    from PIL import Image, ImageGrab
    seq = win32clipboard.GetClipboardSequenceNumber()
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.3)
        now = win32clipboard.GetClipboardSequenceNumber()
        if now != seq:
            seq = now
            img = ImageGrab.grabclipboard()
            if isinstance(img, Image.Image):
                return img
    return None


def choose_mode():
    """Small always-on-top picker at the mouse pointer. Keys 1/2/3 or click; Esc cancels."""
    import tkinter as tk
    choice = {}
    root = tk.Tk()
    root.title("Jarvis")
    root.attributes("-topmost", True)
    root.resizable(False, False)
    x, y = root.winfo_pointerxy()
    root.geometry(f"+{x}+{y}")

    def pick(mode):
        choice["mode"] = mode
        root.destroy()

    for i, (label, mode) in enumerate((("1  Copy Code", "code"), ("2  Copy LaTeX", "latex"),
                                       ("3  Translate", "translate")), 1):
        tk.Button(root, text=label, width=18, anchor="w", command=lambda m=mode: pick(m)).pack(padx=8, pady=3)
        root.bind(str(i), lambda e, m=mode: pick(m))
    root.bind("<Escape>", lambda e: root.destroy())
    root.focus_force()
    root.mainloop()
    return choice.get("mode")


def show_text(title, text):
    import tkinter as tk
    root = tk.Tk()
    root.title(title)
    root.attributes("-topmost", True)
    box = tk.Text(root, wrap="word", width=70, height=20, font=("Segoe UI", 10))
    box.insert("1.0", text)
    box.configure(state="disabled")
    box.pack(fill="both", expand=True, padx=8, pady=8)
    root.bind("<Escape>", lambda e: root.destroy())
    root.mainloop()


def analyze(img, mode):
    from google.genai import types
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    resp = gemini_generate([types.Part.from_bytes(data=buf.getvalue(), mime_type="image/png"), PROMPTS[mode]])
    text = (resp.text or "").strip()
    if mode != "translate":
        text = re.sub(r"^```[\w-]*\n|\n?```$", "", text).strip()
    return text


def snip_flow():
    try:
        os.startfile("ms-screenclip:")
        img = _wait_for_clipboard_image()
        if img is None:
            return
        mode = choose_mode()
        if not mode:
            return
        notify("Jarvis", "Reading snip...")
        text = analyze(img, mode)
        set_clipboard_text(text)
        notify("Copied to clipboard", text[:120])
        if mode == "translate":
            show_text("Jarvis - Translation", text)
    except Exception as e:
        error_box("Jarvis - Snip", e)


def start():
    import keyboard
    keyboard.add_hotkey(config.load()["snip_hotkey"],
                        lambda: threading.Thread(target=snip_flow, daemon=True).start())
