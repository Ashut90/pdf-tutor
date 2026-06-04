"""
Entry point: python -m pdf_tutor
Sets up the ttk theme and launches the main window.
"""
import os
import sys
import warnings
warnings.filterwarnings("ignore", message="Unable to import Axes3D")
from tkinter import ttk
from pdf_tutor.config import INPUT, TEXT, ACCENT, BORDER, MUTED
from pdf_tutor.ui.app import App


def _print_startup_notice():
    if not sys.stdout.isatty():
        return
    CYAN  = "\033[96m"
    YELLOW = "\033[93m"
    BOLD  = "\033[1m"
    RESET = "\033[0m"
    print(f"\n{CYAN}{'─' * 54}{RESET}")
    print(f"  {BOLD}📚 Kritrim Smriti{RESET}  •  AI study assistant")
    print(f"  github.com/Ashut90/pdf-tutor")
    print(f"  {YELLOW}If this saves you time, a ⭐ keeps it alive.{RESET}")
    print(f"{CYAN}{'─' * 54}{RESET}\n")


def main():
    _print_startup_notice()
    app = App()
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure("TCombobox",
        fieldbackground=INPUT, background=INPUT, foreground=TEXT,
        selectbackground=ACCENT, bordercolor=BORDER, lightcolor=BORDER,
        darkcolor=BORDER, arrowcolor=MUTED)
    app.mainloop()


if __name__ == "__main__":
    main()
