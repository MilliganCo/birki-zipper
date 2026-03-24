from __future__ import annotations

import os
import queue
import threading
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText


DEFAULT_ROOT = r"C:\example\labels"
INSTRUCTION_NAME = "инструкция"


@dataclass
class Candidate:
    folder: Path
    source_zip_name: str = ""
    target_zip_name: str = ""
    note: str = ""
    enabled: bool = True
    item_id: str = field(default="", repr=False)


class BirkiZipperApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Архивация папок 'Бирки'")
        self.geometry("1280x760")
        self.minsize(1020, 650)

        self._init_theme()

        self.queue: queue.Queue = queue.Queue()
        self.candidates: list[Candidate] = []
        self.worker_thread: threading.Thread | None = None
        self.busy = False

        self.root_var = tk.StringVar(value=DEFAULT_ROOT)
        self.overwrite_var = tk.BooleanVar(value=True)
        self.only_ready_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Готово")

        self._build_ui()
        self.after(100, self._process_queue)

    def _init_theme(self) -> None:
        style = ttk.Style(self)

        # Базовая тёмная палитра
        bg = "#202124"
        bg_alt = "#2b2d31"
        bg_hover = "#3a3d45"
        fg = "#e8eaed"
        fg_muted = "#b0b3b8"
        accent = "#8ab4f8"
        border = "#3c4043"

        # Цвет фона главного окна
        self.configure(bg=bg)

        # Используем тему 'clam' как наиболее предсказуемую для кастомизации
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", background=bg, foreground=fg, fieldbackground=bg_alt)

        # Кнопки
        style.configure(
            "TButton",
            background=bg_alt,
            foreground=fg,
            bordercolor=border,
            focusthickness=1,
            focuscolor=accent,
            padding=6,
        )
        style.map(
            "TButton",
            background=[("active", bg_hover)],
            foreground=[("disabled", fg_muted)],
        )

        # Метки
        style.configure("TLabel", background=bg, foreground=fg)

        # Checkbutton
        style.configure("TCheckbutton", background=bg, foreground=fg)

        # Frame / Labelframe
        style.configure("TFrame", background=bg)
        style.configure("TLabelframe", background=bg, foreground=fg)
        style.configure("TLabelframe.Label", background=bg, foreground=fg)

        # Поля ввода
        style.configure(
            "TEntry",
            fieldbackground=bg_alt,
            foreground=fg,
            bordercolor=border,
            insertcolor=fg,
        )

        # Treeview
        style.configure(
            "Treeview",
            background=bg_alt,
            foreground=fg,
            fieldbackground=bg_alt,
            bordercolor=border,
            rowheight=22,
        )
        style.map(
            "Treeview",
            background=[("selected", accent)],
            foreground=[("selected", "#000000")],
        )
        style.configure(
            "Treeview.Heading",
            background=bg,
            foreground=fg,
            bordercolor=border,
        )

        # Прогресс-бар
        style.configure(
            "TProgressbar",
            background=accent,
            troughcolor=bg_alt,
            bordercolor=border,
        )

    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="Корневая папка:").grid(row=0, column=0, sticky="w")
        entry = ttk.Entry(top, textvariable=self.root_var)
        entry.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Button(top, text="Обзор", command=self._browse_root).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(top, text="Сканировать", command=self.scan).grid(row=0, column=3, padx=(0, 8))
        ttk.Button(top, text="Запустить архивацию", command=self.archive_selected).grid(row=0, column=4)
        top.columnconfigure(1, weight=1)

        opts = ttk.Frame(self, padding=(10, 0, 10, 10))
        opts.pack(fill="x")
        ttk.Checkbutton(
            opts,
            text="Перезаписывать уже существующий *_.zip",
            variable=self.overwrite_var,
        ).pack(side="left")
        ttk.Checkbutton(
            opts,
            text="Показывать только готовые к архивированию",
            variable=self.only_ready_var,
            command=self._refresh_tree,
        ).pack(side="left", padx=(20, 0))

        mid = ttk.Frame(self, padding=(10, 0, 10, 10))
        mid.pack(fill="both", expand=True)

        left_controls = ttk.Frame(mid)
        left_controls.pack(fill="x", pady=(0, 8))
        ttk.Button(left_controls, text="Выделить всё", command=self._select_all).pack(side="left")
        ttk.Button(left_controls, text="Снять всё", command=self._deselect_all).pack(side="left", padx=(8, 0))
        ttk.Button(left_controls, text="Инвертировать", command=self._invert_selection).pack(side="left", padx=(8, 0))
        ttk.Label(
            left_controls,
            text="Подсказка: клик по колонке 'В работу' переключает включение/исключение папки.",
        ).pack(side="right")

        tree_frame = ttk.Frame(mid)
        tree_frame.pack(fill="both", expand=True)

        columns = ("use", "source", "target", "note")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="tree headings")
        self.tree.heading("#0", text="Папка")
        self.tree.heading("use", text="В работу")
        self.tree.heading("source", text="Исходный zip")
        self.tree.heading("target", text="Новый zip")
        self.tree.heading("note", text="Примечание")

        self.tree.column("#0", width=560, anchor="w")
        self.tree.column("use", width=85, anchor="center")
        self.tree.column("source", width=220, anchor="w")
        self.tree.column("target", width=220, anchor="w")
        self.tree.column("note", width=260, anchor="w")

        yscroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.tree.bind("<Button-1>", self._on_tree_click)

        bottom = ttk.LabelFrame(self, text="Лог", padding=10)
        bottom.pack(fill="both", expand=False, padx=10, pady=(0, 10))

        self.log = ScrolledText(bottom, height=12, wrap="word")
        self.log.pack(fill="both", expand=True)
        # Тёмные цвета для текстового лога
        self.log.configure(
            state="disabled",
            background="#202124",
            foreground="#e8eaed",
            insertbackground="#e8eaed",
        )

        footer = ttk.Frame(self, padding=(10, 0, 10, 10))
        footer.pack(fill="x")
        self.progress = ttk.Progressbar(footer, mode="indeterminate")
        self.progress.pack(side="left", fill="x", expand=True)
        ttk.Label(footer, textvariable=self.status_var).pack(side="left", padx=(12, 0))

    def _browse_root(self) -> None:
        path = filedialog.askdirectory(initialdir=self.root_var.get() or DEFAULT_ROOT)
        if path:
            self.root_var.set(path)

    def _set_busy(self, value: bool, status: str = "") -> None:
        self.busy = value
        if status:
            self.status_var.set(status)
        if value:
            self.progress.start(10)
        else:
            self.progress.stop()

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _on_tree_click(self, event: tk.Event) -> str | None:
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return None
        column = self.tree.identify_column(event.x)
        item = self.tree.identify_row(event.y)
        if not item or column != "#1":
            return None

        candidate = self._candidate_by_item(item)
        if candidate is None:
            return "break"

        candidate.enabled = not candidate.enabled
        self._update_row(candidate)
        return "break"

    def _candidate_by_item(self, item_id: str) -> Candidate | None:
        for candidate in self.candidates:
            if candidate.item_id == item_id:
                return candidate
        return None

    def _select_all(self) -> None:
        for candidate in self.candidates:
            candidate.enabled = True
        self._refresh_tree()

    def _deselect_all(self) -> None:
        for candidate in self.candidates:
            candidate.enabled = False
        self._refresh_tree()

    def _invert_selection(self) -> None:
        for candidate in self.candidates:
            candidate.enabled = not candidate.enabled
        self._refresh_tree()

    def _refresh_tree(self) -> None:
        existing = set(self.tree.get_children())
        for item in existing:
            self.tree.delete(item)

        visible = self.candidates
        if self.only_ready_var.get():
            # Готовыми считаем все папки, для которых уже определено имя целевого архива
            visible = [c for c in self.candidates if c.target_zip_name]

        for candidate in visible:
            item_id = self.tree.insert("", "end", text=str(candidate.folder), values=self._row_values(candidate))
            candidate.item_id = item_id

    def _update_row(self, candidate: Candidate) -> None:
        if candidate.item_id and self.tree.exists(candidate.item_id):
            self.tree.item(candidate.item_id, text=str(candidate.folder), values=self._row_values(candidate))

    @staticmethod
    def _row_values(candidate: Candidate) -> tuple[str, str, str, str]:
        mark = "Да" if candidate.enabled else "Нет"
        return (mark, candidate.source_zip_name, candidate.target_zip_name, candidate.note)

    def scan(self) -> None:
        if self.busy:
            return

        root = self.root_var.get().strip()
        if not root:
            messagebox.showerror("Ошибка", "Укажите корневую папку.")
            return

        root_path = Path(root)
        self.candidates = []
        self._refresh_tree()
        self._append_log(f"[SCAN] Старт сканирования: {root_path}")
        self._set_busy(True, "Сканирование...")

        self.worker_thread = threading.Thread(target=self._scan_worker, args=(root_path,), daemon=True)
        self.worker_thread.start()

    def archive_selected(self) -> None:
        if self.busy:
            return

        # Теперь берём все включённые папки, даже без исходного zip
        selected = [c for c in self.candidates if c.enabled]
        if not selected:
            messagebox.showinfo("Архивация", "Нет выбранных папок, готовых к архивированию.")
            return

        overwrite = self.overwrite_var.get()
        self._append_log(f"[ZIP] Старт архивации. Выбрано папок: {len(selected)}. Перезапись: {'да' if overwrite else 'нет'}")
        self._set_busy(True, "Архивация...")
        self.worker_thread = threading.Thread(target=self._archive_worker, args=(selected, overwrite), daemon=True)
        self.worker_thread.start()

    def _scan_worker(self, root_path: Path) -> None:
        try:
            if not root_path.exists():
                self.queue.put(("error", f"Путь не найден: {root_path}"))
                return

            found: list[Candidate] = []
            visited = 0

            for current_root, dirnames, filenames in os.walk(root_path, topdown=True):
                visited += 1
                current = Path(current_root)
                child_names = {name.casefold(): name for name in dirnames}

                if INSTRUCTION_NAME.casefold() in child_names:
                    candidate = self._make_candidate(current, filenames)
                    found.append(candidate)
                    dirnames[:] = []

                if visited % 200 == 0:
                    self.queue.put(("status", f"Сканирование... просмотрено каталогов: {visited}"))

            found.sort(key=lambda c: str(c.folder).lower())
            self.queue.put(("scan_done", found, visited))
        except Exception as exc:
            self.queue.put(("error", f"Ошибка сканирования: {exc}"))

    def _make_candidate(self, folder: Path, filenames: list[str]) -> Candidate:
        zip_names = sorted([name for name in filenames if name.lower().endswith(".zip")], key=str.lower)
        base_zips = [name for name in zip_names if not Path(name).stem.endswith("_")]

        note = ""
        source_zip_name = ""
        target_zip_name = ""
        enabled = True

        if not base_zips:
            # Нет исходного архива — используем имя папки как есть
            source_zip_name = ""
            target_zip_name = f"{folder.name}_.zip"
            note = "Нет исходного zip, будет создан новый архив"
        else:
            source_zip_name = base_zips[0]
            target_zip_name = f"{Path(source_zip_name).stem}_.zip"
            if len(base_zips) > 1:
                note = f"Найдено zip: {len(base_zips)} шт., выбран: {source_zip_name}"
            else:
                note = "Готово к архивированию"

        return Candidate(
            folder=folder,
            source_zip_name=source_zip_name,
            target_zip_name=target_zip_name,
            note=note,
            enabled=enabled,
        )

    def _archive_worker(self, selected: list[Candidate], overwrite: bool) -> None:
        ok = 0
        skipped = 0
        failed = 0

        for index, candidate in enumerate(selected, start=1):
            self.queue.put(("status", f"Архивация... {index}/{len(selected)}: {candidate.folder.name}"))
            try:
                result_message = self._archive_one(candidate, overwrite)
                ok += 1
                self.queue.put(("log", f"[OK] {result_message}"))
            except FileExistsError as exc:
                skipped += 1
                self.queue.put(("log", f"[SKIP] {candidate.folder} — {exc}"))
            except Exception as exc:
                failed += 1
                self.queue.put(("log", f"[ERR] {candidate.folder} — {exc}"))

        self.queue.put(("archive_done", ok, skipped, failed, len(selected)))

    def _archive_one(self, candidate: Candidate, overwrite: bool) -> str:
        folder = candidate.folder
        target_zip = folder / candidate.target_zip_name

        source_zip = folder / candidate.source_zip_name if candidate.source_zip_name else None

        if source_zip is not None and not source_zip.exists():
            raise FileNotFoundError(f"исходный zip не найден: {source_zip.name}")

        if target_zip.exists() and not overwrite:
            raise FileExistsError(f"целевой архив уже существует: {target_zip.name}")

        temp_zip = folder / f"__tmp__{target_zip.name}"
        if temp_zip.exists():
            temp_zip.unlink()

        exclude_names = {target_zip.name, temp_zip.name}
        if source_zip is not None:
            exclude_names.add(source_zip.name)
        items = sorted(folder.iterdir(), key=lambda p: p.name.lower())
        items_to_pack = [item for item in items if item.name not in exclude_names]

        if not items_to_pack:
            raise RuntimeError("в папке нет содержимого для упаковки")

        try:
            with zipfile.ZipFile(temp_zip, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                for item in items_to_pack:
                    self._add_path_to_zip(zf, item, item.name)

            os.replace(temp_zip, target_zip)
            return f"{folder} -> {target_zip.name}"
        finally:
            if temp_zip.exists():
                try:
                    temp_zip.unlink()
                except OSError:
                    pass

    def _add_path_to_zip(self, zf: zipfile.ZipFile, path: Path, arcname: str) -> None:
        if path.is_dir():
            children = sorted(path.iterdir(), key=lambda p: p.name.lower())
            if not children:
                zf.writestr(arcname.rstrip("/") + "/", "")
                return
            for child in children:
                self._add_path_to_zip(zf, child, f"{arcname}/{child.name}")
            return

        zf.write(path, arcname)

    def _process_queue(self) -> None:
        try:
            while True:
                item = self.queue.get_nowait()
                kind = item[0]

                if kind == "log":
                    self._append_log(item[1])
                elif kind == "status":
                    self.status_var.set(item[1])
                elif kind == "scan_done":
                    found, visited = item[1], item[2]
                    self.candidates = found
                    self._refresh_tree()
                    self._set_busy(False, f"Сканирование завершено. Найдено: {len(found)}")
                    self._append_log(f"[SCAN] Готово. Найдено каталогов-кандидатов: {len(found)}. Просмотрено каталогов: {visited}")
                elif kind == "archive_done":
                    ok, skipped, failed, total = item[1], item[2], item[3], item[4]
                    self._set_busy(False, f"Архивация завершена. Успешно: {ok}, пропущено: {skipped}, ошибок: {failed}")
                    self._append_log(
                        f"[ZIP] Готово. Всего: {total}. Успешно: {ok}. Пропущено: {skipped}. Ошибок: {failed}"
                    )
                    self._refresh_tree()
                elif kind == "error":
                    self._set_busy(False, "Ошибка")
                    self._append_log(f"[ERR] {item[1]}")
                    messagebox.showerror("Ошибка", item[1])
        except queue.Empty:
            pass
        finally:
            self.after(100, self._process_queue)


def main() -> None:
    app = BirkiZipperApp()
    app.mainloop()


if __name__ == "__main__":
    main()
