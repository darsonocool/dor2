#!/usr/bin/env python3
# main.py - rewritten UI-focused entrypoint (soft pink accent)
from dotenv import load_dotenv
load_dotenv()

# --- Auto-enable MYCLI_LIVE for Termux (unless overridden) ---
import os, sys
if os.environ.get("MYCLI_LIVE") is None:
    if "--no-live" in sys.argv:
        try: sys.argv.remove("--no-live")
        except: pass
    elif "--live" in sys.argv:
        os.environ["MYCLI_LIVE"] = "1"
        try: sys.argv.remove("--live")
        except: pass
    else:
        if os.path.exists("/data/data/com.termux") or os.path.exists("/data/data/com.termux/files/home"):
            os.environ["MYCLI_LIVE"] = "1"
# --------------------------------------------------------------

import re
import time
import argparse
import traceback
import json
import pprint
from datetime import datetime
from typing import Optional

# Rich imports
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.align import Align
from rich.text import Text
from rich.markdown import Markdown

# App imports — keep original calls intact
from app.menus.util import clear_screen, pause
# add send_api_request here (used to compute total quota)
from app.client.engsel import get_balance, get_tiering_info, send_api_request
from app.client.famplan import validate_msisdn
from app.menus.payment import show_transaction_history
from app.service.auth import AuthInstance
from app.menus.bookmark import show_bookmark_menu
from app.menus.account import show_account_menu
from app.menus.package import (
    fetch_my_packages,
    get_packages_by_family,
    show_package_details,
)
from app.menus.hot import show_hot_menu, show_hot_menu2
from app.service.sentry import enter_sentry_mode
from app.menus.purchase import purchase_by_family
from app.menus.famplan import show_family_info
from app.menus.circle import show_circle_info
from app.menus.notification import show_notification_menu
from app.menus.store.segments import show_store_segments_menu
from app.menus.store.search import show_family_list_menu, show_store_packages_menu
from app.menus.store.redemables import show_redeemables_menu
from app.client.registration import dukcapil
from app.service.git import check_for_updates

console = Console()

# ----------------------------
# CLI args
# ----------------------------
parser = argparse.ArgumentParser(add_help=False)
parser.add_argument('--live', action='store_true', help='Enable live quota progress view for package screens')
parser.add_argument('--compact', action='store_true', help='Compact display mode')
parser.add_argument('--theme', type=str, default='default', help='Theme: default | neon | dark')
_known_args, _ = parser.parse_known_args()
if _known_args.live:
    os.environ['MYCLI_LIVE'] = '1'
COMPACT = bool(_known_args.compact)
THEME = (_known_args.theme or 'default').lower()

# ----------------------------
# Theme & layout config
# ----------------------------
ACCENT_PINK = "#ffb6c1"  # soft pink
THEMES = {
    "default": {"title_border": ACCENT_PINK, "sub": "cyan", "accent": ACCENT_PINK},
    "neon":    {"title_border": "bright_magenta", "sub": "bright_cyan", "accent": "bright_yellow"},
    "dark":    {"title_border": "white", "sub": "cyan", "accent": "green"},
}
THEME_CFG = THEMES.get(THEME, THEMES["default"])
WIDTH = 72 if not COMPACT else 60

# ----------------------------
# Menu definitions
# ----------------------------
MENU_ITEMS = [
    ("1", "Login / Switch account"),
    ("2", "Lihat Paket Saya"),
    ("3", "Beli Paket 🔥 HOT 🔥"),
    ("4", "Beli Paket 🔥 HOT-2 🔥"),
    ("5", "Beli Paket (Option Code)"),
    ("6", "Beli Paket (Family Code)"),
    ("7", "Beli Semua Paket di Family (loop)"),
    ("8", "Riwayat Transaksi"),
    ("9", "Family Plan / Akrab Organizer"),
    ("10", "Circle"),
    ("11", "Store Segments"),
    ("12", "Store Family List"),
    ("13", "Store Packages"),
    ("14", "Redeemables"),
    ("00", "Bookmark Paket"),
    ("R", "Register (Dukcapil)"),
    ("N", "Notifikasi"),
    ("V", "Validate MSISDN"),
    ("S", "Sentry mode"),
    ("99", "Tutup aplikasi"),
]

ICONS = {
    "1": "👤", "2": "📦", "3": "🔥", "4": "🔥", "5": "🔢", "6": "👪",
    "7": "🔁", "8": "🧾", "9": "🏠", "10": "🔗", "11": "🛍️", "12": "📋",
    "13": "📦", "14": "🎁", "00": "🔖", "r": "📝", "n": "🔔", "v": "✅",
    "s": "🛡️", "99": "✖️"
}

# ----------------------------
# Session state
# ----------------------------
STATS = {"actions": 0, "by_key": {}}
WELCOME_SHOWN_THIS_SESSION = False

def incr_stat(key: str):
    STATS["actions"] += 1
    STATS["by_key"].setdefault(key, 0)
    STATS["by_key"][key] += 1

# ----------------------------
# Helpers
# ----------------------------
def safe_int(v, default=0):
    try:
        return int(v)
    except Exception:
        return default

def fmt_date(ts: Optional[int]):
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
    except Exception:
        return "N/A"

def safe_prompt(prompt_markup: str, default: str = "") -> str:
    try:
        return Prompt.ask(prompt_markup, default=default)
    except Exception:
        plain = re.sub(r"\[/?[^\]]+\]", "", prompt_markup).strip()
        try:
            return input(plain + " ")
        except Exception:
            return default

def call_no_spinner(msg: str, fn, *args, **kwargs):
    """
    Call a function while showing a single-line message (no spinner).
    Ensures the function output isn't overlaid by a spinner and pauses after call
    to allow user scrolling. Returns whatever fn returns (or None on error).
    """
    # safe defaults in case theme keys missing or None
    accent = THEME_CFG.get("accent") or ACCENT_PINK
    border = THEME_CFG.get("title_border") or ACCENT_PINK

    try:
        # avoid passing style=None to Panel
        console.print(Panel(Text(msg, style="bold " + accent), border_style=border))
    except Exception:
        # fallback
        try:
            print(msg)
        except Exception:
            pass

    try:
        result = fn(*args, **kwargs)
        return result
    except Exception as e:
        # show error in safe panel
        try:
            console.print(Panel(f"[red]Error during '{msg}':[/red]\n{e}", border_style=border))
        except Exception:
            print(f"Error during '{msg}': {e}")
        return None
    finally:
        try:
            pause()
        except Exception:
            pass

# ----------------------------
# Profile builder
# ----------------------------
def build_profile(active_user: dict):
    try:
        balance = get_balance(AuthInstance.api_key, active_user["tokens"]["id_token"])
    except Exception:
        balance = {}
    remaining = balance.get("remaining", "0")
    expired_at = balance.get("expired_at", None)
    point_info = "Points: N/A | Tier: N/A"

    if active_user.get("subscription_type") == "PREPAID":
        try:
            tiering = get_tiering_info(AuthInstance.api_key, active_user["tokens"])
            point_info = f"Points: {tiering.get('current_point', 0)} | Tier: {tiering.get('tier', 0)}"
        except Exception:
            pass

    return {
        "number": active_user.get("number", "Unknown"),
        "subscriber_id": active_user.get("subscriber_id", ""),
        "subscription_type": active_user.get("subscription_type", ""),
        "balance": remaining,
        "balance_expired_at": expired_at,
        "point_info": point_info,
    }

# ----------------------------
# Quota progress helper
# ----------------------------
def _fmt_bytes(n: int) -> str:
    """Format bytes ke human readable."""
    try:
        n = int(n)
    except Exception:
        return "0 B"
    if n >= 1024**3:
        return f"{n / (1024**3):.2f} GB"
    if n >= 1024**2:
        return f"{n / (1024**2):.2f} MB"
    if n >= 1024:
        return f"{n / 1024:.2f} KB"
    return f"{n} B"

def render_quota_bar(remaining_bytes: int, total_bytes: int, width: int = 30):
    """
    Return a Rich Text (if available) or plain string representing a progress bar.
    - remaining_bytes: sisa
    - total_bytes: total (jika 0 atau unknown -> show remaining only)
    """
    try:
        rem = int(remaining_bytes or 0)
        tot = int(total_bytes or 0)
    except Exception:
        rem = 0
        tot = 0

    if tot <= 0:
        label = f"{_fmt_bytes(rem)} / -"
        if 'Text' in globals() and Text:
            t = Text(label, style=ACCENT_PINK)
            return t
        return label

    pct = max(0.0, min(1.0, (rem / tot) if tot else 0.0))
    filled_len = int(round(width * pct))
    empty_len = max(0, width - filled_len)

    filled = "█" * filled_len
    empty = "─" * empty_len
    percent_text = f"{int(pct*100):3d}%"
    label = f"{_fmt_bytes(rem)} / {_fmt_bytes(tot)}"

    if 'Text' in globals() and Text:
        t = Text()
        if filled:
            t.append(filled, style=f"bold {ACCENT_PINK}")
        if empty:
            t.append(empty, style="dim")
        t.append(" ")
        pct_style = "bold"
        if pct <= 0.10:
            pct_style += " red"
        elif pct <= 0.30:
            pct_style += " yellow"
        else:
            pct_style += " green"
        t.append(f"{percent_text} ", style=pct_style)
        t.append(label, style="white")
        return t
    else:
        return f"[{percent_text}] {filled}{empty} {label}"

# ----------------------------
# New helper: compute total quota (best-effort)
# ----------------------------
def get_total_quota(active_user: dict) -> tuple:
    """
    Best-effort: fetch 'api/v8/packages/quota-details' and sum DATA benefits:
    returns (remaining_bytes, total_bytes) as integers.
    On error returns (0, 0).
    """
    try:
        api_key = getattr(AuthInstance, "api_key", None)
        tokens = active_user.get("tokens") if isinstance(active_user, dict) else None
        id_token = None
        if isinstance(tokens, dict):
            id_token = tokens.get("id_token") or tokens.get("idToken") or None

        path = "api/v8/packages/quota-details"
        payload = {"is_enterprise": False, "lang": "en", "family_member_id": ""}

        try:
            res = send_api_request(api_key, path, payload, id_token, method="POST")
        except TypeError:
            try:
                res = send_api_request(api_key, path, payload, id_token)
            except Exception:
                res = None
        except Exception:
            res = None

        if not res or res.get("status") != "SUCCESS":
            return (0, 0)

        quotas = res.get("data", {}).get("quotas", []) or []
        remaining_bytes = 0
        total_bytes = 0

        for q in quotas:
            for b in q.get("benefits", []) or []:
                if b.get("data_type") == "DATA":
                    rem = b.get("remaining", None)
                    tot = b.get("total", None)
                    try:
                        if rem is not None:
                            remaining_bytes += int(rem)
                        elif tot is not None:
                            remaining_bytes += int(tot)
                    except Exception:
                        pass
                    try:
                        if tot is not None:
                            total_bytes += int(tot)
                    except Exception:
                        pass

        return (remaining_bytes, total_bytes)
    except Exception:
        return (0, 0)

# ----------------------------
# Rendering helpers (patched header & menu)
# ----------------------------

def render_header(profile: dict):
    # vertical header with Total Kuota progress bar
    number = profile.get("number", "Unknown")
    sub_type = profile.get("subscription_type", "N/A")
    balance = profile.get("balance", "0")
    expired = fmt_date(profile.get("balance_expired_at"))
    points = profile.get("point_info", "")

    # compute remaining & total (best-effort)
    try:
        remaining_bytes, total_bytes = get_total_quota(AuthInstance.get_active_user() or profile)
    except Exception:
        remaining_bytes, total_bytes = (0, 0)

    header_table = Table.grid(expand=True)
    header_table.add_column(ratio=2)
    header_table.add_column(ratio=3, justify="right")

    header_table.add_row("[bold white]Nomor[/]", f"[bold {THEME_CFG['sub']}]{number}[/]")
    header_table.add_row("[bold white]Tipe[/]", f"[bold {THEME_CFG['title_border']}]{sub_type}[/]")
    header_table.add_row("[bold white]Pulsa[/]", f"[bold {THEME_CFG['accent']}]Rp {balance}[/]")

    bar_render = render_quota_bar(remaining_bytes, total_bytes, width=28)
    if isinstance(bar_render, str):
        header_table.add_row("[bold white]Kuota[/]", f"[bold {ACCENT_PINK}]{bar_render}[/]")
    else:
        header_table.add_row("[bold white]Kuota[/]", bar_render)

    header_table.add_row("[bold white]Aktif sampai[/]", f"[bold green]{expired}[/]")

    if not COMPACT:
        header_table.add_row("", Align.right(f"[{ACCENT_PINK}]{points}[/{ACCENT_PINK}]"))

    if profile.get("subscriber_id"):
        status = Text("● Online", style="green")
    else:
        status = Text("● Offline", style="red")

    title_text = Text()
    title_text.append("Evergarden - Dashboard", style="bold")
    title_text.append("  ")
    title_text.append(status)

    try:
        console.print(Panel(header_table, title=title_text, border_style=THEME_CFG.get("title_border", ACCENT_PINK), width=min(WIDTH, 80), padding=(1, 2)))
    except Exception:
        total_label = _fmt_bytes(remaining_bytes) + (" / " + _fmt_bytes(total_bytes) if total_bytes > 0 else "")
        print(f"Nomor: {number} | Pulsa: Rp {balance} | Kuota: {total_label} | Aktif sampai: {expired}")

def render_menu(filter_keyword: str = ""):
    # vertical menu (one item per row) — avoids side-by-side layout on small screens
    # avoid using box=None to prevent Rich errors; use default table creation
    table = Table(show_edge=False, width=WIDTH)
    table.add_column("Kode", style="bold " + ACCENT_PINK, width=8)
    table.add_column("Menu", style="white")

    matched = []
    for key, label in MENU_ITEMS:
        display_label = f"{ICONS.get(key.lower(), '')}  {label}"
        if filter_keyword:
            if filter_keyword.lower() in label.lower():
                matched.append((key, display_label))
        else:
            matched.append((key, display_label))

    # vertical: each item its own row
    for key, label in matched:
        table.add_row(key, label)

    try:
        console.print(Panel(table, border_style=THEME_CFG["title_border"], title="[bold]Menu[/bold]"))
    except Exception:
        # fallback to plain print
        print("Menu:")
        for key, label in matched:
            print(f"{key} - {label}")

# ----------------------------
# Welcome UI
# ----------------------------
def show_welcome_ui(active_user: dict):
    global WELCOME_SHOWN_THIS_SESSION
    if WELCOME_SHOWN_THIS_SESSION:
        return
    WELCOME_SHOWN_THIS_SESSION = True

    art = r"""
      .-'''-.   .-'''-.
     /  .-.  \ /  .-.  \
    |  /   \  Y  /   \  |
     \ |   | / \ |   | /
      '---'`   '---'`
    """

    number_val = None
    if isinstance(active_user, dict):
        try:
            number_val = active_user.get("number", None)
        except Exception:
            number_val = None
    try:
        number_str = str(number_val).strip() if number_val not in (None, "", []) else None
    except Exception:
        number_str = None

    greeting = f"Welcome{', ' + number_str if number_str else ''} — to Evergarden"
    subtitle = " — Ingfoo unlimitedd bangg"

    try:
        md = Markdown(f"**{greeting}**\n\n{subtitle}\n\n*Sc sunset edited by Tobeli & PPTR also AI.")
    except Exception:
        md = Text(f"{greeting}\n\n{subtitle}\n\nTip: Ketik `find <kata>` untuk cari menu cepat.")
    art_text = Text(art, style=ACCENT_PINK)
    combined = Table.grid(expand=True)
    combined.add_column(ratio=2)
    combined.add_column(ratio=3)
    combined.add_row(art_text, md)

    panel = Panel(combined, title=f"[bold]{greeting}[/bold]", border_style=THEME_CFG.get("title_border", ACCENT_PINK), width=min(WIDTH, 80), padding=(1,2))
    try:
        clear_screen()
    except Exception:
        pass
    console.print(panel)

    console.print("\n[bold]Pilihan:[/]")
    console.print(f"  [{ACCENT_PINK}]Enter[/{ACCENT_PINK}]  → Lanjutkan ke dashboard")
    console.print(f"  [yellow]s[/]    → Skip welcome untuk sesi ini\n")

    prompt_text = f"[{ACCENT_PINK}]Tekan Enter untuk lanjut atau ketik 's' untuk skip[/{ACCENT_PINK}]"
    try:
        resp = Prompt.ask(prompt_text, default="")
    except Exception:
        try:
            resp = input("Tekan Enter untuk lanjut atau ketik 's' untuk skip ")
        except Exception:
            resp = ""
    if isinstance(resp, str) and resp.strip().lower() == "s":
        console.print("[italic]Welcome skipped for this session.[/italic]")
        try:
            time.sleep(0.6)
        except Exception:
            pass

# ----------------------------
# Sentry safe wrapper
# ----------------------------
def safe_enter_sentry(active_user):
    incr_stat("sentry")
    try:
        enter_sentry_mode()
        return True
    except Exception:
        pass
    try:
        api_key = getattr(AuthInstance, 'api_key', None)
        tokens = active_user.get('tokens') if active_user else None
        enter_sentry_mode(api_key, tokens)
        return True
    except Exception:
        pass
    try:
        tokens = active_user.get('tokens') if active_user else None
        enter_sentry_mode(tokens)
        return True
    except Exception:
        pass
    console.print('[yellow]Sentry mode could not be started. Please check service or implement fallback.[/yellow]')
    return False

# ----------------------------
# Choice handler
# ----------------------------
def handle_choice(choice: str, active_user: dict, profile: dict):
    c = (choice or "").strip().lower()
    if c == "t" or c == "":
        return

    if c.startswith("find "):
        keyword = c.split(" ", 1)[1].strip()
        incr_stat("find")
        try:
            clear_screen()
        except Exception:
            pass
        render_header(profile)
        console.print(f"[bold]Hasil pencarian untuk[/] [cyan]'{keyword}'[/]\n")
        render_menu(filter_keyword=keyword)
        return

    incr_stat(c)

    if c == "1":
        sel = show_account_menu()
        if sel:
            try:
                AuthInstance.set_active_user(sel)
            except Exception:
                pass
        else:
            console.print("[red]No user selected.[/]")
        return

    if c == "2":
        call_no_spinner("Mengambil paket...", fetch_my_packages)
        return

    if c == "3":
        call_no_spinner("Memuat HOT...", show_hot_menu)
        return

    if c == "4":
        call_no_spinner("Memuat HOT-2...", show_hot_menu2)
        return

    if c == "5":
        code = Prompt.ask(f"[{ACCENT_PINK}]Enter option code (or '99' to cancel)[/{ACCENT_PINK}]")
        if code != "99":
            call_no_spinner("Mengambil package details...", show_package_details, AuthInstance.api_key, active_user["tokens"], code, False)
        return

    if c == "6":
        fc = Prompt.ask(f"[{ACCENT_PINK}]Enter family code (or '99' to cancel)[/{ACCENT_PINK}]")
        if fc != "99":
            call_no_spinner("Mencari packages by family...", get_packages_by_family, fc)
        return

    if c == "7":
        fc = Prompt.ask(f"[{ACCENT_PINK}]Enter family code (or '99' to cancel)[/{ACCENT_PINK}]")
        if fc == "99":
            return
        start = safe_int(Prompt.ask("Start purchasing from option number", default="1"))
        use_decoy = Confirm.ask(f"[{ACCENT_PINK}]Use decoy package?[/]{''}", default=False)
        pause_on_success = Confirm.ask(f"[{ACCENT_PINK}]Pause on each successful purchase?[/]{''}", default=False)
        delay = safe_int(Prompt.ask("Delay seconds between purchases (0 for none)", default="0"))
        call_no_spinner("Memulai pembelian berulang...", purchase_by_family, fc, use_decoy, pause_on_success, delay, start)
        return

    if c == "8":
        call_no_spinner("Mengambil riwayat...", show_transaction_history, AuthInstance.api_key, active_user["tokens"])
        return

    if c == "9":
        call_no_spinner("Memuat family info...", show_family_info, AuthInstance.api_key, active_user["tokens"])
        return

    if c == "10":
        call_no_spinner("Memuat circle info...", show_circle_info, AuthInstance.api_key, active_user["tokens"])
        return

    if c == "11":
        is_ent = Confirm.ask("Is enterprise store?", default=False)
        call_no_spinner("Memuat store segments...", show_store_segments_menu, is_ent)
        return

    if c == "12":
        is_ent = Confirm.ask("Is enterprise?", default=False)
        call_no_spinner("Memuat family list...", show_family_list_menu, profile["subscription_type"], is_ent)
        return

    if c == "13":
        is_ent = Confirm.ask("Is enterprise?", default=False)
        call_no_spinner("Memuat store packages...", show_store_packages_menu, profile["subscription_type"], is_ent)
        return

    if c == "14":
        is_ent = Confirm.ask("Is enterprise?", default=False)
        call_no_spinner("Memuat redeemables...", show_redeemables_menu, is_ent)
        return

    if c == "00":
        show_bookmark_menu()
        return

    if c == "r":
        msisdn = Prompt.ask(f"[{ACCENT_PINK}]Enter msisdn (628xxxx)[/{ACCENT_PINK}]")
        nik = Prompt.ask(f"[{ACCENT_PINK}]Enter NIK[/{ACCENT_PINK}]")
        kk = Prompt.ask(f"[{ACCENT_PINK}]Enter KK[/{ACCENT_PINK}]")
        call_no_spinner("Melakukan registrasi...", dukcapil, AuthInstance.api_key, msisdn, kk, nik)
        return

    if c == "v":
        msisdn = Prompt.ask(f"[{ACCENT_PINK}]Enter the msisdn to validate (628xxxx)[/{ACCENT_PINK}]")
        call_no_spinner("Validating MSISDN...", validate_msisdn, AuthInstance.api_key, active_user["tokens"], msisdn)
        return

    if c == "n":
        show_notification_menu()
        return

    if c == "s":
        safe_enter_sentry(active_user)
        return

    if c == "99":
        console.print(Panel(f"[bold red]Exiting the application.[/]\n\n[bold]Session actions:[/] {STATS['actions']}", border_style=THEME_CFG["title_border"]))
        for k, v in STATS["by_key"].items():
            console.print(f"  {k}: {v}")
        sys.exit(0)

    console.print(Panel("[red]Invalid choice. Please try again.[/]", border_style="red"))
    pause()

# ----------------------------
# Main loop
# ----------------------------
def main_loop():
    while True:
        active_user = AuthInstance.get_active_user()
        if not active_user:
            sel = show_account_menu()
            if sel:
                try:
                    AuthInstance.set_active_user(sel)
                except Exception:
                    pass
            else:
                console.print("[red]No user selected.[/]")
            continue

        # show welcome once per session
        try:
            show_welcome_ui(active_user)
        except Exception:
            # fail silently but continue
            pass

        profile = build_profile(active_user)
        try:
            clear_screen()
        except Exception:
            pass

        render_header(profile)

        if not COMPACT:
            render_menu()
        else:
            console.print(Panel("[bold]Menu (compact):[/]", border_style=THEME_CFG["title_border"]))
            for key, label in MENU_ITEMS:
                console.print(f"{key} - {ICONS.get(key.lower(),'')} {label.split('(')[0]}")

        choice = safe_prompt(f"\n[{ACCENT_PINK}]Pilih menu (atau 'find <kata>')[/{ACCENT_PINK}]", default="")
        handle_choice(choice, active_user, profile)

if __name__ == "__main__":
    try:
        console.print(Panel("[bold]Checking for updates...[/]", border_style=THEME_CFG["title_border"]))
        try:
            if check_for_updates():
                pause()
        except Exception:
            # ignore update checker errors, don't block startup
            pass
        main_loop()
    except KeyboardInterrupt:
        console.print("\n[red]Exiting the application.[/]")
    except Exception:
        console.print(Panel(f"[red]Unhandled error:[/]\n{traceback.format_exc()}", border_style="red"))