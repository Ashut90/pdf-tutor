"""
Entry point: python -m pdf_tutor
Sets up the ttk theme and launches the main window.
"""
from tkinter import ttk
from pdf_tutor.config import INPUT, TEXT, ACCENT, BORDER, MUTED
from pdf_tutor.ui.app import App


def main():
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
