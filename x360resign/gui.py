"""Window version (tkinter) - fill in the boxes, press RESIGN ALL."""

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import __version__
from .config import load_config, save_config
from .ids import IdError, fmt, parse_console_id, parse_device_id, parse_profile_id
from .keyvault import KeyVaultError, load_keyvault_file, parse_cpu_key
from .resigner import Options, collect_inputs, ids_from_package, run_batch
from .stfs import StfsError, StfsPackage

HELP_TEXT = (
    "1) Mod files folder: the folder with the save files you kept (zips inside are read too).\n"
    "2) Profile ID: yours. Easiest: copy any one of your own saves to a FAT32 USB stick on the\n"
    "   Xbox, then use 'Read IDs from one of my saves' and pick that file.\n"
    "3) KV.bin: your console's KeyVault (dumped from an RGH/JTAG console). Without it the saves\n"
    "   get your IDs and fresh hashes but are NOT signed, so a retail console rejects them.\n"
    "4) Press RESIGN ALL. Copy the 'Content' folder from the output to the root of the USB stick.\n"
)


class App:
    def __init__(self, root):
        self.root = root
        self.queue = queue.Queue()
        self.worker = None
        root.title("x360resign %s - WWE '13 save resigner" % __version__)
        root.minsize(760, 560)

        cfg = load_config()
        self.vars = {name: tk.StringVar(value=cfg.get(name, "")) for name in
                     ("input", "output", "profile_id", "console_id", "device_id", "kv", "cpu_key")}
        self.sign_var = tk.BooleanVar(value=True)
        self.patch_var = tk.BooleanVar(value=False)
        self.flat_var = tk.BooleanVar(value=False)

        frame = ttk.Frame(root, padding=10)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        row = 0

        def label(text, r, **kw):
            ttk.Label(frame, text=text).grid(row=r, column=0, sticky="w", pady=2, **kw)

        def entry(name, r, width=60):
            e = ttk.Entry(frame, textvariable=self.vars[name], width=width)
            e.grid(row=r, column=1, sticky="we", pady=2)
            return e

        ttk.Label(frame, text="Files", font=("TkDefaultFont", 10, "bold")).grid(row=row, column=0, sticky="w")
        row += 1
        label("Mod files folder", row)
        entry("input", row)
        ttk.Button(frame, text="Browse...", command=self.pick_input).grid(row=row, column=2, padx=4)
        row += 1
        label("Output folder", row)
        entry("output", row)
        ttk.Button(frame, text="Browse...", command=self.pick_output).grid(row=row, column=2, padx=4)
        row += 1

        ttk.Separator(frame).grid(row=row, column=0, columnspan=3, sticky="we", pady=6)
        row += 1
        ttk.Label(frame, text="Your IDs", font=("TkDefaultFont", 10, "bold")).grid(row=row, column=0, sticky="w")
        ttk.Button(frame, text="Read IDs from one of my saves...", command=self.read_ids).grid(
            row=row, column=1, sticky="w")
        row += 1
        label("Profile ID (16 hex)", row)
        entry("profile_id", row)
        row += 1
        label("Console ID (10 hex)", row)
        entry("console_id", row)
        ttk.Label(frame, text="blank = use KV's").grid(row=row, column=2, sticky="w")
        row += 1
        label("Device ID (40 hex)", row)
        entry("device_id", row)
        ttk.Label(frame, text="optional").grid(row=row, column=2, sticky="w")
        row += 1

        ttk.Separator(frame).grid(row=row, column=0, columnspan=3, sticky="we", pady=6)
        row += 1
        ttk.Label(frame, text="Signing", font=("TkDefaultFont", 10, "bold")).grid(row=row, column=0, sticky="w")
        row += 1
        label("KeyVault (KV.bin)", row)
        entry("kv", row)
        ttk.Button(frame, text="Browse...", command=self.pick_kv).grid(row=row, column=2, padx=4)
        row += 1
        label("CPU key (encrypted KV only)", row)
        entry("cpu_key", row)
        ttk.Button(frame, text="Check KV", command=self.check_kv).grid(row=row, column=2, padx=4)
        row += 1

        opts = ttk.Frame(frame)
        opts.grid(row=row, column=0, columnspan=3, sticky="w", pady=4)
        ttk.Checkbutton(opts, text="Sign with KV", variable=self.sign_var).pack(side="left", padx=4)
        ttk.Checkbutton(opts, text="Also patch old IDs inside the save data",
                        variable=self.patch_var).pack(side="left", padx=4)
        ttk.Checkbutton(opts, text="Flat output (no Content folders)",
                        variable=self.flat_var).pack(side="left", padx=4)
        row += 1

        buttons = ttk.Frame(frame)
        buttons.grid(row=row, column=0, columnspan=3, sticky="we", pady=6)
        self.check_button = ttk.Button(buttons, text="Check mod files", command=self.check_files)
        self.check_button.pack(side="left", padx=4)
        self.run_button = ttk.Button(buttons, text="RESIGN ALL", command=self.start)
        self.run_button.pack(side="left", padx=4)
        ttk.Button(buttons, text="Open output folder", command=self.open_output).pack(side="left", padx=4)
        ttk.Button(buttons, text="Help", command=lambda: self.log(HELP_TEXT)).pack(side="right", padx=4)
        row += 1

        self.text = ScrolledText(frame, height=16, wrap="word", font=("Consolas", 9))
        self.text.grid(row=row, column=0, columnspan=3, sticky="nsew")
        frame.rowconfigure(row, weight=1)
        self.log(HELP_TEXT)
        root.after(100, self.drain)

    # ------------------------------------------------------------ helpers
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

    def get(self, name):
        return self.vars[name].get().strip()

    def pick_input(self):
        path = filedialog.askdirectory(title="Folder with the mod save files")
        if path:
            self.vars["input"].set(path)
            if not self.get("output"):
                self.vars["output"].set(os.path.join(os.path.dirname(path), "resigned_saves"))

    def pick_output(self):
        path = filedialog.askdirectory(title="Where to write the resigned saves")
        if path:
            self.vars["output"].set(path)

    def pick_kv(self):
        path = filedialog.askopenfilename(title="Your KV.bin",
                                          filetypes=[("KeyVault", "*.bin"), ("All files", "*.*")])
        if path:
            self.vars["kv"].set(path)

    def read_ids(self):
        path = filedialog.askopenfilename(title="Pick one of YOUR OWN saves (any game)")
        if not path:
            return
        try:
            profile, console, device = ids_from_package(path)
        except (StfsError, OSError) as exc:
            messagebox.showerror("Not a save", "Could not read that file:\n%s" % exc)
            return
        self.vars["profile_id"].set(fmt(profile))
        self.vars["device_id"].set(fmt(device))
        if not self.get("kv"):
            self.vars["console_id"].set(fmt(console))
        self.log("Read IDs from %s:\n  profile %s\n  console %s\n  device  %s"
                 % (path, fmt(profile), fmt(console), fmt(device)))

    def load_kv(self):
        cpu = parse_cpu_key(self.get("cpu_key")) if self.get("cpu_key") else None
        return load_keyvault_file(self.get("kv"), cpu_key=cpu)

    def check_kv(self):
        if not self.get("kv"):
            messagebox.showinfo("KV", "Pick your KV.bin first.")
            return
        try:
            kv = self.load_kv()
        except (KeyVaultError, OSError) as exc:
            messagebox.showerror("KV problem", str(exc))
            return
        self.log("KeyVault OK:\n" + kv.describe())

    def check_files(self):
        if not os.path.isdir(self.get("input")):
            messagebox.showerror("Missing", "Pick the mod files folder first.")
            return
        folder = self.get("input")
        self.run_in_thread(lambda: self._check_files_worker(folder))

    def _check_files_worker(self, folder):
        # Runs on a worker thread: only touch the queue, never Tk widgets/variables.
        items, skipped = collect_inputs([folder])
        for message in skipped:
            self.log("note: " + message)
        for item in items:
            try:
                pkg = StfsPackage(item.loader(), name=item.filename)
                self.log("%-24s %-28s %s\n    title %08X  profile %s  %s"
                         % (item.filename, pkg.display_name[:28], pkg.magic.decode(),
                            pkg.title_id, fmt(pkg.profile_id), pkg.verify().summary()))
            except (StfsError, OSError) as exc:
                self.log("%s: %s" % (item.source, exc))
        self.log("%d package(s) found.\n" % len(items))

    def build_options(self):
        if not os.path.isdir(self.get("input")):
            raise IdError("Pick the mod files folder first.")
        if not self.get("output"):
            raise IdError("Pick an output folder.")
        kv = None
        if self.sign_var.get():
            if not self.get("kv"):
                raise IdError("'Sign with KV' is ticked but no KV.bin is selected.\n\nPick your "
                              "KV.bin, or untick 'Sign with KV' to only fix IDs and hashes "
                              "(retail consoles will reject unsigned saves).")
            kv = self.load_kv()
        if not self.get("profile_id"):
            raise IdError("Enter your Profile ID (or use 'Read IDs from one of my saves').")
        return Options(
            profile_id=parse_profile_id(self.get("profile_id")),
            console_id=parse_console_id(self.get("console_id")) if self.get("console_id") else None,
            device_id=parse_device_id(self.get("device_id")) if self.get("device_id") else None,
            keyvault=kv,
            patch_embedded=self.patch_var.get(),
            layout="flat" if self.flat_var.get() else "usb",
        )

    def start(self):
        try:
            options = self.build_options()
        except (IdError, KeyVaultError, OSError) as exc:
            messagebox.showerror("Can't start yet", str(exc))
            return
        try:
            save_config({name: self.get(name) for name in self.vars})
        except OSError:
            pass
        folder, output = self.get("input"), self.get("output")
        self.run_in_thread(lambda: self._resign_worker(options, folder, output))

    def _resign_worker(self, options, folder, output):
        # Runs on a worker thread: only touch the queue, never Tk widgets/variables.
        self.log("=" * 70)
        summary = run_batch([folder], output, options, log=self.log)
        self.log("")
        self.log("DONE: %d resigned, %d rehashed (not signed), %d copied, %d skipped, %d failed"
                 % (summary.count("RESIGNED"), summary.count("REHASHED, NOT SIGNED"),
                    summary.count("COPIED UNCHANGED"), summary.count("SKIPPED"), summary.failed))
        if summary.report_path:
            self.log("Report: %s" % summary.report_path)
            self.log("Copy the 'Content' folder inside the output folder to the root of your "
                     "FAT32 USB stick.")

    def run_in_thread(self, func):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Busy", "Still working - wait for it to finish.")
            return

        def wrapper():
            try:
                func()
            except Exception as exc:  # keep the window alive and show the problem
                self.log("ERROR: %s" % exc)
        self.worker = threading.Thread(target=wrapper, daemon=True)
        self.worker.start()

    def open_output(self):
        path = self.get("output")
        if not path or not os.path.isdir(path):
            messagebox.showinfo("Output", "The output folder does not exist yet.")
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
