"""GUI application start is defined here."""
import os
import sys

from loguru import logger
from PySide6 import QtGui

from platform_management.version import VERSION

from .app import get_application
from .init_window import InitWindow, InitWindowDefaultValues


def run_gui(
    db_addr: str, db_port: int, db_name: str, db_user: str, db_pass: str, verbose: bool
):  # pylint: disable=too-many-arguments
    """Launch a graphical user interface application to manipulate Digital Urban Studies Platform database data."""

    logger.remove(0)
    logger.add(sys.stderr, level="INFO" if not verbose else "DEBUG")
    logger.debug("Starting the application")

    InitWindow.default_values = InitWindowDefaultValues(db_addr, db_port, db_name, db_user, db_pass)

    app = get_application()
    app.setWindowIcon(
        QtGui.QIcon(
            QtGui.QPixmap(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "resources", "icon.png"))
        )
    )
    app.setApplicationName(f"IDU - Insertion Tool v.{VERSION}")

    window = InitWindow()
    window.show()

    sys.exit(app.exec())
