#!/usr/bin/env python3
"""Tkinter GUI for scheduling and uploading videos to YouTube."""

import os
import threading
import tkinter as tk
from datetime import datetime, timezone
from tkinter import filedialog, messagebox, ttk

from upload_video import (
    get_credentials,
    parse_publish_at,
    publish_video,
    PRIVACY_CHOICES,
    CATEGORY_DEFAULTS,
)
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


VIDEO_FILETYPES = [
    ("Video files", "*.mp4 *.mov *.avi *.mkv *.wmv *.flv *.webm"),
    ("All files", "*.*"),
]


def utc_to_local_display(iso_str):
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00")).astimezone()
    return dt.strftime("%b %d, %Y %H:%M %Z")


def local_input_to_utc(date_str, time_str):
    raw = f"{date_str.strip()} {time_str.strip()}"
    dt = datetime.strptime(raw, "%Y-%m-%d %H:%M")
    dt = dt.astimezone()
    dt_utc = dt.astimezone(timezone.utc)
    if dt_utc <= datetime.now(timezone.utc):
        raise ValueError("Publish time must be in the future")
    return dt_utc.isoformat(timespec="seconds").replace("+00:00", "Z")


class EditDialog(tk.Toplevel):
    VISIBILITY_CHOICES = ["Public", "Unlisted", "Private", "Scheduled"]

    def __init__(
        self,
        parent,
        video_path,
        current_title,
        current_publish,
        current_description="",
        current_privacy="private",
        current_kids=False,
    ):
        super().__init__(parent)
        self.title("Edit Video")
        self.resizable(False, False)
        self.result = None
        self.transient(parent)
        self.after(10, self._grab_safely)

        padx, pady = 16, 6

        ttk.Label(self, text="Edit Video", style="Header.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", padx=padx, pady=(16, 4)
        )
        path_label = ttk.Label(self, text=os.path.basename(video_path), foreground="gray")
        path_label.grid(row=1, column=0, columnspan=3, sticky="w", padx=padx, pady=(0, 8))

        ttk.Label(self, text="Title:").grid(row=2, column=0, sticky="e", padx=(padx, 4), pady=4)
        self.title_var = tk.StringVar(value=current_title)
        ttk.Entry(self, textvariable=self.title_var, width=45).grid(
            row=2, column=1, sticky="w", padx=(4, padx), pady=4
        )

        ttk.Label(self, text="Description:").grid(row=3, column=0, sticky="ne", padx=(padx, 4), pady=4)
        self.desc_text = tk.Text(self, width=45, height=5, wrap="word")
        self.desc_text.grid(row=3, column=1, sticky="w", padx=(4, 0), pady=4)
        self.desc_text.insert("1.0", current_description or "")

        self.desc_count = tk.StringVar()
        ttk.Label(self, textvariable=self.desc_count, foreground="gray").grid(
            row=3, column=2, sticky="sw", padx=4, pady=4
        )
        self.desc_text.bind("<KeyRelease>", self._desc_changed)
        self._desc_changed()

        self.visibility_var = tk.StringVar(
            value="Scheduled" if current_publish else current_privacy.title()
        )
        self.kids_var = tk.BooleanVar(value=current_kids)

        vis_row = ttk.Frame(self)
        vis_row.grid(row=4, column=0, columnspan=3, sticky="w", padx=padx, pady=(8, 0))
        ttk.Label(vis_row, text="Visibility:").pack(side="left")
        self.visibility_combo = ttk.Combobox(
            vis_row,
            textvariable=self.visibility_var,
            values=self.VISIBILITY_CHOICES,
            width=12,
            state="readonly",
        )
        self.visibility_combo.pack(side="left", padx=(4, 18))
        self.visibility_combo.bind("<<ComboboxSelected>>", self._toggle_schedule)
        ttk.Checkbutton(
            vis_row, text="Made for kids", variable=self.kids_var
        ).pack(side="left")

        from tkcalendar import Calendar

        if current_publish:
            dt = datetime.fromisoformat(current_publish.replace("Z", "+00:00")).astimezone()
            default_date = dt.date()
            default_hh12 = (dt.hour % 12) or 12
            default_hh = str(default_hh12)
            default_mm = dt.strftime("%M")
            default_period = "AM" if dt.hour < 12 else "PM"
        else:
            default_date = datetime.now().date()
            default_hh = "09"
            default_mm = "00"
            default_period = "AM"

        self.sched_frame = ttk.Frame(self)
        self.sched_frame.grid(row=5, column=0, columnspan=3, sticky="w", padx=padx, pady=(8, 0))

        self.calendar = Calendar(
            self.sched_frame,
            selectmode="day",
            firstweekday="monday",
            showweeknumbers=False,
            background="darkblue",
            foreground="white",
            fieldbackground="white",
            mindate=datetime.now().date(),
            year=default_date.year,
            month=default_date.month,
            day=default_date.day,
        )
        self.calendar.pack(side="left")

        time_frame = ttk.Frame(self.sched_frame)
        time_frame.pack(side="left", padx=(14, 0))

        self.hour_var = tk.StringVar(value=default_hh)
        self.minute_var = tk.StringVar(value=default_mm)
        self.period_var = tk.StringVar(value=default_period)
        ttk.Label(time_frame, text="Time:").pack(anchor="w")
        ttk.Label(time_frame, text="").pack()
        time_row = ttk.Frame(time_frame)
        time_row.pack(anchor="w")
        self.hour_combo = ttk.Combobox(
            time_row, textvariable=self.hour_var, values=[f"{h}" for h in range(1, 13)], width=4, state="readonly"
        )
        self.hour_combo.pack(side="left")
        ttk.Label(time_row, text=":").pack(side="left", padx=2)
        self.minute_combo = ttk.Combobox(
            time_row, textvariable=self.minute_var, values=[f"{m:02d}" for m in range(60)], width=4, state="readonly"
        )
        self.minute_combo.pack(side="left")
        self.period_combo = ttk.Combobox(
            time_row, textvariable=self.period_var, values=["AM", "PM"], width=4, state="readonly"
        )
        self.period_combo.pack(side="left", padx=(4, 0))

        self.sel_date_var = tk.StringVar(
            value=f"Selected: {default_date.strftime('%b %d, %Y')}"
        )
        self.sel_label = ttk.Label(self, textvariable=self.sel_date_var, foreground="gray")
        self.sel_label.grid(row=6, column=1, sticky="w", padx=(4, 0), pady=(4, 0))
        self.calendar.bind("<<CalendarSelected>>", self._calendar_changed)

        self.sched_note = ttk.Label(
            self, text="Choose a date and time.", foreground="gray"
        )
        self.sched_note.grid(row=7, column=0, columnspan=3, padx=padx, pady=(8, 0))

        btn_frame = ttk.Frame(self)
        btn_frame.grid(row=8, column=0, columnspan=3, pady=14)
        ttk.Button(btn_frame, text="Save", command=self._save).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(side="left", padx=4)

        self._toggle_schedule()
        self._center_on_parent()
        self.bind("<Escape>", lambda e: self.destroy())

    def _grab_safely(self, attempts=10):
        try:
            self.grab_set()
        except tk.TclError:
            if attempts:
                self.after(50, lambda: self._grab_safely(attempts - 1))

    def _toggle_schedule(self, event=None):
        show = self.visibility_var.get() == "Scheduled"
        for widget in (self.sched_frame, self.sel_label, self.sched_note):
            if show:
                widget.grid()
            else:
                widget.grid_remove()

    def _center_on_parent(self):
        self.update_idletasks()
        parent = self.master
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_reqwidth()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_reqheight()) // 2
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def _calendar_changed(self, event=None):
        date = self.calendar.selection_get()
        if date:
            self.sel_date_var.set(f"Selected: {date.strftime('%b %d, %Y')}")

    def _desc_changed(self, event=None):
        count = len(self.desc_text.get("1.0", "end-1c"))
        self.desc_count.set(f"{count}/5000" + (" (too long)" if count > 5000 else ""))

    def _save(self):
        title = self.title_var.get().strip()
        if not title:
            messagebox.showwarning("Missing title", "Title cannot be empty", parent=self)
            return
        description = self.desc_text.get("1.0", "end-1c").strip()
        if len(description) > 5000:
            messagebox.showerror(
                "Description too long",
                f"Description is {len(description)} characters. YouTube allows up to 5000.",
                parent=self,
            )
            return
        visibility = self.visibility_var.get()
        publish = None
        privacy = visibility.lower()
        if visibility == "Scheduled":
            privacy = "private"
            sel = self.calendar.selection_get()
            if sel is None:
                messagebox.showerror("No date selected", "Click a day on the calendar.", parent=self)
                return
            date = sel.strftime("%Y-%m-%d")
            hour = int(self.hour_var.get())
            if self.period_var.get() == "PM" and hour != 12:
                hour += 12
            elif self.period_var.get() == "AM" and hour == 12:
                hour = 0
            time_ = f"{hour:02d}:{self.minute_var.get()}"
            try:
                publish = local_input_to_utc(date, time_)
            except ValueError as exc:
                messagebox.showerror("Invalid date/time", str(exc), parent=self)
                return
        self.result = {
            "title": title,
            "description": description,
            "visibility": visibility,
            "privacy": privacy,
            "publish_at": publish,
            "kids": bool(self.kids_var.get()),
        }
        self.destroy()


class UploaderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("YouTube Uploader")
        self.root.geometry("820x520")
        self.root.minsize(700, 400)
        self.api = None
        self.meta = {}

        here = os.path.dirname(os.path.abspath(__file__))
        self.client_secret = os.path.join(here, "client_secret.json")
        self.token = os.path.join(here, "token.json")

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._quit)
        threading.Thread(target=self._auth_worker, daemon=True).start()

    def _build_ui(self):
        style = ttk.Style()
        style.configure("Treeview", rowheight=28, font=("Sans", 10))
        style.configure("Treeview.Heading", font=("Sans", 10, "bold"))
        style.configure("Header.TLabel", font=("Sans", 13, "bold"))
        style.configure("Sub.TLabel", font=("Sans", 9), foreground="gray")
        style.configure("Accent.TButton", padding=8)

        header = ttk.Frame(self.root, padding=(16, 16, 16, 8))
        header.pack(side="top", fill="x")
        ttk.Label(header, text="YouTube Uploader", style="Header.TLabel").pack(side="left")
        ttk.Label(header, text="Select videos, set visibility, upload.", style="Sub.TLabel").pack(side="left", padx=(12, 0))

        ttk.Separator(self.root, orient="horizontal").pack(side="top", fill="x", padx=8)

        toolbar = ttk.Frame(self.root, padding=(8, 8))
        toolbar.pack(side="top", fill="x")

        ttk.Button(toolbar, text="Add videos...", command=self._add_videos).pack(side="left", padx=2, pady=2)
        ttk.Button(toolbar, text="Remove", command=self._remove_selected).pack(side="left", padx=2, pady=2)
        ttk.Button(toolbar, text="Edit schedule...", command=self._edit_selected).pack(side="left", padx=2, pady=2)
        ttk.Button(toolbar, text="Upload all", style="Accent.TButton", command=self._upload_all).pack(side="left", padx=(12, 2), pady=2)
        ttk.Button(toolbar, text="Re-authenticate", command=self._re_auth).pack(side="right", padx=2, pady=2)

        tree_frame = ttk.Frame(self.root)
        tree_frame.pack(side="top", fill="both", expand=True, padx=8, pady=(0, 4))

        columns = ("video", "title", "schedule", "status")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="extended")
        self.tree.heading("video", text="File")
        self.tree.heading("title", text="Title")
        self.tree.heading("schedule", text="Visibility / Schedule")
        self.tree.heading("status", text="Status")
        self.tree.column("video", width=180)
        self.tree.column("title", width=240)
        self.tree.column("schedule", width=200)
        self.tree.column("status", width=180)
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.tree.bind("<Double-1>", lambda e: self._edit_selected())

        self.status_var = tk.StringVar(value="Authenticating...")
        ttk.Label(
            self.root, textvariable=self.status_var, anchor="w", padding=(10, 6)
        ).pack(side="bottom", fill="x")

    def _add_videos(self):
        files = filedialog.askopenfilenames(
            title="Select videos", filetypes=VIDEO_FILETYPES, parent=self.root
        )
        for f in files:
            title = os.path.splitext(os.path.basename(f))[0]
            iid = self.tree.insert("", "end", values=(f, title, "Private", "Queued"))
            self.meta[iid] = {
                "title": title,
                "description": "",
                "privacy": "private",
                "publish_at": None,
                "kids": False,
            }

    def _remove_selected(self):
        for item in self.tree.selection():
            self.meta.pop(item, None)
            self.tree.delete(item)

    def _edit_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        for item in sel:
            vid, title, sched, _ = self.tree.item(item, "values")
            info = self.meta.get(item, {})
            dlg = EditDialog(
                self.root,
                vid,
                title,
                info.get("publish_at"),
                info.get("description", ""),
                info.get("privacy", "private"),
                info.get("kids", False),
            )
            self.root.wait_window(dlg)
            if dlg.result:
                self.meta[item] = {
                    "title": dlg.result["title"],
                    "description": dlg.result["description"],
                    "privacy": dlg.result["privacy"],
                    "publish_at": dlg.result["publish_at"],
                    "kids": dlg.result["kids"],
                }
                new_sched = dlg.result["publish_at"]
                if new_sched:
                    display = f"Scheduled {utc_to_local_display(new_sched)}"
                else:
                    display = dlg.result["visibility"]
                self.tree.item(item, values=(vid, dlg.result["title"], display, "Queued"))

    def _upload_all(self):
        if self.api is None:
            messagebox.showwarning("Not connected", "Authentication not complete. Check the status bar.")
            return
        items = list(self.tree.get_children())
        if not items:
            messagebox.showinfo("Nothing to upload", "Add videos first.")
            return

        jobs = []
        for iid in items:
            vid = self.tree.item(iid, "values")[0]
            jobs.append((iid, vid, dict(self.meta.get(iid, {}))))

        self._set_buttons(enabled=False)
        threading.Thread(target=self._upload_worker, args=(jobs,), daemon=True).start()

    def _upload_worker(self, jobs):
        for iid, vid, info in jobs:
            title = info.get("title", os.path.basename(vid))
            description = info.get("description", "")
            privacy = info.get("privacy", "private")
            kids = info.get("kids", False)
            publish_at = info.get("publish_at")
            self._update_status(iid, "Uploading...")
            try:
                def progress(pct, _iid=iid):
                    self._update_status(_iid, f"Uploading... {pct}%")
                publish_video(
                    self.api,
                    vid,
                    title,
                    description=description or None,
                    privacy=privacy,
                    kids=kids,
                    publish_at=publish_at,
                    progress_cb=progress,
                )
                self._update_status(iid, "Done")
            except HttpError as exc:
                detail = exc.error_details or []
                reasons = [d.get("reason") for d in detail if isinstance(d, dict)]
                self._update_status(iid, f"Failed: {', '.join(reasons) or exc.resp.status}")

        self.root.after(0, lambda: self._set_buttons(enabled=True))

    def _update_status(self, iid, text):
        def _set():
            values = list(self.tree.item(iid, "values"))
            values[3] = text
            self.tree.item(iid, values=tuple(values))
        self.root.after(0, _set)

    def _set_buttons(self, enabled=True):
        state = "normal" if enabled else "disabled"
        def _apply():
            for child in self.root.winfo_children():
                if isinstance(child, ttk.Frame):
                    for btn in child.winfo_children():
                        if isinstance(btn, ttk.Button):
                            btn.configure(state=state)
        self.root.after(0, _apply)

    def _auth_worker(self):
        try:
            creds = get_credentials(self.client_secret, self.token)
            api = build("youtube", "v3", credentials=creds)
            self.api = api
            self.root.after(0, lambda: self.status_var.set("Connected"))
        except Exception as exc:
            self.root.after(
                0, lambda e=exc: self.status_var.set(f"Auth failed: {e}")
            )

    def _re_auth(self):
        if os.path.exists(self.token):
            os.remove(self.token)
        self.api = None
        self.status_var.set("Authenticating...")
        threading.Thread(target=self._auth_worker, daemon=True).start()

    def _quit(self):
        self.root.destroy()


def main():
    import sv_ttk

    root = tk.Tk()
    sv_ttk.set_theme("dark")
    UploaderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
