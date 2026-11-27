import os
import sys
import queue
import threading
import argparse
import webbrowser
from tkinter import filedialog, messagebox
try:
    import customtkinter as ctk
except ImportError:
    ctk = None

# Ensure project root is on sys.path when executed directly
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.version import __app_name__, __version__, GITHUB_REPO_URL
from core.utils import (
    load_config,
    save_config,
    get_resource_path,
    find_ffmpeg,
    ensure_ffmpeg,
    find_js_runtimes,
    ensure_js_runtime,
    setup_logging,
)
from core.playlist_extractor import detect_platform
from core.orchestrator import orchestrate_download
from core.updater import (
    check_for_updates,
    can_self_update_exe,
    download_and_apply_exe_update,
)

# Modern Web-inspired Palette (Light, Dark)
BG_CANVAS = ("#FAFAFC", "#0B0D11")
BG_CARD = ("#FFFFFF", "#13161D")
BG_ELEVATED = ("#F3F5F8", "#1B1F2A")
BG_HOVER = ("#EAEFF5", "#222736")
BG_LOG = ("#F5F7FA", "#0E1016")
BORDER_HAIRLINE = ("#E4E7EE", "#222734")
BORDER_SOFT = ("#D4D9E3", "#2E3547")
TEXT_PRIMARY = ("#111318", "#F4F6FA")
TEXT_SECONDARY = ("#5A6476", "#9AA4B8")
TEXT_MUTED = ("#8892A4", "#667085")

ACCENT = "#1DB954"
ACCENT_HOVER = "#18A349"
ACCENT_SOFT = ("#E9F8EF", "#11281B")
DANGER = "#E04444"
DANGER_HOVER = "#C23333"
WARN = "#E5941A"
WARN_HOVER = "#C77D10"

PLATFORM_COLORS = {
    "Spotify": "#1DB954",
    "SoundCloud": "#FF5500",
    "YouTube": "#FF2E2E",
    "YouTube Music": "#FF1E42",
    "Deezer": "#A238FF",
    "Apple Music": "#FA243C",
}

STEP_PROGRESS = {
    "source": 0.16,
    "config": 0.42,
    "folder": 0.68,
    "duplicates": 0.90,
    "download": 1.00,
}

STEP_ORDER = ["source", "config", "folder", "duplicates", "download"]

STEP_META = {
    "source": "Étape 1 sur 4 · Source",
    "config": "Étape 2 sur 4 · Qualité & Workers",
    "folder": "Étape 3 sur 4 · Destination",
    "duplicates": "Étape 4 sur 4 · Doublons",
    "download": "Téléchargement",
    "settings": "Paramètres",
}

_CTK_BASE = ctk.CTk if ctk is not None else object


def _ease_out_cubic(t: float) -> float:
    return 1.0 - (1.0 - t) ** 3


class DjaelYTPlaylistDWNLD(_CTK_BASE):
    def __init__(self):
        super().__init__()

        self.config = load_config()
        theme = self.config.get("theme", "Dark")
        if theme not in ("System", "Dark", "Light"):
            theme = "Dark"
        ctk.set_appearance_mode(theme)
        ctk.set_default_color_theme("green")

        self.title(f"{__app_name__}")
        self.geometry("920x660")
        self.minsize(780, 600)
        self.configure(fg_color=BG_CANVAS)

        self._set_window_icon()

        # Core state
        self.gui_queue = queue.Queue()
        self.download_thread = None
        self.is_downloading = False
        self.stop_requested = False
        self.current_page = None
        self.previous_page = "source"
        self.pending_update = None
        self.last_summary = None
        self._save_debounce_id = None

        # Animation state
        self._top_bar_value = 0.0
        self._top_bar_anim_id = None
        self._dl_bar_value = 0.0
        self._dl_bar_target = 0.0
        self._dl_bar_anim_id = None
        self._page_anim_id = None
        self._pulse_anim_id = None
        self._pulse_step = 0

        # Wizard selections
        self.selected_quality = self.config.get("preferred_quality", "320")
        if self.selected_quality not in ("128", "192", "256", "320"):
            self.selected_quality = "320"
        self.selected_workers = max(1, min(6, int(self.config.get("max_workers") or 3)))
        self.selected_overwrite = self.config.get("overwrite_mode", "upgrade")
        if self.selected_overwrite not in ("upgrade", "replace", "skip"):
            self.selected_overwrite = "upgrade"

        self.pages = {}
        self.overwrite_cards = {}
        self.quality_buttons = {}

        self._build_shell()
        self._load_config_into_widgets()
        self.show_page("source", animate=False)
        self._animate_top_bar(STEP_PROGRESS["source"], duration_ms=420)

        self.protocol("WM_DELETE_WINDOW", self._on_close_window)
        self.after(80, self._poll_queue)
        self.after(1400, lambda: self._start_update_check(force=False))
        if not find_ffmpeg()[0] or not find_js_runtimes():
            threading.Thread(target=self._prewarm_runtimes, daemon=True).start()

    def _prewarm_runtimes(self):
        try:
            if not find_ffmpeg()[0]:
                ensure_ffmpeg()
        except Exception:
            pass
        try:
            if not find_js_runtimes():
                ensure_js_runtime()
        except Exception:
            pass

    def _set_window_icon(self):
        logo_ico = get_resource_path("logo.ico")
        logo_png = get_resource_path("logo.png")
        if os.path.exists(logo_ico):
            try:
                self.iconbitmap(logo_ico)
                return
            except Exception:
                pass
        if os.path.exists(logo_png):
            try:
                from PIL import Image, ImageTk

                self._icon_photo = ImageTk.PhotoImage(Image.open(logo_png))
                self.iconphoto(False, self._icon_photo)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Minimalist Web-Like Shell: Edge-to-edge Animated Progress Bar + Whisper Header
    # ------------------------------------------------------------------
    def _build_shell(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # 1. Edge-to-edge ultra-slim animated loading bar (replaces heavy navbar)
        self.top_progress = ctk.CTkProgressBar(
            self,
            height=4,
            corner_radius=0,
            fg_color=BORDER_HAIRLINE,
            progress_color=ACCENT,
        )
        self.top_progress.grid(row=0, column=0, sticky="ew")
        self.top_progress.set(0.0)

        # 2. Ultra-clean transparent floating header (no box, no borders)
        header = ctk.CTkFrame(self, fg_color="transparent", height=54)
        header.grid(row=1, column=0, sticky="ew", padx=28, pady=(8, 0))
        header.grid_columnconfigure(1, weight=1)
        header.grid_propagate(False)

        left_box = ctk.CTkFrame(header, fg_color="transparent")
        left_box.grid(row=0, column=0, sticky="w", pady=10)

        self.brand_btn = ctk.CTkButton(
            left_box,
            text="DjaelYT",
            font=("Segoe UI", 15, "bold"),
            fg_color="transparent",
            hover_color=BG_ELEVATED,
            text_color=TEXT_PRIMARY,
            width=76,
            height=30,
            corner_radius=8,
            command=self._go_home_if_idle,
        )
        self.brand_btn.pack(side="left")

        self.step_whisper = ctk.CTkLabel(
            left_box,
            text="",
            font=("Segoe UI", 12),
            text_color=TEXT_MUTED,
        )
        self.step_whisper.pack(side="left", padx=(10, 0))

        # Subtle floating GitHub update pill (hidden until update found)
        self.update_pill = ctk.CTkFrame(
            header,
            fg_color=ACCENT_SOFT,
            border_width=1,
            border_color=ACCENT,
            corner_radius=16,
            height=30,
        )
        self.update_pill_btn = ctk.CTkButton(
            self.update_pill,
            text="",
            font=("Segoe UI", 11, "bold"),
            height=26,
            corner_radius=13,
            fg_color="transparent",
            hover_color=BG_HOVER,
            text_color=ACCENT,
            command=self._apply_or_open_update,
        )
        self.update_pill_btn.pack(side="left", padx=(6, 0), pady=2)

        ctk.CTkButton(
            self.update_pill,
            text="×",
            font=("Segoe UI", 13, "bold"),
            width=24,
            height=24,
            corner_radius=12,
            fg_color="transparent",
            hover_color=BG_HOVER,
            text_color=TEXT_SECONDARY,
            command=self._skip_current_update,
        ).pack(side="left", padx=(0, 4), pady=2)

        # Single ghost settings icon on the right
        self.settings_btn = ctk.CTkButton(
            header,
            text="⚙",
            font=("Segoe UI", 16),
            width=36,
            height=36,
            corner_radius=18,
            fg_color="transparent",
            hover_color=BG_ELEVATED,
            text_color=TEXT_SECONDARY,
            command=self._toggle_settings,
        )
        self.settings_btn.grid(row=0, column=2, sticky="e", pady=8)

        # 3. Full-bleed stage hosting sliding pages
        self.stage = ctk.CTkFrame(self, fg_color="transparent")
        self.stage.grid(row=2, column=0, sticky="nsew", padx=28, pady=(0, 22))

        self.pages["source"] = self._build_page_source(self.stage)
        self.pages["config"] = self._build_page_config(self.stage)
        self.pages["folder"] = self._build_page_folder(self.stage)
        self.pages["duplicates"] = self._build_page_duplicates(self.stage)
        self.pages["download"] = self._build_page_download(self.stage)
        self.pages["settings"] = self._build_page_settings(self.stage)

    # ------------------------------------------------------------------
    # Page 1: Ultra-Clean Web Hero (Centered Input + Big Launch Button)
    # ------------------------------------------------------------------
    def _build_page_source(self, parent):
        page = ctk.CTkFrame(parent, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(0, weight=1)
        page.grid_rowconfigure(2, weight=1)

        hero = ctk.CTkFrame(page, fg_color="transparent")
        hero.grid(row=1, column=0, sticky="ew", padx=60)
        hero.grid_columnconfigure(0, weight=1)

        logo_path = get_resource_path("logo.png")
        if os.path.exists(logo_path):
            try:
                from PIL import Image

                self._hero_logo = ctk.CTkImage(Image.open(logo_path), size=(62, 62))
                ctk.CTkLabel(hero, image=self._hero_logo, text="").grid(row=0, column=0, pady=(0, 16))
            except Exception:
                pass

        ctk.CTkLabel(
            hero,
            text="Téléchargez vos playlists.",
            font=("Segoe UI", 32, "bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=1, column=0, pady=(0, 8))

        ctk.CTkLabel(
            hero,
            text="Collez un lien Spotify, SoundCloud, YouTube, Deezer ou Apple Music.",
            font=("Segoe UI", 14),
            text_color=TEXT_SECONDARY,
        ).grid(row=2, column=0, pady=(0, 32))

        # Sleek web-style pill input with focus ring animation
        self.url_box = ctk.CTkFrame(
            hero,
            fg_color=BG_CARD,
            border_width=2,
            border_color=BORDER_HAIRLINE,
            corner_radius=16,
            height=60,
        )
        self.url_box.grid(row=3, column=0, sticky="ew", padx=24)
        self.url_box.grid_columnconfigure(0, weight=1)
        self.url_box.grid_propagate(False)

        self.playlist_entry = ctk.CTkEntry(
            self.url_box,
            placeholder_text="https://open.spotify.com/playlist/...",
            font=("Segoe UI", 14),
            height=52,
            border_width=0,
            fg_color="transparent",
            text_color=TEXT_PRIMARY,
        )
        self.playlist_entry.grid(row=0, column=0, padx=(18, 8), pady=4, sticky="ew")
        self.playlist_entry.bind("<FocusIn>", lambda e: self.url_box.configure(border_color=ACCENT))
        self.playlist_entry.bind("<FocusOut>", lambda e: self.url_box.configure(border_color=BORDER_HAIRLINE))
        self.playlist_entry.bind("<KeyRelease>", self._on_url_changed)
        self.playlist_entry.bind("<<Paste>>", lambda e: self.after(10, self._on_url_changed))
        self.playlist_entry.bind("<Return>", lambda e: self._go_from_source())

        ctk.CTkButton(
            self.url_box,
            text="Coller",
            font=("Segoe UI", 12, "bold"),
            width=74,
            height=38,
            corner_radius=10,
            fg_color=BG_ELEVATED,
            hover_color=BG_HOVER,
            text_color=TEXT_SECONDARY,
            command=self._paste_from_clipboard,
        ).grid(row=0, column=1, padx=(0, 10), pady=10)

        # Live detected platform chip
        self.platform_chip = ctk.CTkLabel(
            hero,
            text="",
            font=("Segoe UI", 12, "bold"),
            text_color=TEXT_MUTED,
            height=26,
        )
        self.platform_chip.grid(row=4, column=0, pady=(12, 24))

        # Big centered primary CTA button
        self.hero_launch_btn = ctk.CTkButton(
            hero,
            text="Lancer   →",
            font=("Segoe UI", 16, "bold"),
            height=54,
            width=250,
            corner_radius=27,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color="#FFFFFF",
            command=self._go_from_source,
        )
        self.hero_launch_btn.grid(row=5, column=0)

        return page

    # ------------------------------------------------------------------
    # Page 2: Workers & Audio Quality Configuration
    # ------------------------------------------------------------------
    def _build_page_config(self, parent):
        page = ctk.CTkFrame(parent, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(0, weight=1)
        page.grid_rowconfigure(2, weight=1)

        card = ctk.CTkFrame(
            page,
            fg_color=BG_CARD,
            border_width=1,
            border_color=BORDER_HAIRLINE,
            corner_radius=20,
        )
        card.grid(row=1, column=0, sticky="ew", padx=90)
        card.grid_columnconfigure(0, weight=1)

        self.config_platform_pill = ctk.CTkLabel(
            card,
            text="SPOTIFY",
            font=("Segoe UI", 10, "bold"),
            text_color=ACCENT,
            fg_color=ACCENT_SOFT,
            corner_radius=8,
            padx=10,
            pady=3,
        )
        self.config_platform_pill.grid(row=0, column=0, padx=34, pady=(28, 8), sticky="w")

        ctk.CTkLabel(
            card,
            text="Qualité & Performances",
            font=("Segoe UI", 24, "bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=1, column=0, padx=34, pady=(0, 4), sticky="w")

        ctk.CTkLabel(
            card,
            text="Ajustez la qualité MP3 et le nombre de téléchargements simultanés.",
            font=("Segoe UI", 13),
            text_color=TEXT_SECONDARY,
        ).grid(row=2, column=0, padx=34, pady=(0, 26), sticky="w")

        # Quality selector (4 clean clickable cards)
        ctk.CTkLabel(
            card,
            text="Qualité audio",
            font=("Segoe UI", 13, "bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=3, column=0, padx=34, pady=(0, 10), sticky="w")

        q_row = ctk.CTkFrame(card, fg_color="transparent")
        q_row.grid(row=4, column=0, padx=34, pady=(0, 26), sticky="ew")
        for col in range(4):
            q_row.grid_columnconfigure(col, weight=1)

        quality_specs = [
            ("128", "128 kbps", "Léger"),
            ("192", "192 kbps", "Standard"),
            ("256", "256 kbps", "Haute"),
            ("320", "320 kbps", "Studio ★"),
        ]
        for col, (q_val, q_title, q_sub) in enumerate(quality_specs):
            btn = ctk.CTkButton(
                q_row,
                text=f"{q_title}\n{q_sub}",
                font=("Segoe UI", 12, "bold"),
                height=58,
                corner_radius=12,
                fg_color=BG_ELEVATED,
                hover_color=BG_HOVER,
                border_width=2,
                border_color=BORDER_HAIRLINE,
                text_color=TEXT_PRIMARY,
                command=lambda v=q_val: self._select_quality(v),
            )
            btn.grid(row=0, column=col, padx=(0 if col == 0 else 5, 0 if col == 3 else 5), sticky="ew")
            self.quality_buttons[q_val] = btn

        # Workers slider with big numeric indicator
        w_head = ctk.CTkFrame(card, fg_color="transparent")
        w_head.grid(row=5, column=0, padx=34, pady=(0, 8), sticky="ew")
        w_head.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            w_head,
            text="Workers simultanés",
            font=("Segoe UI", 13, "bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=0, column=0, sticky="w")

        self.workers_value_label = ctk.CTkLabel(
            w_head,
            text="3 workers · Équilibré",
            font=("Segoe UI", 13, "bold"),
            text_color=ACCENT,
        )
        self.workers_value_label.grid(row=0, column=1, sticky="e")

        self.workers_slider = ctk.CTkSlider(
            card,
            from_=1,
            to=6,
            number_of_steps=5,
            height=22,
            progress_color=ACCENT,
            fg_color=BG_ELEVATED,
            button_color=ACCENT,
            button_hover_color=ACCENT_HOVER,
            command=self._on_workers_changed,
        )
        self.workers_slider.grid(row=6, column=0, padx=34, pady=(4, 28), sticky="ew")

        # Footer navigation
        nav = ctk.CTkFrame(card, fg_color="transparent")
        nav.grid(row=7, column=0, padx=34, pady=(0, 28), sticky="ew")
        nav.grid_columnconfigure(0, weight=1)
        nav.grid_columnconfigure(1, weight=2)

        ctk.CTkButton(
            nav,
            text="←  Retour",
            font=("Segoe UI", 13, "bold"),
            height=46,
            corner_radius=12,
            fg_color=BG_ELEVATED,
            hover_color=BG_HOVER,
            text_color=TEXT_SECONDARY,
            command=lambda: self.show_page("source"),
        ).grid(row=0, column=0, padx=(0, 8), sticky="ew")

        ctk.CTkButton(
            nav,
            text="Continuer   →",
            font=("Segoe UI", 14, "bold"),
            height=46,
            corner_radius=12,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color="#FFFFFF",
            command=lambda: self.show_page("folder"),
        ).grid(row=0, column=1, padx=(8, 0), sticky="ew")

        return page

    # ------------------------------------------------------------------
    # Page 3: Output Folder Selection
    # ------------------------------------------------------------------
    def _build_page_folder(self, parent):
        page = ctk.CTkFrame(parent, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(0, weight=1)
        page.grid_rowconfigure(2, weight=1)

        card = ctk.CTkFrame(
            page,
            fg_color=BG_CARD,
            border_width=1,
            border_color=BORDER_HAIRLINE,
            corner_radius=20,
        )
        card.grid(row=1, column=0, sticky="ew", padx=90)
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            card,
            text="DESTINATION",
            font=("Segoe UI", 10, "bold"),
            text_color=ACCENT,
            fg_color=ACCENT_SOFT,
            corner_radius=8,
            padx=10,
            pady=3,
        ).grid(row=0, column=0, padx=34, pady=(28, 8), sticky="w")

        ctk.CTkLabel(
            card,
            text="Où enregistrer vos musiques ?",
            font=("Segoe UI", 24, "bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=1, column=0, padx=34, pady=(0, 4), sticky="w")

        ctk.CTkLabel(
            card,
            text="Choisissez un dossier existant ou indiquez un nouveau chemin (il sera créé automatiquement).",
            font=("Segoe UI", 13),
            text_color=TEXT_SECONDARY,
        ).grid(row=2, column=0, padx=34, pady=(0, 24), sticky="w")

        self.folder_box = ctk.CTkFrame(
            card,
            fg_color=BG_ELEVATED,
            border_width=2,
            border_color=BORDER_HAIRLINE,
            corner_radius=14,
            height=54,
        )
        self.folder_box.grid(row=3, column=0, padx=34, pady=(0, 12), sticky="ew")
        self.folder_box.grid_columnconfigure(0, weight=1)
        self.folder_box.grid_propagate(False)

        self.dest_entry = ctk.CTkEntry(
            self.folder_box,
            placeholder_text="C:\\Users\\...\\Music\\MaPlaylist",
            font=("Segoe UI", 13),
            height=46,
            border_width=0,
            fg_color="transparent",
            text_color=TEXT_PRIMARY,
        )
        self.dest_entry.grid(row=0, column=0, padx=(14, 8), pady=4, sticky="ew")
        self.dest_entry.bind("<FocusIn>", lambda e: self.folder_box.configure(border_color=ACCENT))
        self.dest_entry.bind("<FocusOut>", lambda e: self.folder_box.configure(border_color=BORDER_HAIRLINE))
        self.dest_entry.bind("<KeyRelease>", lambda e: self._refresh_folder_info())

        ctk.CTkButton(
            self.folder_box,
            text="Parcourir",
            font=("Segoe UI", 12, "bold"),
            height=36,
            width=96,
            corner_radius=9,
            fg_color=BG_CARD,
            hover_color=BG_HOVER,
            text_color=TEXT_PRIMARY,
            command=self._browse_directory,
        ).grid(row=0, column=1, padx=(0, 8), pady=8)

        self.folder_info_label = ctk.CTkLabel(
            card,
            text="",
            font=("Segoe UI", 12),
            text_color=TEXT_MUTED,
        )
        self.folder_info_label.grid(row=4, column=0, padx=34, pady=(0, 30), sticky="w")

        nav = ctk.CTkFrame(card, fg_color="transparent")
        nav.grid(row=5, column=0, padx=34, pady=(0, 28), sticky="ew")
        nav.grid_columnconfigure(0, weight=1)
        nav.grid_columnconfigure(1, weight=2)

        ctk.CTkButton(
            nav,
            text="←  Retour",
            font=("Segoe UI", 13, "bold"),
            height=46,
            corner_radius=12,
            fg_color=BG_ELEVATED,
            hover_color=BG_HOVER,
            text_color=TEXT_SECONDARY,
            command=lambda: self.show_page("config"),
        ).grid(row=0, column=0, padx=(0, 8), sticky="ew")

        ctk.CTkButton(
            nav,
            text="Continuer   →",
            font=("Segoe UI", 14, "bold"),
            height=46,
            corner_radius=12,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color="#FFFFFF",
            command=self._go_from_folder,
        ).grid(row=0, column=1, padx=(8, 0), sticky="ew")

        return page

    # ------------------------------------------------------------------
    # Page 4: Duplicate Handling + Recap + Start
    # ------------------------------------------------------------------
    def _build_page_duplicates(self, parent):
        page = ctk.CTkFrame(parent, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(0, weight=1)
        page.grid_rowconfigure(2, weight=1)

        card = ctk.CTkFrame(
            page,
            fg_color=BG_CARD,
            border_width=1,
            border_color=BORDER_HAIRLINE,
            corner_radius=20,
        )
        card.grid(row=1, column=0, sticky="ew", padx=90)
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            card,
            text="DERNIÈRE ÉTAPE",
            font=("Segoe UI", 10, "bold"),
            text_color=ACCENT,
            fg_color=ACCENT_SOFT,
            corner_radius=8,
            padx=10,
            pady=3,
        ).grid(row=0, column=0, padx=34, pady=(26, 8), sticky="w")

        ctk.CTkLabel(
            card,
            text="Remplacer les éléments existants ?",
            font=("Segoe UI", 24, "bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=1, column=0, padx=34, pady=(0, 4), sticky="w")

        ctk.CTkLabel(
            card,
            text="Choisissez le comportement lorsqu'une piste est déjà présente dans votre dossier.",
            font=("Segoe UI", 13),
            text_color=TEXT_SECONDARY,
        ).grid(row=2, column=0, padx=34, pady=(0, 18), sticky="w")

        options = [
            (
                "upgrade",
                "Améliorer si qualité inférieure",
                "Recommandé · Remplace uniquement les fichiers dont le bitrate est inférieur à la cible.",
            ),
            (
                "replace",
                "Oui, tout remplacer",
                "Re-télécharge et remplace systématiquement les pistes existantes (swap sécurisé).",
            ),
            (
                "skip",
                "Non, conserver les existants",
                "Ignore tous les titres déjà présents dans le dossier sans jamais les modifier.",
            ),
        ]

        for idx, (mode_key, title, desc) in enumerate(options):
            opt_frame = ctk.CTkFrame(
                card,
                fg_color=BG_ELEVATED,
                border_width=2,
                border_color=BORDER_HAIRLINE,
                corner_radius=14,
                cursor="hand2",
            )
            opt_frame.grid(row=3 + idx, column=0, padx=34, pady=5, sticky="ew")
            opt_frame.grid_columnconfigure(1, weight=1)

            check_dot = ctk.CTkLabel(
                opt_frame,
                text="○",
                font=("Segoe UI", 16, "bold"),
                text_color=TEXT_MUTED,
                width=26,
            )
            check_dot.grid(row=0, column=0, rowspan=2, padx=(14, 6), pady=10)

            lbl_title = ctk.CTkLabel(
                opt_frame,
                text=title,
                font=("Segoe UI", 13, "bold"),
                text_color=TEXT_PRIMARY,
                anchor="w",
            )
            lbl_title.grid(row=0, column=1, padx=(0, 14), pady=(10, 1), sticky="ew")

            lbl_desc = ctk.CTkLabel(
                opt_frame,
                text=desc,
                font=("Segoe UI", 11),
                text_color=TEXT_SECONDARY,
                anchor="w",
            )
            lbl_desc.grid(row=1, column=1, padx=(0, 14), pady=(0, 10), sticky="ew")

            def _bind_card(frame, key):
                for w in (frame, check_dot, lbl_title, lbl_desc):
                    w.bind("<Button-1>", lambda e, k=key: self._select_overwrite_mode(k))
                    w.bind("<Enter>", lambda e, f=frame, k=key: self._hover_card(f, k, True))
                    w.bind("<Leave>", lambda e, f=frame, k=key: self._hover_card(f, k, False))

            _bind_card(opt_frame, mode_key)
            self.overwrite_cards[mode_key] = (opt_frame, check_dot, lbl_title)

        # Clean summary strip
        self.recap_label = ctk.CTkLabel(
            card,
            text="",
            font=("Segoe UI", 11),
            text_color=TEXT_MUTED,
        )
        self.recap_label.grid(row=6, column=0, padx=34, pady=(14, 16), sticky="w")

        nav = ctk.CTkFrame(card, fg_color="transparent")
        nav.grid(row=7, column=0, padx=34, pady=(0, 26), sticky="ew")
        nav.grid_columnconfigure(0, weight=1)
        nav.grid_columnconfigure(1, weight=2)

        ctk.CTkButton(
            nav,
            text="←  Retour",
            font=("Segoe UI", 13, "bold"),
            height=46,
            corner_radius=12,
            fg_color=BG_ELEVATED,
            hover_color=BG_HOVER,
            text_color=TEXT_SECONDARY,
            command=lambda: self.show_page("folder"),
        ).grid(row=0, column=0, padx=(0, 8), sticky="ew")

        ctk.CTkButton(
            nav,
            text="Démarrer le téléchargement",
            font=("Segoe UI", 14, "bold"),
            height=46,
            corner_radius=12,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color="#FFFFFF",
            command=self._start_download,
        ).grid(row=0, column=1, padx=(8, 0), sticky="ew")

        return page

    # ------------------------------------------------------------------
    # Page 5: Clean Live Download Dashboard
    # ------------------------------------------------------------------
    def _build_page_download(self, parent):
        page = ctk.CTkFrame(parent, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(2, weight=1)

        # Hero progress card
        top_card = ctk.CTkFrame(
            page,
            fg_color=BG_CARD,
            border_width=1,
            border_color=BORDER_HAIRLINE,
            corner_radius=18,
        )
        top_card.grid(row=0, column=0, sticky="ew", pady=(6, 12))
        top_card.grid_columnconfigure(1, weight=1)

        self.pulse_dot = ctk.CTkLabel(
            top_card,
            text="●",
            font=("Segoe UI", 14),
            text_color=ACCENT,
        )
        self.pulse_dot.grid(row=0, column=0, padx=(20, 8), pady=(18, 6), sticky="w")

        self.status_label = ctk.CTkLabel(
            top_card,
            text="Initialisation...",
            font=("Segoe UI", 14, "bold"),
            text_color=TEXT_PRIMARY,
            anchor="w",
        )
        self.status_label.grid(row=0, column=1, pady=(18, 6), sticky="ew")

        self.percent_label = ctk.CTkLabel(
            top_card,
            text="0%",
            font=("Segoe UI", 18, "bold"),
            text_color=ACCENT,
        )
        self.percent_label.grid(row=0, column=2, padx=(10, 22), pady=(18, 6), sticky="e")

        self.progress_bar = ctk.CTkProgressBar(
            top_card,
            height=8,
            corner_radius=4,
            progress_color=ACCENT,
            fg_color=BG_ELEVATED,
        )
        self.progress_bar.grid(row=1, column=0, columnspan=3, padx=20, pady=(4, 18), sticky="ew")
        self.progress_bar.set(0.0)

        # Minimalist KPI row
        kpi_row = ctk.CTkFrame(page, fg_color="transparent")
        kpi_row.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        for col in range(4):
            kpi_row.grid_columnconfigure(col, weight=1)

        self.kpi_labels = {}
        kpi_specs = [
            ("downloaded", "Téléchargés", ACCENT),
            ("upgraded", "Remplacés", "#3B82F6"),
            ("skipped", "Ignorés", TEXT_SECONDARY),
            ("failed", "Échecs", DANGER),
        ]
        for col, (key, title, color) in enumerate(kpi_specs):
            box = ctk.CTkFrame(
                kpi_row,
                fg_color=BG_CARD,
                border_width=1,
                border_color=BORDER_HAIRLINE,
                corner_radius=14,
            )
            box.grid(row=0, column=col, padx=(0 if col == 0 else 6, 0 if col == 3 else 6), sticky="ew")
            val_lbl = ctk.CTkLabel(box, text="0", font=("Segoe UI", 22, "bold"), text_color=color)
            val_lbl.pack(pady=(10, 0))
            ctk.CTkLabel(box, text=title, font=("Segoe UI", 11), text_color=TEXT_MUTED).pack(pady=(0, 10))
            self.kpi_labels[key] = val_lbl

        # Clean log stream
        self.log_box = ctk.CTkTextbox(
            page,
            font=("Consolas", 11),
            fg_color=BG_LOG,
            text_color=TEXT_PRIMARY,
            border_width=1,
            border_color=BORDER_HAIRLINE,
            corner_radius=14,
        )
        self.log_box.grid(row=2, column=0, sticky="nsew", pady=(0, 12))
        self.log_box.configure(state="disabled")

        # Bottom action bar
        actions = ctk.CTkFrame(page, fg_color="transparent")
        actions.grid(row=3, column=0, sticky="ew")
        actions.grid_columnconfigure(0, weight=1)

        self.stop_btn = ctk.CTkButton(
            actions,
            text="Arrêter le téléchargement",
            font=("Segoe UI", 13, "bold"),
            height=44,
            corner_radius=12,
            fg_color=DANGER,
            hover_color=DANGER_HOVER,
            text_color="#FFFFFF",
            command=self._request_stop,
        )
        self.stop_btn.grid(row=0, column=0, sticky="ew")

        self.done_actions = ctk.CTkFrame(actions, fg_color="transparent")
        self.done_actions.grid_columnconfigure(0, weight=1)
        self.done_actions.grid_columnconfigure(1, weight=1)
        self.done_actions.grid_columnconfigure(2, weight=1)

        ctk.CTkButton(
            self.done_actions,
            text="Ouvrir le dossier",
            font=("Segoe UI", 13, "bold"),
            height=44,
            corner_radius=12,
            fg_color=BG_CARD,
            hover_color=BG_HOVER,
            border_width=1,
            border_color=BORDER_HAIRLINE,
            text_color=TEXT_PRIMARY,
            command=self._open_output_folder,
        ).grid(row=0, column=0, padx=(0, 6), sticky="ew")

        self.retry_failed_btn = ctk.CTkButton(
            self.done_actions,
            text="Réessayer les échecs",
            font=("Segoe UI", 13, "bold"),
            height=44,
            corner_radius=12,
            fg_color=WARN,
            hover_color=WARN_HOVER,
            text_color="#FFFFFF",
            command=self._retry_failed_tracks,
        )
        self.retry_failed_btn.grid(row=0, column=1, padx=6, sticky="ew")

        ctk.CTkButton(
            self.done_actions,
            text="Nouveau téléchargement",
            font=("Segoe UI", 13, "bold"),
            height=44,
            corner_radius=12,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color="#FFFFFF",
            command=self._reset_to_home,
        ).grid(row=0, column=2, padx=(6, 0), sticky="ew")

        return page

    # ------------------------------------------------------------------
    # Settings View (Clean Slide-in Panel)
    # ------------------------------------------------------------------
    def _build_page_settings(self, parent):
        page = ctk.CTkFrame(parent, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(0, weight=1)
        page.grid_rowconfigure(2, weight=1)

        card = ctk.CTkFrame(
            page,
            fg_color=BG_CARD,
            border_width=1,
            border_color=BORDER_HAIRLINE,
            corner_radius=20,
        )
        card.grid(row=1, column=0, sticky="ew", padx=90)
        card.grid_columnconfigure(1, weight=1)

        header = ctk.CTkFrame(card, fg_color="transparent")
        header.grid(row=0, column=0, columnspan=2, padx=32, pady=(26, 18), sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text="Paramètres",
            font=("Segoe UI", 22, "bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkButton(
            header,
            text="←  Fermer",
            font=("Segoe UI", 12, "bold"),
            height=34,
            width=96,
            corner_radius=10,
            fg_color=BG_ELEVATED,
            hover_color=BG_HOVER,
            text_color=TEXT_SECONDARY,
            command=self._toggle_settings,
        ).grid(row=0, column=1, sticky="e")

        # Theme
        ctk.CTkLabel(card, text="Apparence", font=("Segoe UI", 13, "bold"), text_color=TEXT_PRIMARY).grid(
            row=1, column=0, padx=(32, 14), pady=10, sticky="w"
        )
        self.theme_menu = ctk.CTkSegmentedButton(
            card,
            values=["Dark", "Light", "System"],
            font=("Segoe UI", 12, "bold"),
            selected_color=ACCENT,
            selected_hover_color=ACCENT_HOVER,
            unselected_color=BG_ELEVATED,
            unselected_hover_color=BG_HOVER,
            text_color=TEXT_PRIMARY,
            command=self._change_theme,
        )
        self.theme_menu.grid(row=1, column=1, padx=(0, 32), pady=10, sticky="w")

        # GitHub auto-check updates
        ctk.CTkLabel(card, text="Mises à jour GitHub", font=("Segoe UI", 13, "bold"), text_color=TEXT_PRIMARY).grid(
            row=2, column=0, padx=(32, 14), pady=10, sticky="w"
        )
        upd_row = ctk.CTkFrame(card, fg_color="transparent")
        upd_row.grid(row=2, column=1, padx=(0, 32), pady=10, sticky="ew")

        self.auto_update_var = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(
            upd_row,
            text="Auto-check au démarrage",
            font=("Segoe UI", 12),
            variable=self.auto_update_var,
            progress_color=ACCENT,
            command=self._save_settings,
        ).pack(side="left")

        self.check_now_btn = ctk.CTkButton(
            upd_row,
            text="Vérifier",
            font=("Segoe UI", 12, "bold"),
            height=30,
            width=88,
            corner_radius=9,
            fg_color=BG_ELEVATED,
            hover_color=BG_HOVER,
            text_color=TEXT_PRIMARY,
            command=lambda: self._start_update_check(force=True),
        )
        self.check_now_btn.pack(side="right")

        self.update_status_lbl = ctk.CTkLabel(
            card, text="", font=("Segoe UI", 11), text_color=TEXT_MUTED
        )
        self.update_status_lbl.grid(row=3, column=1, padx=(0, 32), pady=(0, 8), sticky="w")

        # Optional custom Spotify credentials
        cred_box = ctk.CTkFrame(card, fg_color=BG_ELEVATED, corner_radius=14)
        cred_box.grid(row=4, column=0, columnspan=2, padx=32, pady=(6, 14), sticky="ew")
        cred_box.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            cred_box,
            text="API Spotify personnalisée (optionnel)",
            font=("Segoe UI", 12, "bold"),
            text_color=ACCENT,
        ).grid(row=0, column=0, columnspan=2, padx=16, pady=(12, 6), sticky="w")

        ctk.CTkLabel(cred_box, text="Client ID", font=("Segoe UI", 12), text_color=TEXT_SECONDARY).grid(
            row=1, column=0, padx=(16, 10), pady=5, sticky="w"
        )
        self.client_id_entry = ctk.CTkEntry(
            cred_box, placeholder_text="Optionnel (laisser vide pour le mode sans clé)", font=("Segoe UI", 12)
        )
        self.client_id_entry.grid(row=1, column=1, padx=(0, 16), pady=5, sticky="ew")
        self.client_id_entry.bind("<KeyRelease>", lambda e: self._schedule_save_settings())

        ctk.CTkLabel(cred_box, text="Client Secret", font=("Segoe UI", 12), text_color=TEXT_SECONDARY).grid(
            row=2, column=0, padx=(16, 10), pady=(5, 12), sticky="w"
        )
        self.client_secret_entry = ctk.CTkEntry(
            cred_box, placeholder_text="Optionnel", show="*", font=("Segoe UI", 12)
        )
        self.client_secret_entry.grid(row=2, column=1, padx=(0, 16), pady=(5, 12), sticky="ew")
        self.client_secret_entry.bind("<KeyRelease>", lambda e: self._schedule_save_settings())

        # FFmpeg + Footer
        ffmpeg_exe, _ = find_ffmpeg()
        ffmpeg_text = "● FFmpeg inclus & prêt" if ffmpeg_exe else "● FFmpeg (installation auto au 1er lancement)"

        footer = ctk.CTkFrame(card, fg_color="transparent")
        footer.grid(row=5, column=0, columnspan=2, padx=32, pady=(4, 24), sticky="ew")

        ctk.CTkLabel(
            footer,
            text=f"{ffmpeg_text}   ·   v{__version__} par djael-ml",
            font=("Segoe UI", 11),
            text_color=TEXT_MUTED,
        ).pack(side="left")

        ctk.CTkButton(
            footer,
            text="GitHub ↗",
            font=("Segoe UI", 11, "bold"),
            height=28,
            width=78,
            corner_radius=8,
            fg_color="transparent",
            hover_color=BG_ELEVATED,
            text_color=ACCENT,
            command=lambda: webbrowser.open(GITHUB_REPO_URL),
        ).pack(side="right")

        return page

    # ------------------------------------------------------------------
    # Smooth 60fps Animations (Top Loading Bar, Page Glide, Download Bar, Pulse)
    # ------------------------------------------------------------------
    def _animate_top_bar(self, target: float, duration_ms: int = 300):
        if self._top_bar_anim_id is not None:
            self.after_cancel(self._top_bar_anim_id)
            self._top_bar_anim_id = None

        start_val = self._top_bar_value
        target = min(1.0, max(0.0, float(target)))
        steps = max(1, duration_ms // 16)
        state = {"step": 0}

        def tick():
            state["step"] += 1
            t = min(1.0, state["step"] / steps)
            eased = start_val + (target - start_val) * _ease_out_cubic(t)
            self._top_bar_value = eased
            self.top_progress.set(eased)
            if t < 1.0:
                self._top_bar_anim_id = self.after(16, tick)
            else:
                self._top_bar_anim_id = None

        tick()

    def _animate_download_bar(self, target: float):
        self._dl_bar_target = min(1.0, max(0.0, float(target)))
        if self._dl_bar_anim_id is not None:
            return

        def tick():
            diff = self._dl_bar_target - self._dl_bar_value
            if abs(diff) < 0.003:
                self._dl_bar_value = self._dl_bar_target
                self.progress_bar.set(self._dl_bar_value)
                self.top_progress.set(self._dl_bar_value)
                self._top_bar_value = self._dl_bar_value
                self._dl_bar_anim_id = None
                return
            self._dl_bar_value += diff * 0.22
            self.progress_bar.set(self._dl_bar_value)
            self.top_progress.set(self._dl_bar_value)
            self._top_bar_value = self._dl_bar_value
            self._dl_bar_anim_id = self.after(16, tick)

        tick()

    def _start_pulse(self):
        if self._pulse_anim_id is not None:
            return
        shades = ["#1DB954", "#24D160", "#4AE07E", "#24D160", "#1DB954", "#158F40"]

        def tick():
            if not self.is_downloading:
                self._pulse_anim_id = None
                return
            self._pulse_step = (self._pulse_step + 1) % len(shades)
            self.pulse_dot.configure(text_color=shades[self._pulse_step])
            self._pulse_anim_id = self.after(160, tick)

        tick()

    def _stop_pulse(self, final_color=ACCENT):
        if self._pulse_anim_id is not None:
            self.after_cancel(self._pulse_anim_id)
            self._pulse_anim_id = None
        self.pulse_dot.configure(text_color=final_color)

    def show_page(self, page_key: str, animate: bool = True):
        if self._page_anim_id is not None:
            self.after_cancel(self._page_anim_id)
            self._page_anim_id = None

        old_key = self.current_page

        # Determine slide direction (+1 forward glides from right, -1 backward glides from left)
        direction = 1
        if old_key in STEP_ORDER and page_key in STEP_ORDER:
            direction = 1 if STEP_ORDER.index(page_key) >= STEP_ORDER.index(old_key) else -1
        elif old_key == "settings":
            direction = -1

        for k, frame in self.pages.items():
            if k != page_key:
                frame.place_forget()

        if page_key != "settings":
            self.previous_page = page_key
        self.current_page = page_key

        # Refresh contextual page info
        self.step_whisper.configure(text=STEP_META.get(page_key, ""))
        if page_key == "config":
            platform = detect_platform(self.playlist_entry.get().strip())
            self.config_platform_pill.configure(
                text=platform.upper() if platform != "Unknown" else "PLAYLIST"
            )
        elif page_key == "folder":
            self._refresh_folder_info()
        elif page_key == "duplicates":
            platform = detect_platform(self.playlist_entry.get().strip())
            folder = self.dest_entry.get().strip()
            short_folder = folder if len(folder) <= 38 else "..." + folder[-35:]
            self.recap_label.configure(
                text=f"{platform}  ·  {self.selected_quality} kbps  ·  {self.selected_workers} worker(s)  ·  {short_folder}"
            )

        # Animate the sleek top loading bar
        if page_key in STEP_PROGRESS and not self.is_downloading:
            self._animate_top_bar(STEP_PROGRESS[page_key], duration_ms=320)

        target_frame = self.pages[page_key]
        if not animate:
            target_frame.place(relx=0.0, rely=0.0, relwidth=1.0, relheight=1.0)
            return

        # Smooth Framer-Motion-like entrance glide (12 frames @ 16ms)
        start_x = 0.055 * direction
        steps = 12
        state = {"i": 0}
        target_frame.place(relx=start_x, rely=0.0, relwidth=1.0, relheight=1.0)

        def glide():
            state["i"] += 1
            t = min(1.0, state["i"] / steps)
            cur_x = start_x * (1.0 - _ease_out_cubic(t))
            target_frame.place(relx=cur_x, rely=0.0, relwidth=1.0, relheight=1.0)
            if t < 1.0:
                self._page_anim_id = self.after(16, glide)
            else:
                target_frame.place(relx=0.0, rely=0.0, relwidth=1.0, relheight=1.0)
                self._page_anim_id = None

        glide()

    # ------------------------------------------------------------------
    # State & Interactions
    # ------------------------------------------------------------------
    def _load_config_into_widgets(self):
        if self.config.get("spotify_client_id"):
            self.client_id_entry.insert(0, self.config["spotify_client_id"])
        if self.config.get("spotify_client_secret"):
            self.client_secret_entry.insert(0, self.config["spotify_client_secret"])
        if self.config.get("output_directory"):
            self.dest_entry.insert(0, self.config["output_directory"])

        self._select_quality(self.selected_quality, save=False)
        self.workers_slider.set(self.selected_workers)
        self._on_workers_changed(self.selected_workers, save=False)
        self._select_overwrite_mode(self.selected_overwrite, save=False)

        theme = self.config.get("theme", "Dark")
        self.theme_menu.set(theme if theme in ("Dark", "Light", "System") else "Dark")
        self.auto_update_var.set(bool(self.config.get("auto_check_updates", True)))
        self._refresh_folder_info()

    def _schedule_save_settings(self):
        if self._save_debounce_id is not None:
            self.after_cancel(self._save_debounce_id)
        self._save_debounce_id = self.after(450, self._save_settings)

    def _save_settings(self):
        self._save_debounce_id = None
        self.config["spotify_client_id"] = self.client_id_entry.get().strip()
        self.config["spotify_client_secret"] = self.client_secret_entry.get().strip()
        self.config["output_directory"] = self.dest_entry.get().strip()
        self.config["preferred_quality"] = self.selected_quality
        self.config["max_workers"] = self.selected_workers
        self.config["overwrite_mode"] = self.selected_overwrite
        self.config["theme"] = self.theme_menu.get()
        self.config["auto_check_updates"] = bool(self.auto_update_var.get())
        save_config(self.config)

    def _go_home_if_idle(self):
        if not self.is_downloading and self.current_page != "source":
            self.show_page("source")

    def _toggle_settings(self):
        if self.current_page == "settings":
            self._save_settings()
            self.show_page(self.previous_page)
        elif not self.is_downloading:
            self.show_page("settings")

    def _change_theme(self, choice):
        ctk.set_appearance_mode(choice)
        self._save_settings()

    def _paste_from_clipboard(self):
        try:
            clip = self.clipboard_get().strip()
            if clip:
                self.playlist_entry.delete(0, "end")
                self.playlist_entry.insert(0, clip)
                self._on_url_changed()
        except Exception:
            pass

    def _on_url_changed(self, event=None):
        url = self.playlist_entry.get().strip()
        if not url:
            self.platform_chip.configure(text="", text_color=TEXT_MUTED)
            return

        platform = detect_platform(url)
        if platform != "Unknown":
            color = PLATFORM_COLORS.get(platform, ACCENT)
            self.platform_chip.configure(text=f"●  {platform} détecté", text_color=color)
        else:
            self.platform_chip.configure(text="●  Lien générique (yt-dlp)", text_color=TEXT_SECONDARY)

    def _go_from_source(self):
        url = self.playlist_entry.get().strip()
        if not url:
            self.url_box.configure(border_color=DANGER)
            self.platform_chip.configure(
                text="Collez un lien ou un ID Spotify pour continuer",
                text_color=DANGER,
            )
            self.playlist_entry.focus()
            return
        self.url_box.configure(border_color=BORDER_HAIRLINE)
        self.show_page("config")

    def _select_quality(self, q_val: str, save: bool = True):
        self.selected_quality = q_val
        for val, btn in self.quality_buttons.items():
            if val == q_val:
                btn.configure(
                    fg_color=ACCENT_SOFT,
                    border_color=ACCENT,
                    text_color=ACCENT,
                )
            else:
                btn.configure(
                    fg_color=BG_ELEVATED,
                    border_color=BORDER_HAIRLINE,
                    text_color=TEXT_PRIMARY,
                )
        if save:
            self._save_settings()

    def _on_workers_changed(self, val, save: bool = True):
        workers = max(1, min(6, int(round(float(val)))))
        self.selected_workers = workers
        if workers == 1:
            desc = "Séquentiel"
        elif workers <= 3:
            desc = "Équilibré"
        else:
            desc = "Rapide"
        suffix = "worker" if workers == 1 else "workers"
        self.workers_value_label.configure(text=f"{workers} {suffix} · {desc}")
        if save:
            self._schedule_save_settings()

    def _browse_directory(self):
        initial = self.dest_entry.get().strip()
        selected = filedialog.askdirectory(initialdir=initial if os.path.isdir(initial) else None)
        if selected:
            self.dest_entry.delete(0, "end")
            self.dest_entry.insert(0, selected)
            self._save_settings()
            self._refresh_folder_info()

    def _refresh_folder_info(self):
        folder = self.dest_entry.get().strip()
        if not folder:
            self.folder_info_label.configure(text="", text_color=TEXT_MUTED)
            return
        if os.path.isdir(folder):
            try:
                mp3_count = sum(1 for f in os.listdir(folder) if f.lower().endswith(".mp3"))
                self.folder_info_label.configure(
                    text=f"●  Dossier prêt · {mp3_count} fichier(s) MP3 déjà présent(s)",
                    text_color=ACCENT,
                )
            except OSError:
                self.folder_info_label.configure(text="●  Dossier prêt", text_color=ACCENT)
        else:
            self.folder_info_label.configure(
                text="●  Ce dossier sera créé automatiquement",
                text_color="#3B82F6",
            )

    def _go_from_folder(self):
        folder = self.dest_entry.get().strip()
        if not folder:
            self.folder_box.configure(border_color=DANGER)
            self.folder_info_label.configure(
                text="Veuillez choisir un dossier de destination",
                text_color=DANGER,
            )
            return
        self.folder_box.configure(border_color=BORDER_HAIRLINE)
        self._save_settings()
        self.show_page("duplicates")

    def _hover_card(self, frame, mode_key, entering: bool):
        if mode_key == self.selected_overwrite:
            return
        frame.configure(
            fg_color=BG_HOVER if entering else BG_ELEVATED,
            border_color=BORDER_SOFT if entering else BORDER_HAIRLINE,
        )

    def _select_overwrite_mode(self, mode_key: str, save: bool = True):
        self.selected_overwrite = mode_key
        for key, (frame, dot, title_lbl) in self.overwrite_cards.items():
            if key == mode_key:
                frame.configure(fg_color=ACCENT_SOFT, border_color=ACCENT)
                dot.configure(text="●", text_color=ACCENT)
                title_lbl.configure(text_color=ACCENT)
            else:
                frame.configure(fg_color=BG_ELEVATED, border_color=BORDER_HAIRLINE)
                dot.configure(text="○", text_color=TEXT_MUTED)
                title_lbl.configure(text_color=TEXT_PRIMARY)
        if save:
            self._save_settings()

    # ------------------------------------------------------------------
    # Download Lifecycle
    # ------------------------------------------------------------------
    def _start_download(self, tracks_override=None):
        playlist_url = self.playlist_entry.get().strip()
        output_dir = self.dest_entry.get().strip()

        if not tracks_override and not playlist_url:
            self.show_page("source")
            return
        if not output_dir:
            self.show_page("folder")
            return

        self._save_settings()
        self.is_downloading = True
        self.stop_requested = False
        self.last_summary = None
        self.show_page("download")

        self.settings_btn.configure(state="disabled")
        self.done_actions.grid_forget()
        self.stop_btn.configure(state="normal", text="Arrêter le téléchargement")
        self.stop_btn.grid(row=0, column=0, sticky="ew")

        self._dl_bar_value = 0.0
        self._dl_bar_target = 0.0
        self.progress_bar.set(0.0)
        self.top_progress.set(0.0)
        self._top_bar_value = 0.0
        self.percent_label.configure(text="0%")
        self.status_label.configure(text="Récupération de la playlist...")
        for lbl in self.kpi_labels.values():
            lbl.configure(text="0")

        self.log_box.configure(state="normal")
        self.log_box.delete("0.0", "end")
        self.log_box.configure(state="disabled")
        self._append_log(f"[START] {__app_name__} v{__version__}")

        self._start_pulse()

        client_id = self.client_id_entry.get().strip() or None
        client_secret = self.client_secret_entry.get().strip() or None

        self.download_thread = threading.Thread(
            target=self._run_orchestrator_worker,
            args=(
                playlist_url,
                output_dir,
                self.selected_quality,
                client_id,
                client_secret,
                self.selected_workers,
                self.selected_overwrite,
                tracks_override,
            ),
            daemon=True,
        )
        self.download_thread.start()

    def _run_orchestrator_worker(
        self,
        playlist_url,
        output_dir,
        bitrate,
        client_id,
        client_secret,
        max_workers,
        overwrite_mode,
        tracks_override,
    ):
        def log_cb(msg):
            self.gui_queue.put({"type": "log", "content": msg})

        def progress_cb(overall, status):
            self.gui_queue.put({"type": "progress", "value": overall, "status": status})

        def stats_cb(summary):
            self.gui_queue.put({"type": "stats", "summary": summary})

        def stop_check_cb():
            return self.stop_requested

        try:
            summary = orchestrate_download(
                playlist_url=playlist_url,
                output_dir=output_dir,
                bitrate=bitrate,
                client_id=client_id,
                client_secret=client_secret,
                max_workers=max_workers,
                overwrite_mode=overwrite_mode,
                tracks_override=tracks_override,
                log_cb=log_cb,
                progress_cb=progress_cb,
                stats_cb=stats_cb,
                stop_check_cb=stop_check_cb,
            )
            self.gui_queue.put({"type": "done", "summary": summary})
        except Exception as e:
            self.gui_queue.put({"type": "error", "content": str(e)})

    def _request_stop(self):
        if self.download_thread and self.download_thread.is_alive():
            self.stop_requested = True
            self.stop_btn.configure(state="disabled", text="Annulation...")
            self.status_label.configure(text="Annulation en cours...")
            self._append_log("[INFO] Demande d'arrêt immédiat envoyée aux workers...")

    def _append_log(self, text: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _open_output_folder(self):
        folder = self.dest_entry.get().strip()
        if os.path.isdir(folder):
            if sys.platform == "win32":
                os.startfile(folder)
            else:
                webbrowser.open(f"file://{folder}")

    def _retry_failed_tracks(self):
        if self.last_summary and self.last_summary.failed_tracks:
            failed_only = [trk for trk, _ in self.last_summary.failed_tracks]
            self._start_download(tracks_override=failed_only)

    def _reset_to_home(self):
        self.playlist_entry.delete(0, "end")
        self._on_url_changed()
        self.show_page("source")
        self.playlist_entry.focus()

    # ------------------------------------------------------------------
    # GitHub Auto-Update Check
    # ------------------------------------------------------------------
    def _start_update_check(self, force: bool = False):
        if force:
            self.check_now_btn.configure(state="disabled", text="...")
            self.update_status_lbl.configure(text="Vérification sur GitHub...", text_color=TEXT_MUTED)

        def worker():
            info = check_for_updates(self.config, force=force)
            save_config(self.config)
            self.gui_queue.put({"type": "update_result", "info": info, "forced": force})

        threading.Thread(target=worker, daemon=True).start()

    def _show_update_pill(self, info):
        self.pending_update = info
        action_txt = "Mettre à jour" if can_self_update_exe(info) else "Voir sur GitHub ↗"
        self.update_pill_btn.configure(text=f"✦  v{info.latest_version} disponible · {action_txt}")
        self.update_pill.grid(row=0, column=1, sticky="e", padx=(0, 12), pady=12)

    def _skip_current_update(self):
        if self.pending_update:
            self.config["skipped_version"] = self.pending_update.latest_version
            save_config(self.config)
        self.update_pill.grid_forget()

    def _apply_or_open_update(self):
        if not self.pending_update:
            return
        info = self.pending_update
        if not can_self_update_exe(info):
            webbrowser.open(info.release_url)
            return

        self.update_pill_btn.configure(state="disabled", text="Téléchargement...")

        def dl_worker():
            try:
                def prog(pct):
                    self.gui_queue.put({"type": "update_dl_progress", "pct": pct})

                ok = download_and_apply_exe_update(info, progress_cb=prog)
                self.gui_queue.put({"type": "update_dl_done", "ok": ok})
            except Exception as exc:
                self.gui_queue.put({"type": "update_dl_error", "error": str(exc)})

        threading.Thread(target=dl_worker, daemon=True).start()

    # ------------------------------------------------------------------
    # GUI Event Queue
    # ------------------------------------------------------------------
    def _poll_queue(self):
        try:
            while True:
                msg = self.gui_queue.get_nowait()
                mtype = msg.get("type")

                if mtype == "log":
                    self._append_log(msg["content"])
                elif mtype == "progress":
                    val = float(msg["value"])
                    self._animate_download_bar(val)
                    self.percent_label.configure(text=f"{int(round(val * 100))}%")
                    self.status_label.configure(text=msg["status"])
                elif mtype == "stats":
                    s = msg["summary"]
                    self.kpi_labels["downloaded"].configure(text=str(s.downloaded))
                    self.kpi_labels["upgraded"].configure(text=str(s.upgraded))
                    self.kpi_labels["skipped"].configure(text=str(s.skipped))
                    self.kpi_labels["failed"].configure(text=str(s.failed))
                elif mtype == "done":
                    summary = msg["summary"]
                    self.last_summary = summary
                    self.is_downloading = False
                    self.settings_btn.configure(state="normal")

                    self.kpi_labels["downloaded"].configure(text=str(summary.downloaded))
                    self.kpi_labels["upgraded"].configure(text=str(summary.upgraded))
                    self.kpi_labels["skipped"].configure(text=str(summary.skipped))
                    self.kpi_labels["failed"].configure(text=str(summary.failed))

                    self.stop_btn.grid_forget()
                    self.done_actions.grid(row=0, column=0, sticky="ew")
                    if summary.failed_tracks:
                        self.retry_failed_btn.configure(
                            state="normal", text=f"Réessayer ({len(summary.failed_tracks)})"
                        )
                    else:
                        self.retry_failed_btn.configure(state="disabled", text="Aucun échec")

                    if summary.cancelled:
                        self._stop_pulse(WARN)
                        self.status_label.configure(text="Téléchargement annulé.")
                    else:
                        self._stop_pulse(ACCENT)
                        self._animate_download_bar(1.0)
                        self.percent_label.configure(text="100%")
                        self.status_label.configure(
                            text=f"Terminé · {summary.downloaded} téléchargé(s), {summary.upgraded} remplacé(s), {summary.skipped} ignoré(s)"
                        )
                elif mtype == "error":
                    self.is_downloading = False
                    self._stop_pulse(DANGER)
                    self.settings_btn.configure(state="normal")
                    self.status_label.configure(text="Erreur lors de l'extraction.")
                    self._append_log(f"[FATAL ERROR] {msg['content']}")
                    self.stop_btn.grid_forget()
                    self.done_actions.grid(row=0, column=0, sticky="ew")
                    self.retry_failed_btn.configure(state="disabled", text="Réessayer")
                    messagebox.showerror("Erreur", f"Le processus a échoué :\n{msg['content']}")
                elif mtype == "update_result":
                    info = msg["info"]
                    forced = msg["forced"]
                    if forced:
                        self.check_now_btn.configure(state="normal", text="Vérifier")
                    if info:
                        self._show_update_pill(info)
                        if forced:
                            self.update_status_lbl.configure(
                                text=f"Nouvelle version v{info.latest_version} disponible sur GitHub !",
                                text_color=ACCENT,
                            )
                    elif forced:
                        self.update_status_lbl.configure(
                            text=f"Vous êtes à jour (v{__version__}).",
                            text_color=ACCENT,
                        )
                elif mtype == "update_dl_progress":
                    pct = int(msg["pct"] * 100)
                    self.update_pill_btn.configure(text=f"Téléchargement {pct}%")
                elif mtype == "update_dl_done":
                    if msg["ok"]:
                        self.destroy()
                        return
                elif mtype == "update_dl_error":
                    self.update_pill_btn.configure(state="normal", text="Mettre à jour")
                    messagebox.showerror("Erreur de mise à jour", msg["error"])

                self.gui_queue.task_done()
        except queue.Empty:
            pass

        self.after(80, self._poll_queue)

    def _on_close_window(self):
        if self.is_downloading:
            if not messagebox.askyesno(
                "Téléchargement en cours",
                "Un téléchargement est en cours. Voulez-vous vraiment arrêter et quitter ?",
            ):
                return
            self.stop_requested = True
        self._save_settings()
        self.destroy()


# ----------------------------------------------------------------------
# CLI Mode
# ----------------------------------------------------------------------
def attach_to_console():
    """Attach stdout/stderr to parent console ONLY when frozen without a console."""
    if sys.platform == "win32" and getattr(sys, "frozen", False) and sys.stdout is None:
        import ctypes

        try:
            if ctypes.windll.kernel32.AttachConsole(-1):
                sys.stdout = open("CONOUT$", "w", encoding="utf-8")
                sys.stderr = open("CONOUT$", "w", encoding="utf-8")
        except Exception:
            pass


def run_cli(args):
    """Executes the downloader in headless CLI mode."""
    attach_to_console()
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    config = load_config()

    if args.check_update:
        info = check_for_updates(config, force=True)
        save_config(config)
        if info:
            print(f"[UPDATE] New version available: v{info.latest_version} (current: v{info.current_version})")
            print(f"         Release URL: {info.release_url}")
        else:
            print(f"[UPDATE] Already up to date (v{__version__}).")
        if not args.playlist:
            sys.exit(0)

    if not args.no_update_check and not args.check_update:
        info = check_for_updates(config, force=False, timeout=3)
        if info:
            save_config(config)
            print(f"[UPDATE] New version v{info.latest_version} available: {info.release_url}\n")

    if not args.playlist:
        print("Error: Playlist URL or Spotify ID must be specified using --playlist or -p.")
        sys.exit(1)

    if not args.output:
        print("Error: Output directory must be specified using --output or -o.")
        sys.exit(1)

    os.makedirs(args.output, exist_ok=True)

    workers = max(1, min(8, int(args.workers)))
    print(f"{__app_name__} v{__version__} (CLI Mode)")
    print(f"Playlist         : {args.playlist}")
    print(f"Output Directory : {args.output}")
    print(f"Quality          : {args.quality} kbps")
    print(f"Workers          : {workers}")
    print(f"Duplicates Mode  : {args.overwrite}")
    print("Starting download process...\n")

    client_id = config.get("spotify_client_id") or None
    client_secret = config.get("spotify_client_secret") or None
    cancelled = {"flag": False}

    try:
        summary = orchestrate_download(
            playlist_url=args.playlist,
            output_dir=args.output,
            bitrate=args.quality,
            client_id=client_id,
            client_secret=client_secret,
            max_workers=workers,
            overwrite_mode=args.overwrite,
            stop_check_cb=lambda: cancelled["flag"],
        )
        if summary.cancelled:
            print("\nDownload process was interrupted.")
            sys.exit(130)
        print("\nDownload process completed!")
        sys.exit(0 if summary.failed == 0 else 2)
    except KeyboardInterrupt:
        cancelled["flag"] = True
        print("\n[STOP] Interrupted by user (Ctrl+C).")
        sys.exit(130)
    except Exception as e:
        print(f"\nFatal Error: {e}")
        sys.exit(1)


def run_gui():
    if ctk is None:
        print("Error: 'customtkinter' is required for GUI mode. Install it with: pip install -r requirements.txt")
        sys.exit(1)
    try:
        app = DjaelYTPlaylistDWNLD()
        app.mainloop()
    except Exception:
        logger = setup_logging()
        logger.exception("Unhandled GUI exception")
        raise


def main():
    parser = argparse.ArgumentParser(
        description=f"{__app_name__} v{__version__} - Multi-platform Playlist to MP3 Downloader"
    )
    parser.add_argument("--version", "-v", action="version", version=f"{__app_name__} {__version__}")
    parser.add_argument("--playlist", "-p", help="Playlist/album/track URL or Spotify ID")
    parser.add_argument("--output", "-o", help="Output directory path to store downloaded MP3s")
    parser.add_argument(
        "--quality", "-q", choices=["320", "256", "192", "128"], default="320", help="Target bitrate in kbps"
    )
    parser.add_argument(
        "--workers", "-w", type=int, default=3, help="Number of parallel download workers (1-8, default: 3)"
    )
    parser.add_argument(
        "--overwrite",
        choices=["upgrade", "replace", "skip"],
        default="upgrade",
        help="Duplicate handling: upgrade (default), replace, or skip",
    )
    parser.add_argument("--check-update", action="store_true", help="Check GitHub for a newer release")
    parser.add_argument("--no-update-check", action="store_true", help="Skip automatic GitHub update check")
    parser.add_argument("--cli", action="store_true", help="Force headless CLI execution mode")
    args = parser.parse_args()

    if args.cli or args.playlist or args.output or args.check_update:
        run_cli(args)
    else:
        run_gui()


if __name__ == "__main__":
    main()
