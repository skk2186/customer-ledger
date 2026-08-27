"""PyInstaller entry point for the Windows desktop application."""

from customer_ledger.desktop import run_desktop

if __name__ == "__main__":
    raise SystemExit(run_desktop())
