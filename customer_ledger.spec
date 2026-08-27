from pathlib import Path

from PyInstaller.building.build_main import Analysis, COLLECT, EXE, PYZ


project_root = Path(SPECPATH).resolve()
src_root = project_root / "src"
package_root = src_root / "customer_ledger"

datas = [
    (str(package_root / "templates"), "customer_ledger/templates"),
    (str(package_root / "static"), "customer_ledger/static"),
    (str(project_root / "migrations"), "migrations"),
]

hiddenimports = [
    "customer_ledger.backup_service",
    "customer_ledger.bookkeeping_service",
    "customer_ledger.calculation_service",
    "customer_ledger.customer_service",
    "customer_ledger.desktop",
    "customer_ledger.export_service",
    "customer_ledger.legacy_import_service",
    "customer_ledger.models",
    "customer_ledger.routes",
    "customer_ledger.runtime_paths",
    "customer_ledger.validation",
    "customer_ledger.version",
    "alembic",
    "flask_migrate",
    "logging.config",
    "waitress",
    "webview",
    "webview.platforms.edgechromium",
]

analysis = Analysis(
    [str(project_root / "desktop_entry.py")],
    pathex=[str(src_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "ruff"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="CustomerLedger",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    analysis.zipfiles,
    strip=False,
    upx=False,
    name="CustomerLedger",
)
