# Rminer - fluxo limpo por perfil
# - Descobre perfis na FY com criterios proprios.
# - Ao encontrar perfil aprovado, valida os Reels dentro do perfil automaticamente.
# - Remove botoes/fluxos manuais antigos de abrir links/Reels um por um.
#
# Requisitos:
#   pip install customtkinter playwright instaloader yt-dlp
#
# Uso:
#   py Rminer.pyw

import ctypes
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import List, Optional

import customtkinter as ctk

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    PlaywrightTimeoutError = Exception


INSTAGRAM_REELS_URL = "https://www.instagram.com/reels/"
APP_DIR = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
ASSETS_DIR = APP_DIR / "assets"
SESSIONS_BASE_DIR = Path(r"C:\Temp\instagram_reels_validator")
CONFIG_PATH = ASSETS_DIR / "instagram_reels_gui_v19_config.json"
ICON_PATH = ASSETS_DIR / "Rminer.ico"
VALIDATED_PROFILES_PATH = ASSETS_DIR / "perfis_validados.json"
DOWNLOADS_BASE_DIR = APP_DIR / "\u2022 Reels"
AUTO_CDP_PORT = 9222
AUTO_WAIT_SECONDS = 35

# Caminho do cookies.txt exportado do navegador (formato Netscape), usado pelo
# yt-dlp para autenticar downloads. Exporte com uma extensão tipo
# "Get cookies.txt LOCALLY" depois de logar no Instagram, e salve/atualize
# este arquivo periodicamente (a sessão expira de tempos em tempos).
COOKIES_TXT_PATH = ASSETS_DIR / "cookies.txt"
AUTO_COOKIES_TXT_PATH = ASSETS_DIR / "cookies_ytdlp_auto.txt"


@dataclass
class ChromeProfile:
    id: str
    name: str
    port: str
    session: str
    active: bool = True

    @property
    def label(self) -> str:
        status = "ativo" if self.active else "inativo"
        return f"{self.name}  |  porta {self.port}  |  {status}"

    @property
    def safe_session_name(self) -> str:
        raw = f"{self.id}_{self.name}_{self.port}".strip("_")
        raw = re.sub(r"[^a-zA-Z0-9._@-]+", "_", raw)
        return raw[:120] or self.id


def find_chrome_exe() -> Optional[str]:
    if sys.platform.startswith("win"):
        candidates = [
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        ]

        for path in candidates:
            if path.exists():
                return str(path)

        return shutil.which("chrome") or shutil.which("chrome.exe")

    if sys.platform == "darwin":
        path = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        if path.exists():
            return str(path)
        return shutil.which("google-chrome") or shutil.which("chrome")

    return (
        shutil.which("google-chrome")
        or shutil.which("google-chrome-stable")
        or shutil.which("chromium")
        or shutil.which("chromium-browser")
    )


_YTDLP_READY = False


def ensure_yt_dlp_installed(log_fn=None) -> bool:
    """
    Garante que o pacote yt-dlp esteja disponível, instalando-o via pip
    na primeira vez que for necessário (download de Reels).
    """
    global _YTDLP_READY

    if _YTDLP_READY:
        return True

    def _log(msg: str) -> None:
        if log_fn:
            try:
                log_fn(msg)
                return
            except Exception:
                pass
        print(msg)

    try:
        import yt_dlp  # noqa: F401
        _YTDLP_READY = True
        return True
    except ImportError:
        pass

    _log("yt-dlp não encontrado. Instalando automaticamente (pip install yt-dlp)...")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        _log(f"Falha ao instalar yt-dlp automaticamente: {e.stderr or e}")
        return False
    except Exception as e:
        _log(f"Falha ao instalar yt-dlp automaticamente: {e}")
        return False

    try:
        import yt_dlp  # noqa: F401
        _YTDLP_READY = True
        _log("yt-dlp instalado com sucesso.")
        return True
    except ImportError as e:
        _log(f"yt-dlp foi instalado mas não pôde ser importado: {e}")
        return False


def wait_debug_port(port: int, timeout: int = 25) -> bool:
    url = f"http://127.0.0.1:{port}/json/version"
    end = time.time() + timeout

    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)

    return False


def enable_dark_title_bar(window) -> None:
    if not sys.platform.startswith("win"):
        return

    try:
        window.update()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1)
        black = ctypes.c_int(0x000000)
        white = ctypes.c_int(0xFFFFFF)

        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(value), ctypes.sizeof(value))
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(black), ctypes.sizeof(black))
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(white), ctypes.sizeof(white))
    except Exception:
        pass


class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.title("Rminer")
        if ICON_PATH.exists():
            try:
                self.iconbitmap(str(ICON_PATH))
            except Exception:
                pass
        self.geometry("1280x1000")
        self.minsize(1280, 850)
        self.configure(fg_color="#170b2e")

        self.profiles: List[ChromeProfile] = []
        self.chrome_process: Optional[subprocess.Popen] = None
        self.chrome_processes = {}
        self.stop_requested = False
        self.config = self.load_config()
        self.validated_profiles_lock = threading.Lock()
        self.validated_profiles = self.load_validated_profiles()
        self._validated_profiles_save_job = None
        self._validated_profiles_dirty = False
        self._autosave_job = None
        self.review_pages = []

        self._build_ui()
        enable_dark_title_bar(self)
        self.after(0, self.maximize_window)
        self.refresh_profiles()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def maximize_window(self):
        try:
            self.state("zoomed")
        except Exception:
            try:
                self.attributes("-zoomed", True)
            except Exception:
                pass

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0, minsize=430)
        self.grid_rowconfigure(0, weight=1)

        self.main_scroll = ctk.CTkScrollableFrame(
            self,
            fg_color="#170b2e",
            corner_radius=0,
            scrollbar_button_color="#6d28d9",
            scrollbar_button_hover_color="#8b35f6",
        )
        self.main_scroll.grid(row=0, column=0, sticky="nsew")

        log_panel = ctk.CTkFrame(self, fg_color="#170b2e", corner_radius=0, width=430)
        log_panel.grid(row=0, column=1, sticky="nsew")
        log_panel.grid_propagate(False)
        log_panel.grid_columnconfigure(0, weight=1)
        log_panel.grid_rowconfigure(1, weight=1)

        main = self.main_scroll
        main.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            main,
            text="Rminer - Fluxo por perfil",
            font=ctk.CTkFont(size=25, weight="bold"),
            text_color="#fbf7ff",
        ).grid(row=0, column=0, padx=20, pady=(14, 4), sticky="w")

        ctk.CTkLabel(
            main,
            text="Descobre perfis na FY, valida os Reels dentro do perfil e baixa automaticamente os aprovados.",
            font=ctk.CTkFont(size=14),
            text_color="#d8c5ff",
        ).grid(row=1, column=0, padx=20, pady=(0, 12), sticky="w")

        profile_card = ctk.CTkFrame(main, fg_color="#24113f", corner_radius=10)
        profile_card.grid(row=2, column=0, padx=20, pady=0, sticky="ew")
        profile_card.grid_columnconfigure(1, weight=1)
        profile_card.grid_columnconfigure(3, weight=0)

        ctk.CTkLabel(
            profile_card,
            text="Perfis Rminer",
            text_color="#fbf7ff",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=0, padx=14, pady=(12, 6), sticky="w")

        self.profile_var = ctk.StringVar()
        self.profile_combo = ctk.CTkComboBox(
            profile_card,
            variable=self.profile_var,
            values=[],
            height=34,
            fg_color="#120820",
            button_color="#6d28d9",
            button_hover_color="#8b35f6",
            border_color="#6d28d9",
            dropdown_fg_color="#24113f",
            dropdown_hover_color="#6d28d9",
            text_color="#fbf7ff",
            dropdown_text_color="#fbf7ff",
            command=lambda _value=None: self.profile_selected(),
        )
        self.profile_combo.grid_forget()

        self.profile_active_var = ctk.BooleanVar(value=True)
        self.profile_active_check = ctk.CTkCheckBox(
            profile_card,
            text="Ativo",
            variable=self.profile_active_var,
            text_color="#eadcff",
            fg_color="#6d28d9",
            hover_color="#8b35f6",
            border_color="#6d28d9",
            command=self.save_selected_profile,
        )
        self.profile_active_check.grid(row=0, column=4, padx=(0, 14), pady=(12, 6), sticky="e")

        ctk.CTkLabel(profile_card, text="Nome:", text_color="#eadcff").grid(row=1, column=0, padx=14, pady=4, sticky="w")
        self.profile_name_entry = ctk.CTkEntry(profile_card, height=32, fg_color="#170b2e", border_color="#6d28d9", text_color="#fbf7ff")
        self.profile_name_entry.grid(row=1, column=1, padx=(6, 14), pady=4, sticky="ew")

        ctk.CTkLabel(profile_card, text="Porta:", text_color="#eadcff").grid(row=1, column=2, padx=(0, 8), pady=4, sticky="e")
        self.profile_port_entry = ctk.CTkEntry(profile_card, width=90, height=32, fg_color="#170b2e", border_color="#6d28d9", text_color="#fbf7ff")
        self.profile_port_entry.grid(row=1, column=3, padx=(0, 14), pady=4, sticky="w")

        ctk.CTkLabel(profile_card, text="Sessão:", text_color="#eadcff").grid(row=2, column=0, padx=14, pady=4, sticky="w")
        self.profile_session_entry = ctk.CTkEntry(profile_card, height=32, fg_color="#170b2e", border_color="#6d28d9", text_color="#fbf7ff")
        self.profile_session_entry.grid(row=2, column=1, columnspan=3, padx=(6, 14), pady=4, sticky="ew")
        ctk.CTkButton(
            profile_card,
            text="Pasta",
            width=86,
            height=32,
            fg_color="#6d28d9",
            hover_color="#8b35f6",
            command=self.browse_profile_session_dir,
        ).grid(row=2, column=4, padx=(0, 14), pady=4, sticky="e")

        profile_buttons = ctk.CTkFrame(profile_card, fg_color="#24113f")
        profile_buttons.grid(row=3, column=0, columnspan=5, padx=14, pady=(8, 16), sticky="ew")
        for txt, cmd, width in [
            ("Novo", self.new_profile, 80),
            ("Salvar", self.save_selected_profile, 90),
            ("Excluir", self.delete_selected_profile, 90),
            ("Abrir sel.", self.open_selected_profile, 110),
            ("Abrir ativos", self.open_active_profiles, 120),
        ]:
            ctk.CTkButton(
                profile_buttons,
                text=txt,
                width=width,
                height=32,
                fg_color="#6d28d9",
                hover_color="#8b35f6",
                command=cmd,
            ).pack(side="left", padx=(0, 8))

        self._profiles_list_frame = ctk.CTkFrame(profile_card, fg_color="#170b2e", corner_radius=8)
        self._profiles_list_frame.grid(row=4, column=0, columnspan=5, padx=14, pady=(0, 12), sticky="ew")
        self._profiles_list_frame.grid_columnconfigure(0, weight=1)
        self._profile_check_vars = {}
        self._profile_row_frames = {}

        card = ctk.CTkFrame(main, fg_color="#24113f", corner_radius=10)
        card.grid(row=3, column=0, padx=20, pady=(18, 0), sticky="ew")
        card.grid_columnconfigure(1, weight=1)
        card.grid_columnconfigure(3, weight=1)

        # ── Validação do perfil encontrado na FY ──────────────────────────────
        ctk.CTkLabel(
            card,
            text="VALIDAÇÃO DO PERFIL ENCONTRADO NA FY",
            text_color="#fbf7ff",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=1, column=0, columnspan=5, padx=14, pady=(10, 3), sticky="w")

        ctk.CTkLabel(
            card,
            text="Likes mínimos:",
            text_color="#eadcff",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=2, column=0, padx=14, pady=4, sticky="w")

        self.min_likes_entry = ctk.CTkEntry(
            card,
            width=90,
            height=34,
            fg_color="#170b2e",
            border_color="#6d28d9",
            text_color="#fbf7ff",
        )
        self.min_likes_entry.grid(row=2, column=1, padx=8, pady=4, sticky="w")
        self.min_likes_entry.insert(0, str(self.config.get("min_likes", "1000")))

        ctk.CTkLabel(
            card,
            text="Comentários-chave mínimos:",
            text_color="#eadcff",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=2, column=2, padx=(14, 6), pady=4, sticky="w")

        self.required_entry = ctk.CTkEntry(
            card,
            width=90,
            height=34,
            fg_color="#170b2e",
            border_color="#6d28d9",
            text_color="#fbf7ff",
        )
        self.required_entry.grid(row=2, column=3, padx=8, pady=4, sticky="w")
        self.required_entry.insert(0, str(self.config.get("profile_keyword_required_count", self.config.get("required_count", "1"))))

        ctk.CTkLabel(
            card,
            text="Postado até:",
            text_color="#eadcff",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=3, column=0, padx=14, pady=4, sticky="w")

        self.recent_hours_entry = ctk.CTkEntry(
            card,
            width=90,
            height=34,
            fg_color="#170b2e",
            border_color="#6d28d9",
            text_color="#fbf7ff",
        )
        self.recent_hours_entry.grid(row=3, column=1, padx=8, pady=4, sticky="w")
        self.recent_hours_entry.insert(0, str(self.config.get("profile_post_recent_days", self.config.get("comment_recent_days", "1"))))

        ctk.CTkLabel(
            card,
            text="dias",
            text_color="#d8c5ff",
        ).grid(row=3, column=1, padx=(108, 8), pady=4, sticky="w")

        ctk.CTkLabel(
            card,
            text="Quantidade de reels:",
            text_color="#eadcff",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=3, column=2, padx=(14, 6), pady=4, sticky="w")

        self.profile_save_target_entry = ctk.CTkEntry(
            card,
            width=90,
            height=34,
            fg_color="#170b2e",
            border_color="#6d28d9",
            text_color="#fbf7ff",
        )
        self.profile_save_target_entry.grid(row=3, column=3, padx=8, pady=4, sticky="w")
        self.profile_save_target_entry.insert(
            0,
            str(self.config.get("reel_download_target", self.config.get("profile_save_target", self.config.get("download_target", "5")))),
        )

        ctk.CTkLabel(
            card,
            text="reels",
            text_color="#d8c5ff",
        ).grid(row=3, column=3, padx=(108, 8), pady=4, sticky="w")

        ctk.CTkLabel(
            card,
            text="Abas simultâneas:",
            text_color="#eadcff",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=4, column=0, padx=14, pady=4, sticky="w")

        self.parallel_tabs_entry = ctk.CTkEntry(
            card,
            width=90,
            height=34,
            fg_color="#170b2e",
            border_color="#6d28d9",
            text_color="#fbf7ff",
        )
        self.parallel_tabs_entry.grid(row=4, column=1, padx=8, pady=4, sticky="w")
        self.parallel_tabs_entry.insert(0, str(self.config.get("parallel_tabs", "4")))

        ctk.CTkLabel(
            card,
            text="abas",
            text_color="#d8c5ff",
        ).grid(row=4, column=1, padx=(108, 8), pady=4, sticky="w")

        ctk.CTkLabel(
            card,
            text="Perfis simultâneos:",
            text_color="#eadcff",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=4, column=2, padx=(14, 6), pady=4, sticky="w")

        self.profile_parallel_entry = ctk.CTkEntry(
            card,
            width=90,
            height=34,
            fg_color="#170b2e",
            border_color="#6d28d9",
            text_color="#fbf7ff",
        )
        self.profile_parallel_entry.grid(row=4, column=3, padx=8, pady=4, sticky="w")
        self.profile_parallel_entry.insert(0, str(self.config.get("max_parallel_profiles", "1")))

        ctk.CTkLabel(
            card,
            text="perfis",
            text_color="#d8c5ff",
        ).grid(row=4, column=3, padx=(108, 8), pady=4, sticky="w")

        # ── Validação dos Reels dentro do perfil ───────────────────────────
        ctk.CTkFrame(card, fg_color="#6d28d9", height=1, corner_radius=0).grid(
            row=5, column=0, columnspan=5, padx=14, pady=(10, 2), sticky="ew"
        )

        ctk.CTkLabel(
            card,
            text="VALIDAÇÃO DOS REELS DENTRO DO PERFIL",
            text_color="#fbf7ff",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=6, column=0, columnspan=5, padx=14, pady=(10, 4), sticky="w")

        ctk.CTkLabel(
            card,
            text="Comentários mínimos:",
            text_color="#eadcff",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=7, column=0, padx=14, pady=(4, 10), sticky="w")

        self.download_target_entry = ctk.CTkEntry(
            card,
            width=90,
            height=34,
            fg_color="#170b2e",
            border_color="#6d28d9",
            text_color="#fbf7ff",
        )
        self.download_target_entry.grid(row=7, column=1, padx=8, pady=(4, 10), sticky="w")
        self.download_target_entry.insert(0, str(self.config.get("reel_required_count", self.config.get("required_count", "1"))))
        self.reel_required_entry = self.download_target_entry

        ctk.CTkLabel(
            card,
            text="comentários",
            text_color="#d8c5ff",
        ).grid(row=7, column=1, padx=(108, 8), pady=(4, 10), sticky="w")

        ctk.CTkLabel(
            card,
            text="Postado até:",
            text_color="#eadcff",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=7, column=2, padx=(14, 6), pady=(4, 10), sticky="w")

        self.reel_hours_entry = ctk.CTkEntry(
            card,
            width=90,
            height=34,
            fg_color="#170b2e",
            border_color="#6d28d9",
            text_color="#fbf7ff",
        )
        self.reel_hours_entry.grid(row=7, column=3, padx=8, pady=(4, 10), sticky="w")
        self.reel_hours_entry.insert(0, str(self.config.get("reel_recent_days", "1")))

        ctk.CTkLabel(
            card,
            text="dias",
            text_color="#d8c5ff",
        ).grid(row=7, column=3, padx=(108, 8), pady=(4, 10), sticky="w")

        ctk.CTkLabel(
            card,
            text="Salvar Reels em:",
            text_color="#eadcff",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=8, column=0, padx=14, pady=(0, 12), sticky="w")

        self.download_dir_entry = ctk.CTkEntry(
            card,
            height=34,
            fg_color="#170b2e",
            border_color="#6d28d9",
            text_color="#fbf7ff",
        )
        self.download_dir_entry.grid(row=8, column=1, columnspan=3, padx=8, pady=(0, 12), sticky="ew")
        self.download_dir_entry.insert(0, str(self.config.get("download_dir", str(DOWNLOADS_BASE_DIR))))

        ctk.CTkButton(
            card,
            text="Pasta",
            height=34,
            width=110,
            fg_color="#6d28d9",
            hover_color="#8b35f6",
            command=self.browse_download_dir,
        ).grid(row=8, column=4, padx=(6, 14), pady=(0, 12), sticky="e")

        ctk.CTkLabel(
            card,
            text="Contas validadas:",
            text_color="#eadcff",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=9, column=0, padx=14, pady=(0, 12), sticky="w")

        self.validated_profiles_label = ctk.CTkLabel(
            card,
            text="",
            text_color="#d8c5ff",
            font=ctk.CTkFont(size=13),
            anchor="w",
        )
        self.validated_profiles_label.grid(row=9, column=1, columnspan=3, padx=8, pady=(0, 12), sticky="ew")

        ctk.CTkButton(
            card,
            text="Limpar",
            height=34,
            width=110,
            fg_color="#8f1d5c",
            hover_color="#b12a75",
            command=self.clear_validated_profiles,
        ).grid(row=9, column=4, padx=(6, 14), pady=(0, 12), sticky="e")

        list_card = ctk.CTkFrame(main, fg_color="#24113f", corner_radius=10)
        list_card.grid(row=4, column=0, padx=20, pady=(18, 0), sticky="ew")
        list_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            list_card,
            text="Palavras-chave nos comentarios",
            text_color="#eadcff",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, padx=12, pady=(10, 4), sticky="w")

        self.profile_list_text = ctk.CTkTextbox(
            list_card,
            height=72,
            fg_color="#170b2e",
            border_color="#6d28d9",
            border_width=1,
            text_color="#fbf7ff",
            wrap="none",
        )
        self.profile_list_text.grid(row=1, column=0, padx=12, pady=(0, 12), sticky="ew")
        saved_profile_list = str(self.config.get("keyword_list", self.config.get("profile_list", "")) or "")
        if saved_profile_list:
            self.profile_list_text.insert("1.0", saved_profile_list)

        selector_card = ctk.CTkFrame(main, fg_color="#24113f", corner_radius=10)
        selector_card.grid(row=5, column=0, padx=20, pady=(18, 0), sticky="ew")
        selector_card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            selector_card,
            text="PAINEL DE SELETORES FIXOS",
            text_color="#fbf7ff",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=0, columnspan=3, padx=12, pady=(10, 3), sticky="w")

        ctk.CTkLabel(
            selector_card,
            text="Contador de comentários da grade:",
            text_color="#eadcff",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=1, column=0, padx=12, pady=(6, 4), sticky="w")

        self.grid_comment_selector_entry = ctk.CTkEntry(
            selector_card,
            height=34,
            fg_color="#170b2e",
            border_color="#6d28d9",
            text_color="#fbf7ff",
        )
        self.grid_comment_selector_entry.grid(row=1, column=1, padx=8, pady=(6, 4), sticky="ew")
        self.grid_comment_selector_entry.insert(0, str(self.config.get("grid_comment_count_selector", "")))

        ctk.CTkButton(
            selector_card,
            text="Capturar",
            height=34,
            width=110,
            fg_color="#6d28d9",
            hover_color="#8b35f6",
            command=lambda: self.start_selector_capture("grid_comment_count_selector", self.grid_comment_selector_entry, "contador de comentários da grade"),
        ).grid(row=1, column=2, padx=(6, 12), pady=(6, 4), sticky="e")

        ctk.CTkLabel(
            selector_card,
            text="Uso: abra um perfil em /reels/, passe o mouse em cima de um card para aparecer o overlay e clique no número/ícone de comentários. Esse será o único seletor usado; sem fallback.",
            text_color="#d8c5ff",
            font=ctk.CTkFont(size=12),
            wraplength=1120,
            justify="left",
        ).grid(row=2, column=0, columnspan=3, padx=12, pady=(0, 12), sticky="w")

        action = ctk.CTkFrame(main, fg_color="#170b2e")
        action.grid(row=6, column=0, padx=20, pady=14, sticky="ew")
        action.grid_columnconfigure(2, weight=1)

        self.run_btn = ctk.CTkButton(
            action,
            text="EXECUTAR RMINER",
            height=42,
            width=210,
            fg_color="#9b35ff",
            hover_color="#7a24d6",
            font=ctk.CTkFont(size=15, weight="bold"),
            command=self.start_validation_thread,
        )
        self.run_btn.grid(row=0, column=0, padx=(0, 10), sticky="w")

        self.stop_btn = ctk.CTkButton(
            action,
            text="PARAR",
            height=42,
            width=90,
            fg_color="#8f1d5c",
            hover_color="#b12a75",
            command=self.request_stop,
        )
        self.stop_btn.grid(row=0, column=1, padx=(0, 10), sticky="w")

        self.result_label = ctk.CTkLabel(
            action,
            text="AGUARDANDO",
            text_color="#d8c5ff",
            font=ctk.CTkFont(size=18, weight="bold"),
            anchor="e",
        )
        self.result_label.grid(row=0, column=2, sticky="e")

        info = ctk.CTkFrame(main, fg_color="#24113f", corner_radius=10)
        info.grid(row=7, column=0, padx=20, pady=(0, 12), sticky="ew")

        self.info_label = ctk.CTkLabel(
            info,
            text=f"Destino FY: {INSTAGRAM_REELS_URL}  |  Sessões: {SESSIONS_BASE_DIR}  |  Downloads: {self.get_downloads_dir()}",
            text_color="#d8c5ff",
            font=ctk.CTkFont(size=13),
        )
        self.info_label.grid(row=0, column=0, padx=12, pady=4, sticky="w")
        self.update_validated_profiles_label()

        self.log_box = ctk.CTkTextbox(
            log_panel,
            fg_color="#24113f",
            border_color="#6d28d9",
            border_width=1,
            text_color="#fbf7ff",
            wrap="word",
        )
        ctk.CTkLabel(
            log_panel,
            text="Log",
            text_color="#fbf7ff",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, padx=(0, 24), pady=(24, 10), sticky="w")
        self.log_box.grid(row=1, column=0, padx=(0, 24), pady=(0, 24), sticky="nsew")

        self.setup_persistent_fields()

        self.log("Pronto.")
        self.log("Fluxo atual: encontra perfil na FY, entra no /reels/ do perfil aprovado, valida os Reels e baixa automaticamente os aprovados.")
        self.log(f"Campos e perfil usado ficam salvos automaticamente em {CONFIG_PATH}.")
        self.log("Conexão e tempo de espera são automáticos; não precisam mais ser configurados na tela.")

    def load_config(self) -> dict:
        try:
            if CONFIG_PATH.exists():
                with CONFIG_PATH.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass

        return {}

    def load_validated_profiles(self) -> dict:
        try:
            if not VALIDATED_PROFILES_PATH.exists():
                return {}
            with VALIDATED_PROFILES_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("profiles"), dict):
                raw_profiles = data.get("profiles") or {}
            elif isinstance(data, dict):
                raw_profiles = data
            elif isinstance(data, list):
                raw_profiles = {str(item): {"username": str(item)} for item in data}
            else:
                raw_profiles = {}

            profiles = {}
            for key, item in raw_profiles.items():
                if isinstance(item, dict):
                    username = self.normalize_instagram_profile(item.get("username") or key)
                    record = dict(item)
                else:
                    username = self.normalize_instagram_profile(str(item or key))
                    record = {"username": username}
                if not username:
                    continue
                record["username"] = username
                record.setdefault("first_validated_at", record.get("last_validated_at", ""))
                record.setdefault("last_validated_at", record.get("first_validated_at", ""))
                record["count"] = int(record.get("count") or 1)
                profiles[username.lower()] = record
            return profiles
        except Exception:
            return {}

    def save_validated_profiles(self) -> None:
        try:
            with self.validated_profiles_lock:
                profiles_snapshot = dict(self.validated_profiles)
            ASSETS_DIR.mkdir(parents=True, exist_ok=True)
            with VALIDATED_PROFILES_PATH.open("w", encoding="utf-8") as f:
                json.dump(
                    {
                        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        "profiles": profiles_snapshot,
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            self._validated_profiles_dirty = False
        except Exception as e:
            self.log(f"Não consegui salvar lista de contas validadas: {e}")

    def schedule_validated_profiles_save(self, delay_ms: int = 1200) -> None:
        self._validated_profiles_dirty = True
        if self._validated_profiles_save_job:
            return

        def flush():
            self._validated_profiles_save_job = None
            if self._validated_profiles_dirty:
                self.save_validated_profiles()

        try:
            self._validated_profiles_save_job = self.after(delay_ms, flush)
        except Exception:
            self.save_validated_profiles()

    def validated_profiles_summary(self) -> str:
        with self.validated_profiles_lock:
            records = list(self.validated_profiles.values())
        if not records:
            return "Nenhuma conta salva."

        first_record = records[0]
        raw = str(first_record.get("first_validated_at") or first_record.get("last_validated_at") or "")
        try:
            first_dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            first_dt = None

        if first_dt is None:
            return f"{len(records)} conta(s) salva(s). Primeiro salvo: sem data."

        now = datetime.now(timezone.utc)
        days = max(0, int((now - first_dt).total_seconds() // 86400))
        return f"{len(records)} conta(s) salva(s). Primeiro salvo há {days} dia(s)."

    def update_validated_profiles_label(self) -> None:
        if not hasattr(self, "validated_profiles_label"):
            return

        def apply():
            self.validated_profiles_label.configure(text=self.validated_profiles_summary())

        try:
            self.after(0, apply)
        except Exception:
            apply()

    def clear_validated_profiles(self) -> None:
        if not messagebox.askyesno("Contas validadas", "Limpar toda a lista de contas já validadas?"):
            return
        with self.validated_profiles_lock:
            self.validated_profiles = {}
        self.save_validated_profiles()
        self.update_validated_profiles_label()
        self.log("Lista de contas validadas foi limpa.")

    def is_profile_already_validated(self, owner: str) -> bool:
        owner = self.normalize_instagram_profile(owner)
        if not owner:
            return False
        with self.validated_profiles_lock:
            return owner.lower() in self.validated_profiles

    def mark_profile_validated(
        self,
        owner: str,
        shortcode: str = "",
        source_profile: str = "",
        likes_count: int = 0,
        matches: int = 0,
        status: str = "validado",
    ) -> None:
        owner = self.normalize_instagram_profile(owner)
        if not owner:
            return
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        key = owner.lower()
        with self.validated_profiles_lock:
            record = dict(self.validated_profiles.get(key) or {})
            record.setdefault("first_validated_at", now)
            record["last_validated_at"] = now
            record["username"] = owner
            record["count"] = int(record.get("count") or 0) + 1
            record["last_reel_shortcode"] = str(shortcode or "")
            record["last_source_profile"] = str(source_profile or "")
            record["last_likes"] = int(likes_count or 0)
            record["last_keyword_matches"] = int(matches or 0)
            record["status"] = str(status or "validado")
            self.validated_profiles[key] = record
        self.schedule_validated_profiles_save()
        self.update_validated_profiles_label()

    def get_downloads_dir(self) -> Path:
        raw = ""
        try:
            if hasattr(self, "download_dir_entry"):
                raw = self.download_dir_entry.get().strip()
        except Exception:
            raw = ""
        if not raw:
            raw = str(self.config.get("download_dir", "") or "")
        return Path(raw) if raw else DOWNLOADS_BASE_DIR

    def browse_download_dir(self):
        initial = str(self.get_downloads_dir())
        chosen = filedialog.askdirectory(title="Escolha onde salvar os Reels", initialdir=initial if Path(initial).exists() else str(APP_DIR))
        if not chosen:
            return
        self.download_dir_entry.delete(0, "end")
        self.download_dir_entry.insert(0, chosen)
        self.update_info_label()
        self.save_config()

    def update_info_label(self):
        if hasattr(self, "info_label"):
            self.info_label.configure(
                text=f"Destino FY: {INSTAGRAM_REELS_URL}  |  Sessões: {SESSIONS_BASE_DIR}  |  Downloads: {self.get_downloads_dir()}"
            )

    def default_profiles(self) -> List[ChromeProfile]:
        return [
            ChromeProfile(
                id="perfil_1",
                name="Perfil 1",
                port="9222",
                session=str(SESSIONS_BASE_DIR / "perfil_1"),
                active=True,
            )
        ]

    def normalize_profiles(self) -> List[ChromeProfile]:
        raw_profiles = self.config.get("chrome_profiles")
        if not isinstance(raw_profiles, list) or not raw_profiles:
            raw_profiles = [
                {
                    "id": str(self.config.get("profile_safe_session_name") or "perfil_1"),
                    "name": str(self.config.get("profile_name") or "Perfil 1"),
                    "port": str(self.config.get("profile_port") or AUTO_CDP_PORT),
                    "session": str(SESSIONS_BASE_DIR / "perfil_1"),
                    "active": True,
                }
            ]

        profiles = []
        used_ids = set()
        used_ports = set()
        for index, item in enumerate(raw_profiles, start=1):
            if not isinstance(item, dict):
                continue
            profile_id = str(item.get("id") or f"perfil_{index}").strip() or f"perfil_{index}"
            base_id = profile_id
            n = 2
            while profile_id in used_ids:
                profile_id = f"{base_id}_{n}"
                n += 1
            used_ids.add(profile_id)

            name = str(item.get("name") or f"Perfil {index}").strip() or f"Perfil {index}"
            port = str(item.get("port") or (9221 + index)).strip()
            if not port.isdigit() or not (1024 <= int(port) <= 65535):
                port = str(9221 + index)
            while port in used_ports:
                port = str(int(port) + 1)
            used_ports.add(port)

            session = str(item.get("session") or (SESSIONS_BASE_DIR / profile_id)).strip()
            profiles.append(ChromeProfile(
                id=profile_id,
                name=name,
                port=port,
                session=session,
                active=bool(item.get("active", True)),
            ))

        if not profiles:
            profiles = self.default_profiles()
        self.profiles = profiles
        self.config["chrome_profiles"] = [self.profile_to_dict(p) for p in profiles]
        return profiles

    def profile_to_dict(self, profile: ChromeProfile) -> dict:
        return {
            "id": profile.id,
            "name": profile.name,
            "port": str(profile.port),
            "session": str(profile.session),
            "active": bool(profile.active),
        }

    def rebuild_profiles_list(self):
        if not hasattr(self, "_profiles_list_frame"):
            return
        for widget in self._profiles_list_frame.winfo_children():
            widget.destroy()
        self._profile_check_vars = {}
        self._profile_row_frames = {}

        selected_index = self.profile_index()
        for index, profile in enumerate(self.profiles):
            selected = index == selected_index
            row_bg = "#6d28d9" if selected else "#170b2e"
            text_color = "#fbf7ff" if selected else "#eadcff"
            status_text = "ativo" if profile.active else "desativado"
            dot_color = "#4ade80" if profile.active else "#d8c5ff"

            row = ctk.CTkFrame(self._profiles_list_frame, fg_color=row_bg, corner_radius=6)
            row.grid(row=index, column=0, sticky="ew", padx=6, pady=(6 if index == 0 else 0, 6))
            row.grid_columnconfigure(2, weight=1)
            self._profile_row_frames[index] = row

            dot = ctk.CTkLabel(row, text="●", width=18, text_color=dot_color, font=ctk.CTkFont(size=12))
            dot.grid(row=0, column=0, padx=(8, 2), pady=4, sticky="w")

            var = ctk.BooleanVar(value=bool(profile.active))
            self._profile_check_vars[index] = var
            check = ctk.CTkCheckBox(
                row,
                text="",
                width=24,
                variable=var,
                fg_color="#9b35ff",
                hover_color="#8b35f6",
                border_color="#8b35f6",
                command=lambda i=index, v=var: self.toggle_profile_active(i, v.get()),
            )
            check.grid(row=0, column=1, padx=(2, 4), pady=4, sticky="w")

            label = ctk.CTkLabel(
                row,
                text=f"{profile.name}  |  {profile.port}  |  {status_text}",
                text_color=text_color,
                font=ctk.CTkFont(family="Consolas", size=12),
                anchor="w",
            )
            label.grid(row=0, column=2, padx=(0, 8), pady=4, sticky="ew")

            def select_row(_event=None, i=index):
                self.select_profile_row(i)

            for widget in (row, dot, label):
                widget.bind("<Button-1>", select_row)

    def select_profile_row(self, index: int):
        if index is None or not (0 <= index < len(self.profiles)):
            return
        self.profile_var.set(self.profiles[index].label)
        self.profile_selected()

    def toggle_profile_active(self, index: int, active: bool):
        if index is None or not (0 <= index < len(self.profiles)):
            return
        self.profiles[index].active = bool(active)
        self.config["chrome_profiles"] = [self.profile_to_dict(p) for p in self.profiles]
        self.profile_var.set(self.profiles[index].label)
        if self.profile_index() == index:
            self.profile_active_var.set(bool(active))
        self.save_config()
        self.rebuild_profiles_list()
        self.log(f"Perfil Rminer {'ativado' if active else 'desativado'}: {self.profiles[index].name}")

    def profile_index(self):
        selected = self.profile_var.get() if hasattr(self, "profile_var") else ""
        for index, profile in enumerate(self.profiles):
            if profile.label == selected:
                return index
        return 0 if self.profiles else None

    def profile_selected(self, _value=None):
        index = self.profile_index()
        if index is None or not (0 <= index < len(self.profiles)):
            return
        profile = self.profiles[index]
        for entry, value in [
            (self.profile_name_entry, profile.name),
            (self.profile_port_entry, profile.port),
            (self.profile_session_entry, profile.session),
        ]:
            entry.delete(0, "end")
            entry.insert(0, str(value))
        self.profile_active_var.set(bool(profile.active))
        self.rebuild_profiles_list()

    def validate_profile_editor(self) -> ChromeProfile:
        index = self.profile_index()
        current = self.profiles[index] if index is not None and 0 <= index < len(self.profiles) else None
        name = self.profile_name_entry.get().strip()
        port = self.profile_port_entry.get().strip()
        session = self.profile_session_entry.get().strip()
        if not name:
            raise ValueError("Informe um nome para o perfil.")
        if not port.isdigit():
            raise ValueError("A porta precisa ser um número.")
        port_num = int(port)
        if not (1024 <= port_num <= 65535):
            raise ValueError("A porta deve ficar entre 1024 e 65535.")
        for other_index, other in enumerate(self.profiles):
            if current is not None and other_index == index:
                continue
            if str(other.port) == str(port_num):
                raise ValueError(f"A porta {port_num} já está sendo usada por '{other.name}'.")
        if not session:
            safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", name).strip("_") or f"perfil_{port_num}"
            session = str(SESSIONS_BASE_DIR / safe)
        return ChromeProfile(
            id=current.id if current else f"perfil_{port_num}",
            name=name,
            port=str(port_num),
            session=session,
            active=bool(self.profile_active_var.get()),
        )

    def save_selected_profile(self):
        if not hasattr(self, "profile_name_entry"):
            return
        index = self.profile_index()
        if index is None or not (0 <= index < len(self.profiles)):
            return
        try:
            profile = self.validate_profile_editor()
        except ValueError as exc:
            messagebox.showerror("Perfil Rminer", str(exc))
            return
        self.profiles[index] = profile
        self.config["chrome_profiles"] = [self.profile_to_dict(p) for p in self.profiles]
        self.save_config()
        self.refresh_profiles(select_id=profile.id)
        self.log(f"Perfil salvo: {profile.name} | porta {profile.port}")

    def new_profile(self):
        profiles = self.normalize_profiles()
        used_ports = {int(p.port) for p in profiles if str(p.port).isdigit()}
        port = 9222
        while port in used_ports:
            port += 1
        used_ids = {p.id for p in profiles}
        number = 1
        while f"perfil_{number}" in used_ids:
            number += 1
        profile = ChromeProfile(
            id=f"perfil_{number}",
            name=f"Perfil {number}",
            port=str(port),
            session=str(SESSIONS_BASE_DIR / f"perfil_{number}"),
            active=True,
        )
        self.profiles.append(profile)
        self.config["chrome_profiles"] = [self.profile_to_dict(p) for p in self.profiles]
        self.save_config()
        self.refresh_profiles(select_id=profile.id)

    def delete_selected_profile(self):
        index = self.profile_index()
        if index is None or not (0 <= index < len(self.profiles)):
            return
        if len(self.profiles) <= 1:
            messagebox.showwarning("Perfil Rminer", "Deve existir pelo menos um perfil.")
            return
        profile = self.profiles[index]
        if not messagebox.askyesno("Excluir perfil", f"Excluir '{profile.name}' da lista?\n\nA sessão salva no computador será mantida."):
            return
        self.profiles.pop(index)
        self.config["chrome_profiles"] = [self.profile_to_dict(p) for p in self.profiles]
        self.save_config()
        self.refresh_profiles(select_id=self.profiles[max(0, index - 1)].id)

    def browse_profile_session_dir(self):
        initial = self.profile_session_entry.get().strip() or str(SESSIONS_BASE_DIR)
        chosen = filedialog.askdirectory(title="Escolha a pasta da sessão Chrome", initialdir=initial if Path(initial).exists() else str(SESSIONS_BASE_DIR))
        if chosen:
            self.profile_session_entry.delete(0, "end")
            self.profile_session_entry.insert(0, chosen)
            self.save_selected_profile()

    def active_profiles(self, limit: int = 0) -> List[ChromeProfile]:
        profiles = [profile for profile in self.normalize_profiles() if profile.active]
        if limit > 0:
            return profiles[:limit]
        return profiles

    def get_max_parallel_profiles_setting(self) -> int:
        try:
            raw = self.profile_parallel_entry.get().strip() if hasattr(self, "profile_parallel_entry") else ""
            value = int(raw or "1")
        except Exception:
            value = 1
        return max(1, value)

    def open_selected_profile(self):
        self.save_selected_profile()
        profile = self.selected_profile()
        if not profile:
            messagebox.showwarning("Perfil Rminer", "Selecione um perfil para abrir.")
            return
        self.open_profiles_in_background([profile], title="Perfil Rminer")

    def open_active_profiles(self):
        self.save_selected_profile()
        profiles = self.active_profiles(limit=self.get_max_parallel_profiles_setting())
        if not profiles:
            messagebox.showwarning("Perfis Rminer", "Marque pelo menos um perfil como ativo.")
            return
        self.open_profiles_in_background(profiles, title="Perfis Rminer")

    def open_profiles_in_background(self, profiles: List[ChromeProfile], title: str = "Perfis Rminer"):
        profiles = list(profiles or [])
        if not profiles:
            return
        self.log(f"Abrindo {len(profiles)} perfil(is) em segundo plano...")

        def open_one(profile):
            try:
                existed = Path(profile.session).exists()
                ok = self.launch_chrome_debug(
                    profile,
                    int(profile.port),
                    INSTAGRAM_REELS_URL,
                    wait_ready=False,
                )
                return profile, existed, ok, None
            except Exception as exc:
                return profile, False, False, exc

        def worker():
            with ThreadPoolExecutor(max_workers=max(1, len(profiles))) as executor:
                results = list(executor.map(open_one, profiles))

            errors = [f"{profile.name}: {exc}" for profile, _existed, _ok, exc in results if exc]
            created = [profile.name for profile, existed, ok, _exc in results if ok and not existed]

            def finish():
                if errors:
                    messagebox.showerror(title, "Não consegui abrir todos os perfis:\n" + "\n".join(errors))
                if created:
                    self.log("Sessões novas criadas: " + ", ".join(created) + ". Faça login nas janelas abertas.")

            self.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def setup_persistent_fields(self):
        """Salva automaticamente os campos e o perfil escolhido sem precisar fechar o app."""
        try:
            for entry in [
                self.min_likes_entry,
                self.required_entry,
                self.recent_hours_entry,
                self.profile_save_target_entry,
                self.parallel_tabs_entry,
                self.profile_parallel_entry,
                self.download_target_entry,
                self.reel_hours_entry,
                self.download_dir_entry,
                self.grid_comment_selector_entry,
            ]:
                entry.bind("<KeyRelease>", lambda _event: self.schedule_save_config())
                entry.bind("<FocusOut>", lambda _event: self.save_config())
                entry.bind("<Return>", lambda _event: self.save_config())

            self.profile_var.trace_add("write", lambda *_args: self.profile_selected())
            if hasattr(self, "profile_list_text"):
                self.profile_list_text.bind("<KeyRelease>", lambda _event: self.schedule_save_config())
                self.profile_list_text.bind("<FocusOut>", lambda _event: self.save_config())
        except Exception:
            pass

    def schedule_save_config(self):
        try:
            self.update_info_label()
            if self._autosave_job:
                self.after_cancel(self._autosave_job)
            self._autosave_job = self.after(600, self.save_config)
        except Exception:
            self.save_config()

    def save_config(self):
        try:
            data = {
                "selected_profile_id": (self.selected_profile().id if hasattr(self, "selected_profile") and self.selected_profile() else ""),
                "chrome_profiles": [self.profile_to_dict(p) for p in getattr(self, "profiles", [])],
                "min_likes": self.min_likes_entry.get().strip() if hasattr(self, "min_likes_entry") else "1000",
                "profile_keyword_required_count": self.required_entry.get().strip() if hasattr(self, "required_entry") else "1",
                "profile_post_recent_days": self.recent_hours_entry.get().strip() if hasattr(self, "recent_hours_entry") else "1",
                "reel_download_target": self.profile_save_target_entry.get().strip() if hasattr(self, "profile_save_target_entry") else "5",
                "parallel_tabs": self.parallel_tabs_entry.get().strip() if hasattr(self, "parallel_tabs_entry") else "4",
                "max_parallel_profiles": self.profile_parallel_entry.get().strip() if hasattr(self, "profile_parallel_entry") else "1",
                "reel_required_count": self.download_target_entry.get().strip() if hasattr(self, "download_target_entry") else "1",
                "reel_recent_days": self.reel_hours_entry.get().strip() if hasattr(self, "reel_hours_entry") else "1",
                "download_dir": self.download_dir_entry.get().strip() if hasattr(self, "download_dir_entry") else str(DOWNLOADS_BASE_DIR),
                "keyword_list": self.profile_list_text.get("1.0", "end").strip() if hasattr(self, "profile_list_text") else "",
                "grid_comment_count_selector": self.grid_comment_selector_entry.get().strip() if hasattr(self, "grid_comment_selector_entry") else "",
            }

            ASSETS_DIR.mkdir(parents=True, exist_ok=True)
            with CONFIG_PATH.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            try:
                self.log(f"Não consegui salvar configurações: {e}")
            except Exception:
                pass

    def start_selector_capture(self, config_key: str, entry_widget, label: str) -> None:
        """Inicia captura manual de seletor no Chrome conectado. Sem fallback."""
        threading.Thread(
            target=self.capture_selector_from_browser,
            args=(config_key, entry_widget, label),
            daemon=True,
        ).start()

    def capture_selector_from_browser(self, config_key: str, entry_widget, label: str) -> None:
        if not PLAYWRIGHT_AVAILABLE:
            self.log("Playwright não está instalado. Não consigo capturar seletor.")
            return
        self.log(f"Captura ativa: clique no elemento de {label} dentro do Chrome aberto.")
        self.log("Importante: para contador da grade, deixe o overlay visível e clique exatamente no número/ícone de comentários.")
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{self.get_port()}")
                context = browser.contexts[0] if browser.contexts else None
                if not context or not context.pages:
                    self.log("Não encontrei página aberta no Chrome para capturar seletor.")
                    return
                page = context.pages[-1]
                page.evaluate(r'''
                    () => {
                        window.__rminerCapturedSelector = null;
                        function cssEscape(value) {
                            if (window.CSS && CSS.escape) return CSS.escape(value);
                            return String(value).replace(/[^a-zA-Z0-9_-]/g, '\\$&');
                        }
                        function isDynamicId(id) {
                            return !id || /^mount_|^react-|^radix-|^headlessui-|^:/.test(id);
                        }
                        function nthOfType(el) {
                            let i = 1;
                            let sib = el;
                            while ((sib = sib.previousElementSibling)) {
                                if (sib.tagName === el.tagName) i++;
                            }
                            return i;
                        }
                        function selectorFor(el) {
                            if (!el || el.nodeType !== 1) return '';
                            if (el.id && !isDynamicId(el.id)) return '#' + cssEscape(el.id);
                            const stableAttrs = ['data-testid', 'data-visualcompletion', 'aria-label', 'role', 'href'];
                            let parts = [];
                            let cur = el;
                            while (cur && cur.nodeType === 1 && cur !== document.documentElement && parts.length < 7) {
                                let tag = cur.tagName.toLowerCase();
                                let part = tag;
                                for (const attr of stableAttrs) {
                                    const value = cur.getAttribute && cur.getAttribute(attr);
                                    if (!value) continue;
                                    if (attr === 'href') {
                                        const m = String(value).match(/\/reel\/[^\/?#]+/i);
                                        if (m) {
                                            part = tag + '[href*="' + m[0].replace(/"/g, '\\"') + '"]';
                                            break;
                                        }
                                        continue;
                                    }
                                    if (String(value).length <= 80) {
                                        part = tag + '[' + attr + '="' + String(value).replace(/"/g, '\\"') + '"]';
                                        break;
                                    }
                                }
                                if (part === tag) part = tag + ':nth-of-type(' + nthOfType(cur) + ')';
                                parts.unshift(part);
                                const candidate = parts.join(' > ');
                                try {
                                    if (document.querySelectorAll(candidate).length === 1) return candidate;
                                } catch (_) {}
                                cur = cur.parentElement;
                            }
                            return parts.join(' > ');
                        }
                        const old = window.__rminerCaptureHandler;
                        if (old) document.removeEventListener('click', old, true);
                        window.__rminerCaptureHandler = function(ev) {
                            ev.preventDefault();
                            ev.stopPropagation();
                            const el = ev.target;
                            const selector = selectorFor(el);
                            window.__rminerCapturedSelector = {
                                selector,
                                text: (el.innerText || el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 160),
                                tag: el.tagName ? el.tagName.toLowerCase() : ''
                            };
                            document.removeEventListener('click', window.__rminerCaptureHandler, true);
                            window.__rminerCaptureHandler = null;
                        };
                        document.addEventListener('click', window.__rminerCaptureHandler, true);
                        return true;
                    }
                ''')
                deadline = time.time() + 45
                captured = None
                while time.time() < deadline and not self.stop_requested:
                    time.sleep(0.35)
                    captured = page.evaluate("window.__rminerCapturedSelector || null")
                    if captured and captured.get("selector"):
                        break
                if not captured or not captured.get("selector"):
                    self.log(f"Captura de {label} cancelada ou expirada sem clique.")
                    return
                selector = str(captured.get("selector") or "").strip()

                def apply_to_ui():
                    try:
                        entry_widget.delete(0, "end")
                        entry_widget.insert(0, selector)
                        self.config[config_key] = selector
                        self.save_config()
                        preview = str(captured.get("text") or "").strip()
                        extra = f" | texto: {preview}" if preview else ""
                        self.log(f"Seletor salvo para {label}: {selector}{extra}")
                    except Exception as e:
                        self.log(f"Não consegui aplicar seletor capturado: {e}")

                try:
                    self.after(0, apply_to_ui)
                except Exception:
                    apply_to_ui()
        except Exception as e:
            self.log(f"Erro na captura de seletor: {e}")

    def parse_localized_number_text(self, raw_text: str) -> Optional[int]:
        txt = (raw_text or "").strip().lower()
        if not txt:
            return None
        txt = txt.replace("\u00a0", " ")
        txt = txt.replace("comentários", "").replace("comentarios", "").replace("comentário", "").replace("comentario", "")
        txt = txt.replace("comments", "").replace("comment", "")
        m = re.search(r"(\d+(?:[\.,]\d+)?)\s*(mil|mi|m|k)?", txt, re.I)
        if not m:
            return None
        num = m.group(1).replace(".", "").replace(",", ".")
        try:
            value = float(num)
        except Exception:
            return None
        suffix = (m.group(2) or "").lower()
        if suffix in ("mil", "k"):
            value *= 1000
        elif suffix in ("mi", "m"):
            value *= 1000000
        return int(value)

    def on_close(self):
        self.save_config()
        if self._validated_profiles_dirty:
            self.save_validated_profiles()
        self.destroy()

    def parse_int_entry(self, entry, default: int, minimum: int = 1) -> int:
        try:
            value = int(str(entry.get()).strip())
        except Exception:
            value = default
        return max(minimum, value)

    def parse_float_entry(self, entry, default: float, minimum: float = 0.1) -> float:
        try:
            value = float(str(entry.get()).strip().replace(",", "."))
        except Exception:
            value = default
        return max(minimum, value)

    def normalize_instagram_profile(self, raw: str) -> str:
        value = (raw or "").strip()
        if not value:
            return ""
        value = value.split("#", 1)[0].strip()
        if not value:
            return ""

        match = re.search(r"instagram\.com/([^/?#\s]+)/?", value, re.I)
        if match:
            value = match.group(1)

        value = value.strip().lstrip("@").strip("/")
        if "/" in value:
            value = value.split("/", 1)[0]

        reserved = {"reel", "reels", "p", "tv", "stories", "explore", "accounts", "direct"}
        if value.lower() in reserved:
            return ""
        if not re.fullmatch(r"[A-Za-z0-9._]{1,30}", value):
            return ""
        return value



    def looks_like_instagram_internal_id(self, value: str) -> bool:
        """
        Detecta IDs internos do Instagram que podem aparecer no yt-dlp como
        channel_id/uploader_id. Esses valores sao numericos/longos e nao sao @.
        """
        value = str(value or "").strip().lstrip("@").strip()
        if not value:
            return False
        compact = re.sub(r"[^A-Za-z0-9._]", "", value)
        # IDs internos geralmente sao apenas numeros e longos.
        if re.fullmatch(r"\d{8,}", compact):
            return True
        # Alguns retornos podem vir como instagram ID com muitos digitos embutidos.
        if re.fullmatch(r"(?:ig_|instagram_)?\d{8,}", compact, flags=re.I):
            return True
        return False


    def get_keyword_list(self) -> list:
        if not hasattr(self, "profile_list_text"):
            return []
        raw = self.profile_list_text.get("1.0", "end")
        keywords = []
        seen = set()
        for line in raw.splitlines():
            value = re.sub(r"\s+", " ", (line or "").strip())
            if not value:
                continue
            key = value.casefold()
            if key in seen:
                continue
            seen.add(key)
            keywords.append(value)
        return keywords


    def log(self, message: str):
        timestamp = time.strftime("%H:%M:%S")
        text = f"[{timestamp}] {message}\n"

        def write():
            self.log_box.insert("end", text)
            self.log_box.see("end")

        self.after(0, write)

    def compact_log_reason(self, text: str, limit: int = 260) -> str:
        text = re.sub(r"\s+", " ", str(text or "")).strip()
        text = re.sub(r"See\s+https?://\S+.*$", "", text, flags=re.I).strip()
        text = re.sub(r"Otherwise,\s+if\s+.*$", "", text, flags=re.I).strip()
        if len(text) > limit:
            text = text[: limit - 3].rstrip() + "..."
        return text

    def export_ytdlp_cookies_from_page(self, page, log_prefix: str = "") -> bool:
        """
        Exporta cookies do contexto Playwright logado para Netscape cookies.txt.
        O yt-dlp costuma falhar com cookiesfrombrowser em Chrome recente; esse
        arquivo temporario evita depender da descriptografia direta do Chrome.
        """
        try:
            cookies = page.context.cookies(["https://www.instagram.com", "https://instagram.com"])
            if not cookies:
                self.log(f"{log_prefix}Nao encontrei cookies do Instagram no navegador logado.")
                return False

            lines = [
                "# Netscape HTTP Cookie File",
                "# Gerado automaticamente pelo Rminer para yt-dlp.",
            ]
            kept = 0
            for c in cookies:
                domain = str(c.get("domain") or ".instagram.com")
                if "instagram.com" not in domain:
                    continue
                include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
                path = str(c.get("path") or "/")
                secure = "TRUE" if c.get("secure") else "FALSE"
                expires_raw = c.get("expires", 0)
                try:
                    expires = int(float(expires_raw))
                    if expires < 0:
                        expires = 0
                except Exception:
                    expires = 0
                name = str(c.get("name") or "")
                value = str(c.get("value") or "")
                if not name:
                    continue
                line_domain = domain
                if c.get("httpOnly"):
                    line_domain = "#HttpOnly_" + line_domain
                lines.append("\t".join([line_domain, include_subdomains, path, secure, str(expires), name, value]))
                kept += 1

            if kept <= 0:
                self.log(f"{log_prefix}Nao encontrei cookies validos do Instagram para exportar.")
                return False

            ASSETS_DIR.mkdir(parents=True, exist_ok=True)
            AUTO_COOKIES_TXT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
            self.log(f"{log_prefix}Cookies do Instagram exportados para yt-dlp: {kept} cookie(s).")
            return True
        except Exception as e:
            self.log(f"{log_prefix}Falha ao exportar cookies para yt-dlp: {e}")
            return False

    def set_result(self, text: str, color: str = "#d8c5ff"):
        def apply():
            self.result_label.configure(text=text, text_color=color)
        self.after(0, apply)

    def set_running(self, running: bool):
        def apply():
            state = "disabled" if running else "normal"
            if hasattr(self, "run_btn"):
                self.run_btn.configure(state=state)
        self.after(0, apply)

    def request_stop(self):
        self.stop_requested = True
        self.log("Parada solicitada. Vou encerrar após a etapa atual.")
        self.set_result("PARANDO...", "#f5c542")

    def refresh_profiles(self, select_id: str = ""):
        try:
            self.profiles = self.normalize_profiles()
            values = [p.label for p in self.profiles]
            self.profile_combo.configure(values=values)

            if values:
                saved_id = select_id or str(self.config.get("selected_profile_id", ""))
                chosen = values[0]
                if saved_id:
                    for profile in self.profiles:
                        if profile.id == saved_id:
                            chosen = profile.label
                            break
                self.profile_var.set(chosen)
                self.profile_selected()
                self.log(f"{len(values)} perfil(is) Rminer carregado(s). Perfil selecionado: {chosen}")
            else:
                self.profile_var.set("")
                self.log("Nenhum perfil encontrado.")
        except Exception as e:
            self.log(f"Erro ao carregar perfis: {e}")

    def selected_profile(self) -> Optional[ChromeProfile]:
        selected = self.profile_var.get() if hasattr(self, "profile_var") else ""
        for profile in getattr(self, "profiles", []):
            if profile.label == selected:
                return profile
        return None

    def get_port(self) -> int:
        profile = self.selected_profile()
        if profile and str(profile.port).isdigit():
            return int(profile.port)
        return AUTO_CDP_PORT

    def get_wait_seconds(self) -> int:
        return AUTO_WAIT_SECONDS

    def get_session_dir(self, profile: ChromeProfile) -> Path:
        return Path(profile.session or (SESSIONS_BASE_DIR / profile.safe_session_name))

    def reset_session_if_needed(self, session_dir: Path):
        # Sessão não é mais resetada pela interface. Mantém login/cookies do perfil isolado.
        return

    def close_chrome_process(self):
        try:
            closed = 0
            for port, process in list(getattr(self, "chrome_processes", {}).items()):
                if process and process.poll() is None:
                    process.terminate()
                    closed += 1
                self.chrome_processes.pop(port, None)
            if self.chrome_process and self.chrome_process.poll() is None:
                self.chrome_process.terminate()
                closed += 1
            self.chrome_process = None
            if closed:
                self.log(f"{closed} Chrome(s) iniciado(s) pelo programa foram fechados.")
            else:
                self.log("Nenhum Chrome iniciado por este programa para fechar.")
        except Exception as e:
            self.log(f"Erro ao fechar Chrome: {e}")

    def launch_chrome_debug(
        self,
        profile: ChromeProfile,
        port: int,
        url: str = INSTAGRAM_REELS_URL,
        wait_ready: bool = True,
    ) -> Optional[Path]:
        chrome_exe = find_chrome_exe()

        if not chrome_exe:
            self.log("Chrome não encontrado.")
            return None

        SESSIONS_BASE_DIR.mkdir(parents=True, exist_ok=True)
        session_dir = self.get_session_dir(profile)

        # No fluxo automático, confirma se já existe um Chrome pronto nessa porta.
        # Na abertura manual, o clique deve apenas disparar o Chrome sem prender a UI.
        if wait_ready and wait_debug_port(port, timeout=2):
            self.log(f"Porta 127.0.0.1:{port} já está ativa. Reaproveitando Chrome aberto.")
            return session_dir

        self.reset_session_if_needed(session_dir)
        session_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            chrome_exe,
            f"--remote-debugging-port={port}",
            "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={str(session_dir)}",
            "--no-first-run",
            "--no-default-browser-check",
            url,
        ]

        self.log(f"Abrindo Chrome isolado [{profile.name}] na porta {port}...")
        self.log(f"Sessão: {session_dir}")

        popen_kwargs = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
        }
        if sys.platform.startswith("win"):
            flags = 0
            flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
            flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if flags:
                popen_kwargs["creationflags"] = flags
        else:
            popen_kwargs["start_new_session"] = True

        process = subprocess.Popen(cmd, **popen_kwargs)
        self.chrome_process = process
        self.chrome_processes[str(port)] = process

        if not wait_ready:
            return session_dir

        time.sleep(1.5)

        if process.poll() is not None:
            self.log(f"Chrome fechou cedo. Código: {process.returncode}")
            return None

        if not wait_debug_port(port, timeout=25):
            self.log(f"A porta http://127.0.0.1:{port}/json/version não respondeu.")
            self.log("Feche outro Chrome isolado usando a porta automática e tente novamente.")
            return None

        self.log("Porta CDP respondeu OK.")
        return session_dir

    def start_validation_thread(self):
        threading.Thread(target=self.run_validation, daemon=True).start()

    def _extract_owner_from_page(self, pg, url: str) -> str:
        """
        Extrai o username REAL do dono do Reel aberto.

        Correção v24:
        - Não usa mais o primeiro link de perfil da página inteira, porque isso pode pegar
          o @ da conta logada no menu/topo do Instagram.
        - Prioriza URL canônica quando ela já vem no formato /usuario/reel/...
        - Depois procura somente dentro de <main>, especialmente links/span do cabeçalho/área
          do Reel, que é onde fica o @ do dono do conteúdo.
        """
        reserved = {
            "reel", "reels", "p", "tv", "stories", "story", "explore", "accounts",
            "direct", "inbox", "notifications", "create", "developer", "about", "legal",
            "privacy", "api", "web", "challenge", "login", "logout", "instagram",
        }

        def clean_candidate(value: str) -> str:
            value = self.normalize_instagram_profile(value or "")
            if not value:
                return ""
            if value.lower() in reserved:
                return ""
            if not re.fullmatch(r"[A-Za-z0-9._]{1,30}", value):
                return ""
            return value

        # Quando o Instagram abre no formato antigo/canônico /usuario/reel/CODIGO/,
        # essa é a fonte mais confiável.
        m = re.search(r"instagram\.com/([^/?#]+)/reel/", url or "", re.I)
        if m:
            candidate = clean_candidate(m.group(1))
            if candidate:
                return candidate

        try:
            result = pg.evaluate(r"""() => {
                const reserved = new Set([
                    'reel','reels','p','tv','stories','story','explore','accounts','direct','inbox',
                    'notifications','create','developer','about','legal','privacy','api','web',
                    'challenge','login','logout','instagram'
                ]);

                function clean(value) {
                    value = String(value || '')
                        .replace(/^@+/, '')
                        .replace(/^\/+/, '')
                        .replace(/\/+$/, '')
                        .trim();
                    value = value.split(/[/?#]/)[0].trim();
                    if (!value) return '';
                    if (!/^[A-Za-z0-9._]{1,30}$/.test(value)) return '';
                    if (reserved.has(value.toLowerCase())) return '';
                    return value;
                }

                function userFromHref(href) {
                    href = String(href || '').trim();
                    if (!href) return '';
                    try {
                        if (href.startsWith('http')) {
                            const u = new URL(href);
                            if (!/instagram\.com$/i.test(u.hostname.replace(/^www\./i, ''))) return '';
                            href = u.pathname || '';
                        }
                    } catch (e) {}
                    const m = href.match(/^\/([^/?#]+)\/?$/);
                    return m ? clean(m[1]) : '';
                }

                function visible(el) {
                    if (!el) return false;
                    const s = getComputedStyle(el);
                    if (s.display === 'none' || s.visibility === 'hidden' || Number(s.opacity) === 0) return false;
                    const r = el.getBoundingClientRect();
                    return r.width > 1 && r.height > 1 && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
                }

                function shortText(el) {
                    return String((el && (el.innerText || el.textContent)) || '')
                        .replace(/^@+/, '')
                        .replace(/\s+/g, ' ')
                        .trim();
                }

                const main = document.querySelector('main') || document.body;
                const candidates = [];
                const seen = new Set();

                function add(user, score, source, el) {
                    user = clean(user);
                    if (!user || seen.has(source + ':' + user)) return;
                    seen.add(source + ':' + user);
                    let rect = null;
                    try {
                        const r = el ? el.getBoundingClientRect() : null;
                        rect = r ? { top: Math.round(r.top), left: Math.round(r.left), width: Math.round(r.width), height: Math.round(r.height) } : null;
                    } catch (e) {}
                    candidates.push({ user, score, source, rect });
                }

                // 1) Metadados: em várias telas o Instagram deixa o autor no title/meta.
                for (const sel of ['meta[property="og:title"]', 'meta[name="twitter:title"]', 'title']) {
                    const el = document.querySelector(sel);
                    const content = sel === 'title' ? (document.title || '') : (el ? el.getAttribute('content') || '' : '');
                    if (!content) continue;
                    const patterns = [
                        /(?:^|\s)@([A-Za-z0-9._]{1,30})(?:\s|$)/,
                        /([A-Za-z0-9._]{1,30})\s+(?:on|no|na)\s+Instagram/i,
                        /Instagram\s+.+?\s+([A-Za-z0-9._]{1,30})/i
                    ];
                    for (const p of patterns) {
                        const m = content.match(p);
                        if (m) add(m[1], 170, 'meta-title', el || document.body);
                    }
                }

                // 2) Prioridade máxima: links de perfil dentro do MAIN, principalmente span dentro de <a>.
                // Isso cobre o ponto que você indicou: ... main ... div:nth-child(1) > a > span.
                const ownerSpans = Array.from(main.querySelectorAll('a[href^="/"] > span, span a[href^="/"] span, header a[href^="/"] span'));
                ownerSpans.forEach((span, idx) => {
                    const a = span.closest('a[href^="/"]');
                    if (!a || !visible(a)) return;
                    const user = userFromHref(a.getAttribute('href') || '');
                    if (!user) return;
                    const txt = clean(shortText(span) || shortText(a));
                    let score = 260 - Math.min(idx, 80);
                    if (txt && txt.toLowerCase() === user.toLowerCase()) score += 90;
                    if (a.closest('header')) score += 130;
                    if (a.closest('article')) score += 70;
                    if (a.closest('main')) score += 80;
                    if (a.closest('nav, aside')) score -= 250;
                    if (a.closest('ul, li')) score -= 80; // geralmente comentário, não dono
                    const r = a.getBoundingClientRect();
                    if (r.top < innerHeight * 0.35) score += 40;
                    add(user, score, 'main-a-span', a);
                });

                // 3) Fallback controlado: todos os links de perfil dentro do MAIN, nunca na página inteira.
                const links = Array.from(main.querySelectorAll('a[href^="/"]'));
                links.forEach((a, idx) => {
                    if (!visible(a)) return;
                    const user = userFromHref(a.getAttribute('href') || '');
                    if (!user) return;
                    const txt = clean(shortText(a));
                    const r = a.getBoundingClientRect();
                    let score = 130 - Math.min(idx, 80);
                    if (txt && txt.toLowerCase() === user.toLowerCase()) score += 70;
                    if (a.closest('header')) score += 120;
                    if (a.closest('article')) score += 70;
                    if (a.querySelector('span')) score += 45;
                    if (a.closest('nav, aside')) score -= 250;
                    if (a.closest('ul, li')) score -= 80;
                    if (r.top < innerHeight * 0.35) score += 30;
                    if (r.left > innerWidth * 0.08 && r.left < innerWidth * 0.92) score += 20;
                    add(user, score, 'main-a', a);
                });

                // 4) Se o Instagram expuser algum JSON interno com owner/username, usa como fallback.
                try {
                    const bodyText = document.documentElement.innerHTML.slice(0, 900000);
                    const patterns = [
                        /"owner"\s*:\s*\{[^{}]{0,500}"username"\s*:\s*"([A-Za-z0-9._]{1,30})"/i,
                        /"owner_username"\s*:\s*"([A-Za-z0-9._]{1,30})"/i,
                        /"username"\s*:\s*"([A-Za-z0-9._]{1,30})"[^{}]{0,500}"is_verified"/i
                    ];
                    for (const p of patterns) {
                        const m = bodyText.match(p);
                        if (m) add(m[1], 90, 'html-json', document.body);
                    }
                } catch (e) {}

                candidates.sort((a, b) => (b.score - a.score));
                return candidates.length ? candidates[0] : { user: '', score: 0, source: 'none' };
            }""")

            if isinstance(result, dict):
                candidate = clean_candidate(str(result.get("user") or ""))
                return candidate
            return clean_candidate(str(result or ""))
        except Exception:
            return ""


    def make_reel_review_url(self, shortcode: str, fallback_url: str = "") -> str:
        shortcode = (shortcode or "").strip().strip("/")
        if shortcode:
            return f"https://www.instagram.com/reel/{shortcode}/"
        return fallback_url or INSTAGRAM_REELS_URL


    def close_comments_if_open(self, page):
        """Fecha painel/modal de comentários para liberar a rolagem do Reel."""
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(650)
        except Exception:
            pass

        js_close = r"""
        () => {
            const labels = ['fechar', 'close'];
            function norm(txt) {
                return (txt || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').trim();
            }
            function isVisible(el) {
                if (!el) return false;
                const style = getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
                const r = el.getBoundingClientRect();
                return r.width > 8 && r.height > 8 && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
            }
            const buttons = Array.from(document.querySelectorAll('[aria-label], button, [role="button"]'))
                .filter(isVisible)
                .filter(el => labels.some(x => norm(el.getAttribute('aria-label') || el.innerText || el.textContent).includes(x)))
                .sort((a, b) => {
                    const ar = a.getBoundingClientRect();
                    const br = b.getBoundingClientRect();
                    return (br.top + br.right) - (ar.top + ar.right);
                });
            if (buttons.length) {
                buttons[0].click();
                return true;
            }
            return false;
        }
        """
        try:
            page.evaluate(js_close)
            page.wait_for_timeout(450)
        except Exception:
            pass

    def like_current_reel_if_needed(self, page, tab_index: int = 0) -> bool:
        """
        Curte o Reel atual somente quando ele foi aprovado.
        Usa seletores robustos por aria-label e evita clicar em botões de comentários.
        """
        js_like = r"""
        () => {
            function norm(txt) {
                return (txt || '')
                    .toLowerCase()
                    .normalize('NFD')
                    .replace(/[\u0300-\u036f]/g, '')
                    .trim();
            }

            function isVisible(el) {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
                const r = el.getBoundingClientRect();
                return r.width > 6 && r.height > 6 && r.bottom > 0 && r.right > 0 && r.top < window.innerHeight && r.left < window.innerWidth;
            }

            function clickableFromSvg(svg) {
                return svg.closest('button,[role="button"]') || svg.closest('div[role="button"]') || svg.closest('span') || svg.parentElement;
            }

            function isInsideComments(el) {
                let p = el;
                for (let i = 0; i < 10 && p; i++, p = p.parentElement) {
                    const txt = norm(p.innerText || '');
                    if (txt.includes('responder') || txt.includes('ver respostas') || txt.includes('reply') || txt.includes('view replies')) {
                        return true;
                    }
                    const r = p.getBoundingClientRect && p.getBoundingClientRect();
                    if (r && r.width > window.innerWidth * 0.45 && txt.includes('coment')) {
                        return true;
                    }
                }
                return false;
            }

            // Se aparecer "Descurtir/Unlike", já está curtido. Não clica para não remover a curtida.
            const alreadyLiked = Array.from(document.querySelectorAll('svg[aria-label], [aria-label]'))
                .filter(isVisible)
                .find(el => {
                    const label = norm(el.getAttribute('aria-label') || '');
                    return ['descurtir', 'unlike', 'remove like', 'remover curtida'].some(x => label.includes(x)) && !isInsideComments(el);
                });

            if (alreadyLiked) {
                return { ok: true, clicked: false, reason: 'Reel já estava curtido.' };
            }

            const candidates = [];

            for (const el of Array.from(document.querySelectorAll('svg[aria-label], button[aria-label], [role="button"][aria-label]'))) {
                if (!isVisible(el)) continue;
                const label = norm(el.getAttribute('aria-label') || '');
                if (!label) continue;

                const isLike = label === 'curtir' || label === 'like' || label.includes('curtir') || label.includes('like');
                const isBad = label.includes('coment') || label.includes('comment') || label.includes('reply') || label.includes('responder') || label.includes('descurtir') || label.includes('unlike');
                if (!isLike || isBad) continue;
                if (isInsideComments(el)) continue;

                const clickable = el.tagName.toLowerCase() === 'svg' ? clickableFromSvg(el) : el;
                if (!clickable || !isVisible(clickable)) continue;

                const r = clickable.getBoundingClientRect();
                let score = 0;
                // Botões do Reel costumam ficar na metade direita/área central inferior.
                score += Math.max(0, r.left / Math.max(1, window.innerWidth)) * 40;
                score += Math.max(0, (window.innerHeight - r.top) / Math.max(1, window.innerHeight)) * 20;
                if (el.tagName.toLowerCase() === 'svg') score += 20;

                candidates.push({ el: clickable, score, label, top: r.top, left: r.left });
            }

            candidates.sort((a, b) => b.score - a.score);

            if (!candidates.length) {
                return { ok: false, clicked: false, reason: 'Botão Curtir/Like visível não encontrado.' };
            }

            candidates[0].el.click();
            return { ok: true, clicked: true, reason: 'Cliquei no botão de curtir.', label: candidates[0].label };
        }
        """
        try:
            result = page.evaluate(js_like)
            if isinstance(result, dict):
                reason = result.get("reason") or "sem detalhe"
                if result.get("ok") and result.get("clicked"):
                    self.log(f"Aba {tab_index}: Reel aprovado curtido ✅")
                    return True
                if result.get("ok") and not result.get("clicked"):
                    self.log(f"Aba {tab_index}: {reason}")
                    return True
                self.log(f"Aba {tab_index}: não consegui curtir o aprovado: {reason}")
                return False
        except Exception as e:
            self.log(f"Aba {tab_index}: erro ao tentar curtir aprovado: {e}")
        return False

    def _find_existing_tab_in_contexts(self, contexts, shortcode: str):
        """
        Percorre uma lista de contextos Playwright e devolve a primeira Page
        cuja URL contenha o shortcode. Loga cada aba visitada para debug.
        Retorna None se não encontrar.
        """
        for ctx in (contexts or []):
            pages = []
            try:
                pages = ctx.pages or []
            except Exception as e:
                self.log(f"[debug] erro ao listar pages do contexto: {e}")
                continue
            for pg in pages:
                try:
                    closed = pg.is_closed()
                except Exception:
                    closed = True
                if closed:
                    continue
                try:
                    url = pg.url or ""
                except Exception:
                    url = ""
                sc = self.extract_shortcode_from_url(url)
                self.log(f"[debug] aba existente: sc={sc!r} url={url[:80]!r}")
                if sc == shortcode:
                    return pg
        return None



    def click_profile_next_reel(self, page, tab_index: int = 0) -> bool:
        """Avança no painel de Reels aberto a partir do perfil, sem cair no feed aleatório."""
        before_url = ""
        try:
            before_url = page.url or ""
        except Exception:
            pass
        before_shortcode = self.extract_shortcode_from_url(before_url)

        for attempt in range(1, 4):
            try:
                viewport = page.viewport_size or {"width": 1280, "height": 720}
                x = int(viewport.get("width", 1280) * 0.42)
                y = int(viewport.get("height", 720) * 0.50)
                try:
                    page.mouse.click(x, y)
                    page.wait_for_timeout(120)
                except Exception:
                    pass

                page.keyboard.press("ArrowRight")
                page.wait_for_timeout(1800)

                after_url = page.url or ""
                after_shortcode = self.extract_shortcode_from_url(after_url)
                if after_shortcode and after_shortcode != before_shortcode:
                    self.log(
                        f"Aba {tab_index}: proximo Reel via ArrowRight "
                        f"({attempt}/3): {before_shortcode or before_url} -> {after_shortcode}"
                    )
                    return True

                self.log(
                    f"Aba {tab_index}: ArrowRight {attempt}/3 nao mudou o shortcode "
                    f"({before_shortcode or before_url})."
                )
            except Exception as e:
                self.log(f"Aba {tab_index}: ArrowRight {attempt}/3 falhou: {e}")

        self.log(f"Aba {tab_index}: nao consegui clicar no proximo Reel do perfil.")
        return False

    def collect_profile_grid_reels(self, page) -> list:
        try:
            items = page.evaluate(
                r"""
                () => {
                    function visible(el) {
                        if (!el) return false;
                        const s = getComputedStyle(el);
                        if (s.display === 'none' || s.visibility === 'hidden' || Number(s.opacity) === 0) return false;
                        const r = el.getBoundingClientRect();
                        return r.width > 30 && r.height > 30 && r.bottom > 0 && r.right > 0;
                    }
                    const seen = new Set();
                    return Array.from(document.querySelectorAll('main a[href*="/reel/"], a[href*="/reel/"]'))
                        .filter(a => /\/reel\/[^\/?#]+/i.test(a.getAttribute('href') || '') && visible(a))
                        .map(a => {
                            const href = a.href || a.getAttribute('href') || '';
                            const m = href.match(/\/reel\/([^\/?#]+)/i);
                            const r = a.getBoundingClientRect();
                            return { href, shortcode: m ? m[1] : '', top: r.top + scrollY, left: r.left + scrollX };
                        })
                        .filter(x => {
                            if (!x.shortcode || seen.has(x.shortcode)) return false;
                            seen.add(x.shortcode);
                            return true;
                        })
                        .sort((a, b) => (a.top - b.top) || (a.left - b.left));
                }
                """
            )
            return items if isinstance(items, list) else []
        except Exception as e:
            self.log(f"Falha ao ler grade de Reels do perfil: {e}")
            return []

    def read_grid_reel_comment_count(self, page, shortcode: str, tab_index: int = 0) -> dict:
        """Lê o contador de comentários da grade usando somente o seletor configurado no painel."""
        configured_selector = ""
        try:
            if hasattr(self, "grid_comment_selector_entry"):
                configured_selector = self.grid_comment_selector_entry.get().strip()
        except Exception:
            configured_selector = ""
        if not configured_selector:
            configured_selector = str(self.config.get("grid_comment_count_selector", "") or "").strip()

        if not configured_selector:
            return {"ok": False, "reason": "seletor do contador de comentarios da grade nao configurado"}

        card_selector = f'a[href*="/reel/{shortcode}/"]'
        try:
            card = page.locator(card_selector).first
            card.wait_for(state="visible", timeout=4000)
            card.scroll_into_view_if_needed(timeout=4000)
            box = card.bounding_box()
            if not box:
                return {"ok": False, "reason": f"card sem bounding box: {card_selector}"}
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            page.wait_for_timeout(900)
        except Exception as e:
            return {"ok": False, "reason": f"nao consegui posicionar mouse no card {shortcode}: {e}"}

        try:
            result = page.evaluate(
                r'''
                ({ selector, cardSelector }) => {
                    const card = document.querySelector(cardSelector);
                    if (!card) {
                        return [];
                    }

                    const cardRect = card.getBoundingClientRect();
                    const cardCenterX = cardRect.left + cardRect.width / 2;
                    const cardCenterY = cardRect.top + cardRect.height / 2;

                    function textOf(el) {
                        if (!el) return '';
                        const bits = [];
                        for (const attr of ['aria-label', 'title', 'alt']) {
                            const value = el.getAttribute && el.getAttribute(attr);
                            if (value) bits.push(value);
                        }
                        if (el.innerText) bits.push(el.innerText);
                        if (el.textContent) bits.push(el.textContent);
                        return bits.join(' ').replace(/\s+/g, ' ').trim();
                    }

                    function visible(el) {
                        if (!el) return false;
                        const s = getComputedStyle(el);
                        const r = el.getBoundingClientRect();
                        return s.display !== 'none'
                            && s.visibility !== 'hidden'
                            && Number(s.opacity) !== 0
                            && r.width > 0
                            && r.height > 0
                            && r.bottom > 0
                            && r.right > 0
                            && r.top < innerHeight
                            && r.left < innerWidth;
                    }

                    function intersectsExpandedCard(r) {
                        const padX = Math.max(40, cardRect.width * 0.18);
                        const padY = Math.max(40, cardRect.height * 0.18);
                        return !(
                            r.right < cardRect.left - padX ||
                            r.left > cardRect.right + padX ||
                            r.bottom < cardRect.top - padY ||
                            r.top > cardRect.bottom + padY
                        );
                    }

                    const els = Array.from(document.querySelectorAll(selector));
                    const candidates = [];

                    els.forEach((el, idx) => {
                        if (!visible(el)) return;
                        const r = el.getBoundingClientRect();
                        const text = textOf(el);
                        if (!text) return;

                        const cx = r.left + r.width / 2;
                        const cy = r.top + r.height / 2;
                        const dx = cx - cardCenterX;
                        const dy = cy - cardCenterY;
                        const distance = Math.sqrt(dx * dx + dy * dy);

                        let score = 0;
                        if (intersectsExpandedCard(r)) score += 10000;
                        score -= distance;

                        // Mantém um único caminho: seletor configurado. Esta pontuação só escolhe
                        // qual ocorrência do mesmo seletor pertence ao card que acabou de receber hover.
                        candidates.push({
                            idx,
                            tag: el.tagName ? el.tagName.toLowerCase() : '',
                            text,
                            visible: true,
                            score,
                            rect: {
                                top: Math.round(r.top),
                                left: Math.round(r.left),
                                width: Math.round(r.width),
                                height: Math.round(r.height)
                            },
                            cardRect: {
                                top: Math.round(cardRect.top),
                                left: Math.round(cardRect.left),
                                width: Math.round(cardRect.width),
                                height: Math.round(cardRect.height)
                            }
                        });
                    });

                    candidates.sort((a, b) => b.score - a.score);
                    return candidates.slice(0, 12);
                }
                ''',
                {"selector": configured_selector, "cardSelector": card_selector},
            )

        except Exception as e:
            return {"ok": False, "reason": f"seletor configurado invalido ou nao executou: {e}"}

        if not result:
            return {"ok": False, "reason": f"seletor configurado nao encontrou elemento: {configured_selector}"}

        for item in result:
            text_value = str(item.get("text") or "").strip()
            parsed = self.parse_localized_number_text(text_value)
            if parsed is not None:
                self.log(
                    f"Aba {tab_index}: comentarios grade {shortcode}: {parsed} "
                    f"via seletor configurado | texto='{text_value[:80]}'"
                )
                return {
                    "ok": True,
                    "count": parsed,
                    "text": text_value,
                    "source": "configured_grid_comment_selector",
                    "selector": configured_selector,
                }

        texts = [str(item.get("text") or "").strip() for item in result]
        return {"ok": False, "reason": f"seletor encontrou elemento, mas sem numero legivel: {texts[:4]}"}

    def open_grid_reel_by_shortcode(self, page, shortcode: str, tab_index: int, wait_seconds: int) -> bool:
        selector = f'a[href*="/reel/{shortcode}/"]'
        try:
            card = page.locator(selector).first
            if card.count() <= 0:
                self.log(f"Aba {tab_index}: card {shortcode} nao encontrado para abrir.")
                return False
            card.scroll_into_view_if_needed(timeout=3000)
            card.click(timeout=wait_seconds * 1000)
            page.wait_for_timeout(1800)
            self.log(f"Aba {tab_index}: Reel {shortcode} aberto a partir da grade.")
            return True
        except Exception as e:
            self.log(f"Aba {tab_index}: falha ao abrir Reel {shortcode} pela grade: {e}")
            return False

    def return_to_profile_grid(self, page, profile_url: str, tab_index: int, wait_seconds: int) -> None:
        try:
            page.goto(profile_url, wait_until="domcontentloaded", timeout=wait_seconds * 1000)
            page.wait_for_timeout(1200)
        except Exception as e:
            self.log(f"Aba {tab_index}: falha ao voltar para grade do perfil: {e}")

    def get_current_reel_like_count(self, page, tab_index: int = 0) -> dict:
        try:
            result = page.evaluate(
                r"""
                () => {
                    function norm(txt) {
                        return (txt || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/\s+/g, ' ').trim();
                    }
                    function parseLocalizedNumber(rawText) {
                        let txt = norm(rawText).replace(/curtidas?|likes?/g, '').trim();
                        if (!txt) return null;
                        const m = txt.match(/(\d+(?:[.,]\d+)*)(?:\s*)(milhao|milhoes|mil|mi|m|k)?/i);
                        if (!m) return null;
                        let numberPart = m[1];
                        const suffix = (m[2] || '').toLowerCase();
                        let value;
                        if (suffix) {
                            value = parseFloat(numberPart.replace(/\./g, '').replace(',', '.'));
                        } else if (/^\d+[.,]\d{3}$/.test(numberPart) || (numberPart.match(/[.,]/g) || []).length > 1) {
                            value = parseInt(numberPart.replace(/[.,]/g, ''), 10);
                        } else {
                            value = parseFloat(numberPart.replace(',', '.'));
                        }
                        if (!Number.isFinite(value)) return null;
                        if (['mil', 'k'].includes(suffix)) value *= 1000;
                        if (['milhao', 'milhoes', 'mi', 'm'].includes(suffix)) value *= 1000000;
                        return Math.round(value);
                    }
                    function visible(el) {
                        if (!el) return false;
                        const s = getComputedStyle(el);
                        if (s.display === 'none' || s.visibility === 'hidden' || Number(s.opacity) === 0) return false;
                        const r = el.getBoundingClientRect();
                        return r.width > 1 && r.height > 1 && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
                    }

                    const candidates = Array.from(document.querySelectorAll('span, div'))
                        .filter(visible)
                        .map(el => {
                            const text = (el.innerText || el.textContent || '').trim();
                            const count = parseLocalizedNumber(text);
                            const r = el.getBoundingClientRect();
                            let score = 0;
                            if (count === null) return null;
                            if (r.left > innerWidth * 0.55) score += 120;
                            if (r.top > innerHeight * 0.25 && r.top < innerHeight * 0.85) score += 80;
                            if (r.width < 160 && r.height < 80) score += 40;
                            if (norm(text).includes('coment')) score -= 120;
                            return { text, count, score, left: Math.round(r.left), top: Math.round(r.top) };
                        })
                        .filter(Boolean)
                        .filter(x => x.score >= 120)
                        .sort((a, b) => (b.score - a.score) || (a.top - b.top));
                    if (!candidates.length) return { ok: false, reason: 'sem candidato visual de likes' };
                    return { ok: true, count: candidates[0].count, text: candidates[0].text, candidates: candidates.slice(0, 5) };
                }
                """
            )
            if isinstance(result, dict) and result.get("ok"):
                self.log(f"Aba {tab_index}: likes visuais: {result.get('text')} => {result.get('count')}")
            return result if isinstance(result, dict) else {"ok": False, "reason": "retorno invalido"}
        except Exception as e:
            return {"ok": False, "reason": f"erro lendo likes: {e}"}

    def get_current_reel_owner(self, page, tab_index: int = 0) -> str:
        try:
            url = page.url or ""
        except Exception:
            url = ""

        owner = self._extract_owner_from_page(page, url)
        owner = self.normalize_instagram_profile(owner)
        if owner:
            self.log(f"Aba {tab_index}: dono do Reel detectado: @{owner}")
        return owner

    def count_keyword_comments(self, page, keywords: list, required_count: int, wait_seconds: int, tab_index: int = 0) -> dict:
        if not keywords:
            return {"ok": False, "matches": 0, "reason": "sem palavras-chave"}
        if not self.click_visible_comment_button(page, timeout=wait_seconds):
            return {"ok": False, "matches": 0, "reason": "nao abriu comentarios"}

        seen = set()
        details = []
        no_change = 0
        no_new_keyword_scrolls = 0
        last_keyword_count = 0
        last_seen = 0
        for round_idx in range(1, 13):
            try:
                data = page.evaluate(
                    r"""
                    (keywords) => {
                        function norm(txt) {
                            return (txt || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/\s+/g, ' ').trim();
                        }
                        const kws = keywords.map(norm).filter(Boolean);
                        function visible(el) {
                            if (!el) return false;
                            const s = getComputedStyle(el);
                            if (s.display === 'none' || s.visibility === 'hidden' || Number(s.opacity) === 0) return false;
                            const r = el.getBoundingClientRect();
                            return r.width > 1 && r.height > 1 && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
                        }
                        function scoreCommentPanel(el) {
                            if (!visible(el)) return null;
                            const r = el.getBoundingClientRect();
                            if (r.width < 300 || r.height < 260) return null;
                            const timeCount = el.querySelectorAll('time').length;
                            const profileCount = Array.from(el.querySelectorAll('a[href^="/"]'))
                                .filter(a => /^\/[^\/?#]+\/?$/.test(a.getAttribute('href') || '')).length;
                            const commentTextSpanCount = Array.from(el.querySelectorAll('span[dir="auto"]'))
                                .filter(span => !span.closest('a') && !span.closest('time'))
                                .map(span => (span.innerText || span.textContent || '').trim())
                                .filter(text => text.length > 1).length;
                            if ((timeCount < 1 || profileCount < 1) && commentTextSpanCount < 3) return null;
                            let score = 0;
                            score += timeCount * 80;
                            score += profileCount * 30;
                            score += commentTextSpanCount * 45;
                            score += Math.min(300, r.height);
                            if (r.left > innerWidth * 0.38) score += 180;
                            if (el.scrollHeight > el.clientHeight + 20) score += 260;
                            if (norm(el.innerText || '').includes('responder')) score += 80;
                            return { el, score, left: r.left };
                        }
                        const preferredPanels = Array.from(document.querySelectorAll(
                            'div.xv54qhq.xf7dkkf.xw2csxc, div.xv54qhq.xf7dkkf, div.xw2csxc.x1odjw0f'
                        ))
                            .map(scoreCommentPanel)
                            .filter(Boolean)
                            .sort((a, b) => (b.score - a.score) || (b.left - a.left));
                        const panels = (preferredPanels.length ? preferredPanels : Array.from(document.querySelectorAll('div'))
                            .map(scoreCommentPanel)
                            .filter(Boolean)
                            .sort((a, b) => (b.score - a.score) || (b.left - a.left)));
                        const panel = panels.length ? panels[0].el : document;
                        function commentBlockFromTime(timeEl) {
                            let p = timeEl;
                            for (let i = 0; i < 10 && p; i++, p = p.parentElement) {
                                const hasProfile = !!p.querySelector('a[href^="/"]');
                                const hasTime = !!p.querySelector('time');
                                const spans = Array.from(p.querySelectorAll('span'));
                                const textSpans = spans.filter(span => !span.closest('a') && !span.closest('time'));
                                const text = textSpans.map(s => (s.innerText || s.textContent || '').trim()).filter(Boolean).join(' ').trim();
                                if (hasProfile && hasTime && text.length > 0 && text.length < 700) return p;
                            }
                            return timeEl.parentElement || timeEl;
                        }
                        function extractComment(block) {
                            const userLink = Array.from(block.querySelectorAll('a[href^="/"]'))
                                .find(a => /^\/[^\/?#]+\/?$/.test(a.getAttribute('href') || ''));
                            const username = userLink ? (userLink.getAttribute('href') || '').replace(/^\/|\/$/g, '') : '';
                            const timeEl = block.querySelector('time');
                            const datetime = timeEl ? (timeEl.getAttribute('datetime') || '') : '';
                            const ageText = timeEl ? (timeEl.innerText || timeEl.textContent || '').trim() : '';
                            const spans = Array.from(block.querySelectorAll('span'))
                                .filter(span => !span.closest('a') && !span.closest('time'));
                            let text = spans
                                .map(span => (span.innerText || span.textContent || '').trim())
                                .filter(Boolean)
                                .join(' ')
                                .replace(/\s+/g, ' ')
                                .trim();

                            // Remove sobras comuns que nao fazem parte do comentario.
                            text = text
                                .replace(/\b(responder|reply|ver tradu[cç][aã]o|see translation)\b/gi, ' ')
                                .replace(/\b\d+\s*(curtida|curtidas|like|likes)\b/gi, ' ')
                                .replace(/\s+/g, ' ')
                                .trim();
                            return { username, datetime, ageText, text };
                        }
                        const commentsByTime = Array.from(panel.querySelectorAll('time'))
                            .map(t => {
                                const block = commentBlockFromTime(t);
                                const item = extractComment(block);
                                const n = norm(item.text);
                                const hit = kws.find(k => n.includes(k)) || '';
                                return { text: item.text, hit, username: item.username, datetime: item.datetime, ageText: item.ageText };
                            })
                            .filter(x => x.text);

                        const ignoredText = /^(responder|reply|ver tradu[cç][aã]o|see translation|curtir|like|mais|more)$/i;
                        const commentSpans = Array.from(panel.querySelectorAll('span[dir="auto"]'))
                            .filter(span => !span.closest('a') && !span.closest('time'))
                            .map(span => {
                                let text = (span.innerText || span.textContent || '')
                                    .replace(/\s+/g, ' ')
                                    .trim();
                                text = text
                                    .replace(/\b(responder|reply|ver tradu[cç][aã]o|see translation)\b/gi, ' ')
                                    .replace(/\b\d+\s*(curtida|curtidas|like|likes)\b/gi, ' ')
                                    .replace(/\s+/g, ' ')
                                    .trim();
                                if (!text || ignoredText.test(text)) return null;
                                const n = norm(text);
                                const hit = kws.find(k => n.includes(k)) || '';
                                return { text, hit, username: '', datetime: '', ageText: '' };
                            })
                            .filter(Boolean);

                        const seenTexts = new Set();
                        const comments = [];
                        for (const item of commentsByTime.concat(commentSpans)) {
                            const key = norm(item.text);
                            if (!key || seenTexts.has(key)) continue;
                            seenTexts.add(key);
                            comments.push(item);
                        }

                        let panelBox = null;
                        let scrolled = false;
                        if (panel && panel !== document) {
                            const scrollTargets = [panel].concat(Array.from(panel.querySelectorAll('div')))
                                .filter(el => visible(el) && el.scrollHeight > el.clientHeight + 20)
                                .sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight));
                            const target = scrollTargets[0] || panel;
                            const before = target.scrollTop;
                            target.scrollTop += Math.floor(target.clientHeight * 0.85);
                            scrolled = target.scrollTop !== before;
                            const r = target.getBoundingClientRect();
                            panelBox = {
                                x: Math.round(r.left + r.width / 2),
                                y: Math.round(r.top + r.height / 2),
                                width: Math.round(r.width),
                                height: Math.round(r.height),
                                before,
                                after: target.scrollTop,
                                scrollHeight: target.scrollHeight,
                                clientHeight: target.clientHeight,
                                className: String(target.className || '').slice(0, 180)
                            };
                        } else {
                            window.scrollBy(0, Math.floor(innerHeight * 0.5));
                        }
                        return { comments, panelBox, scrolled };
                    }
                    """,
                    keywords,
                )
            except Exception:
                data = []

            panel_box = None
            scrolled_by_js = False
            if isinstance(data, dict):
                panel_box = data.get("panelBox")
                scrolled_by_js = bool(data.get("scrolled"))
                data = data.get("comments") or []
            elif not isinstance(data, list):
                data = []

            for item in data or []:
                text = (item.get("text") or "").strip()
                if not text:
                    continue
                key = text[:300]
                if key in seen:
                    continue
                seen.add(key)
                if item.get("hit"):
                    details.append({
                        "keyword": item.get("hit"),
                        "username": item.get("username") or "",
                        "age_text": item.get("ageText") or "",
                        "datetime": item.get("datetime") or "",
                        "text": text[:180],
                    })

            self.log(f"Aba {tab_index}: comentarios com palavras-chave {len(details)}/{required_count} | rodada {round_idx}")
            if len(details) >= required_count:
                return {"ok": True, "matches": len(details), "details": details[:10]}

            if panel_box:
                self.log(
                    f"Aba {tab_index}: painel comentarios scroll "
                    f"{panel_box.get('before')}->{panel_box.get('after')} "
                    f"({panel_box.get('clientHeight')}/{panel_box.get('scrollHeight')})"
                )
                if not scrolled_by_js:
                    try:
                        page.mouse.move(int(panel_box.get("x", 900)), int(panel_box.get("y", 500)))
                        page.mouse.wheel(0, 1200)
                        page.wait_for_timeout(450)
                        self.log(f"Aba {tab_index}: forcei wheel dentro do painel de comentarios.")
                    except Exception as e:
                        self.log(f"Aba {tab_index}: falha no wheel do painel de comentarios: {e}")

            if len(seen) == last_seen:
                no_change += 1
            else:
                no_change = 0
            last_seen = len(seen)
            if len(details) == last_keyword_count:
                no_new_keyword_scrolls += 1
                if no_new_keyword_scrolls >= 4:
                    self.log(
                        f"Aba {tab_index}: 4 rolagens sem palavra-chave nova "
                        f"({len(details)}/{required_count}). Pulando Reel."
                    )
                    return {
                        "ok": False,
                        "matches": len(details),
                        "details": details[:10],
                        "reason": "4 rolagens sem palavra-chave nova",
                    }
            else:
                no_new_keyword_scrolls = 0
                last_keyword_count = len(details)
            if no_change >= 4:
                break
            try:
                page.wait_for_timeout(900)
            except Exception:
                time.sleep(0.9)

        return {"ok": False, "matches": len(details), "details": details[:10]}


    def run_fy_discovery_validation(
        self,
        profile: ChromeProfile,
        keywords: list,
        keyword_required_count: int,
        min_likes: int,
        profile_post_days: float,
        reel_required_count: int,
        reel_max_days: float,
        target_to_open: int,
        parallel_tabs: int,
        wait_seconds: int,
        port: int,
    ) -> int:
        total_downloaded = 0
        remaining = max(1, int(target_to_open))
        cycle = 0
        stalled_cycles = 0
        max_cycles = max(3, int(target_to_open) * 3)

        while not self.stop_requested and remaining > 0 and cycle < max_cycles:
            cycle += 1
            if cycle > 1:
                self.log(
                    f"Voltando para a FY ({INSTAGRAM_REELS_URL}) para continuar. "
                    f"Faltam {remaining}/{target_to_open} Reel(s); mantendo limite de data de {reel_max_days:g} dia(s)."
                )

            downloaded = self._run_fy_discovery_validation_cycle(
                profile=profile,
                keywords=keywords,
                keyword_required_count=keyword_required_count,
                min_likes=min_likes,
                profile_post_days=profile_post_days,
                reel_required_count=reel_required_count,
                reel_max_days=reel_max_days,
                target_to_open=remaining,
                parallel_tabs=parallel_tabs,
                wait_seconds=wait_seconds,
                port=port,
                cycle=cycle,
            )
            downloaded = max(0, int(downloaded or 0))
            total_downloaded += downloaded
            remaining = max(0, int(target_to_open) - total_downloaded)

            if downloaded > 0:
                stalled_cycles = 0
                self.log(f"Rodada {cycle}: {downloaded} Reel(s) baixado(s). Total: {total_downloaded}/{target_to_open}.")
            else:
                stalled_cycles += 1
                self.log(f"Rodada {cycle}: nenhum Reel novo baixado.")
                if stalled_cycles >= 2:
                    self.log("Parei depois de 2 rodadas sem baixar Reel novo. A FY nao entregou candidatos suficientes dentro das regras.")
                    break

            if remaining > 0 and not self.stop_requested:
                time.sleep(random.uniform(1.0, 2.0))

        if self.stop_requested:
            self.set_result("PARADO", "#d8c5ff")
        elif total_downloaded >= int(target_to_open):
            self.log(f"Meta concluida: {total_downloaded}/{target_to_open} Reel(s) baixado(s).")
            self.set_result(f"CONCLUIDO {total_downloaded}/{target_to_open}", "#4ade80")
        elif total_downloaded > 0:
            self.log(f"Processo terminou com {total_downloaded}/{target_to_open} Reel(s) baixado(s).")
            self.set_result(f"BAIXADOS {total_downloaded}/{target_to_open}", "#f59e0b")
        return total_downloaded

    def _run_fy_discovery_validation_cycle(
        self,
        profile: ChromeProfile,
        keywords: list,
        keyword_required_count: int,
        min_likes: int,
        profile_post_days: float,
        reel_required_count: int,
        reel_max_days: float,
        target_to_open: int,
        parallel_tabs: int,
        wait_seconds: int,
        port: int,
        cycle: int = 1,
    ) -> int:
        profile_post_max_hours = profile_post_days * 24
        self.log(
            f"Descoberta FY/perfil: likes >= {min_likes} + "
            f"{keyword_required_count} comentario(s)-chave + postado até {profile_post_days:g} dia(s)."
        )
        self.log(f"Palavras-chave: {', '.join(keywords[:12])}")

        session_dir = self.launch_chrome_debug(profile, port, INSTAGRAM_REELS_URL)
        if not session_dir:
            self.set_result("FALHOU PORTA", "#ff5f5f")
            return 0

        discovered_profiles = []
        discovered_set = set()
        checked_shortcodes = set()
        state_lock = threading.Lock()
        workers = []
        max_profiles = max(parallel_tabs * 2, target_to_open)
        max_cycles_per_worker = max(120, target_to_open * 100)
        target_reached_event = threading.Event()

        def discovered_count() -> int:
            with state_lock:
                return len(discovered_profiles)

        def should_continue() -> bool:
            if self.stop_requested or target_reached_event.is_set():
                return False
            with state_lock:
                return len(discovered_profiles) < max_profiles

        def ensure_fy_page(page, tab_index: int, reason: str = "") -> bool:
            current_url = page.url or ""
            if self.is_fy_reels_url(current_url):
                return True
            suffix = f" ({reason})" if reason else ""
            self.log(f"Aba {tab_index}: estava fora da FY{suffix}: {current_url}. Voltando para {INSTAGRAM_REELS_URL}")
            try:
                page.goto(INSTAGRAM_REELS_URL, wait_until="domcontentloaded", timeout=wait_seconds * 1000)
                page.wait_for_timeout(1800)
                if not self.is_fy_reels_url(page.url or ""):
                    self.log(f"Aba {tab_index}: ainda nao voltou para a FY. URL atual: {page.url}")
                    return False
                return True
            except Exception as e:
                self.log(f"Aba {tab_index}: falha ao voltar para a FY: {e}")
                return False

        def advance_fy_page(page, tab_index: int, steps: int = 1, reason: str = "") -> None:
            steps = max(1, int(steps))
            if reason:
                self.log(f"Aba {tab_index}: saltando {steps} Reel(s) na FY ({reason}).")
            for step in range(steps):
                if not should_continue():
                    self.log(f"Aba {tab_index}: salto interrompido; parada solicitada ou meta atingida.")
                    break
                if not ensure_fy_page(page, tab_index, reason="antes de saltar Reel da FY"):
                    break
                try:
                    before = self.extract_shortcode_from_url(page.url)
                    self.scroll_to_next_reel(page)
                    page.wait_for_timeout(random.randint(350, 750))
                    after = self.extract_shortcode_from_url(page.url)
                    if before and after and before == after:
                        page.keyboard.press("ArrowDown")
                        page.wait_for_timeout(random.randint(450, 850))
                except Exception as e:
                    self.log(f"Aba {tab_index}: falha ao saltar Reel {step + 1}/{steps}: {e}")

        def add_profile(owner: str, shortcode: str, tab_index: int, likes_count: int, matches: int) -> bool:
            owner = self.normalize_instagram_profile(owner)
            if not owner:
                return False
            key = owner.lower()
            with state_lock:
                if key in discovered_set:
                    return False
                if len(discovered_profiles) >= max_profiles:
                    return False
                discovered_set.add(key)
                discovered_profiles.append(owner)
                total = len(discovered_profiles)
                if total >= max_profiles:
                    target_reached_event.set()
            self.mark_profile_validated(
                owner,
                shortcode=shortcode,
                source_profile=profile.name,
                likes_count=likes_count,
                matches=matches,
                status="aprovado_fy",
            )
            self.log(
                f"Aba {tab_index}: perfil aprovado pela FY: @{owner} | "
                f"Reel {shortcode} | likes={likes_count} | comentarios-chave={matches} | {total}/{max_profiles}"
            )
            self.set_result(f"PERFIL {total}/{max_profiles}", "#4ade80")
            return True

        def worker_loop(tab_index: int):
            page = None
            kept_profile_page = False
            try:
                with sync_playwright() as worker_pw:
                    browser = worker_pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                    context = browser.contexts[0] if browser.contexts else browser.new_context()
                    page = context.new_page()
                    page.goto(INSTAGRAM_REELS_URL, wait_until="domcontentloaded", timeout=wait_seconds * 1000)
                    page.wait_for_timeout(2500)
                    self.export_ytdlp_cookies_from_page(page, log_prefix=f"Aba {tab_index}: ")

                    lane_stride = max(8, parallel_tabs * 6)
                    duplicate_stride = max(3, parallel_tabs + 2)
                    offset = (tab_index - 1) * lane_stride
                    if offset:
                        advance_fy_page(page, tab_index, steps=offset, reason="separar abas")
                        if not should_continue():
                            return

                    cycle = 0
                    while should_continue() and cycle < max_cycles_per_worker:
                        cycle += 1
                        if not ensure_fy_page(page, tab_index, reason="inicio do ciclo FY"):
                            break
                        shortcode = self.extract_shortcode_from_url(page.url)
                        self.log(f"Aba {tab_index}: FY ciclo {cycle} | shortcode={shortcode or 'indefinido'}")

                        if shortcode:
                            with state_lock:
                                if shortcode in checked_shortcodes:
                                    already_seen = True
                                else:
                                    checked_shortcodes.add(shortcode)
                                    already_seen = False
                            if already_seen:
                                advance_fy_page(page, tab_index, steps=duplicate_stride, reason="shortcode repetido")
                                continue

                        owner_marked_current = False
                        owner = self.get_current_reel_owner(page, tab_index=tab_index)
                        if not owner and shortcode:
                            owner = self.get_reel_owner_ytdlp(shortcode, tab_index=tab_index)
                        if owner and self.is_profile_already_validated(owner):
                            self.log(f"Aba {tab_index}: @{owner} ja foi validado antes. Pulando Reel da FY.")
                            advance_fy_page(page, tab_index, reason="perfil ja validado")
                            continue
                        if owner:
                            self.mark_profile_validated(
                                owner,
                                shortcode=shortcode,
                                source_profile=profile.name,
                                status="visto_fy",
                            )
                            owner_marked_current = True

                        like_info = self.get_current_reel_like_count(page, tab_index=tab_index)
                        if not like_info.get("ok"):
                            self.log(f"Aba {tab_index}: nao li likes: {like_info.get('reason')}. Pulando.")
                            advance_fy_page(page, tab_index, reason="sem likes")
                            continue

                        likes_count = int(like_info.get("count", 0))
                        if likes_count < min_likes:
                            self.log(f"Aba {tab_index}: likes insuficientes {likes_count}/{min_likes}. Pulando.")
                            advance_fy_page(page, tab_index, reason="likes insuficientes")
                            continue

                        if not shortcode:
                            if not self.is_fy_reels_url(page.url or ""):
                                self.log(f"Aba {tab_index}: shortcode indefinido porque saiu da FY. Corrigindo rota.")
                                ensure_fy_page(page, tab_index, reason="shortcode indefinido fora da FY")
                                continue
                            self.log(f"Aba {tab_index}: shortcode indefinido; nao consigo validar data do perfil. Pulando.")
                            advance_fy_page(page, tab_index, reason="sem shortcode/data")
                            continue

                        profile_post_info = self.get_reel_post_date_ytdlp(shortcode)
                        if not profile_post_info.get("ok"):
                            reason = self.compact_log_reason(profile_post_info.get("reason"))
                            self.log(f"Aba {tab_index}: nao consegui data do post do perfil {shortcode}: {reason}. Pulando.")
                            advance_fy_page(page, tab_index, reason="sem data do perfil")
                            continue

                        profile_age_hours = float(profile_post_info.get("age_hours", 999999))
                        profile_age_days = profile_age_hours / 24
                        if profile_age_hours > profile_post_max_hours:
                            self.log(
                                f"Aba {tab_index}: post do perfil antigo {profile_age_days:.2f}/{profile_post_days:g} dia(s). Pulando."
                            )
                            advance_fy_page(page, tab_index, reason="post do perfil antigo")
                            continue

                        self.log(
                            f"Aba {tab_index}: data do post do perfil aprovada: "
                            f"{profile_post_info.get('text') or profile_post_info.get('datetime')} | "
                            f"idade {profile_age_days:.2f} dia(s)"
                        )

                        if not owner:
                            owner = self.get_current_reel_owner(page, tab_index=tab_index)
                        keyword_result = self.count_keyword_comments(
                            page=page,
                            keywords=keywords,
                            required_count=keyword_required_count,
                            wait_seconds=wait_seconds,
                            tab_index=tab_index,
                        )
                        self.close_comments_if_open(page)

                        matches = int(keyword_result.get("matches", 0))
                        if not keyword_result.get("ok"):
                            self.log(f"Aba {tab_index}: palavras-chave insuficientes {matches}/{keyword_required_count}. Pulando.")
                            advance_fy_page(page, tab_index, reason="palavras-chave insuficientes")
                            continue

                        if not should_continue():
                            break

                        if not owner:
                            self.close_comments_if_open(page)
                            page.wait_for_timeout(500)
                            owner = self.get_current_reel_owner(page, tab_index=tab_index)
                        if not should_continue():
                            break
                        if not owner and shortcode:
                            owner = self.get_reel_owner_ytdlp(shortcode, tab_index=tab_index)
                        if not should_continue():
                            break
                        if owner and not owner_marked_current:
                            if self.is_profile_already_validated(owner):
                                self.log(f"Aba {tab_index}: @{owner} ja foi validado antes. Pulando Reel da FY.")
                                advance_fy_page(page, tab_index, reason="perfil ja validado")
                                continue
                            self.mark_profile_validated(
                                owner,
                                shortcode=shortcode,
                                source_profile=profile.name,
                                likes_count=likes_count,
                                matches=matches,
                                status="visto_fy",
                            )
                            owner_marked_current = True

                        if add_profile(owner, shortcode, tab_index, likes_count, matches):
                            if not should_continue():
                                break
                            ensure_fy_page(page, tab_index, reason=f"depois de coletar @{owner}")
                            advance_fy_page(page, tab_index, reason="perfil coletado")
                        else:
                            self.log(
                                f"Aba {tab_index}: Reel {shortcode} aprovado ({matches}/{keyword_required_count}), "
                                f"mas nao consegui identificar/salvar o @ do dono. Pulando sem contar na meta."
                            )
                            advance_fy_page(page, tab_index, reason="dono nao identificado")

            except Exception as e:
                self.log(f"Aba {tab_index}: worker FY caiu com erro: {e}")
                self.log(traceback.format_exc())
            finally:
                try:
                    if page and not page.is_closed() and not kept_profile_page:
                        page.close()
                except Exception:
                    pass
                self.log(f"Aba {tab_index}: worker FY finalizado.")

        self.log(f"Iniciando descoberta FY com {parallel_tabs} aba(s).")
        for idx in range(1, parallel_tabs + 1):
            t = threading.Thread(target=worker_loop, args=(idx,), daemon=True)
            workers.append(t)
            t.start()
        for t in workers:
            t.join()

        with state_lock:
            profiles = list(discovered_profiles)

        if not profiles:
            self.log("Descoberta FY terminou sem perfis aprovados.")
            self.set_result("NENHUM PERFIL", "#d8c5ff")
            return 0

        self.log(f"Descoberta FY rodada {cycle} encontrou {len(profiles)} perfil(is) candidato(s): {', '.join('@' + p for p in profiles)}")
        return self.run_profile_list_validation(
            profile=profile,
            profiles_to_scan=profiles,
            required_count=reel_required_count,
            comment_recent_days=profile_post_days,
            reel_max_days=reel_max_days,
            target_to_open=target_to_open,
            parallel_tabs=parallel_tabs,
            wait_seconds=wait_seconds,
            port=port,
        )

    def run_profile_list_validation(
        self,
        profile: ChromeProfile,
        profiles_to_scan: list,
        required_count: int,
        comment_recent_days: float,
        reel_max_days: float,
        target_to_open: int,
        parallel_tabs: int,
        wait_seconds: int,
        port: int,
    ) -> int:
        comment_recent_hours = comment_recent_days * 24
        reel_max_hours = reel_max_days * 24
        first_url = f"https://www.instagram.com/{profiles_to_scan[0]}/reels/" if profiles_to_scan else INSTAGRAM_REELS_URL

        grid_comment_selector = ""
        try:
            if hasattr(self, "grid_comment_selector_entry"):
                grid_comment_selector = self.grid_comment_selector_entry.get().strip()
        except Exception:
            grid_comment_selector = ""
        if not grid_comment_selector:
            grid_comment_selector = str(self.config.get("grid_comment_count_selector", "") or "").strip()
        if not grid_comment_selector:
            self.log("Validação dos Reels parada: configure o seletor do contador de comentários da grade no Painel de Seletores Fixos.")
            self.set_result("CONFIGURE SELETOR", "#f59e0b")
            return 0

        profile_worker_total = max(1, min(int(parallel_tabs), len(profiles_to_scan), max(1, int(target_to_open))))
        self.log(f"Rminer: validando {len(profiles_to_scan)} perfil(is) aprovado(s) pela FY.")
        self.log(f"Meta: baixar ate {target_to_open} Reel(s) aprovado(s) dentro dos perfis.")
        self.log(f"Abas de validação de perfil: {profile_worker_total} worker(s).")
        self.log(f"Regra por perfil: ignora ate 5 primeiros Reels antigos; depois dos recentes, para no primeiro antigo.")
        self.log(f"Validação dos Reels do perfil: postado até {reel_max_days:g} dia(s) + mínimo {required_count} comentário(s) pelo seletor configurado da grade.")

        session_dir = self.launch_chrome_debug(profile, port, first_url)
        if not session_dir:
            self.set_result("FALHOU PORTA", "#ff5f5f")
            return 0

        approved_shortcodes = set()
        checked_shortcodes = set()
        approved_items = []
        state_lock = threading.Lock()
        cursor = {"next": 0}
        workers = []

        def approved_count() -> int:
            with state_lock:
                return len(approved_shortcodes)

        def should_worker_continue() -> bool:
            if self.stop_requested:
                return False
            with state_lock:
                return len(approved_shortcodes) < target_to_open and cursor["next"] <= len(profiles_to_scan)

        def next_profile():
            with state_lock:
                if self.stop_requested or len(approved_shortcodes) >= target_to_open:
                    return None
                if cursor["next"] >= len(profiles_to_scan):
                    return None
                idx = cursor["next"]
                cursor["next"] += 1
                return idx + 1, profiles_to_scan[idx]

        def validate_profile(page, tab_index: int, list_index: int, username: str) -> None:
            profile_url = f"https://www.instagram.com/{username}/reels/"
            try:
                page.goto(profile_url, wait_until="domcontentloaded", timeout=wait_seconds * 1000)
                page.wait_for_timeout(2200)
                self.export_ytdlp_cookies_from_page(page, log_prefix=f"Aba {tab_index}: ")
            except Exception as e:
                self.log(f"Aba {tab_index}: falha ao abrir grade de @{username}: {e}")
                return

            found_recent = False
            scanned = 0
            max_reels_per_profile = 80
            profile_history = []
            grid_index = 0

            while not self.stop_requested and approved_count() < target_to_open and scanned < max_reels_per_profile:
                cards = self.collect_profile_grid_reels(page)
                while grid_index >= len(cards) and len(cards) < max_reels_per_profile:
                    try:
                        page.evaluate("window.scrollBy(0, Math.floor(window.innerHeight * 0.9))")
                        page.wait_for_timeout(900)
                    except Exception:
                        break
                    new_cards = self.collect_profile_grid_reels(page)
                    if len(new_cards) <= len(cards):
                        break
                    cards = new_cards

                if grid_index >= len(cards):
                    self.log(f"Aba {tab_index}: @{username} sem mais cards carregados na grade. Proximo perfil.")
                    break

                card = cards[grid_index]
                grid_index += 1
                shortcode = (card.get("shortcode") or "").strip()
                if not shortcode:
                    continue

                scanned += 1
                self.log(f"Aba {tab_index}: @{username} card {scanned} ({shortcode}) | perfil {list_index}/{len(profiles_to_scan)}")

                if shortcode in checked_shortcodes:
                    self.log(f"Aba {tab_index}: {shortcode} ja foi analisado. Pulando card da grade.")
                    continue

                post_info = self.get_reel_post_date_ytdlp(shortcode)
                if not post_info.get("ok"):
                    checked_shortcodes.add(shortcode)
                    reason = self.compact_log_reason(post_info.get("reason"))
                    self.log(f"Aba {tab_index}: nao consegui data por yt-dlp para {shortcode}: {reason}. Pulando.")
                    if not found_recent and scanned >= 5:
                        self.log(f"Aba {tab_index}: @{username} descartado: 5 primeiros sem data recente confirmada.")
                        break
                    continue
                else:
                    age_hours = float(post_info.get("age_hours", 999999))
                    is_recent = age_hours <= reel_max_hours
                    age_days = age_hours / 24
                    self.log(
                        f"Aba {tab_index}: data yt-dlp {shortcode}: "
                        f"{post_info.get('text') or post_info.get('datetime')} | idade {age_days:.2f} dia(s)"
                    )

                profile_history.append(shortcode)
                if len(profile_history) >= 3 and len(set(profile_history[-3:])) == 1:
                    self.log(f"Aba {tab_index}: @{username} travou no mesmo card ({shortcode}). Proximo perfil.")
                    break

                if is_recent is False:
                    if not found_recent and scanned < 5:
                        self.log(f"Aba {tab_index}: @{username} card antigo/fixado {scanned}/5. Tentando proximo.")
                        continue
                    if not found_recent:
                        self.log(f"Aba {tab_index}: @{username} descartado: nenhum Reel dentro da data nos 5 primeiros.")
                    else:
                        self.log(f"Aba {tab_index}: @{username} chegou no primeiro card antigo depois dos recentes. Proximo perfil.")
                    break

                if is_recent is True:
                    found_recent = True

                selector_total = self.read_grid_reel_comment_count(page, shortcode, tab_index=tab_index)
                if isinstance(selector_total, dict) and selector_total.get("ok"):
                    total_comments = int(selector_total.get("count", 0))
                    if total_comments < required_count:
                        checked_shortcodes.add(shortcode)
                        self.log(
                            f"Aba {tab_index}: {shortcode} pulado pela grade. "
                            f"Comentarios totais ({total_comments}) < meta ({required_count})."
                        )
                        continue
                    source_url = self.make_reel_review_url(shortcode)
                    with state_lock:
                        if len(approved_shortcodes) >= target_to_open:
                            self.log(f"Aba {tab_index}: meta ja atingida. Nao vou adicionar {shortcode}.")
                            break
                        if shortcode in approved_shortcodes:
                            checked_shortcodes.add(shortcode)
                            self.log(f"Aba {tab_index}: {shortcode} ja estava aprovado. Pulando.")
                            continue
                        approved_shortcodes.add(shortcode)
                        checked_shortcodes.add(shortcode)
                        approved_items.append({
                            "shortcode": shortcode,
                            "url": source_url,
                            "owner": username,
                            "tab_index": tab_index,
                            "recent_count": total_comments,
                            "total_seen": total_comments,
                            "source": "configured_grid_comment_selector",
                        })
                        current_count = len(approved_shortcodes)
                    self.log(
                        f"Aba {tab_index}: APROVADO pela grade: {shortcode} | "
                        f"comentarios seletor={total_comments} | {current_count}/{target_to_open}"
                    )
                    self.set_result(f"APROVADO {current_count}/{target_to_open}", "#4ade80")
                    continue
                else:
                    reason = selector_total.get("reason") if isinstance(selector_total, dict) else "sem retorno"
                    checked_shortcodes.add(shortcode)
                    self.log(f"Aba {tab_index}: nao li comentarios pelo seletor configurado de {shortcode}: {reason}. Pulando sem abrir.")
                    continue

            if scanned >= max_reels_per_profile:
                self.log(f"Aba {tab_index}: @{username} atingiu limite interno de {max_reels_per_profile} Reels. Proximo perfil.")

        def worker_loop(tab_index: int):
            page = None
            try:
                with sync_playwright() as worker_pw:
                    browser = worker_pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                    context = browser.contexts[0] if browser.contexts else browser.new_context()
                    self.log(f"Aba {tab_index}: worker de perfis iniciado.")

                    while not self.stop_requested and approved_count() < target_to_open:
                        item = next_profile()
                        if not item:
                            break
                        list_index, username = item

                        if page is None or page.is_closed():
                            page = context.new_page()

                        validate_profile(page, tab_index, list_index, username)

            except Exception as e:
                self.log(f"Aba {tab_index}: worker de perfis caiu com erro: {e}")
                self.log(traceback.format_exc())
            finally:
                try:
                    if page and not page.is_closed():
                        page.close()
                except Exception:
                    pass
                self.log(f"Aba {tab_index}: worker de perfis finalizado.")

        for idx in range(1, profile_worker_total + 1):
            t = threading.Thread(target=worker_loop, args=(idx,), daemon=True)
            workers.append(t)
            t.start()

        for t in workers:
            t.join()

        with state_lock:
            final_items = list(approved_items)[:target_to_open]

        if final_items:
            self.log(f"✅ Validação dos perfis finalizada: {len(final_items)} Reel(s) aprovado(s). Iniciando download automático.")
            downloaded = 0
            for idx, item in enumerate(final_items, start=1):
                if self.stop_requested:
                    break
                shortcode = item.get("shortcode", "")
                try:
                    ok = self.download_approved_reel(shortcode, item)
                except Exception as e:
                    ok = False
                    self.log(f"Erro ao baixar {shortcode}: {e}")

                if ok:
                    downloaded += 1
                    self.log(f"✅ Download OK: {shortcode} ({downloaded}/{len(final_items)})")
                    self.set_result(f"BAIXADO {downloaded}/{len(final_items)}", "#4ade80")
                else:
                    self.log(f"❌ Falha no download: {shortcode}")

                if idx < len(final_items) and not self.stop_requested:
                    time.sleep(random.uniform(1.5, 3.0))

            if downloaded:
                self.log(f"Download automático concluído: {downloaded}/{len(final_items)} Reel(s).")
                self.set_result(f"BAIXADOS {downloaded}/{len(final_items)}", "#4ade80")
            elif self.stop_requested:
                self.log("Download automático interrompido pelo usuário.")
                self.set_result("PARADO", "#d8c5ff")
            else:
                self.log("Nenhum dos Reels aprovados conseguiu ser baixado.")
                self.set_result("FALHOU DOWNLOAD", "#ff5f5f")
            return downloaded
        elif self.stop_requested:
            self.log("Validacao interrompida pelo usuario antes de encontrar Reels aprovados.")
            self.set_result("PARADO", "#d8c5ff")
        else:
            self.log("Rminer 2 terminou a lista sem separar Reels.")
            self.set_result("NENHUM", "#d8c5ff")
        return 0

    def run_validation(self):
        self.set_running(True)
        self.stop_requested = False
        self.review_pages = []
        self.set_result("VALIDANDO...", "#f5c542")

        if not PLAYWRIGHT_AVAILABLE:
            self.log("Playwright n?o instalado. Rode: py -m pip install playwright")
            self.set_result("SEM PLAYWRIGHT", "#ff5f5f")
            self.set_running(False)
            return

        try:
            min_likes = self.parse_int_entry(self.min_likes_entry, default=1000, minimum=0)
            profile_keyword_required_count = self.parse_int_entry(self.required_entry, default=1, minimum=1)
            profile_post_days = self.parse_float_entry(self.recent_hours_entry, default=1.0, minimum=0.1)
            reel_download_target = self.parse_int_entry(self.profile_save_target_entry, default=5, minimum=1)

            reel_required_count = self.parse_int_entry(self.download_target_entry, default=1, minimum=0)
            reel_max_days = self.parse_float_entry(self.reel_hours_entry, default=1.0, minimum=0.1)

            parallel_tabs = self.parse_int_entry(self.parallel_tabs_entry, default=4, minimum=1)
            parallel_tabs = max(1, min(8, parallel_tabs))
            max_parallel_profiles = self.get_max_parallel_profiles_setting()
            wait_seconds = self.get_wait_seconds()

            self.save_selected_profile()
            self.save_config()
            keywords = self.get_keyword_list()
            if not keywords:
                self.log("Preencha a lista de palavras-chave nos comentários antes de executar.")
                self.set_result("SEM PALAVRAS", "#ff5f5f")
                return

            profiles = self.active_profiles(limit=min(max_parallel_profiles, reel_download_target))
            if not profiles:
                self.log("Marque pelo menos um perfil Rminer como ativo.")
                self.set_result("SEM PERFIL", "#ff5f5f")
                return

            profile_count = len(profiles)
            base_quota = reel_download_target // profile_count
            remainder = reel_download_target % profile_count
            quotas = [base_quota + (1 if idx < remainder else 0) for idx in range(profile_count)]
            quotas = [quota for quota in quotas if quota > 0]
            profiles = profiles[:len(quotas)]
            workers_per_profile = parallel_tabs

            self.log(f"Meta de reels para baixar: {reel_download_target}.")
            self.log(
                f"Perfis simultâneos em uso: {len(profiles)}/{max_parallel_profiles} | "
                f"abas por perfil: {workers_per_profile}."
            )
            for profile, quota in zip(profiles, quotas):
                self.log(f"- {profile.name} | porta {profile.port} | meta {quota} Reel(s)")
            self.log(
                f"Validação do perfil: likes >= {min_likes}; "
                f"comentários-chave >= {profile_keyword_required_count}; "
                f"postado at? {profile_post_days:g} dia(s)."
            )
            self.log(
                f"Validação dos Reels do perfil: comentários >= {reel_required_count}; "
                f"postado at? {reel_max_days:g} dia(s)."
            )

            def run_profile(profile, quota):
                self.run_fy_discovery_validation(
                    profile=profile,
                    keywords=keywords,
                    keyword_required_count=profile_keyword_required_count,
                    min_likes=min_likes,
                    profile_post_days=profile_post_days,
                    reel_required_count=reel_required_count,
                    reel_max_days=reel_max_days,
                    target_to_open=quota,
                    parallel_tabs=workers_per_profile,
                    wait_seconds=wait_seconds,
                    port=int(profile.port),
                )

            if len(profiles) == 1:
                run_profile(profiles[0], quotas[0])
            else:
                threads = []
                for profile, quota in zip(profiles, quotas):
                    thread = threading.Thread(target=run_profile, args=(profile, quota), daemon=True)
                    threads.append(thread)
                    thread.start()
                for thread in threads:
                    thread.join()
        except Exception as e:
            self.log(f"Erro geral na validação: {e}")
            self.log(traceback.format_exc())
            self.set_result("ERRO", "#ff5f5f")
        finally:
            self.set_running(False)


    def extract_shortcode_from_url(self, url: str) -> str:
        match = re.search(r"/(?:reel|reels|p)/([^/?#]+)/?", url or "", re.I)
        return match.group(1) if match else ""

    def is_fy_reels_url(self, url: str) -> bool:
        return bool(re.match(r"^https?://(?:www\.)?instagram\.com/reels(?:/|$|[?#])", url or "", re.I))

    def download_approved_reel(self, shortcode: str, post_info: dict) -> bool:
        """Baixa o Reel aprovado usando yt-dlp."""
        shortcode = (shortcode or post_info.get("shortcode") or "").strip()

        if not shortcode:
            self.log("N?o consegui baixar: shortcode vazio.")
            return False

        if not ensure_yt_dlp_installed(log_fn=self.log):
            self.log("yt-dlp n?o est? dispon?vel e n?o foi poss?vel instalar automaticamente.")
            return False

        import yt_dlp

        url = post_info.get("url") or self.make_reel_review_url(shortcode)
        auto_cookies_txt = AUTO_COOKIES_TXT_PATH if AUTO_COOKIES_TXT_PATH.exists() else None
        manual_cookies_txt = COOKIES_TXT_PATH if COOKIES_TXT_PATH.exists() else None
        cookies_txt = auto_cookies_txt or manual_cookies_txt

        if auto_cookies_txt:
            self.log(f"yt-dlp vai usar cookies autom?ticos de: {auto_cookies_txt}")
        elif manual_cookies_txt:
            self.log(f"yt-dlp vai usar cookies manuais de: {manual_cookies_txt}")
        else:
            self.log(
                f"Aviso: {COOKIES_TXT_PATH.name} n?o encontrado. "
                "Tentando sem autenticação (só funciona para posts públicos)."
            )

        try:
            downloads_dir = self.get_downloads_dir()
            downloads_dir.mkdir(parents=True, exist_ok=True)
            outtmpl = str(downloads_dir / "%(upload_date)s_UTC_%(id)s.%(ext)s")

            ydl_opts = {
                "outtmpl": outtmpl,
                "format": "mp4/bestvideo+bestaudio/best",
                "merge_output_format": "mp4",
                "quiet": True,
                "no_warnings": True,
                "noprogress": True,
                "retries": 3,
            }
            if cookies_txt:
                ydl_opts["cookiefile"] = str(cookies_txt)

            self.log(f"Baixando Reel aprovado via yt-dlp: {shortcode}")
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
            except Exception as e:
                if "cookiefile" in ydl_opts:
                    self.log(f"Aviso: falha lendo cookies ({e}). Tentando sem cookies...")
                    ydl_opts_no_cookies = dict(ydl_opts)
                    ydl_opts_no_cookies.pop("cookiefile", None)
                    with yt_dlp.YoutubeDL(ydl_opts_no_cookies) as ydl:
                        info = ydl.extract_info(url, download=True)
                else:
                    raise

            owner = ""
            try:
                owner = (info or {}).get("uploader") or (info or {}).get("channel") or ""
            except Exception:
                owner = ""

            if owner:
                self.log(f"Download: @{owner}")

            self.log(f"DOWNLOAD OK ? Pasta: {downloads_dir.resolve()}")
            return True

        except Exception as e:
            self.log(f"Falha ao baixar Reel {shortcode}: {type(e).__name__}: {e}")
            return False

    def get_reel_post_date_ytdlp(self, shortcode: str) -> dict:
        shortcode = (shortcode or "").strip()
        if not shortcode:
            return {"ok": False, "reason": "Shortcode vazio."}

        if not ensure_yt_dlp_installed(log_fn=self.log):
            return {"ok": False, "reason": "yt-dlp indisponivel.", "shortcode": shortcode}

        import yt_dlp

        url = self.make_reel_review_url(shortcode)
        auto_cookies_txt = AUTO_COOKIES_TXT_PATH if AUTO_COOKIES_TXT_PATH.exists() else None
        manual_cookies_txt = COOKIES_TXT_PATH if COOKIES_TXT_PATH.exists() else None
        base_opts = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "skip_download": True,
            "extract_flat": False,
            "retries": 2,
        }

        try:
            attempts = []
            if auto_cookies_txt:
                opts = dict(base_opts)
                opts["cookiefile"] = str(auto_cookies_txt)
                attempts.append(("cookies-auto", opts))
            if manual_cookies_txt and manual_cookies_txt != auto_cookies_txt:
                opts = dict(base_opts)
                opts["cookiefile"] = str(manual_cookies_txt)
                attempts.append(("cookies-manual", opts))
            attempts.append(("sem-cookies", dict(base_opts)))

            info = None
            errors = []
            used_method = ""
            for method, opts in attempts:
                try:
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        info = ydl.extract_info(url, download=False)
                    used_method = method
                    break
                except Exception as e:
                    errors.append(f"{method}: {type(e).__name__}: {e}")

            if info is None:
                return {
                    "ok": False,
                    "reason": self.compact_log_reason(" | ".join(errors), limit=360),
                    "shortcode": shortcode,
                }

            timestamp = (info or {}).get("timestamp") or (info or {}).get("release_timestamp")
            upload_date = (info or {}).get("upload_date")

            dt_utc = None
            source = ""
            if timestamp:
                dt_utc = datetime.fromtimestamp(float(timestamp), tz=timezone.utc)
                source = "yt-dlp:timestamp"
            elif upload_date and re.fullmatch(r"\d{8}", str(upload_date)):
                dt_utc = datetime.strptime(str(upload_date), "%Y%m%d").replace(tzinfo=timezone.utc)
                source = "yt-dlp:upload_date"

            if not dt_utc:
                return {
                    "ok": False,
                    "reason": "yt-dlp nao retornou timestamp/upload_date.",
                    "shortcode": shortcode,
                }

            now_utc = datetime.now(timezone.utc)
            age_hours = max(0, (now_utc - dt_utc).total_seconds() / 3600)
            return {
                "ok": True,
                "shortcode": shortcode,
                "datetime": dt_utc.isoformat().replace("+00:00", "Z"),
                "text": dt_utc.strftime("%Y-%m-%d %H:%M UTC"),
                "age_hours": age_hours,
                "source": f"{source}/{used_method}",
                "title": (info or {}).get("title") or "",
            }
        except Exception as e:
            return {
                "ok": False,
                "reason": f"yt-dlp metadata falhou: {type(e).__name__}: {e}",
                "shortcode": shortcode,
            }

    def get_reel_owner_ytdlp(self, shortcode: str, tab_index: int = 0) -> str:
        shortcode = (shortcode or "").strip()
        if not shortcode:
            return ""
        if not ensure_yt_dlp_installed(log_fn=self.log):
            return ""

        import yt_dlp

        url = self.make_reel_review_url(shortcode)
        auto_cookies_txt = AUTO_COOKIES_TXT_PATH if AUTO_COOKIES_TXT_PATH.exists() else None
        manual_cookies_txt = COOKIES_TXT_PATH if COOKIES_TXT_PATH.exists() else None
        base_opts = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "skip_download": True,
            "extract_flat": False,
            "retries": 2,
        }

        attempts = []
        if auto_cookies_txt:
            opts = dict(base_opts)
            opts["cookiefile"] = str(auto_cookies_txt)
            attempts.append(("cookies-auto", opts))
        if manual_cookies_txt and manual_cookies_txt != auto_cookies_txt:
            opts = dict(base_opts)
            opts["cookiefile"] = str(manual_cookies_txt)
            attempts.append(("cookies-manual", opts))
        attempts.append(("sem-cookies", dict(base_opts)))

        errors = []
        for method, opts in attempts:
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
            except Exception as e:
                errors.append(f"{method}: {type(e).__name__}: {e}")
                continue

            candidates = []

            def add_candidate(value, source: str) -> None:
                value = str(value or "").strip()
                if not value:
                    return
                candidates.append((value, source))
                for match in re.findall(r"@([A-Za-z0-9._]{1,30})", value):
                    candidates.append((match, f"{source}:mention"))

            if isinstance(info, dict):
                # Campos sem "_id" costumam ser nome/username. Campos *_id podem
                # ser o ID interno numerico do Instagram e nao devem virar @.
                add_candidate(info.get("uploader"), "uploader")
                add_candidate(info.get("channel"), "channel")
                add_candidate(info.get("creator"), "creator")
                add_candidate(info.get("title"), "title")
                add_candidate(info.get("description"), "description")
                add_candidate(info.get("webpage_url"), "webpage_url")
                add_candidate(info.get("uploader_id"), "uploader_id")
                add_candidate(info.get("channel_id"), "channel_id")

            seen_candidates = set()
            for candidate, source in candidates:
                owner = self.normalize_instagram_profile(candidate)
                if not owner or owner.lower() in seen_candidates:
                    continue
                seen_candidates.add(owner.lower())
                if self.looks_like_instagram_internal_id(owner):
                    self.log(f"Aba {tab_index}: ignorei ID interno do Instagram vindo de {source}: {owner}")
                    continue
                if source.endswith("_id") and re.fullmatch(r"\d+", owner):
                    self.log(f"Aba {tab_index}: ignorei campo {source} numerico: {owner}")
                    continue
                self.log(f"Aba {tab_index}: dono do Reel via yt-dlp: @{owner} ({method}/{source})")
                return owner

        if errors:
            self.log(f"Aba {tab_index}: yt-dlp nao conseguiu identificar dono: {self.compact_log_reason(' | '.join(errors), limit=260)}")
        return ""


    def get_reel_post_date_instaloader(self, shortcode: str) -> dict:
        """
        Fonte principal da data do Reel.

        Usa Instaloader para consultar o post pelo shortcode e retorna date_utc.
        Não baixa mídia nem comentários.
        """
        shortcode = (shortcode or "").strip()

        if not shortcode:
            return {"ok": False, "reason": "Shortcode vazio."}

        try:
            import instaloader
            from instaloader import Post
        except Exception as e:
            return {
                "ok": False,
                "reason": f"Instaloader não instalado/importado: {e}",
                "shortcode": shortcode,
            }

        try:
            loader = instaloader.Instaloader(
                download_pictures=False,
                download_videos=False,
                download_video_thumbnails=False,
                download_geotags=False,
                download_comments=False,
                save_metadata=False,
                compress_json=False,
                quiet=True,
            )

            # Tenta carregar sessão salva do Instaloader para evitar o bloqueio 401.
            # Para salvar uma sessão: abra um terminal e rode:
            #   instaloader --login SEU_USUARIO
            # (isso salva em %APPDATA%\instaloader\session-SEU_USUARIO no Windows)
            _session_loaded = False
            try:
                _ig_session_dir = Path(os.environ.get("APPDATA", Path.home())) / "instaloader"
                if not _ig_session_dir.exists():
                    _ig_session_dir = Path.home() / ".config" / "instaloader"
                if _ig_session_dir.exists():
                    _session_files = sorted(_ig_session_dir.glob("session-*"))
                    if _session_files:
                        _session_file = _session_files[0]
                        _username = _session_file.stem.replace("session-", "")
                        loader.load_session_from_file(_username, str(_session_file))
                        _session_loaded = True
                        self.log(f"Sessão Instaloader carregada: @{_username}")
            except Exception as _e:
                self.log(f"Sessão Instaloader não encontrada/carregável ({_e}). Tentando sem login (pode falhar com 401).")

            post = Post.from_shortcode(loader.context, shortcode)

            dt = getattr(post, "date_utc", None)
            if dt is None:
                return {
                    "ok": False,
                    "reason": "Instaloader retornou post sem date_utc.",
                    "shortcode": shortcode,
                }

            if dt.tzinfo is None:
                dt_utc = dt.replace(tzinfo=timezone.utc)
            else:
                dt_utc = dt.astimezone(timezone.utc)

            now_utc = datetime.now(timezone.utc)
            age_hours = max(0.0, (now_utc - dt_utc).total_seconds() / 3600)

            owner = ""
            caption_preview = ""

            try:
                owner = getattr(post, "owner_username", "") or ""
            except Exception:
                owner = ""

            try:
                caption_preview = (getattr(post, "caption", "") or "").replace("\\n", " ").strip()[:140]
            except Exception:
                caption_preview = ""

            try:
                local_text = str(getattr(post, "date_local", dt_utc))
            except Exception:
                local_text = str(dt_utc)

            return {
                "ok": True,
                "source": "instaloader:Post.from_shortcode",
                "shortcode": shortcode,
                "datetime": dt_utc.isoformat().replace("+00:00", "Z"),
                "text": local_text,
                "raw": str(dt),
                "age_hours": age_hours,
                "owner": owner,
                "caption_preview": caption_preview,
            }

        except Exception as e:
            return {
                "ok": False,
                "reason": f"Instaloader falhou: {type(e).__name__}: {e}",
                "shortcode": shortcode,
            }


    def get_reel_post_date(self, page, timeout: int = 20) -> dict:
        """
        Descobre a data de postagem do Reel atual.

        v17:
        - A URL do Reels muda, mas o shortcode atual pode não aparecer no HTML/script.
        - Então a fonte principal agora é o vídeo visível atual:
          pegamos video.currentSrc/src/poster e tentamos casar com o cache do feed.
        - Quando encontra o objeto de mídia correspondente, pega taken_at/taken_at_timestamp.
        """

        try:
            current_url = page.url
        except Exception:
            current_url = ""

        shortcode_match = re.search(r"/reels?/([^/?#]+)/?", current_url, re.I)
        shortcode = shortcode_match.group(1) if shortcode_match else ""

        js = r"""
        async () => {
            function safeText(el) {
                return ((el && (el.innerText || el.textContent)) || '').trim();
            }

            function currentShortcode() {
                const m = location.pathname.match(/\/reels?\/([^\/?#]+)/i);
                return m ? m[1] : '';
            }

            function parseDateValue(value) {
                if (value === null || value === undefined) return null;

                if (typeof value === 'number') {
                    const ms = value < 100000000000 ? value * 1000 : value;
                    const d = new Date(ms);
                    return isNaN(d.getTime()) ? null : d;
                }

                const txt = String(value).trim();
                if (!txt) return null;

                if (/^\d{10}$/.test(txt)) {
                    const d = new Date(Number(txt) * 1000);
                    return isNaN(d.getTime()) ? null : d;
                }

                if (/^\d{13}$/.test(txt)) {
                    const d = new Date(Number(txt));
                    return isNaN(d.getTime()) ? null : d;
                }

                const d = new Date(txt);
                return isNaN(d.getTime()) ? null : d;
            }

            function makeResult(source, value, text, extra = {}) {
                const d = parseDateValue(value);
                if (!d) return null;

                const ageHours = Math.max(0, (Date.now() - d.getTime()) / 36e5);

                if (ageHours > 24 * 365 * 10) return null;
                if (d.getTime() > Date.now() + 10 * 60 * 1000) return null;

                return {
                    ok: true,
                    source,
                    datetime: d.toISOString(),
                    raw: String(value),
                    text: text || String(value),
                    title: extra.title || '',
                    age_hours: ageHours,
                    score: extra.score || 0,
                    extra
                };
            }

            function getCookie(name) {
                const m = document.cookie.match(new RegExp('(?:^|; )' + name.replace(/[.$?*|{}()[\]\\/+^]/g, '\\$&') + '=([^;]*)'));
                return m ? decodeURIComponent(m[1]) : '';
            }

            function findDateFields(obj, path = '', out = [], depth = 0) {
                if (!obj || depth > 9) return out;

                if (Array.isArray(obj)) {
                    for (let i = 0; i < obj.length; i++) {
                        findDateFields(obj[i], path + '[' + i + ']', out, depth + 1);
                    }
                    return out;
                }

                if (typeof obj !== 'object') return out;

                for (const [key, value] of Object.entries(obj)) {
                    const lower = key.toLowerCase();
                    const nextPath = path ? path + '.' + key : key;

                    if (
                        lower === 'taken_at' ||
                        lower === 'taken_at_timestamp' ||
                        lower === 'datepublished' ||
                        lower === 'uploaddate' ||
                        lower === 'datecreated' ||
                        lower === 'created_time' ||
                        lower === 'publish_time'
                    ) {
                        out.push({key, value, path: nextPath});
                    }

                    if (value && typeof value === 'object') {
                        findDateFields(value, nextPath, out, depth + 1);
                    }
                }

                return out;
            }

            function isVisible(el) {
                if (!el) return false;
                const s = getComputedStyle(el);
                if (s.display === 'none' || s.visibility === 'hidden' || Number(s.opacity) === 0) return false;
                const r = el.getBoundingClientRect();
                return r.width > 40 && r.height > 40 && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
            }

            function normUrl(s) {
                return String(s || '')
                    .replace(/\\u0026/g, '&')
                    .replace(/\\u002F/g, '/')
                    .replace(/\\\//g, '/')
                    .replace(/&amp;/g, '&');
            }

            function stripQuery(s) {
                return normUrl(s).split('?')[0].split('#')[0];
            }

            function urlTokens(url) {
                const out = new Set();
                const raw = normUrl(url);
                const noQuery = stripQuery(raw);

                if (!raw) return [];

                out.add(raw);
                out.add(noQuery);

                try {
                    const u = new URL(raw, location.href);
                    out.add(u.pathname);
                    const parts = u.pathname.split('/').filter(Boolean);

                    for (const p of parts) {
                        const clean = decodeURIComponent(p).trim();
                        if (clean.length >= 10) out.add(clean);
                    }

                    const file = parts[parts.length - 1] || '';
                    if (file.length >= 10) {
                        out.add(file);
                        out.add(file.split('.')[0]);
                    }
                } catch (e) {}

                // Tokens longos do CDN costumam ser únicos.
                const matches = raw.match(/[A-Za-z0-9_-]{18,}/g) || [];
                for (const m of matches) out.add(m);

                return Array.from(out)
                    .map(x => x.trim())
                    .filter(x => x.length >= 10)
                    .sort((a, b) => b.length - a.length)
                    .slice(0, 40);
            }

            function collectStrings(obj, out = [], depth = 0) {
                if (!obj || depth > 8) return out;

                if (typeof obj === 'string') {
                    if (
                        obj.length >= 15 &&
                        (
                            obj.includes('http') ||
                            obj.includes('instagram') ||
                            obj.includes('cdn') ||
                            obj.includes('.mp4') ||
                            obj.includes('.jpg') ||
                            obj.includes('video') ||
                            obj.includes('scontent')
                        )
                    ) {
                        out.push(obj);
                    }
                    return out;
                }

                if (Array.isArray(obj)) {
                    for (const v of obj) collectStrings(v, out, depth + 1);
                    return out;
                }

                if (typeof obj === 'object') {
                    for (const v of Object.values(obj)) collectStrings(v, out, depth + 1);
                }

                return out;
            }

            function collectMediaObjects(obj, out = [], path = '', depth = 0) {
                if (!obj || depth > 10) return out;

                if (Array.isArray(obj)) {
                    for (let i = 0; i < obj.length; i++) {
                        collectMediaObjects(obj[i], out, path + '[' + i + ']', depth + 1);
                    }
                    return out;
                }

                if (typeof obj !== 'object') return out;

                const hasMediaShape =
                    obj.code ||
                    obj.shortcode ||
                    obj.taken_at ||
                    obj.taken_at_timestamp ||
                    obj.video_versions ||
                    obj.image_versions2 ||
                    obj.clips_metadata ||
                    obj.media_type ||
                    obj.product_type === 'clips';

                if (hasMediaShape) {
                    const fields = findDateFields(obj);
                    const strings = collectStrings(obj, [], 0);

                    out.push({
                        obj,
                        path,
                        code: obj.code || obj.shortcode || '',
                        id: obj.id || obj.pk || '',
                        dateFields: fields,
                        strings: strings.slice(0, 120)
                    });
                }

                for (const [key, value] of Object.entries(obj)) {
                    if (value && typeof value === 'object') {
                        collectMediaObjects(value, out, path ? path + '.' + key : key, depth + 1);
                    }
                }

                return out;
            }

            function visibleVideos() {
                return Array.from(document.querySelectorAll('video'))
                    .map((v, i) => {
                        const r = v.getBoundingClientRect();
                        const src = v.currentSrc || v.src || v.getAttribute('src') || '';
                        const poster = v.poster || v.getAttribute('poster') || '';

                        let score = 0;
                        if (isVisible(v)) score += 300;
                        if (!v.paused) score += 80;
                        if (v.readyState >= 2) score += 50;
                        if (src) score += 60;

                        const area = Math.max(0, r.width) * Math.max(0, r.height);
                        score += Math.min(150, area / 5000);

                        const cx = r.left + r.width / 2;
                        const cy = r.top + r.height / 2;
                        const dist = Math.abs(cx - innerWidth * 0.52) + Math.abs(cy - innerHeight * 0.52);
                        score += Math.max(0, 120 - dist / 8);

                        return {
                            i,
                            src,
                            currentSrc: v.currentSrc || '',
                            poster,
                            paused: v.paused,
                            readyState: v.readyState,
                            rect: {x: r.x, y: r.y, w: r.width, h: r.height},
                            visible: isVisible(v),
                            score,
                            tokens: [...urlTokens(src), ...urlTokens(poster)].slice(0, 60)
                        };
                    })
                    .filter(v => v.src || v.currentSrc || v.poster)
                    .sort((a, b) => b.score - a.score);
            }

            function matchMediaToVideo(media, video) {
                if (!video || !video.tokens || !video.tokens.length) return 0;

                const strings = media.strings || [];
                const mediaText = strings.map(normUrl).join('\n');

                let score = 0;

                for (const token of video.tokens) {
                    if (!token || token.length < 10) continue;

                    if (mediaText.includes(token)) {
                        score += Math.min(250, token.length * 4);
                    }
                }

                // Se o script não tem o src completo, às vezes tem só o arquivo CDN.
                for (const s of strings) {
                    const ns = normUrl(s);
                    if (!ns) continue;

                    for (const token of video.tokens.slice(0, 20)) {
                        if (token.length >= 18 && ns.includes(token)) {
                            score += Math.min(160, token.length * 3);
                        }
                    }
                }

                return score;
            }

            const shortcode = currentShortcode();
            const candidates = [];
            const api_debug = [];
            const videos = visibleVideos();
            const bestVideo = videos[0] || null;

            // 1) Parse dos scripts JSON do próprio feed.
            // O Instagram coloca o cache do Reels em scripts JSON com xdt_api__v1__clips__home__connection_v2.
            const scriptTexts = Array.from(document.scripts)
                .map((s, i) => ({i, text: s.textContent || ''}))
                .filter(x => x.text && (x.text.includes('xdt_api__v1__clips') || x.text.includes('taken_at') || x.text.includes('video_versions') || x.text.includes('ScheduledServerJS')));

            const mediaObjects = [];
            const script_parse_debug = [];

            for (const s of scriptTexts) {
                const variants = [
                    {name: 'raw', text: s.text},
                    {name: 'unescaped', text: s.text.replace(/\\u0022/g, '"').replace(/\\"/g, '"').replace(/\\\//g, '/')}
                ];

                for (const variant of variants) {
                    try {
                        const data = JSON.parse(variant.text);
                        const found = collectMediaObjects(data, [], 'script[' + s.i + ']', 0);
                        if (found.length) {
                            script_parse_debug.push({script: s.i, variant: variant.name, media_count: found.length});
                            for (const m of found) {
                                m.script = s.i;
                                m.variant = variant.name;
                                mediaObjects.push(m);
                            }
                        }
                    } catch (e) {}
                }
            }

            // 2) Casamento principal: vídeo visível atual -> objeto de mídia no cache -> taken_at.
            if (bestVideo && mediaObjects.length) {
                const mediaMatches = [];

                for (const media of mediaObjects) {
                    const matchScore = matchMediaToVideo(media, bestVideo);
                    if (matchScore <= 0) continue;

                    const fields = media.dateFields || [];

                    for (const f of fields) {
                        const result = makeResult('visible_video_cache:' + media.path + ':' + f.path, f.value, String(f.value), {
                            score: 2500 + matchScore,
                            shortcode,
                            media_code: media.code || '',
                            media_id: media.id || '',
                            match_score: matchScore,
                            video_src: bestVideo.src,
                            video_poster: bestVideo.poster,
                            field_path: f.path,
                            script: media.script,
                            variant: media.variant
                        });

                        if (result) {
                            candidates.push(result);
                            mediaMatches.push({
                                code: media.code,
                                id: media.id,
                                path: media.path,
                                match_score: matchScore,
                                field_path: f.path,
                                value: f.value
                            });
                        }
                    }
                }

                mediaMatches.sort((a, b) => b.match_score - a.match_score);
            }

            // 3) Fallback por texto bruto: procura tokens do vídeo visível dentro dos scripts e pega taken_at perto.
            const rawScriptText = scriptTexts.map(x => x.text).join('\n').slice(0, 14000000);
            const normalizedRawScript = rawScriptText
                .replace(/\\u0022/g, '"')
                .replace(/\\"/g, '"')
                .replace(/\\u002F/g, '/')
                .replace(/\\\//g, '/')
                .replace(/&quot;/g, '"')
                .replace(/&amp;/g, '&');

            const visible_token_debug = [];

            if (bestVideo) {
                for (const token of bestVideo.tokens.slice(0, 30)) {
                    if (!token || token.length < 12) continue;

                    const idx = normalizedRawScript.indexOf(token);
                    if (idx < 0) continue;

                    const context = normalizedRawScript.slice(Math.max(0, idx - 20000), Math.min(normalizedRawScript.length, idx + 20000));

                    const re = /["']?(taken_at|taken_at_timestamp|datePublished|uploadDate|created_time|publish_time)["']?\s*:\s*["']?([^"',} ]{4,40})["']?/gi;
                    let m;
                    let guard = 0;

                    while ((m = re.exec(context)) && guard < 80) {
                        guard++;
                        const key = m[1];
                        const value = m[2];

                        const result = makeResult('visible_video_text_match:' + key, value, value, {
                            score: 2100 + Math.min(300, token.length * 4),
                            shortcode,
                            token,
                            token_length: token.length,
                            video_src: bestVideo.src,
                            video_poster: bestVideo.poster,
                            snippet: context.slice(Math.max(0, m.index - 250), Math.min(context.length, m.index + 250))
                        });

                        if (result) candidates.push(result);
                    }

                    visible_token_debug.push({
                        token: token.slice(0, 80),
                        token_len: token.length,
                        found_at: idx
                    });

                    if (visible_token_debug.length >= 6) break;
                }
            }

            // 4) API como fallback. Em muitos casos retorna 404/not-logged-in, mas mantemos.
            if (shortcode) {
                const csrf = getCookie('csrftoken');

                const urls = [
                    `/api/v1/media/shortcode/${shortcode}/info/`,
                    `/reel/${shortcode}/?__a=1&__d=dis`,
                    `/reels/${shortcode}/?__a=1&__d=dis`,
                    `/p/${shortcode}/?__a=1&__d=dis`
                ];

                for (const url of urls) {
                    try {
                        const resp = await fetch(url, {
                            method: 'GET',
                            credentials: 'include',
                            cache: 'no-store',
                            headers: {
                                'accept': 'application/json,text/html,*/*',
                                'x-ig-app-id': '936619743392459',
                                'x-asbd-id': '129477',
                                'x-requested-with': 'XMLHttpRequest',
                                ...(csrf ? {'x-csrftoken': csrf} : {})
                            }
                        });

                        const contentType = resp.headers.get('content-type') || '';
                        const txt = await resp.text();

                        api_debug.push({
                            url,
                            status: resp.status,
                            ok: resp.ok,
                            content_type: contentType,
                            text_start: txt.slice(0, 120).replace(/\s+/g, ' ')
                        });

                        if (!resp.ok) continue;

                        let data = null;
                        try {
                            data = JSON.parse(txt);
                        } catch (e) {
                            continue;
                        }

                        const fields = findDateFields(data);

                        for (const f of fields) {
                            const result = makeResult('api:' + url + ':' + f.path, f.value, String(f.value), {
                                score: 1000,
                                shortcode,
                                api_url: url,
                                field_path: f.path,
                                field_key: f.key
                            });

                            if (result) candidates.push(result);
                        }

                    } catch (e) {
                        api_debug.push({url, error: String(e)});
                    }
                }
            }

            // 5) DOM time como fallback.
            function isInsideDialog(el) {
                return !!(el && el.closest && el.closest('[role="dialog"]'));
            }

            const timeEls = Array.from(document.querySelectorAll('time[datetime]'));

            for (const el of timeEls) {
                const dt = el.getAttribute('datetime');
                const r = el.getBoundingClientRect();
                let score = 120;

                if (el.closest('main')) score += 20;
                if (el.closest('article')) score += 15;
                if (isInsideDialog(el)) score -= 80;
                if (r.width > 0 && r.height > 0) score += 10;
                if (r.top > 0 && r.top < window.innerHeight) score += 8;
                if (r.left > window.innerWidth * 0.15) score += 5;

                const text = safeText(el) || el.getAttribute('title') || dt;

                const result = makeResult('dom:time[datetime]', dt, text, {
                    score,
                    title: el.getAttribute('title') || '',
                    rect: {x: r.x, y: r.y, w: r.width, h: r.height},
                    html: el.outerHTML.slice(0, 300),
                    inside_dialog: isInsideDialog(el),
                    shortcode
                });

                if (result) candidates.push(result);
            }

            // 6) Último fallback: script perto do shortcode atual.
            // Não usa script global sem shortcode, pois isso já pegou data errada antes.
            const scriptVariants = [
                {name: 'raw', text: rawScriptText},
                {name: 'normalized', text: normalizedRawScript}
            ];

            const regexes = [
                {source: 'script:datePublished', re: /["']datePublished["']\s*:\s*["']([^"']+)["']/g, score: 230},
                {source: 'script:uploadDate', re: /["']uploadDate["']\s*:\s*["']([^"']+)["']/g, score: 230},
                {source: 'script:taken_at_timestamp', re: /["']taken_at_timestamp["']\s*:\s*["']?(\d{10,13})["']?/g, score: 240},
                {source: 'script:taken_at_flex', re: /(?:^|[^a-zA-Z0-9_])["']?taken_at["']?\s*:\s*["']?(\d{10,13})["']?/g, score: 240},
                {source: 'script:taken_at_escaped_raw', re: /\\?["']taken_at\\?["']\s*:\s*\\?["']?(\d{10,13})\\?["']?/g, score: 240}
            ];

            const stale_script_candidates = [];

            if (shortcode) {
                for (const variant of scriptVariants) {
                    const txt = variant.text;

                    for (const item of regexes) {
                        item.re.lastIndex = 0;
                        let m;
                        let guard = 0;

                        while ((m = item.re.exec(txt)) && guard < 120) {
                            guard++;

                            const value = m[1];
                            const idx = Math.max(0, m.index);
                            const context = txt.slice(Math.max(0, idx - 5000), Math.min(txt.length, idx + 5000));
                            const hasShortcode = context.includes(shortcode);

                            const parsed = makeResult(
                                item.source + ':' + variant.name,
                                value,
                                value,
                                {
                                    score: item.score + (hasShortcode ? 500 : 0),
                                    variant: variant.name,
                                    shortcode,
                                    context_has_shortcode: hasShortcode,
                                    snippet: context.slice(0, 500)
                                }
                            );

                            if (!parsed) continue;

                            if (hasShortcode) {
                                candidates.push(parsed);
                            } else {
                                stale_script_candidates.push({
                                    source: parsed.source,
                                    datetime: parsed.datetime,
                                    raw: parsed.raw,
                                    age_hours: parsed.age_hours,
                                    score: parsed.score
                                });
                            }
                        }
                    }
                }
            }

            const map = new Map();

            for (const c of candidates) {
                const key = c.datetime + '|' + c.source;
                const old = map.get(key);
                if (!old || c.score > old.score) map.set(key, c);
            }

            const unique = Array.from(map.values());
            unique.sort((a, b) => b.score - a.score || a.age_hours - b.age_hours);

            if (unique.length) {
                const best = unique[0];

                best.candidates = unique.length;
                best.shortcode = shortcode;
                best.api_debug = api_debug.slice(0, 6);
                best.stale_script_candidates = stale_script_candidates.slice(0, 5);
                best.visible_video = bestVideo ? {
                    src: bestVideo.src,
                    poster: bestVideo.poster,
                    paused: bestVideo.paused,
                    readyState: bestVideo.readyState,
                    score: bestVideo.score,
                    rect: bestVideo.rect,
                    token_sample: bestVideo.tokens.slice(0, 8)
                } : null;
                best.videos_count = videos.length;
                best.script_parse_debug = script_parse_debug.slice(0, 8);
                best.media_objects_count = mediaObjects.length;
                best.visible_token_debug = visible_token_debug.slice(0, 8);
                best.debug_candidates = unique.slice(0, 8).map(x => ({
                    source: x.source,
                    datetime: x.datetime,
                    text: x.text,
                    age_hours: x.age_hours,
                    score: x.score,
                    raw: x.raw,
                    variant: x.extra && x.extra.variant,
                    context_has_shortcode: x.extra && x.extra.context_has_shortcode,
                    field_path: x.extra && x.extra.field_path,
                    media_code: x.extra && x.extra.media_code,
                    match_score: x.extra && x.extra.match_score,
                    token_length: x.extra && x.extra.token_length
                }));

                return best;
            }

            return {
                ok: false,
                reason: 'Nenhuma data confiável encontrada para o vídeo/Reel atual.',
                url: location.href,
                shortcode,
                api_debug,
                stale_script_candidates: stale_script_candidates.slice(0, 8),
                visible_video: bestVideo ? {
                    src: bestVideo.src,
                    poster: bestVideo.poster,
                    paused: bestVideo.paused,
                    readyState: bestVideo.readyState,
                    score: bestVideo.score,
                    rect: bestVideo.rect,
                    token_sample: bestVideo.tokens.slice(0, 8)
                } : null,
                videos_count: videos.length,
                script_parse_debug: script_parse_debug.slice(0, 8),
                media_objects_count: mediaObjects.length,
                visible_token_debug: visible_token_debug.slice(0, 8),
                body_has_shortcode: shortcode ? document.body.innerHTML.includes(shortcode) : false,
                body_has_date: /taken_at|datePublished|uploadDate|created_time|publish_time/i.test(document.body.innerHTML),
                time_count: document.querySelectorAll('time').length,
                time_datetime_count: document.querySelectorAll('time[datetime]').length,
                times_sample: Array.from(document.querySelectorAll('time')).slice(0, 20).map(el => ({
                    text: safeText(el),
                    datetime: el.getAttribute('datetime'),
                    title: el.getAttribute('title'),
                    html: el.outerHTML.slice(0, 400)
                }))
            };
        }
        """

        end_time = time.time() + timeout
        last = None

        while time.time() < end_time and not self.stop_requested:
            try:
                result = page.evaluate(js)
                last = result

                if isinstance(result, dict) and result.get("ok"):
                    shortcode = result.get("shortcode") or ""
                    if shortcode:
                        self.log(f"Shortcode da URL: {shortcode}")

                    vv = result.get("visible_video") or {}
                    if vv:
                        src = (vv.get("src") or "")[:120]
                        self.log(f"Vídeo visível usado na data: score={vv.get('score')} | src={src}")

                    spd = result.get("script_parse_debug") or []
                    if spd:
                        compact = []
                        for x in spd[:3]:
                            compact.append(f"script {x.get('script')}:{x.get('variant')} mídias={x.get('media_count')}")
                        self.log("Cache de mídia lido: " + " | ".join(compact))

                    api_debug = result.get("api_debug") or []
                    if api_debug:
                        compact = []
                        for x in api_debug[:3]:
                            compact.append(f"{x.get('status', '?')} {x.get('url', '')}")
                        self.log("Tentativas API data: " + " | ".join(compact))

                    debug = result.get("debug_candidates") or []
                    if debug:
                        self.log("Candidatos de data confiáveis:")
                        for item in debug[:5]:
                            h = float(item.get('age_hours', 0))
                            marker = ""
                            if item.get("media_code"):
                                marker += f" | code={item.get('media_code')}"
                            if item.get("match_score"):
                                marker += f" | match={item.get('match_score')}"
                            if item.get("context_has_shortcode"):
                                marker += " | shortcode OK"
                            if item.get("field_path"):
                                marker += f" | {item.get('field_path')}"
                            self.log(f"- {item.get('source')} | {item.get('datetime')} | idade {h/24:.2f} dia(s) / {h:.2f}h | score {item.get('score')}{marker}")

                    stale = result.get("stale_script_candidates") or []
                    if stale:
                        self.log(f"Ignorados {len(stale)} taken_at de scripts sem shortcode/vídeo atual para evitar data antiga.")

                    return result

            except Exception as e:
                last = {"ok": False, "reason": str(e)}

            time.sleep(0.8)

        # ── Fallback de último recurso: stale_script_candidates ──────────────
        # Quando o shortcode do Reel atual não aparece nos scripts (comum após
        # navegar vários Reels seguidos), o Instagram já carregou o próximo Reel
        # no cache mas ainda não injetou o shortcode no HTML/script.
        # O candidato com MENOR age_hours dos stale é muito provavelmente o
        # Reel atual ou o imediatamente anterior — aceitamos com tolerância.
        if isinstance(last, dict) and not last.get("ok"):
            stale = last.get("stale_script_candidates") or []
            if stale:
                # Ordena pelo mais recente (menor age_hours)
                stale_sorted = sorted(stale, key=lambda x: float(x.get("age_hours", 9999)))
                best_stale = stale_sorted[0]
                age_h = float(best_stale.get("age_hours", 9999))
                dt_str = best_stale.get("datetime", "")
                raw_val = best_stale.get("raw", "")
                self.log(
                    f"Usando stale mais recente como fallback: {dt_str} | "                    f"idade {age_h/24:.2f} dia(s) / {age_h:.2f}h | raw={raw_val}"
                )
                return {
                    "ok": True,
                    "source": "stale_script_fallback",
                    "shortcode": last.get("shortcode", ""),
                    "datetime": dt_str,
                    "text": dt_str,
                    "raw": raw_val,
                    "age_hours": age_h,
                    "stale_fallback": True,
                }

        if isinstance(last, dict):
            self.log(f"Diagnóstico data: {json.dumps(last, ensure_ascii=False)[:4000]}")
            return last

        return {"ok": False, "reason": "Timeout lendo data do Reel."}



    def scroll_to_next_reel(self, page):
        """
        Rolagem reforçada para o próximo Reels.

        O Instagram às vezes não responde apenas ao mouse.wheel().
        Então tentamos, em ordem:
        - fechar modal/popups com Escape;
        - focar a página;
        - mouse wheel no centro da tela;
        - tecla PageDown;
        - tecla ArrowDown;
        - JS: wheel event + scrollBy + tentativa em containers scrolláveis.
        """

        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
        except Exception:
            pass

        before_url = ""
        try:
            before_url = page.url
        except Exception:
            pass

        self.log(f"Tentando rolar para o próximo Reel. URL antes: {before_url}")

        js_scroll = r"""
        () => {
            const beforeUrl = location.href;

            function isVisible(el) {
                if (!el) return false;
                const s = getComputedStyle(el);
                if (s.display === 'none' || s.visibility === 'hidden' || Number(s.opacity) === 0) return false;
                const r = el.getBoundingClientRect();
                return r.width > 40 && r.height > 40 && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
            }

            // 1) Dispara wheel event, que o Reels costuma escutar.
            const centerX = Math.floor(innerWidth / 2);
            const centerY = Math.floor(innerHeight / 2);
            const target = document.elementFromPoint(centerX, centerY) || document.body;

            const wheel = new WheelEvent('wheel', {
                deltaY: Math.floor(innerHeight * 1.2),
                deltaX: 0,
                bubbles: true,
                cancelable: true,
                view: window
            });

            target.dispatchEvent(wheel);
            document.dispatchEvent(wheel);
            window.dispatchEvent(wheel);

            // 2) Tenta rolar janela.
            window.scrollBy(0, Math.floor(innerHeight * 0.95));

            // 3) Tenta rolar containers grandes/scrolláveis do Instagram.
            const containers = Array.from(document.querySelectorAll('main, section, div'))
                .filter(el => {
                    if (!isVisible(el)) return false;
                    if (el.scrollHeight <= el.clientHeight + 50) return false;
                    const r = el.getBoundingClientRect();

                    // Prioriza área principal, não menu lateral.
                    if (r.right < innerWidth * 0.35) return false;
                    return true;
                })
                .map(el => {
                    const r = el.getBoundingClientRect();
                    let score = 0;
                    score += Math.min(100, el.scrollHeight - el.clientHeight);
                    score += Math.min(80, r.height);
                    if (el.querySelector('video')) score += 200;
                    if ((el.innerText || '').toLowerCase().includes('coment')) score -= 40;
                    return {el, score, before: el.scrollTop};
                })
                .sort((a, b) => b.score - a.score)
                .slice(0, 6);

            const moved = [];

            for (const item of containers) {
                try {
                    item.el.scrollTop = item.el.scrollTop + Math.floor(innerHeight * 0.95);
                    moved.push({
                        before: item.before,
                        after: item.el.scrollTop,
                        score: item.score
                    });
                } catch (e) {}
            }

            // 4) Simula teclas em JS também.
            try {
                document.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowDown', code: 'ArrowDown', bubbles: true}));
                document.dispatchEvent(new KeyboardEvent('keydown', {key: 'PageDown', code: 'PageDown', bubbles: true}));
            } catch (e) {}

            return {
                beforeUrl,
                afterUrl: location.href,
                scrollY: window.scrollY,
                moved,
                targetTag: target && target.tagName
            };
        }
        """

        def url_changed() -> bool:
            try:
                after = page.url
                return bool(before_url and after and after != before_url)
            except Exception:
                return False

        # Até 5 ciclos internos. Não é limite de validação; é só para uma rolagem ficar robusta.
        for attempt in range(1, 6):
            try:
                viewport = page.viewport_size or {"width": 1280, "height": 720}
                x = int(viewport.get("width", 1280) * 0.55)
                y = int(viewport.get("height", 720) * 0.55)

                # Foca na área do Reels.
                page.mouse.move(x, y)
                page.wait_for_timeout(100)

                # Wheel forte.
                page.mouse.wheel(0, 1400)
                page.wait_for_timeout(1200)

                if url_changed():
                    break

                # Teclas reais.
                page.keyboard.press("PageDown")
                page.wait_for_timeout(900)

                if url_changed():
                    break

                page.keyboard.press("ArrowDown")
                page.wait_for_timeout(900)

                if url_changed():
                    break

                # JS fallback.
                result = page.evaluate(js_scroll)
                self.log(f"Rolagem reforçada {attempt}/5: {result}")
                page.wait_for_timeout(1300)

                if url_changed():
                    break

            except Exception as e:
                self.log(f"Falha em tentativa de rolagem {attempt}/5: {e}")
                try:
                    page.keyboard.press("ArrowDown")
                    page.wait_for_timeout(1200)
                except Exception:
                    pass

        try:
            after_url = page.url
            if after_url != before_url:
                self.log(f"Rolagem OK: {before_url} -> {after_url}")
            else:
                self.log(f"A URL não mudou após rolagem. Talvez o Reels esteja travado, a página sem foco ou o feed não carregou próximo item. URL atual: {after_url}")
        except Exception:
            self.log("Tentei rolar para carregar o próximo Reel.")

    def get_reel_comment_total_before_open(self, page) -> dict:
        """
        Lê o total de comentários exibido no Reel antes de abrir o painel.
        Se conseguir ler e o total for menor que a meta, o fluxo pula o vídeo sem gastar tempo rolando comentários.
        """
        js_total = r"""
        () => {
            function norm(txt) {
                return (txt || '')
                    .toLowerCase()
                    .normalize('NFD')
                    .replace(/[\u0300-\u036f]/g, '')
                    .replace(/\s+/g, ' ')
                    .trim();
            }

            function isVisible(el) {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
                const r = el.getBoundingClientRect();
                if (r.width < 1 || r.height < 1) return false;
                if (r.bottom <= 0 || r.right <= 0) return false;
                if (r.top >= window.innerHeight || r.left >= window.innerWidth) return false;
                return true;
            }

            function center(el) {
                const r = el.getBoundingClientRect();
                return { x: r.left + r.width / 2, y: r.top + r.height / 2, rect: r };
            }

            function parseLocalizedNumber(rawText) {
                let txt = norm(rawText)
                    .replace(/comentarios?/g, '')
                    .replace(/comments?/g, '')
                    .replace(/visualizacoes?/g, '')
                    .replace(/views?/g, '')
                    .replace(/curtidas?/g, '')
                    .replace(/likes?/g, '')
                    .trim();

                if (!txt) return null;

                // Exemplos aceitos: 123, 1.234, 1,234, 1,2 mil, 2 mil, 12k, 1.5m, 1 mi.
                const m = txt.match(/(\d+(?:[.,]\d+)*)(?:\s*)(milhao|milhoes|mil|mi|m|k)?/i);
                if (!m) return null;

                let numberPart = m[1];
                const suffix = (m[2] || '').toLowerCase();
                let value;

                if (suffix) {
                    // Com sufixo, separador é decimal: 1,2 mil / 1.2k.
                    value = parseFloat(numberPart.replace(/\./g, '').replace(',', '.'));
                    if (numberPart.includes('.') && numberPart.includes(',')) {
                        value = parseFloat(numberPart.replace(/\./g, '').replace(',', '.'));
                    } else if (numberPart.includes('.') && !numberPart.includes(',')) {
                        const parts = numberPart.split('.');
                        value = parts.length === 2 && parts[1].length <= 2
                            ? parseFloat(numberPart)
                            : parseFloat(numberPart.replace(/\./g, ''));
                    }

                    if (!isFinite(value)) return null;
                    if (suffix === 'k' || suffix === 'mil') value *= 1000;
                    else if (suffix === 'm' || suffix === 'mi' || suffix.startsWith('milh')) value *= 1000000;
                    return Math.round(value);
                }

                // Sem sufixo: trata . ou , como milhares quando há 3 dígitos no final.
                const hasDot = numberPart.includes('.');
                const hasComma = numberPart.includes(',');

                if (hasDot || hasComma) {
                    const sep = hasDot ? '.' : ',';
                    const parts = numberPart.split(sep);
                    const last = parts[parts.length - 1];
                    if (last.length === 3) {
                        value = parseInt(parts.join(''), 10);
                    } else {
                        value = parseFloat(numberPart.replace(',', '.'));
                    }
                } else {
                    value = parseInt(numberPart, 10);
                }

                if (!isFinite(value)) return null;
                return Math.round(value);
            }

            function getClickable(el) {
                return (
                    el.closest('button') ||
                    el.closest('[role="button"]') ||
                    el.closest('a') ||
                    el.closest('[tabindex="0"]') ||
                    el.parentElement ||
                    el
                );
            }

            function isCommentElement(el) {
                const label = norm(el.getAttribute('aria-label') || el.innerText || el.textContent || '');
                return label.includes('coment') || label.includes('comment');
            }

            const specificSelectors = [
                // Versão flexível do seletor enviado pelo usuário: troca o ID dinâmico #mount_0_0_* por [id^="mount_"].
                '[id^="mount_"] section main > div > div:nth-child(1) > div > div > div > div:nth-child(2) span > span',
                '[id^="mount_"] section main span > span'
            ];

            const commentButtons = Array.from(document.querySelectorAll('[aria-label], svg[aria-label]'))
                .filter(el => isVisible(el) && isCommentElement(el))
                .map(el => {
                    const clickable = getClickable(el);
                    const target = isVisible(clickable) ? clickable : el;
                    const c = center(target);
                    return { el, target, x: c.x, y: c.y, rect: c.rect };
                });

            function scoreCandidate(el, source) {
                if (!el || !isVisible(el)) return null;
                const text = (el.innerText || el.textContent || '').trim();
                if (!text || text.length > 40) return null;

                const count = parseLocalizedNumber(text);
                if (count === null || count < 0) return null;

                const c = center(el);
                let score = 0;

                // O contador de comentários costuma ficar no lado direito/centro do Reel, perto do botão de comentário.
                if (c.x > window.innerWidth * 0.28) score += 8;
                if (c.y > window.innerHeight * 0.10 && c.y < window.innerHeight * 0.94) score += 6;
                if (/^\s*[\d.,]+\s*(milhao|milhoes|mil|mi|m|k)?\s*$/i.test(norm(text))) score += 12;
                if (source === 'selector') score += 14;

                let bestDistance = Infinity;
                for (const btn of commentButtons) {
                    const dx = Math.abs(c.x - btn.x);
                    const dy = Math.abs(c.y - btn.y);
                    const distance = Math.sqrt(dx * dx + dy * dy);
                    bestDistance = Math.min(bestDistance, distance);

                    // Normalmente o número fica logo abaixo/ao lado do ícone de comentário.
                    if (dx < 90 && dy < 110) score += 35;
                    if (dx < 120 && c.y >= btn.y - 15 && c.y <= btn.y + 140) score += 20;
                }

                // Evita pegar contadores muito longe do botão de comentário quando existem botões detectados.
                if (commentButtons.length && bestDistance > 260 && source !== 'selector') score -= 30;

                // Penaliza textos que claramente pertencem a likes/visualizações.
                const nearText = norm((el.parentElement?.innerText || '') + ' ' + (el.closest('div')?.innerText || ''));
                if (nearText.includes('curtida') || nearText.includes('like')) score -= 20;
                if (nearText.includes('visualizacao') || nearText.includes('view')) score -= 20;

                return { ok: true, count, text, source, score, x: c.x, y: c.y };
            }

            const candidates = [];
            const seen = new Set();

            for (const selector of specificSelectors) {
                for (const el of Array.from(document.querySelectorAll(selector))) {
                    if (seen.has(el)) continue;
                    seen.add(el);
                    const item = scoreCandidate(el, 'selector');
                    if (item) candidates.push(item);
                }
            }

            // Fallback geral: procura spans curtos e numéricos dentro do main.
            const root = document.querySelector('main') || document.body;
            for (const el of Array.from(root.querySelectorAll('span, div'))) {
                if (seen.has(el)) continue;
                seen.add(el);
                const item = scoreCandidate(el, 'visual-fallback');
                if (item) candidates.push(item);
            }

            candidates.sort((a, b) => b.score - a.score);

            if (!candidates.length) {
                return {
                    ok: false,
                    reason: 'Não encontrei contador numérico de comentários visível.',
                    comment_buttons: commentButtons.length
                };
            }

            const best = candidates[0];
            return {
                ok: true,
                count: best.count,
                text: best.text,
                source: best.source,
                score: best.score,
                candidates: candidates.slice(0, 8).map(x => ({ text: x.text, count: x.count, source: x.source, score: x.score, x: Math.round(x.x), y: Math.round(x.y) })),
                comment_buttons: commentButtons.length
            };
        }
        """
        try:
            result = page.evaluate(js_total)
            if isinstance(result, dict):
                return result
        except Exception as e:
            return {"ok": False, "reason": str(e)}
        return {"ok": False, "reason": "retorno inválido"}


    def click_visible_comment_button(self, page, timeout: int = 30) -> bool:
        """
        Procura o botão de comentários apenas no Reel atual.
        O loop principal decide rolar para o próximo Reel quando não encontrar.
        """

        js_find_and_click = r"""
        () => {
            const labels = ['comentar', 'comentario', 'comentarios', 'comentários', 'comment', 'comments'];

            function norm(txt) {
                return (txt || '')
                    .toLowerCase()
                    .normalize('NFD')
                    .replace(/[\u0300-\u036f]/g, '');
            }

            function hasCommentLabel(el) {
                const label = norm(el.getAttribute('aria-label'));
                return labels.some(x => label.includes(norm(x)));
            }

            function getClickable(el) {
                return (
                    el.closest('button') ||
                    el.closest('[role="button"]') ||
                    el.closest('a') ||
                    el.closest('[tabindex="0"]') ||
                    el.parentElement ||
                    el
                );
            }

            function isReallyVisible(el) {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) {
                    return false;
                }
                const r = el.getBoundingClientRect();
                if (r.width < 8 || r.height < 8) return false;
                if (r.bottom <= 0 || r.right <= 0) return false;
                if (r.top >= window.innerHeight || r.left >= window.innerWidth) return false;
                return true;
            }

            function centerOf(el) {
                const r = el.getBoundingClientRect();
                return { x: r.left + r.width / 2, y: r.top + r.height / 2, rect: r };
            }

            const rawByLabel = Array.from(document.querySelectorAll('[aria-label], svg[aria-label]'))
                .filter(hasCommentLabel);

            const unique = [];
            const seen = new Set();

            for (const el of rawByLabel) {
                const clickable = getClickable(el);
                if (!clickable || seen.has(clickable)) continue;
                seen.add(clickable);

                if (!isReallyVisible(clickable) && !isReallyVisible(el)) continue;

                const target = isReallyVisible(clickable) ? clickable : el;
                const c = centerOf(target);

                let score = 0;
                if (c.x > window.innerWidth * 0.30) score += 5;
                if (c.y > window.innerHeight * 0.12 && c.y < window.innerHeight * 0.92) score += 5;
                const distCenterY = Math.abs(c.y - window.innerHeight * 0.55);
                score += Math.max(0, 5 - distCenterY / 120);

                unique.push({
                    clickable,
                    target,
                    score,
                    x: c.x,
                    y: c.y,
                    label: el.getAttribute('aria-label') || clickable.getAttribute('aria-label') || '',
                    method: 'aria-label'
                });
            }

            unique.sort((a, b) => b.score - a.score);

            if (unique.length) {
                const best = unique[0];
                best.target.scrollIntoView({ block: 'center', inline: 'center' });

                const r = best.target.getBoundingClientRect();
                const x = Math.floor(r.left + r.width / 2);
                const y = Math.floor(r.top + r.height / 2);

                let pointEl = document.elementFromPoint(x, y);
                let clickEl =
                    (pointEl && (
                        pointEl.closest('button') ||
                        pointEl.closest('[role="button"]') ||
                        pointEl.closest('a') ||
                        pointEl.closest('[tabindex="0"]')
                    )) ||
                    best.clickable ||
                    best.target;

                clickEl.click();

                return {
                    ok: true,
                    label: best.label,
                    method: best.method,
                    score: best.score,
                    x,
                    y,
                    candidates: unique.length
                };
            }

            // Fallback visual: se o SVG não tiver aria-label.
            const main = document.querySelector('main') || document.body;
            const svgCandidates = Array.from(main.querySelectorAll('svg'))
                .filter(isReallyVisible)
                .map(svg => {
                    const clickable = getClickable(svg);
                    const target = isReallyVisible(clickable) ? clickable : svg;
                    const c = centerOf(target);
                    const r = target.getBoundingClientRect();

                    let score = 0;
                    if (c.x > window.innerWidth * 0.35) score += 10;
                    if (c.y > window.innerHeight * 0.15 && c.y < window.innerHeight * 0.90) score += 8;
                    if (r.width >= 18 && r.width <= 80 && r.height >= 18 && r.height <= 80) score += 5;

                    const distRightMid = Math.abs(c.x - window.innerWidth * 0.68);
                    score += Math.max(0, 5 - distRightMid / 180);

                    return { clickable, target, x: c.x, y: c.y, score };
                })
                .filter(item => item.score >= 12)
                .sort((a, b) => {
                    if (Math.abs(a.x - b.x) > 80) return b.score - a.score;
                    return a.y - b.y;
                });

            if (svgCandidates.length) {
                const columns = {};
                for (const item of svgCandidates) {
                    const key = Math.round(item.x / 60) * 60;
                    if (!columns[key]) columns[key] = [];
                    columns[key].push(item);
                }

                let bestColumn = null;
                for (const key of Object.keys(columns)) {
                    const col = columns[key].sort((a, b) => a.y - b.y);
                    if (!bestColumn || col.length > bestColumn.length) {
                        bestColumn = col;
                    }
                }

                const chosen = (bestColumn && bestColumn.length >= 2 ? bestColumn[1] : null) || svgCandidates[0];

                chosen.target.scrollIntoView({ block: 'center', inline: 'center' });

                const r = chosen.target.getBoundingClientRect();
                const x = Math.floor(r.left + r.width / 2);
                const y = Math.floor(r.top + r.height / 2);

                let pointEl = document.elementFromPoint(x, y);
                let clickEl =
                    (pointEl && (
                        pointEl.closest('button') ||
                        pointEl.closest('[role="button"]') ||
                        pointEl.closest('a') ||
                        pointEl.closest('[tabindex="0"]')
                    )) ||
                    chosen.clickable ||
                    chosen.target;

                clickEl.click();

                return {
                    ok: true,
                    label: '',
                    method: 'visual-fallback',
                    score: chosen.score,
                    x,
                    y,
                    candidates: svgCandidates.length
                };
            }

            return {
                ok: false,
                reason: 'Nenhum botão de comentários encontrado no Reel atual.',
                candidates: 0,
                url: location.href
            };
        }
        """

        end_time = time.time() + timeout
        last_result = None

        while time.time() < end_time and not self.stop_requested:
            try:
                result = page.evaluate(js_find_and_click)
                last_result = result

                if isinstance(result, dict) and result.get("ok"):
                    method = result.get("method", "")
                    label = result.get("label", "")
                    candidates = result.get("candidates", 0)

                    self.log(f"Botão de comentários clicado. Método: {method} | Label: {label} | candidatos: {candidates}")
                    page.wait_for_timeout(2200)
                    return True

            except Exception as e:
                last_result = str(e)

            time.sleep(0.7)

        self.log(f"Resultado da busca do botão de comentários: {last_result}")
        return False

    def count_recent_comments(self, page, required_count: int, recent_hours: float) -> dict:
        js_count = r"""
        (recentHours) => {
        function norm(txt) {
            return (txt || '')
                .toLowerCase()
                .normalize('NFD')
                .replace(/[\u0300-\u036f]/g, '')
                .trim();
        }

        function isVisible(el) {
            if (!el) return false;
            const style = window.getComputedStyle(el);
            if (
                style.display === 'none' ||
                style.visibility === 'hidden' ||
                Number(style.opacity) === 0
            ) {
                return false;
            }

            const r = el.getBoundingClientRect();
            if (r.width < 1 || r.height < 1) return false;
            if (r.bottom <= 0 || r.right <= 0) return false;
            if (r.top >= window.innerHeight || r.left >= window.innerWidth) return false;
            return true;
        }

        function hasVisibleAncestor(el) {
            let p = el;
            for (let i = 0; i < 8 && p; i++, p = p.parentElement) {
                if (isVisible(p)) return true;
            }
            return false;
        }

        function parseAgeHours(timeEl) {
            const dt = timeEl.getAttribute('datetime');
            if (dt) {
                const date = new Date(dt);
                if (!isNaN(date.getTime())) {
                    const diffMs = Date.now() - date.getTime();
                    return Math.max(0, diffMs / 36e5);
                }
            }

            let raw = norm(
                timeEl.innerText ||
                timeEl.textContent ||
                timeEl.getAttribute('title') ||
                timeEl.getAttribute('aria-label') ||
                timeEl.parentElement?.innerText ||
                ''
            );

            raw = raw
                .replace(/^ha\s+/, '')
                .replace(/^há\s+/, '')
                .replace(/^about\s+/, '')
                .replace(/^aprox\.?\s+/, '')
                .trim();

            if (!raw) return null;

            // Exemplos: "3 d", "3d", "4 h", "2 sem", "1 min", "3 days", "4w".
            const m = raw.match(/(\d+(?:[.,]\d+)?)\s*([a-z]+)/i);
            if (!m) return null;

            const value = parseFloat(m[1].replace(',', '.'));
            const unit = m[2];

            if (!isFinite(value)) return null;

            if (unit.startsWith('s') || unit.startsWith('seg') || unit.startsWith('second')) return value / 3600;
            if (unit.startsWith('m') || unit.startsWith('min') || unit.startsWith('minute')) return value / 60;
            if (unit.startsWith('h') || unit.startsWith('hora') || unit.startsWith('hour')) return value;
            if (unit.startsWith('d') || unit.startsWith('dia') || unit.startsWith('day')) return value * 24;
            if (unit.startsWith('sem') || unit.startsWith('w') || unit.startsWith('week')) return value * 24 * 7;
            if (unit.startsWith('mes') || unit.startsWith('month') || unit === 'mo') return value * 24 * 30;
            if (unit.startsWith('ano') || unit.startsWith('year') || unit === 'y') return value * 24 * 365;

            return null;
        }

        function profileLinksInside(el) {
            return Array.from(el.querySelectorAll('a[href^="/"]'))
                .filter(a => {
                    const txt = (a.innerText || a.textContent || '').trim();
                    const href = a.getAttribute('href') || '';
                    if (!txt) return false;
                    if (!/^\/[^\/?#]+\/?$/.test(href)) return false;
                    const bad = norm(txt);
                    if (bad.includes('responder') || bad.includes('reply') || bad.includes('ver respostas') || bad.includes('view replies')) return false;
                    return true;
                });
        }

        function nearestCommentBlock(timeEl) {
            // O Instagram coloca a data como: bloco-do-comentario > ... > span > a > time.
            // Subimos a partir do <time> até achar um pai que contenha: 1 data + link de perfil + texto do comentário.
            let el = timeEl;
            let best = null;
            let bestScore = -999;

            for (let depth = 0; depth < 22 && el; depth++, el = el.parentElement) {
                if (!el.querySelectorAll) continue;

                const timeCount = el.querySelectorAll('time').length;
                const profileLinks = profileLinksInside(el);
                const txt = (el.innerText || el.textContent || '').trim();
                const nTxt = norm(txt);
                const r = el.getBoundingClientRect ? el.getBoundingClientRect() : {width: 0, height: 0};

                if (!profileLinks.length) continue;
                if (txt.length < 2 || txt.length > 1800) continue;
                if (timeCount < 1 || timeCount > 6) continue;

                let score = 0;
                score += profileLinks.length * 25;
                score += Math.min(50, txt.length / 8);
                score += Math.max(0, 40 - depth * 2);
                if (nTxt.includes('responder') || nTxt.includes('reply')) score += 20;
                if (nTxt.includes('curtir') || nTxt.includes('like')) score += 5;
                if (r.width > 80 && r.height > 20) score += 15;
                if (timeCount === 1) score += 12;

                if (score > bestScore) {
                    bestScore = score;
                    best = el;
                }
            }

            return best || timeEl.closest('div') || timeEl.parentElement || timeEl;
        }

        function extractUsername(block) {
            const links = profileLinksInside(block);

            for (const a of links) {
                const txt = (a.innerText || a.textContent || '').trim();
                const href = a.getAttribute('href') || '';
                const usernameFromHref = href.replace(/^\//, '').replace(/\/$/, '').trim();

                if (!txt && !usernameFromHref) continue;
                return (txt || usernameFromHref).replace('@', '').trim();
            }

            return '';
        }

        function cleanText(block, username, ageText) {
            let text = (block.innerText || block.textContent || '').trim();

            const removals = [
                username,
                '@' + username,
                ageText,
                'Responder',
                'Reply',
                'Ver respostas',
                'View replies',
                'Curtir',
                'Like',
                'Ocultar',
                'Hide'
            ];

            for (const r of removals) {
                if (!r) continue;
                text = text.split(r).join(' ');
            }

            text = text
                .split('\n')
                .map(x => x.trim())
                .filter(Boolean)
                .join(' ')
                .replace(/\s+/g, ' ')
                .trim();

            return text;
        }

        const rawTimes = Array.from(document.querySelectorAll('time, a > time, span a time, time[datetime]'));
        const times = rawTimes.filter(el => isVisible(el) || hasVisibleAncestor(el));
        const items = [];
        const seen = new Set();
        const parsedDebug = [];

        for (const timeEl of times) {
            const ageHours = parseAgeHours(timeEl);
            const ageText = (timeEl.innerText || timeEl.textContent || timeEl.getAttribute('datetime') || timeEl.getAttribute('title') || '').trim();

            if (ageHours === null || !isFinite(ageHours)) continue;

            const block = nearestCommentBlock(timeEl);
            if (!block || !hasVisibleAncestor(block)) continue;

            const username = extractUsername(block);
            const text = cleanText(block, username, ageText);

            parsedDebug.push({
                age_text: ageText,
                age_hours: ageHours,
                username,
                text: text.slice(0, 120),
                visible: isVisible(timeEl),
                datetime: timeEl.getAttribute('datetime') || ''
            });

            if (!username) continue;

            // Ignora linhas que são claramente do post/painel e não de comentário.
            const nText = norm(text);
            if (!nText || nText === norm(username)) continue;
            if (nText.includes('adicionar comentario') || nText.includes('add a comment')) continue;

            const key = username + '|' + ageText + '|' + text.slice(0, 140);
            if (seen.has(key)) continue;
            seen.add(key);

            const recent = ageHours <= recentHours;

            items.push({
                key,
                username,
                age_text: ageText,
                age_hours: ageHours,
                text,
                recent,
                y: timeEl.getBoundingClientRect().top
            });
        }

        items.sort((a, b) => a.age_hours - b.age_hours);

        const recentItems = items.filter(x => x.recent);

        // IMPORTANTE:
        // O Instagram virtualiza/recicla os comentários no DOM. Quando rola, comentários
        // antigos somem da tela e novos entram, então a contagem visível pode diminuir.
        // Por isso mantemos um acumulador por Reel dentro da página.
        const state = window.__rminerCommentState || (window.__rminerCommentState = {
            seen: {},
            items: []
        });

        let newlyAdded = 0;
        for (const item of items) {
            if (!item.key) continue;
            if (state.seen[item.key]) continue;
            state.seen[item.key] = true;
            state.items.push(item);
            newlyAdded += 1;
        }

        state.items.sort((a, b) => a.age_hours - b.age_hours);
        const accumulatedRecentItems = state.items.filter(x => x.recent);

        return {
            total_seen: state.items.length,
            recent_count: accumulatedRecentItems.length,
            details: accumulatedRecentItems.slice(0, 30),
            all_sample: state.items.slice(0, 10),
            visible_total_seen: items.length,
            visible_recent_count: recentItems.length,
            newly_added: newlyAdded,
            debug: {
                raw_time_count: rawTimes.length,
                usable_time_count: times.length,
                parsed_time_count: parsedDebug.length,
                parsed_sample: parsedDebug.slice(0, 12)
            }
        };
        }
        """

        js_scroll = r"""
        () => {
        function isVisible(el) {
            if (!el) return false;
            const style = window.getComputedStyle(el);
            if (
                style.display === 'none' ||
                style.visibility === 'hidden' ||
                Number(style.opacity) === 0
            ) {
                return false;
            }

            const r = el.getBoundingClientRect();
            if (r.width < 30 || r.height < 30) return false;
            if (r.bottom <= 0 || r.right <= 0) return false;
            if (r.top >= window.innerHeight || r.left >= window.innerWidth) return false;
            return true;
        }

        function findBestScrollContainer() {
            const candidates = Array.from(document.querySelectorAll('div'))
                .filter(el => {
                    if (!isVisible(el)) return false;
                    if (el.scrollHeight <= el.clientHeight + 80) return false;

                    const times = el.querySelectorAll('time').length;
                    const text = (el.innerText || '').toLowerCase();

                    let score = 0;
                    score += times * 25;
                    if (text.includes('coment')) score += 30;
                    if (text.includes('comment')) score += 30;
                    if (text.includes('responder') || text.includes('reply')) score += 25;

                    const r = el.getBoundingClientRect();
                    score += Math.min(50, r.height / 16);

                    return score > 0;
                })
                .map(el => {
                    const r = el.getBoundingClientRect();
                    const times = el.querySelectorAll('time').length;
                    const text = (el.innerText || '').toLowerCase();

                    let score = times * 25;
                    if (text.includes('coment')) score += 30;
                    if (text.includes('comment')) score += 30;
                    if (text.includes('responder') || text.includes('reply')) score += 25;
                    score += Math.min(50, r.height / 16);

                    return { el, score };
                })
                .sort((a, b) => b.score - a.score);

            return candidates.length ? candidates[0].el : null;
        }

        const container = findBestScrollContainer();

        if (!container) {
            window.scrollBy(0, Math.floor(window.innerHeight * 0.7));
            return {
                ok: false,
                reason: 'Sem container específico. Rolei a janela.',
                scrollTop: window.scrollY
            };
        }

        const before = container.scrollTop;
        container.scrollTop = container.scrollTop + Math.floor(container.clientHeight * 0.85);

        return {
            ok: true,
            before,
            after: container.scrollTop,
            scrollHeight: container.scrollHeight,
            clientHeight: container.clientHeight,
            times: container.querySelectorAll('time').length
        };
        }
        """

        try:
            # Reseta o acumulador a cada Reel. Dentro do Reel, ele permanece vivo
            # durante todas as rolagens para não perder comentários que saem do DOM.
            page.evaluate("""() => {
                window.__rminerCommentState = { seen: {}, items: [] };
            }""")
        except Exception:
            pass

        best_result = {
            "total_seen": 0,
            "recent_count": 0,
            "details": [],
        }

        # Se acumularmos esse número de comentários sem encontrar nenhum recente,
        # desistimos imediatamente (os mais recentes ficam no topo, então rolar mais
        # só encontrará comentários ainda mais velhos).
        EARLY_BAIL_SAMPLE = 10

        no_change_rounds = 0
        last_total = -1
        i = 0

        while not self.stop_requested:
            try:
                result = page.evaluate(js_count, recent_hours)

                if isinstance(result, dict):
                    best_result = result
                    recent_count = int(result.get("recent_count", 0))
                    total_seen = int(result.get("total_seen", 0))
                    debug = result.get("debug") or {}

                    visible_total = result.get("visible_total_seen", "?")
                    visible_recent = result.get("visible_recent_count", "?")
                    newly_added = result.get("newly_added", "?")

                    self.log(
                        f"Contagem {i + 1}: recentes acumulados={recent_count} | total acumulado={total_seen} | "
                        f"visíveis agora={visible_recent}/{visible_total} | novos={newly_added} | "
                        f"time tags={debug.get('raw_time_count', '?')} | parseados={debug.get('parsed_time_count', '?')}"
                    )

                    if recent_count >= required_count:
                        return result

                    # Bail-out antecipado: os comentários mais recentes ficam no topo do
                    # painel. Se já lemos EARLY_BAIL_SAMPLE comentários e nenhum é recente,
                    # os de baixo serão ainda mais velhos — não adianta rolar mais.
                    if total_seen >= EARLY_BAIL_SAMPLE and recent_count == 0:
                        self.log(
                            f"Bail-out antecipado: {total_seen} comentário(s) lido(s) sem nenhum "
                            f"dentro do prazo. Reprovando sem rolar mais."
                        )
                        return result

                    if total_seen == last_total:
                        no_change_rounds += 1
                    else:
                        no_change_rounds = 0

                    last_total = total_seen

                    # Encerra quando parece que não há mais comentários para carregar.
                    if no_change_rounds >= 5:
                        self.log("A contagem parou de aumentar. Provável fim dos comentários carregáveis.")
                        sample = (debug.get("parsed_sample") or [])[:5]
                        if sample:
                            self.log("Amostra de datas encontradas nos comentários:")
                            for item in sample:
                                self.log(
                                    f"- @{item.get('username') or '?'} | {item.get('age_text') or item.get('datetime') or '?'} | "
                                    f"idade {float(item.get('age_hours') or 0) / 24:.2f} dia(s)"
                                )
                        return result

                page.evaluate(js_scroll)
                self.log("Rolando comentários para carregar mais...")
                page.wait_for_timeout(1400)
                i += 1

            except Exception as e:
                self.log(f"Erro durante contagem/rolagem: {e}")
                time.sleep(1)
                i += 1

        return best_result


if __name__ == "__main__":
    app = App()
    app.mainloop()
