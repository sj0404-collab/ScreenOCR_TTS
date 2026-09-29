"""
Material Design 3 color palette and dark theme for ScreenOCR-TTS.

MD3 Dark Theme colors (tonal palette):
- Primary: #A8C7FA (blue)
- Secondary: #C2E7FF (light blue)
- Tertiary: #FFB4AB (red accent)
- Surface: #1C1B1F (dark surface)
- On-Surface: #E6E1E5 (light text)
"""

# ── MD3 Dark Theme Palette ──────────────────────────────────────────
MD3 = {
    # Primary
    "primary": "#A8C7FA",
    "on_primary": "#003061",
    "primary_container": "#004881",
    "on_primary_container": "#D1E4FF",

    # Secondary
    "secondary": "#BEC6DC",
    "on_secondary": "#283141",
    "secondary_container": "#3E4758",
    "on_secondary_container": "#DAE2F9",

    # Tertiary
    "tertiary": "#EFBCBA",
    "on_tertiary": "#4A2524",
    "tertiary_container": "#633B3A",
    "on_tertiary_container": "#FFDAD6",

    # Error
    "error": "#FFB4AB",
    "on_error": "#690005",
    "error_container": "#93000A",
    "on_error_container": "#FFDAD6",

    # Surface (dark)
    "surface": "#1C1B1F",
    "surface_dim": "#141218",
    "surface_bright": "#3B383E",
    "surface_container_lowest": "#0F0D13",
    "surface_container_low": "#1D1B20",
    "surface_container": "#211F26",
    "surface_container_high": "#2B2930",
    "surface_container_highest": "#36343B",
    "on_surface": "#E6E1E5",
    "on_surface_variant": "#CAC4D0",

    # Outline
    "outline": "#938F99",
    "outline_variant": "#49454F",

    # Background
    "background": "#1C1B1F",
    "on_background": "#E6E1E5",

    # Inverse
    "inverse_surface": "#E6E1E5",
    "inverse_on_surface": "#313033",
    "inverse_primary": "#0061A4",

    # Shadows
    "shadow": "#000000",
    "scrim": "#000000",

    # Specific accents
    "accent_green": "#81C784",
    "accent_blue": "#64B5F6",
    "accent_orange": "#FFB74D",
    "accent_red": "#EF5350",
    "accent_pink": "#F48FB1",
    "accent_purple": "#CE93D8",
    "accent_cyan": "#4DD0E1",
}


def get_stylesheet():
    """Return the full MD3 dark theme stylesheet."""
    m = MD3
    return f"""
        /* ── Global ─────────────────────────────────────────────── */
        * {{
            font-size: 14px;
            font-family: "Segoe UI Variable", "Segoe UI", "SF Pro Display", sans-serif;
            color: {m['on_surface']};
        }}

        QMainWindow, QWidget {{
            background-color: {m['surface']};
        }}

        /* ── Typography ─────────────────────────────────────────── */
        QLabel {{
            color: {m['on_surface']};
            background: transparent;
        }}

        /* ── Buttons ────────────────────────────────────────────── */
        QPushButton {{
            background-color: {m['secondary_container']};
            color: {m['on_secondary_container']};
            border: none;
            border-radius: 20px;
            padding: 10px 24px;
            font-weight: 600;
            min-height: 16px;
        }}
        QPushButton:hover {{
            background-color: {m['surface_container_high']};
        }}
        QPushButton:pressed {{
            background-color: {m['surface_container_highest']};
        }}
        QPushButton:disabled {{
            background-color: {m['surface_container']};
            color: {m['outline']};
        }}
        QPushButton:checked {{
            background-color: {m['primary']};
            color: {m['on_primary']};
        }}

        /* ── Check Box ──────────────────────────────────────────── */
        QCheckBox {{
            color: {m['on_surface']};
            spacing: 8px;
        }}
        QCheckBox::indicator {{
            width: 20px;
            height: 20px;
            border-radius: 4px;
            border: 2px solid {m['outline']};
            background: transparent;
        }}
        QCheckBox::indicator:checked {{
            background-color: {m['primary']};
            border-color: {m['primary']};
        }}
        QCheckBox::indicator:hover {{
            border-color: {m['on_surface']};
        }}

        /* ── Combo Box ──────────────────────────────────────────── */
        QComboBox {{
            background-color: {m['surface_container_high']};
            color: {m['on_surface']};
            border: 1px solid {m['outline']};
            border-radius: 8px;
            padding: 6px 12px;
            min-height: 20px;
        }}
        QComboBox:hover {{
            border-color: {m['on_surface']};
        }}
        QComboBox::drop-down {{
            border: none;
            width: 30px;
        }}
        QComboBox QAbstractItemView {{
            background-color: {m['surface_container_high']};
            color: {m['on_surface']};
            border: 1px solid {m['outline']};
            border-radius: 8px;
            selection-background-color: {m['primary_container']};
            selection-color: {m['on_primary_container']};
            padding: 4px;
        }}

        /* ── Spin Box ───────────────────────────────────────────── */
        QSpinBox {{
            background-color: {m['surface_container_high']};
            color: {m['on_surface']};
            border: 1px solid {m['outline']};
            border-radius: 8px;
            padding: 4px 8px;
        }}
        QSpinBox:hover {{
            border-color: {m['on_surface']};
        }}

        /* ── Text Input ─────────────────────────────────────────── */
        QLineEdit, QTextEdit, QPlainTextEdit {{
            background-color: {m['surface_container_high']};
            color: {m['on_surface']};
            border: 1px solid {m['outline']};
            border-radius: 8px;
            padding: 8px;
            selection-background-color: {m['primary']};
            selection-color: {m['on_primary']};
        }}
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
            border-color: {m['primary']};
        }}

        /* ── Slider ─────────────────────────────────────────────── */
        QSlider::groove:horizontal {{
            background: {m['surface_container_highest']};
            height: 4px;
            border-radius: 2px;
        }}
        QSlider::handle:horizontal {{
            background: {m['primary']};
            width: 20px;
            height: 20px;
            margin: -8px 0;
            border-radius: 10px;
        }}
        QSlider::handle:horizontal:hover {{
            background: {m['on_primary_container']};
        }}
        QSlider::sub-page:horizontal {{
            background: {m['primary']};
            border-radius: 2px;
        }}

        /* ── Group Box ──────────────────────────────────────────── */
        QGroupBox {{
            color: {m['on_surface']};
            font-weight: bold;
            border: 1px solid {m['outline_variant']};
            border-radius: 12px;
            margin-top: 12px;
            padding: 16px 12px 12px 12px;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 16px;
            padding: 0 6px;
            color: {m['on_surface']};
        }}

        /* ── Scroll Bar ─────────────────────────────────────────── */
        QScrollArea {{
            border: none;
            background: transparent;
        }}
        QScrollBar:vertical {{
            background: transparent;
            width: 8px;
            margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background: {m['outline']};
            border-radius: 4px;
            min-height: 30px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {m['on_surface']};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0px;
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: transparent;
        }}

        /* ── Tree Widget ────────────────────────────────────────── */
        QTreeWidget {{
            background-color: {m['surface_container_low']};
            color: {m['on_surface']};
            border: none;
            outline: none;
            border-radius: 8px;
        }}
        QTreeWidget::item {{
            padding: 6px 8px;
            border-radius: 8px;
            margin: 1px 4px;
        }}
        QTreeWidget::item:selected {{
            background-color: {m['secondary_container']};
            color: {m['on_secondary_container']};
        }}
        QTreeWidget::item:hover:!selected {{
            background-color: {m['surface_container_high']};
        }}

        /* ── Tab Widget ─────────────────────────────────────────── */
        QTabWidget::pane {{
            border: 1px solid {m['outline_variant']};
            border-radius: 8px;
            background: {m['surface']};
        }}
        QTabBar::tab {{
            background: {m['surface_container']};
            color: {m['on_surface_variant']};
            border: none;
            border-radius: 8px 8px 0 0;
            padding: 8px 20px;
            margin-right: 2px;
        }}
        QTabBar::tab:selected {{
            background: {m['surface']};
            color: {m['primary']};
            font-weight: bold;
        }}
        QTabBar::tab:hover:!selected {{
            background: {m['surface_container_high']};
        }}

        /* ── Menu ───────────────────────────────────────────────── */
        QMenu {{
            background-color: {m['surface_container_high']};
            color: {m['on_surface']};
            border: 1px solid {m['outline']};
            border-radius: 8px;
            padding: 6px;
        }}
        QMenu::item {{
            padding: 8px 24px;
            border-radius: 4px;
        }}
        QMenu::item:selected {{
            background-color: {m['secondary_container']};
            color: {m['on_secondary_container']};
        }}

        /* ── Tool Button ────────────────────────────────────────── */
        QToolButton {{
            background: transparent;
            color: {m['on_surface_variant']};
            border: none;
            border-radius: 8px;
            padding: 6px;
        }}
        QToolButton:hover {{
            background: {m['surface_container_high']};
        }}
        QToolButton:checked {{
            background: {m['secondary_container']};
            color: {m['on_secondary_container']};
        }}

        /* ── Status Bar ─────────────────────────────────────────── */
        QStatusBar {{
            background-color: {m['surface_container_low']};
            color: {m['on_surface_variant']};
            border-top: 1px solid {m['outline_variant']};
        }}

        /* ── Splitter ───────────────────────────────────────────── */
        QSplitter::handle {{
            background: {m['outline_variant']};
            width: 2px;
        }}

        /* ── Progress Bar ───────────────────────────────────────── */
        QProgressBar {{
            background-color: {m['surface_container_highest']};
            border: none;
            border-radius: 4px;
            height: 6px;
            text-align: center;
            color: transparent;
        }}
        QProgressBar::chunk {{
            background-color: {m['primary']};
            border-radius: 4px;
        }}

        /* ── Radio Button ───────────────────────────────────────── */
        QRadioButton {{
            color: {m['on_surface']};
            spacing: 8px;
        }}
        QRadioButton::indicator {{
            width: 20px;
            height: 20px;
            border-radius: 10px;
            border: 2px solid {m['outline']};
            background: transparent;
        }}
        QRadioButton::indicator:checked {{
            background: {m['primary']};
            border-color: {m['primary']};
        }}
        QRadioButton::indicator:hover {{
            border-color: {m['on_surface']};
        }}

        /* ── Scroll Bar Horizontal ──────────────────────────────── */
        QScrollBar:horizontal {{
            background: transparent;
            height: 8px;
        }}
        QScrollBar::handle:horizontal {{
            background: {m['outline']};
            border-radius: 4px;
            min-width: 30px;
        }}
        QScrollBar::handle:horizontal:hover {{
            background: {m['on_surface']};
        }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
            width: 0px;
        }}
    """
