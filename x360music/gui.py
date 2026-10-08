"""Window version (tkinter) - pick your music and the USB stick, press CONVERT."""

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import __version__
from .tasks import convert_all, song_folder

HELP_TEXT = (
    "1) Music folder: the folder with your MP3s (subfolders are searched too).\n"
    "2) USB stick: the stick you'll plug into the Xbox. The songs go in its HddMusic folder.\n"
    "3) Press CONVERT. Needs ffmpeg.exe and ffprobe.exe next to this program\n"
    "   (from https://ffmpeg.org/download.html).\n"
    "4) Plug the stick into the Xbox (with the HddMusic plugin loaded), hold BACK and press X.\n"
    "   When it says the songs were added, restart the console: they're in\n"
    "   Music Player > Hard Drive, and you can play them in any game from the Guide.\n"
)


class App:
    def __init__(self, root):
        self.root = root
        self.queue = queue.Queue()
        self.worker = None
        self.cancel = threading.Event()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.title("x360music %s - songs for the Xbox 360 hard drive" % __version__)
        root.minsize(720, 480)
        self.music = tk.StringVar()
        self.usb = tk.StringVar()

        frame = ttk.Frame(root, padding=10)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        for row, (text, var, command) in enumerate((
                ("Music folder", self.music, self.pick_music),
                ("USB stick", self.usb, self.pick_usb))):
            ttk.Label(frame, text=text).grid(row=row, column=0, sticky="w", pady=2)
            ttk.Entry(frame, textvariable=var, width=60).grid(row=row, column=1, sticky="we", pady=2)
            ttk.Button(frame, text="Browse...", command=command).grid(row=row, column=2, padx=4)

        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, columnspan=3, sticky="we", pady=6)
        ttk.Button(buttons, text="CONVERT", command=self.start).pack(side="left", padx=4)
        ttk.Button(buttons, text="Open song folder", command=self.open_output).pack(side="left", padx=4)
        ttk.Button(buttons, text="Help", command=lambda: self.log(HELP_TEXT)).pack(side="right", padx=4)

        self.text = ScrolledText(frame, height=16, wrap="word", font=("Consolas", 9))
        self.text.grid(row=3, column=0, columnspan=3, sticky="nsew")
        frame.rowconfigure(3, weight=1)
        self.log(HELP_TEXT)
        root.after(100, self.drain)

    def log(self, message):
        self.queue.put(message)

    def drain(self):
        try:
            while True:
                message = self.queue.get_nowait()
                self.text.insert("end", message + "\n")
                self.text.see("end")
        except queue.Empty:
            pass
        self.root.after(100, self.drain)

    def pick_music(self):
        path = filedialog.askdirectory(title="Folder with your music")
        if path:
            self.music.set(path)

    def pick_usb(self):
        path = filedialog.askdirectory(title="The USB stick (its top folder)")
        if path:
            self.usb.set(path)

    def start(self):
        music, usb = self.music.get().strip(), self.usb.get().strip()
        if not os.path.isdir(music):
            messagebox.showerror("Missing", "Pick the folder with your music first.")
            return
        if not os.path.isdir(usb):
            messagebox.showerror("Missing", "Pick the USB stick first.")
            return
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Busy", "Still working - wait for it to finish.")
            return

        def work():
            # Runs on a worker thread: only touch the queue, never Tk widgets/variables.
            try:
                self.log("=" * 70)
                summary = convert_all([music], usb, log=self.log, cancel=self.cancel)
                self.log("")
                self.log("DONE: %d converted, %d already there, %d duplicates skipped, %d failed"
                         % (summary.converted, summary.existing, summary.duplicates,
                            len(summary.failed)))
                for path, message in summary.failed:
                    self.log("  FAILED %s: %s" % (path, message))
                self.log("Now plug the stick into the Xbox, hold BACK and press X.")
            except Exception as exc:  # keep the window alive and show the problem
                self.log("ERROR: %s" % exc)
        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def close(self):
        self.cancel.set()  # songs already converting finish; the rest are skipped
        self.root.destroy()

    def open_output(self):
        usb = self.usb.get().strip()
        path = song_folder(usb) if usb else ""
        if not path or not os.path.isdir(path):
            messagebox.showinfo("Songs", "The HddMusic folder does not exist yet.")
            return
        if sys.platform.startswith("win"):
            os.startfile(path)  # noqa: S606 - opening a folder the user picked
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0
