"""Compact Tk presentation layer, independent from window operations."""

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

BG = "#F6F7F8"
SURFACE = "#FFFFFF"
TEXT = "#20262E"
MUTED = "#69727E"
LINE = "#DEE2E6"
ACCENT = "#137C70"
TINT = "#E8F3F0"


def rounded(canvas, x1, y1, x2, y2, radius, **options):
    canvas.create_polygon(
        x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
        x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
        x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
        smooth=True, splinesteps=16, **options,
    )


class ActionButton(tk.Canvas):
    """Keyboard-accessible button with the controller's ttk state/style API."""

    def __init__(self, parent, *, app, text="", command=None, style="TButton",
                 width=None, height=34, icon=None, state="normal", **kwargs):
        self._px = app._px
        self._font = (app._ui_font, 10)
        self._text, self._style, self._icon = text, style, icon
        self._command = command
        self._disabled = state == "disabled"
        self._hover = self._pressed = self._focused = False
        self._auto_width = width is None
        measured = tkfont.Font(root=app, font=self._font).measure(text) + self._px(26)
        super().__init__(parent, width=measured if width is None else self._px(width),
                         height=self._px(height), bg=parent.cget("background"),
                         highlightthickness=0, borderwidth=0, takefocus=not self._disabled,
                         cursor="arrow" if self._disabled else "hand2", **kwargs)
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Enter>", lambda _e: self._feedback(hover=True))
        self.bind("<Leave>", lambda _e: self._feedback(hover=False, pressed=False))
        self.bind("<FocusIn>", lambda _e: self._feedback(focused=True))
        self.bind("<FocusOut>", lambda _e: self._feedback(focused=False, pressed=False))
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<KeyRelease-space>", self._keyboard_invoke)
        self.bind("<KeyRelease-Return>", self._keyboard_invoke)

    def _feedback(self, **values):
        for name, value in values.items():
            setattr(self, "_" + name, value)
        self._draw()

    def _press(self, _event):
        if not self._disabled:
            self.focus_set()
            self._feedback(pressed=True)

    def _release(self, event):
        invoke = self._pressed and 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height()
        self._feedback(pressed=False)
        if invoke:
            self.invoke()

    def _keyboard_invoke(self, _event):
        self.invoke()
        return "break"

    def invoke(self):
        if not self._disabled and self._command:
            return self._command()

    def state(self, statespec=None):
        if statespec is None:
            return ("disabled",) if self._disabled else ()
        disabled = self._disabled
        for state in statespec:
            if state in ("disabled", "!disabled"):
                disabled = state == "disabled"
        if disabled != self._disabled:
            self._disabled = disabled
            super().configure(takefocus=not disabled, cursor="arrow" if disabled else "hand2")
            self._draw()

    def instate(self, statespec):
        return all((self._disabled if state == "disabled" else not self._disabled)
                   for state in statespec if state in ("disabled", "!disabled"))

    def configure(self, cnf=None, **kwargs):
        options = dict(cnf or {})
        options.update(kwargs)
        redraw = False
        for key in ("text", "style"):
            if key in options:
                value = options.pop(key)
                if getattr(self, "_" + key) != value:
                    setattr(self, "_" + key, value)
                    redraw = True
                    if key == "text" and self._auto_width:
                        options["width"] = tkfont.Font(font=self._font).measure(value) + self._px(26)
        if options:
            super().configure(**options)
        if redraw:
            self._draw()

    config = configure

    def cget(self, key):
        if key in ("text", "style"):
            return getattr(self, "_" + key)
        return super().cget(key)

    def _draw(self):
        self.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        if width <= 1 or height <= 1:
            return
        selected = self._style.startswith("Selected")
        primary = self._style.startswith("Primary")
        quiet = self._style.startswith("Quiet")
        fill, border, foreground = SURFACE, LINE, TEXT
        if primary:
            fill, border, foreground = ACCENT, ACCENT, "white"
        elif selected:
            fill, border, foreground = TINT, ACCENT, ACCENT
        elif quiet:
            fill, border, foreground = "#E7EAEE", "#CDD2D8", TEXT
        if self._disabled:
            fill, border, foreground = "#EEF0F2", "#E7E9EC", "#A2A8B0"
        elif self._hover or self._pressed:
            fill = ("#106B60" if primary else "#DBEEE8" if selected
                    else ("#CDD4DC" if self._pressed else "#DCE1E7") if quiet else "#EBEEF0")
        if self._focused and not self._disabled:
            border = ACCENT
        inset = self._px(1)
        rounded(self, inset, inset, width - inset, height - inset, self._px(7),
                fill=fill, outline=border, width=max(1, self._px(1)))
        if self._icon:
            cx, top = width / 2, self._px(16)
            left, right, bottom = cx - self._px(16), cx + self._px(16), top + self._px(21)
            self.create_rectangle(left, top, right, bottom, outline=foreground, width=max(1, self._px(1)))
            if self._icon == "windowed_fs":
                self.create_line(left, top + self._px(5), right, top + self._px(5), fill=foreground)
            elif self._icon == "free":
                self.create_line(right - self._px(9), bottom - self._px(3), right - self._px(3), bottom - self._px(9), fill=foreground)
            else:
                self.create_rectangle(left + self._px(4), top + self._px(4), right - self._px(4), bottom - self._px(4),
                                      outline=foreground, dash=(2, 2))
            self.create_text(cx, height - self._px(18), text=self._text, font=self._font, fill=foreground)
            if selected:
                self.create_oval(width - self._px(14), self._px(9), width - self._px(9), self._px(14), fill=ACCENT, outline="")
        else:
            self.create_text(width / 2, height / 2, text=self._text, font=self._font, fill=foreground)


class Toggle(ActionButton):
    """Labelled switch; click or Space toggles the same Tk BooleanVar."""

    def __init__(self, parent, *, app, text, variable, command=None, state="normal", width=176):
        self._variable, self._on_change = variable, command
        super().__init__(parent, app=app, text=text, command=self._toggle,
                         width=width, height=30, state=state)
        self._trace = variable.trace_add("write", lambda *_args: self._draw())
        self.bind("<Destroy>", self._remove_trace, add="+")

    def _remove_trace(self, event):
        if event.widget is self:
            self._variable.trace_remove("write", self._trace)

    def _toggle(self):
        self._variable.set(not self._variable.get())
        if self._on_change:
            self._on_change()

    def _draw(self):
        self.delete("all")
        if self.winfo_width() <= 1:
            return
        center = self.winfo_height() / 2
        active = self._variable.get()
        color, foreground = (ACCENT if active else "#BAC1C8"), TEXT
        if self._disabled:
            color, foreground = "#E0E4E8", "#9BA3AD"
        self.create_line(self._px(12), center, self._px(28), center,
                         fill=color, width=self._px(19), capstyle=tk.ROUND)
        knob = self._px(28 if active else 12)
        radius = self._px(7)
        self.create_oval(knob - radius, center - radius, knob + radius, center + radius, fill="white", outline="")
        self.create_text(self._px(48), center, text=self._text, anchor=tk.W, font=self._font, fill=foreground)


class WindowUI:
    def _setup_styles(self):
        self.configure(background=BG)
        families = tkfont.families(self)
        self._ui_font = next((name for name in ("Microsoft JhengHei UI", "Microsoft JhengHei", "Segoe UI")
                              if name in families), "Segoe UI")
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
            tkfont.nametofont(name).configure(family=self._ui_font, size=10)
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", font=(self._ui_font, 10), foreground=TEXT, background=BG)
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Small.TLabel", foreground=MUTED, font=(self._ui_font, 9))
        style.configure("Section.TLabel", font=(self._ui_font, 10, "bold"))
        style.configure("Heading.TLabel", font=(self._ui_font, 15, "bold"))
        style.configure("Accent.TLabel", foreground=ACCENT)
        style.configure("TEntry", padding=(8, 6), fieldbackground=SURFACE, bordercolor=LINE,
                        lightcolor=SURFACE, darkcolor=SURFACE, insertcolor=TEXT)
        style.map("TEntry", bordercolor=[("focus", ACCENT)],
                  fieldbackground=[("disabled", "#EEF0F2")], foreground=[("disabled", "#929AA5")])
        style.configure("TCombobox", padding=(8, 6), fieldbackground=SURFACE, background=SURFACE,
                        bordercolor=LINE, lightcolor=SURFACE, darkcolor=SURFACE, arrowsize=self._px(13))
        style.map("TCombobox", fieldbackground=[("disabled", "#EEF0F2"), ("readonly", SURFACE)],
                  selectbackground=[("readonly", SURFACE)], selectforeground=[("readonly", TEXT)],
                  bordercolor=[("focus", ACCENT)])
        style.layout("Pages.TNotebook.Tab", [])
        style.configure("Pages.TNotebook", background=BG, borderwidth=0, tabmargins=0,
                        bordercolor=BG, lightcolor=BG, darkcolor=BG)
        style.layout("Slim.Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
            ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        style.configure("Slim.Vertical.TScrollbar", background="#C5CCD2", troughcolor=BG,
                        borderwidth=0, arrowsize=self._px(8), width=self._px(8))

    def _frame(self, parent, **kwargs):
        return tk.Frame(parent, background=BG, **kwargs)

    def _button(self, parent, text, command, **kwargs):
        return ActionButton(parent, app=self, text=text, command=command, **kwargs)

    def _label(self, parent, text=None, *, style="TLabel", **kwargs):
        return ttk.Label(parent, text=text, style=style, **kwargs)

    def _section(self, parent, title, hint=None):
        row = self._frame(parent)
        row.pack(fill=tk.X, pady=(0, self._px(8)))
        self._label(row, title, style="Section.TLabel").pack(side=tk.LEFT)
        if hint is not None:
            self._label(row, textvariable=hint, style="Small.TLabel").pack(side=tk.RIGHT)
        return row

    def _divider(self, parent):
        tk.Frame(parent, bg=LINE, height=1).pack(fill=tk.X, pady=self._px(14))

    def _new_page(self, title):
        page = self._frame(self._notebook)
        self._notebook.add(page, text=title)
        canvas = tk.Canvas(page, background=BG, highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(page, orient=tk.VERTICAL, style="Slim.Vertical.TScrollbar", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        body = self._frame(canvas, padx=self._px(24), pady=self._px(4))
        window = canvas.create_window((0, 0), window=body, anchor=tk.NW)
        self._pages[str(page)] = (canvas, body, scrollbar, window)
        body.bind("<Configure>", lambda _e: self._layout_page(page))
        canvas.bind("<Configure>", lambda _e: self._layout_page(page))
        return page, body

    def _layout_page(self, page):
        canvas, body, scrollbar, window = self._pages[str(page)]
        canvas.itemconfigure(window, width=canvas.winfo_width())
        canvas.configure(scrollregion=canvas.bbox("all"))
        if body.winfo_reqheight() > canvas.winfo_height() + 1:
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        else:
            scrollbar.pack_forget()
            canvas.yview_moveto(0)
        for label in getattr(self, "_wrap_labels", []):
            if label.master is body:
                label.configure(wraplength=max(self._px(120), canvas.winfo_width() - self._px(52)))

    def _on_page_changed(self, _event=None):
        selected = self._notebook.select()
        if selected not in self._pages:
            return
        self._body_canvas, self._body, self._body_scrollbar, self._body_window = self._pages[selected]
        main = selected == str(self._adjust_page)
        self._page_title.set("視窗調整" if main else "已儲存設定" if selected == str(self._profiles_page) else "程式設定")
        if main:
            self._back_button.pack_forget()
            self._restore_button.pack(side=tk.LEFT)
            self._save_button.pack(side=tk.RIGHT)
            self._action_bar.pack(fill=tk.X, before=self._status_label, pady=(0, self._px(8)))
        else:
            self._back_button.pack(side=tk.LEFT, before=self._heading, padx=(0, self._px(8)))
            self._action_bar.pack_forget()
        self._profiles_nav.configure(style="Selected.TButton" if selected == str(self._profiles_page) else "Quiet.TButton")
        self._settings_nav.configure(style="Selected.TButton" if selected == str(self._settings_page) else "Quiet.TButton")
        self._layout_page(selected)

    def _scroll_body(self, event):
        if isinstance(event.widget, (ttk.Combobox, ttk.Entry)):
            return
        if self._body.winfo_reqheight() > self._body_canvas.winfo_height():
            self._body_canvas.yview_scroll(-int(event.delta / 120), "units")
            return "break"

    def _build_ui(self):
        self._setup_styles()
        self._wrap_labels = []
        header = self._frame(self, padx=self._px(24), pady=self._px(14))
        header.pack(fill=tk.X)
        self._back_button = self._button(header, "‹", lambda: self._notebook.select(self._adjust_page), width=28, style="Quiet.TButton")
        self._page_title = tk.StringVar(value="視窗調整")
        self._heading = self._label(header, textvariable=self._page_title, style="Heading.TLabel")
        self._heading.pack(side=tk.LEFT)
        self._settings_nav = self._button(header, "程式設定", lambda: self._notebook.select(self._settings_page), style="Quiet.TButton")
        self._settings_nav.pack(side=tk.RIGHT)
        self._profiles_nav = self._button(header, "已儲存設定", lambda: self._notebook.select(self._profiles_page), style="Quiet.TButton")
        self._profiles_nav.pack(side=tk.RIGHT, padx=(0, self._px(4)))

        footer = self._frame(self, padx=self._px(24), pady=self._px(12))
        footer.pack(side=tk.BOTTOM, fill=tk.X)
        self._action_bar = self._frame(footer)
        self._action_bar.pack(fill=tk.X, pady=(0, self._px(8)))
        self._restore_button = self._button(self._action_bar, "還原原狀", self.on_restore, width=112, height=38)
        self._restore_button.pack(side=tk.LEFT)
        self._save_button = self._button(self._action_bar, "儲存目前設定", self.on_save_profile, width=156, height=38, style="Primary.TButton")
        self._save_button.pack(side=tk.RIGHT)
        self.status_var = tk.StringVar(value="選取視窗後即可開始調整。")
        self._status_label = self._label(footer, textvariable=self.status_var, style="Small.TLabel",
                                       wraplength=self._px(592), justify=tk.LEFT)
        self._status_label.pack(fill=tk.X)
        footer.bind("<Configure>", lambda event: self._status_label.configure(wraplength=max(120, event.width - self._px(48))))
        self._notebook = ttk.Notebook(self, style="Pages.TNotebook", takefocus=False)
        self._notebook.pack(fill=tk.BOTH, expand=True)
        self._pages = {}
        self._adjust_page, adjust = self._new_page("調整視窗")
        self._profiles_page, profiles = self._new_page("已儲存設定")
        self._settings_page, settings = self._new_page("程式設定")
        self._build_adjust_page(adjust)
        self._build_profiles_page(profiles)
        self._build_settings_page(settings)
        self._notebook.bind("<<NotebookTabChanged>>", self._on_page_changed)
        self._notebook.select(self._adjust_page)
        self._on_page_changed()
        self.bind("<MouseWheel>", self._scroll_body, add="+")
        self.bind("<F5>", lambda _e: self.refresh_windows())
        self.bind("<Control-s>", lambda _e: self.on_save_profile())
        self.bind("<Escape>", lambda _e: self._notebook.select(self._adjust_page))

    def _build_adjust_page(self, parent):
        self._target_count = tk.StringVar(value="偵測中")
        title = self._section(parent, "目標視窗")
        self.show_all = tk.BooleanVar(value=False)
        Toggle(title, app=self, text="顯示所有視窗", variable=self.show_all, command=self.refresh_windows, width=160).pack(side=tk.RIGHT)
        row = self._frame(parent)
        row.pack(fill=tk.X)
        self.win_var = tk.StringVar()
        self.win_combo = ttk.Combobox(row, textvariable=self.win_var, state="readonly", height=12)
        self.win_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.win_combo.bind("<<ComboboxSelected>>", lambda _e: self.on_target_changed())
        self._button(row, "重新整理", self.refresh_windows, width=88).pack(side=tk.RIGHT, padx=(self._px(8), 0))
        self.info_var = tk.StringVar(value="先開啟遊戲，再從清單選取視窗。")
        info = self._label(parent, textvariable=self.info_var, style="Small.TLabel", wraplength=self._px(572))
        info.pack(anchor=tk.W, pady=(self._px(7), 0))
        self._wrap_labels.append(info)
        self._divider(parent)

        self.keep_ratio = tk.BooleanVar(value=False)
        self.cover_taskbar = tk.BooleanVar(value=False)
        self.watch_var = tk.BooleanVar(value=False)
        self.mode_var = tk.StringVar(value="尚未套用")
        self._section(parent, "顯示模式", self.mode_var)
        modes = self._frame(parent)
        modes.pack(fill=tk.X)
        self._mode_buttons = {}
        for column, (mode, label, command) in enumerate((
            ("borderless_fs", "無邊框全螢幕", self.on_borderless_fullscreen),
            ("windowed_fs", "有邊框全螢幕", self.on_windowed_fullscreen),
            ("free", "自由縮放", self.on_enable_resize),
        )):
            modes.columnconfigure(column, weight=1, uniform="modes")
            button = self._button(modes, label, command, width=160, height=78, icon=mode, style="Mode.TButton")
            button.grid(row=0, column=column, sticky=tk.EW, padx=(0 if column == 0 else self._px(8), 0))
            self._mode_buttons[mode] = button
        self._divider(parent)

        self._section(parent, "畫面尺寸")
        row = self._frame(parent)
        row.pack(fill=tk.X)
        self.width_var, self.height_var = tk.StringVar(), tk.StringVar()
        self._label(row, "寬", style="Muted.TLabel").pack(side=tk.LEFT, padx=(0, self._px(6)))
        self._width_entry = ttk.Entry(row, textvariable=self.width_var, width=7, justify=tk.CENTER)
        self._width_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._label(row, "高", style="Muted.TLabel").pack(side=tk.LEFT, padx=self._px(8))
        self._height_entry = ttk.Entry(row, textvariable=self.height_var, width=7, justify=tk.CENTER)
        self._height_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._size_apply_button = self._button(row, "套用尺寸", self.on_apply_size, width=88)
        self._size_apply_button.pack(side=tk.RIGHT)
        self._size_presets = ttk.Combobox(row, state="readonly", width=14,
                                        values=("1024 × 768", "1280 × 720", "1600 × 900", "1920 × 1080"))
        self._size_presets.set("常用尺寸")
        self._size_presets.pack(side=tk.RIGHT, padx=self._px(8))
        self._size_presets.bind("<<ComboboxSelected>>", lambda _e: self.on_size_preset())
        self._setting_size_fields = False
        self._size_dirty = False
        self._size_target_key = None
        for variable in (self.width_var, self.height_var):
            variable.trace_add("write", self._on_size_edit)
        for entry in (self._width_entry, self._height_entry):
            entry.bind("<Return>", lambda _e: self.on_apply_size())
        self._label(parent, "單位 px · 套用後切換為自由縮放；鎖定比例時以寬度為準。", style="Small.TLabel").pack(
            anchor=tk.W, pady=(self._px(7), 0))
        self._divider(parent)

        options = self._frame(parent)
        options.pack(fill=tk.X)
        for column, (text, variable, command, hint, key) in enumerate((
            ("保持原始比例", self.keep_ratio, self.on_ratio_toggle, "避免畫面拉伸變形", "ratio"),
            ("蓋住工作列", self.cover_taskbar, self.on_cover_toggle, "無邊框全螢幕可用", "cover"),
            ("自動維持", self.watch_var, self.on_watch_toggle, "遊戲重設時自動恢復", "watch"),
        )):
            options.columnconfigure(column, weight=1, uniform="options")
            cell = self._frame(options)
            cell.grid(row=0, column=column, sticky=tk.EW, padx=(0 if column == 0 else self._px(8), 0))
            control = Toggle(cell, app=self, text=text, variable=variable, command=command, width=164)
            control.pack(fill=tk.X)
            label = self._label(cell, hint, style="Small.TLabel", wraplength=self._px(164))
            label.pack(anchor=tk.W, pady=(self._px(2), 0))
            if key == "cover":
                self._cover_check, self._cover_hint = control, label
            elif key == "ratio":
                self._ratio_check = control
            else:
                self._watch_check = control
        self._divider(parent)
        self._save_hint_var = tk.StringVar(value="儲存後，下次開啟遊戲就能自動套用。")
        hint = self._label(parent, textvariable=self._save_hint_var, style="Accent.TLabel", wraplength=self._px(580))
        hint.pack(anchor=tk.W, pady=(0, self._px(2)))
        self._wrap_labels.append(hint)

    def _build_profiles_page(self, parent):
        self._section(parent, "選擇設定")
        self.profile_combo = ttk.Combobox(parent, state="readonly")
        self.profile_combo.pack(fill=tk.X)
        self.profile_combo.bind("<<ComboboxSelected>>", lambda _e: self._show_profile())
        self._divider(parent)
        self._section(parent, "設定內容")
        self.profile_info = tk.StringVar()
        details = self._label(parent, textvariable=self.profile_info, wraplength=self._px(580), justify=tk.LEFT)
        details.pack(fill=tk.X)
        self._wrap_labels.append(details)
        self.auto_profile_var = tk.BooleanVar(value=False)
        self._auto_check = Toggle(parent, app=self, text="遊戲開啟時自動套用", variable=self.auto_profile_var,
                                  command=self.on_profile_auto_toggle, width=280)
        self._auto_check.pack(anchor=tk.W, pady=(self._px(12), self._px(4)))
        self._profile_target_hint = tk.StringVar()
        hint = self._label(parent, textvariable=self._profile_target_hint, style="Small.TLabel", wraplength=self._px(580))
        hint.pack(anchor=tk.W)
        self._wrap_labels.append(hint)
        self._apply_profile_button = self._button(parent, "立即套用設定", self.on_apply_profile, style="Primary.TButton", height=36)
        self._apply_profile_button.pack(anchor=tk.W, pady=(self._px(12), 0))
        self._divider(parent)
        self._section(parent, "設定名稱")
        row = self._frame(parent)
        row.pack(fill=tk.X)
        self._profile_name_var = tk.StringVar()
        ttk.Entry(row, textvariable=self._profile_name_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._rename_profile_button = self._button(row, "重新命名", self.on_rename_profile)
        self._rename_profile_button.pack(side=tk.RIGHT, padx=(self._px(8), 0))
        row = self._frame(parent)
        row.pack(fill=tk.X, pady=(self._px(16), 0))
        self._delete_profile_button = self._button(row, "刪除設定", self.on_delete_profile)
        self._delete_profile_button.pack(side=tk.LEFT)
        self._undo_profile_button = self._button(row, "復原刪除", self.on_undo_delete, state="disabled")
        self._undo_profile_button.pack(side=tk.LEFT, padx=self._px(8))
        self._label(parent, "MWT 執行中或縮到系統匣時，才會自動套用設定。", style="Small.TLabel").pack(
            anchor=tk.W, pady=(self._px(16), 0))

    def _build_settings_page(self, parent):
        self._section(parent, "啟動")
        self.startup_var = tk.BooleanVar(value=False)
        self._startup_check = Toggle(parent, app=self, text="開機自動啟動", variable=self.startup_var,
                                     command=self.on_startup_toggle, state="disabled", width=260)
        self._startup_check.pack(anchor=tk.W)
        self._startup_hint = self._label(parent, "正在讀取設定…", style="Small.TLabel", wraplength=self._px(570))
        self._startup_hint.pack(anchor=tk.W, pady=(self._px(4), 0))
        self._wrap_labels.append(self._startup_hint)
        actions = self._frame(parent)
        actions.pack(fill=tk.X)
        self._startup_retry = self._button(actions, "重試", self.on_startup_retry)
        self._divider(parent)
        self._section(parent, "視窗與背景執行")
        self.minimize_to_tray = tk.BooleanVar(value=True)
        Toggle(parent, app=self, text="關閉時縮到系統匣", variable=self.minimize_to_tray,
               command=self.on_tray_toggle, width=280).pack(anchor=tk.W)
        self._label(parent, "背景繼續維持遊戲設定；從系統匣選單可完全結束。", style="Small.TLabel").pack(anchor=tk.W, pady=(self._px(4), self._px(10)))
        self.pin_window = tk.BooleanVar(value=True)
        Toggle(parent, app=self, text="工具視窗保持置頂", variable=self.pin_window,
               command=self.on_pin_toggle, width=280).pack(anchor=tk.W)
        self._label(parent, "方便一邊看遊戲、一邊調整。", style="Small.TLabel").pack(anchor=tk.W, pady=(self._px(4), self._px(12)))
        self._button(parent, "立即收到系統匣", self.hide_to_tray).pack(anchor=tk.W)
        self._divider(parent)
        self._section(parent, f"MWT  {self.app_version}")
        self._label(parent, "遊戲視窗調整工具", style="Muted.TLabel").pack(anchor=tk.W)
        self._label(parent, "F5  重新整理     Ctrl+S  儲存     Esc  返回調整", style="Small.TLabel").pack(anchor=tk.W, pady=(self._px(8), 0))
        self._label(parent, "調整視窗外框，不改變遊戲內部渲染解析度。", style="Small.TLabel").pack(anchor=tk.W, pady=(self._px(4), 0))
        self._button(parent, "結束 MWT", self.quit_app).pack(anchor=tk.W, pady=(self._px(16), 0))

