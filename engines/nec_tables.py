"""
NEC Reference Tables and Constants for PV Cable Sizing.

Contains all National Electrical Code lookup tables, conductor reference data,
and module-level constants used across calculation engines.
"""

# ─── Color Scheme ────────────────────────────────────────────────────────────
COLORS = {
    'primary': '#1A1A1A',
    'secondary': '#DC143C',
    'success': '#C41E3A',
    'danger': '#FF0000',
    'bg_light': '#F5F5F5',
    'bg_medium': '#E0E0E0',
    'white': '#FFFFFF',
    'text_primary': '#1A1A1A',
    'text_secondary': '#666666',
    'calculated': '#FFE6E6',
    'combiner_a': '#E3F2FD',
    'combiner_b': '#FFF8E1',
    'editable_highlight': '#FFF9C4',
}

SWITCHBOARDS = ["A", "B", "C", "D", "E"]

# ─── NEC 310.15(B)(1) Temperature Correction Factors (90°C conductors) ──────
NEC_TEMP_CORRECTION_90C = [
    (10, 1.15), (15, 1.12), (20, 1.08), (25, 1.04), (30, 1.00),
    (35, 0.96), (40, 0.91), (45, 0.87), (50, 0.82), (55, 0.76),
    (60, 0.71), (65, 0.65), (70, 0.58), (75, 0.50), (80, 0.41), (85, 0.29),
]

# ─── NEC 310.15(C)(1) Conduit Fill Adjustment Factors ───────────────────────
NEC_CONDUIT_FILL_FACTORS = [
    (3, 1.00), (6, 0.80), (9, 0.70), (20, 0.50),
    (30, 0.45), (40, 0.40), (999, 0.35),
]

# ─── NEC Table 250.122 — Equipment Grounding Conductor ─────────────────────
NEC_GROUND_CONDUCTOR_CU = [
    (15, "#14 AWG"), (20, "#12 AWG"), (30, "#10 AWG"), (40, "#10 AWG"),
    (60, "#10 AWG"), (100, "#8 AWG"), (200, "#6 AWG"), (300, "#4 AWG"),
    (400, "#3 AWG"), (500, "#2 AWG"), (600, "#1 AWG"), (800, "1/0 AWG"),
    (1000, "2/0 AWG"), (1200, "3/0 AWG"), (1600, "4/0 AWG"), (2000, "250 kcmil"),
    (2500, "350 kcmil"), (3000, "400 kcmil"), (4000, "500 kcmil"),
    (5000, "700 kcmil"), (6000, "800 kcmil"),
]

NEC_GROUND_CONDUCTOR_AL = [
    (15, "#12 AWG"), (20, "#10 AWG"), (30, "#8 AWG"), (40, "#8 AWG"),
    (60, "#8 AWG"), (100, "#6 AWG"), (200, "#4 AWG"), (300, "#2 AWG"),
    (400, "#1 AWG"), (500, "1/0 AWG"), (600, "2/0 AWG"), (800, "3/0 AWG"),
    (1000, "4/0 AWG"), (1200, "250 kcmil"), (1600, "350 kcmil"), (2000, "400 kcmil"),
    (2500, "500 kcmil"), (3000, "600 kcmil"), (4000, "750 kcmil"),
    (5000, "1000 kcmil"), (6000, "1000 kcmil"),
]

# ─── NEC 240.6 Standard OCPD Ratings ───────────────────────────────────────
NEC_STANDARD_OCPD_RATINGS = [
    15, 20, 25, 30, 35, 40, 45, 50, 60, 70, 80, 90, 100,
    110, 125, 150, 175, 200, 225, 250, 300, 350, 400, 450, 500,
    600, 700, 800, 1000, 1200, 1600, 2000, 2500, 3000, 4000, 5000, 6000,
]

# ─── Circular Mils for conductor sizes ──────────────────────────────────────
CIRCULAR_MILS = {
    "#14 AWG": 4110, "#12 AWG": 6530, "#10 AWG": 10380, "#8 AWG": 16510,
    "#6 AWG": 26240, "#4 AWG": 41740, "#3 AWG": 52620, "#2 AWG": 66360,
    "#1 AWG": 83690, "1/0 AWG": 105600, "2/0 AWG": 133100, "3/0 AWG": 167800,
    "4/0 AWG": 211600, "250 kcmil": 250000, "300 kcmil": 300000,
    "350 kcmil": 350000, "400 kcmil": 400000, "500 kcmil": 500000,
    "600 kcmil": 600000, "700 kcmil": 700000, "750 kcmil": 750000,
    "800 kcmil": 800000, "900 kcmil": 900000, "1000 kcmil": 1000000,
}

# ─── Ordered conductor sizes for auto-sizing ────────────────────────────────
CONDUCTOR_SIZES_ORDERED = [
    "#14 AWG", "#12 AWG", "#10 AWG", "#8 AWG", "#6 AWG", "#4 AWG", "#3 AWG",
    "#2 AWG", "#1 AWG", "1/0 AWG", "2/0 AWG", "3/0 AWG", "4/0 AWG",
    "250 kcmil", "300 kcmil", "350 kcmil", "400 kcmil", "500 kcmil",
    "600 kcmil", "700 kcmil", "750 kcmil", "800 kcmil", "900 kcmil", "1000 kcmil",
]

# O(1) index lookup
CONDUCTOR_SIZE_INDEX = {s: i for i, s in enumerate(CONDUCTOR_SIZES_ORDERED)}

# ─── Conductor Reference Data (NEC Table 310.16 ampacities + resistance) ────
CONDUCTOR_REFERENCE_DATA = {
    "COPPER": {
        "#12 AWG": {"Free Air": 40, "60C": 20, "75C": 25, "90C": 30, "Ohms/kFt": 1.9300},
        "#10 AWG": {"Free Air": 55, "60C": 30, "75C": 35, "90C": 40, "Ohms/kFt": 1.2100},
        "#8 AWG": {"Free Air": 80, "60C": 40, "75C": 50, "90C": 55, "Ohms/kFt": 0.7640},
        "#6 AWG": {"Free Air": 105, "60C": 55, "75C": 65, "90C": 75, "Ohms/kFt": 0.4910},
        "#4 AWG": {"Free Air": 140, "60C": 70, "75C": 85, "90C": 95, "Ohms/kFt": 0.3080},
        "#3 AWG": {"Free Air": 165, "60C": 85, "75C": 100, "90C": 115, "Ohms/kFt": 0.2450},
        "#2 AWG": {"Free Air": 190, "60C": 95, "75C": 115, "90C": 130, "Ohms/kFt": 0.1940},
        "#1 AWG": {"Free Air": 220, "60C": 110, "75C": 130, "90C": 145, "Ohms/kFt": 0.1540},
        "1/0 AWG": {"Free Air": 260, "60C": 125, "75C": 150, "90C": 170, "Ohms/kFt": 0.1220},
        "2/0 AWG": {"Free Air": 300, "60C": 145, "75C": 175, "90C": 195, "Ohms/kFt": 0.0967},
        "3/0 AWG": {"Free Air": 350, "60C": 165, "75C": 200, "90C": 225, "Ohms/kFt": 0.0766},
        "4/0 AWG": {"Free Air": 405, "60C": 195, "75C": 230, "90C": 260, "Ohms/kFt": 0.0608},
        "250 kcmil": {"Free Air": 455, "60C": 215, "75C": 255, "90C": 290, "Ohms/kFt": 0.0515},
        "300 kcmil": {"Free Air": 505, "60C": 240, "75C": 285, "90C": 320, "Ohms/kFt": 0.0429},
        "350 kcmil": {"Free Air": 570, "60C": 260, "75C": 310, "90C": 350, "Ohms/kFt": 0.0367},
        "400 kcmil": {"Free Air": 615, "60C": 280, "75C": 335, "90C": 380, "Ohms/kFt": 0.0321},
        "500 kcmil": {"Free Air": 700, "60C": 320, "75C": 380, "90C": 430, "Ohms/kFt": 0.0258},
        "600 kcmil": {"Free Air": 780, "60C": 350, "75C": 420, "90C": 475, "Ohms/kFt": 0.0214},
        "700 kcmil": {"Free Air": 850, "60C": 385, "75C": 460, "90C": 520, "Ohms/kFt": 0.0184},
        "750 kcmil": {"Free Air": 885, "60C": 400, "75C": 475, "90C": 535, "Ohms/kFt": 0.0171},
        "800 kcmil": {"Free Air": 920, "60C": 410, "75C": 490, "90C": 555, "Ohms/kFt": 0.0161},
        "900 kcmil": {"Free Air": 985, "60C": 435, "75C": 520, "90C": 585, "Ohms/kFt": 0.0143},
        "1000 kcmil": {"Free Air": 1040, "60C": 455, "75C": 545, "90C": 615, "Ohms/kFt": 0.0129},
    },
    "ALUMINUM": {
        "#12 AWG": {"Free Air": 30, "60C": 15, "75C": 20, "90C": 25, "Ohms/kFt": 3.1800},
        "#10 AWG": {"Free Air": 40, "60C": 25, "75C": 30, "90C": 35, "Ohms/kFt": 2.0000},
        "#8 AWG": {"Free Air": 60, "60C": 30, "75C": 40, "90C": 45, "Ohms/kFt": 1.2600},
        "#6 AWG": {"Free Air": 80, "60C": 40, "75C": 50, "90C": 60, "Ohms/kFt": 0.8080},
        "#4 AWG": {"Free Air": 105, "60C": 55, "75C": 65, "90C": 75, "Ohms/kFt": 0.5080},
        "#3 AWG": {"Free Air": 130, "60C": 65, "75C": 75, "90C": 85, "Ohms/kFt": 0.4030},
        "#2 AWG": {"Free Air": 150, "60C": 75, "75C": 90, "90C": 100, "Ohms/kFt": 0.3190},
        "#1 AWG": {"Free Air": 175, "60C": 85, "75C": 100, "90C": 115, "Ohms/kFt": 0.2530},
        "1/0 AWG": {"Free Air": 205, "60C": 100, "75C": 120, "90C": 135, "Ohms/kFt": 0.2010},
        "2/0 AWG": {"Free Air": 235, "60C": 115, "75C": 135, "90C": 150, "Ohms/kFt": 0.1590},
        "3/0 AWG": {"Free Air": 275, "60C": 130, "75C": 155, "90C": 175, "Ohms/kFt": 0.1260},
        "4/0 AWG": {"Free Air": 315, "60C": 150, "75C": 180, "90C": 205, "Ohms/kFt": 0.1000},
        "250 kcmil": {"Free Air": 355, "60C": 170, "75C": 205, "90C": 230, "Ohms/kFt": 0.0847},
        "300 kcmil": {"Free Air": 395, "60C": 190, "75C": 230, "90C": 260, "Ohms/kFt": 0.0707},
        "350 kcmil": {"Free Air": 445, "60C": 210, "75C": 250, "90C": 280, "Ohms/kFt": 0.0605},
        "400 kcmil": {"Free Air": 480, "60C": 225, "75C": 270, "90C": 305, "Ohms/kFt": 0.0529},
        "500 kcmil": {"Free Air": 545, "60C": 260, "75C": 310, "90C": 350, "Ohms/kFt": 0.0424},
        "600 kcmil": {"Free Air": 615, "60C": 285, "75C": 340, "90C": 385, "Ohms/kFt": 0.0353},
        "700 kcmil": {"Free Air": 675, "60C": 310, "75C": 375, "90C": 425, "Ohms/kFt": 0.0303},
        "750 kcmil": {"Free Air": 700, "60C": 320, "75C": 385, "90C": 435, "Ohms/kFt": 0.0282},
        "800 kcmil": {"Free Air": 725, "60C": 330, "75C": 395, "90C": 445, "Ohms/kFt": 0.0265},
        "900 kcmil": {"Free Air": 785, "60C": 355, "75C": 425, "90C": 480, "Ohms/kFt": 0.0235},
        "1000 kcmil": {"Free Air": 845, "60C": 375, "75C": 445, "90C": 500, "Ohms/kFt": 0.0212},
    }
}

# Pre-computed ohms/kFt cache
CONDUCTOR_OHMS_CACHE = {}
for _mat_key in ("COPPER", "ALUMINUM"):
    for _size, _data in CONDUCTOR_REFERENCE_DATA[_mat_key].items():
        CONDUCTOR_OHMS_CACHE[(_mat_key, _size)] = _data["Ohms/kFt"]

# ─── Dropdown Options ───────────────────────────────────────────────────────
INSULATION_TYPES = ["RHW-2", "THWN-2", "XHHW-2", "USE-2", "RHH", "THHN", "XLP"]
VOLTAGE_RATINGS = ["600", "1000", "2000"]
RACEWAY_TYPES = [
    "Open Air -> Conduit", "Conduit", "Open Air", "Cable Tray",
    "Direct Buried", "Cable Tray -> Conduit",
]
AMPACITY_LIMITING_OPTIONS = ["Conduit", "Open Air", "Cable Tray", "Direct Buried"]
RACEWAY_FINAL_TYPES = ["Conduit", "Cable Tray", "Direct Buried", "Open Air"]
CONDUIT_MATERIALS = ["PVC SCH 40", "PVC SCH 80", "RGS", "IMC", "EMT", "HDPE"]

# AC-specific insulation types
AC_INSULATION_TYPES = ["XHHW-2", "THWN-2", "RHW-2", "THHN", "RHH", "XLP"]
AC_VOLTAGE_RATINGS = ["600", "1000"]
