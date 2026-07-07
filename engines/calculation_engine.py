"""
DC Calculation Engine, File Importers, and CEC Database Access.

Extracted from pv_stringing_calculator.py for modularity.
"""
import csv
import math
import os
import re
import sys
import threading
import time
from urllib.request import urlopen
from urllib.error import URLError

# tkinter is only used by CECBrowserDialog (a GUI picker). Import it lazily so
# this module stays importable on a headless server (e.g. the FastAPI web app
# on Azure), where Tk is not installed. The `tk`/`messagebox` names are only
# ever touched when CECBrowserDialog is actually instantiated in the desktop app.
try:
    import tkinter as tk
    from tkinter import messagebox
except ImportError:  # pragma: no cover - headless environments
    tk = None
    messagebox = None

from nec_tables import (
    COLORS, CONDUCTOR_REFERENCE_DATA, CONDUCTOR_SIZES_ORDERED,
    CONDUCTOR_SIZE_INDEX, CONDUCTOR_OHMS_CACHE, CIRCULAR_MILS,
    NEC_TEMP_CORRECTION_90C, NEC_CONDUIT_FILL_FACTORS,
    NEC_GROUND_CONDUCTOR_CU, NEC_GROUND_CONDUCTOR_AL,
)


class FileImporter:
    """Handles file parsing for PV modules and inverters"""

    @staticmethod
    def parse_pan_file(filepath):
        """Parse .PAN file (PV module data)"""
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        patterns = {
            'manufacturer': r'Manufacturer=([^\n]+)', 'model': r'Model=([^\n]+)',
            'pnom': r'PNom=([\d.]+)', 'vmp': r'Vmp=([\d.]+)', 'imp': r'Imp=([\d.]+)',
            'voc': r'Voc=([\d.]+)', 'isc': r'Isc=([\d.]+)', 'muisc': r'muISC=([\d.]+)',
            'muvoc': r'muVocSpec=([\-\d.]+)', 'mupmp': r'muPmpReq=([\-\d.]+)',
            'bifacial': r'BifacialityFactor=([\d.]+)',
        }

        data = {k: re.search(v, content).group(1).strip() if re.search(v, content) else '0'
                for k, v in patterns.items()}

        return {
            'manufacturer': data['manufacturer'], 'model': data['model'],
            'pmax': data['pnom'], 'vmp': data['vmp'], 'imp': data['imp'],
            'voc': data['voc'], 'isc': data['isc'],
            'temp_coef_isc': str(round(float(data['muisc']) / 100, 2)),
            'temp_coef_voc': str(round(float(data['muvoc']) / float(data['voc']) / 10, 2)),
            'temp_coef_pmpp': data['mupmp'],
            'bifacial': 'Y' if float(data['bifacial']) > 0 else 'N',
        }

    @staticmethod
    def parse_ond_file(filepath):
        """Parse .OND file (inverter data)"""
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        patterns = {
            'manufacturer': r'Manufacturer=([^\n]+)', 'model': r'Model=([^\n]+)',
            'pnom': r'PNomConv=([\d.]+)', 'vout': r'VOutConv=([\d.]+)',
            'vmppt_min': r'VMppMin=([\d.]+)', 'vmppt_max': r'VMPPMax=([\d.]+)',
            'vabs_max': r'VAbsMax=([\d.]+)', 'idc_max': r'IMaxDC=([\d.]+)',
            'imax_ac': r'IMaxAC=([\d.]+)',
        }

        data = {k: re.search(v, content).group(1).strip() if re.search(v, content) else '0'
                for k, v in patterns.items()}

        # PVsyst .OND PNomConv is already in kW (e.g. "100.000" = 100 kW), so it
        # maps straight to the "AC Output Power (kW)" field with no scaling.
        return {
            'manufacturer': data['manufacturer'], 'model': data['model'],
            'rated_ac_output': data['pnom'], 'ac_output_voltage': data['vout'],
            'min_mppt': data['vmppt_min'], 'max_mppt': data['vmppt_max'],
            'max_dc_input': data['vabs_max'], 'max_dc_current': data['idc_max'],
            'max_ac_current': data['imax_ac'],
        }


class CECDatabase:
    """Downloads and caches CEC module/inverter databases from SAM GitHub repo."""

    SAM_BASE = "https://raw.githubusercontent.com/NREL/SAM/develop/deploy/libraries"
    MODULES_URL = f"{SAM_BASE}/CEC%20Modules.csv"
    INVERTERS_URL = f"{SAM_BASE}/CEC%20Inverters.csv"
    SKIP_NAMES = {"Units", "[0]", ""}
    CACHE_MAX_AGE_HOURS = 168  # 7 days

    _modules_cache = None
    _inverters_cache = None

    @classmethod
    def _cache_dir(cls):
        """User-writable cache dir; uses %LOCALAPPDATA% on Windows."""
        localapp = os.environ.get('LOCALAPPDATA') or os.environ.get('APPDATA')
        if localapp:
            base = os.path.join(localapp, 'PV_Stringing_Calculator')
        else:
            base = os.path.join(os.path.expanduser('~'),
                                '.pv_stringing_calculator')
        cache = os.path.join(base, 'cec_data')
        try:
            os.makedirs(cache, exist_ok=True)
        except OSError:
            # Fall back to temp if AppData isn't writable
            import tempfile
            cache = os.path.join(tempfile.gettempdir(),
                                 'PV_Stringing_Calculator', 'cec_data')
            os.makedirs(cache, exist_ok=True)
        return cache

    @classmethod
    def _bundled_csv_path(cls, filename):
        """Path to CSV bundled with the exe (via PyInstaller --add-data).

        In frozen mode, PyInstaller extracts data files to sys._MEIPASS.
        In dev mode, files live next to the source in ./cec_data/.
        """
        if getattr(sys, 'frozen', False):
            base = getattr(sys, '_MEIPASS',
                           os.path.dirname(sys.executable))
        else:
            base = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base, 'cec_data', filename)

    @classmethod
    def _download_csv(cls, url, local_path):
        resp = urlopen(url, timeout=60)
        data = resp.read().decode('utf-8')
        with open(local_path, 'w', encoding='utf-8') as f:
            f.write(data)
        return local_path

    @classmethod
    def _parse_csv(cls, csv_path):
        db = {}
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = row.get('Name', '').strip()
                if name in cls.SKIP_NAMES:
                    continue
                db[name] = dict(row)
        return db

    @classmethod
    def _get_cached_or_download(cls, url, filename):
        """Return parsed CSV. Priority:
        1. Fresh user cache (<7 days old)
        2. Try download → write to user cache
        3. Fall back to bundled CSV (shipped with exe)
        4. Fall back to stale user cache if any
        """
        cache_path = os.path.join(cls._cache_dir(), filename)

        # 1. Fresh cache
        if os.path.exists(cache_path):
            age_hours = (time.time() - os.path.getmtime(cache_path)) / 3600
            if age_hours < cls.CACHE_MAX_AGE_HOURS:
                return cls._parse_csv(cache_path)

        # 2. Try download
        try:
            cls._download_csv(url, cache_path)
            return cls._parse_csv(cache_path)
        except (URLError, OSError, TimeoutError):
            pass

        # 3. Bundled CSV (works offline on any machine)
        bundled = cls._bundled_csv_path(filename)
        if os.path.exists(bundled):
            return cls._parse_csv(bundled)

        # 4. Stale cache as last resort
        if os.path.exists(cache_path):
            return cls._parse_csv(cache_path)

        raise FileNotFoundError(
            f"CEC database '{filename}' unavailable "
            f"(no internet and no bundled copy).")

    @classmethod
    def get_modules(cls):
        if cls._modules_cache is None:
            cls._modules_cache = cls._get_cached_or_download(
                cls.MODULES_URL, 'CEC_Modules.csv')
        return cls._modules_cache

    @classmethod
    def get_inverters(cls):
        if cls._inverters_cache is None:
            cls._inverters_cache = cls._get_cached_or_download(
                cls.INVERTERS_URL, 'CEC_Inverters.csv')
        return cls._inverters_cache

    @staticmethod
    def module_to_app_fields(name, data):
        voc = float(data.get('V_oc_ref', 0) or 0)
        isc = float(data.get('I_sc_ref', 0) or 0)
        alpha_sc = float(data.get('alpha_sc', 0) or 0)
        beta_oc = float(data.get('beta_oc', 0) or 0)

        manufacturer = data.get('Manufacturer', '').strip()
        model = name
        if manufacturer and name.startswith(manufacturer):
            model = name[len(manufacturer):].strip()

        return {
            'manufacturer': manufacturer,
            'model': model,
            'pmax': data.get('STC', '0'),
            'vmp': data.get('V_mp_ref', '0'),
            'imp': data.get('I_mp_ref', '0'),
            'voc': data.get('V_oc_ref', '0'),
            'isc': data.get('I_sc_ref', '0'),
            'temp_coef_isc': str(round((alpha_sc / isc * 100) if isc else 0, 2)),
            'temp_coef_voc': str(round((beta_oc / voc * 100) if voc else 0, 2)),
            'temp_coef_pmpp': data.get('gamma_pmp', '0'),
            'bifacial': 'Y' if data.get('Bifacial', '0').strip() not in ('0', '', 'N') else 'N',
        }

    @staticmethod
    def inverter_to_app_fields(name, data):
        manufacturer = ''
        model = name
        if ':' in name:
            manufacturer, model = name.split(':', 1)
            manufacturer = manufacturer.strip()
            model = model.strip()

        paco = float(data.get('Paco', 0) or 0)
        return {
            'manufacturer': manufacturer,
            'model': model,
            'rated_ac_output': str(round(paco / 1000, 2)),
            'ac_output_voltage': data.get('Vac', '0'),
            'min_mppt': data.get('Mppt_low', '0'),
            'max_mppt': data.get('Mppt_high', '0'),
            'max_dc_input': data.get('Vdcmax', '0'),
            'max_dc_current': data.get('Idcmax', '0'),
            'max_ac_current': '',
        }


class CECBrowserDialog:
    """Popup search dialog for browsing CEC module/inverter databases."""

    def __init__(self, parent, title, db_getter, field_mapper, on_select):
        self.parent = parent
        self.db_getter = db_getter
        self.field_mapper = field_mapper
        self.on_select = on_select
        self.db = None
        self.filtered_names = []

        self.win = tk.Toplevel(parent)
        self.win.title(title)
        self.win.geometry('750x550')
        self.win.configure(bg=COLORS['bg_light'])
        self.win.transient(parent)
        self.win.grab_set()

        search_frame = tk.Frame(self.win, bg=COLORS['bg_light'])
        search_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        tk.Label(search_frame, text="Search:", font=('Jost', 10, 'bold'),
                 bg=COLORS['bg_light']).pack(side=tk.LEFT, padx=(0, 5))

        self.search_var = tk.StringVar()
        self.search_var.trace_add('write', self._on_search)
        self.search_entry = tk.Entry(search_frame, textvariable=self.search_var,
                                     font=('Jost', 10), width=50)
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))

        self.count_label = tk.Label(search_frame, text="", font=('Jost', 9),
                                    bg=COLORS['bg_light'], fg=COLORS['text_secondary'])
        self.count_label.pack(side=tk.RIGHT)

        list_frame = tk.Frame(self.win, bg=COLORS['bg_light'])
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.listbox = tk.Listbox(list_frame, font=('Consolas', 9),
                                  yscrollcommand=self._on_scroll,
                                  selectmode=tk.SINGLE, height=20)
        self.listbox.pack(fill=tk.BOTH, expand=True)
        self.scrollbar = scrollbar
        scrollbar.config(command=self.listbox.yview)
        self.listbox.bind('<Double-Button-1>', self._on_double_click)

        self.detail_var = tk.StringVar(value="")
        detail_label = tk.Label(self.win, textvariable=self.detail_var,
                                font=('Consolas', 9), bg=COLORS['bg_medium'],
                                justify=tk.LEFT, anchor='w', padx=10, pady=5)
        detail_label.pack(fill=tk.X, padx=10, pady=(0, 5))
        self.listbox.bind('<<ListboxSelect>>', self._on_select_item)

        btn_frame = tk.Frame(self.win, bg=COLORS['bg_light'])
        btn_frame.pack(fill=tk.X, padx=10, pady=(0, 10))

        tk.Button(btn_frame, text="Select", command=self._confirm,
                  bg=COLORS['secondary'], fg=COLORS['white'],
                  font=('Jost', 10, 'bold'), padx=20, pady=6,
                  relief=tk.FLAT, cursor='hand2').pack(side=tk.RIGHT, padx=5)
        tk.Button(btn_frame, text="Cancel", command=self.win.destroy,
                  bg=COLORS['text_secondary'], fg=COLORS['white'],
                  font=('Jost', 10, 'bold'), padx=20, pady=6,
                  relief=tk.FLAT, cursor='hand2').pack(side=tk.RIGHT, padx=5)

        self.status_var = tk.StringVar(value="Loading database...")
        self.status_label = tk.Label(self.win, textvariable=self.status_var,
                                     font=('Jost', 9), bg=COLORS['bg_light'],
                                     fg=COLORS['text_secondary'])
        self.status_label.pack(pady=(0, 5))

        self.search_entry.config(state='disabled')
        threading.Thread(target=self._load_db, daemon=True).start()

    def _load_db(self):
        try:
            self.db = self.db_getter()
            self.filtered_names = list(self.db.keys())
            self.win.after(0, self._db_loaded)
        except URLError as e:
            self.win.after(0, lambda: self._db_error(
                f"Download failed. Check internet connection.\n{e}"))
        except Exception as e:
            self.win.after(0, lambda: self._db_error(str(e)))

    def _db_loaded(self):
        self.search_entry.config(state='normal')
        self.search_entry.focus_set()
        self.status_var.set("")
        self._update_list()

    def _db_error(self, msg):
        self.status_var.set(f"Error: {msg}")

    def _on_search(self, *args):
        if not self.db:
            return
        query = self.search_var.get().lower().strip()
        if not query:
            self.filtered_names = list(self.db.keys())
        else:
            terms = query.split()
            self.filtered_names = [
                n for n in self.db
                if all(t in n.lower() for t in terms)
            ]
        self._update_list()

    def _update_list(self):
        self.listbox.delete(0, tk.END)
        self._page_loaded = 0
        self._load_next_page()

    def _load_next_page(self):
        page_size = 200
        start = self._page_loaded
        end = start + page_size
        for name in self.filtered_names[start:end]:
            self.listbox.insert(tk.END, name)
        self._page_loaded = min(end, len(self.filtered_names))
        total = len(self.filtered_names)
        if self._page_loaded < total:
            self.count_label.config(
                text=f"{total} results (showing {self._page_loaded}, scroll for more)")
        else:
            self.count_label.config(text=f"{total} results")

    def _on_scroll(self, first, last):
        self.scrollbar.set(first, last)
        if float(last) >= 0.95 and self._page_loaded < len(self.filtered_names):
            self._load_next_page()

    def _on_select_item(self, event):
        sel = self.listbox.curselection()
        if not sel:
            return
        name = self.listbox.get(sel[0])
        data = self.db.get(name, {})
        mapped = self.field_mapper(name, data)
        lines = [f"{k}: {v}" for k, v in mapped.items() if v]
        self.detail_var.set("  |  ".join(lines[:6]))

    def _on_double_click(self, event):
        self._confirm()

    def _confirm(self):
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showwarning("No Selection", "Please select an item first.",
                                   parent=self.win)
            return
        name = self.listbox.get(sel[0])
        data = self.db.get(name, {})
        mapped = self.field_mapper(name, data)
        self.win.destroy()
        self.on_select(mapped)


class CalculationEngine:
    """Centralized DC calculation logic"""

    @staticmethod
    def calculate_string_values(module_params, temp_params, stringing_params):
        """Calculate all string-level values"""
        pmax, vmp, imp, voc, isc = module_params['pmax'], module_params['vmp'], \
            module_params['imp'], module_params['voc'], module_params['isc']
        temp_coef_isc, temp_coef_voc = module_params['temp_coef_isc'], \
            module_params['temp_coef_voc']
        extreme_min_c, high_temp_c = temp_params['extreme_min_c'], temp_params['high_temp_c']
        cell_temp_rise = temp_params.get('cell_temp_rise', 35)
        modules_per_string = stringing_params['modules_per_string']

        rated_string_power = (pmax * modules_per_string) / 1000
        nominal_vmp = vmp * modules_per_string
        max_voc = voc * modules_per_string * \
            (1 + temp_coef_voc * (extreme_min_c - 25))
        max_vmp = vmp * modules_per_string * \
            (1 + temp_coef_voc * (extreme_min_c - 25))
        min_vmp = vmp * modules_per_string * \
            (1 + temp_coef_voc * (high_temp_c + cell_temp_rise - 25))
        cell_temp_c = high_temp_c + cell_temp_rise
        nec_ampacity_factor = 1.25 * (1 + temp_coef_isc * (cell_temp_c - 25))
        nec_sizing_ampacity = isc * nec_ampacity_factor
        nec_min_table_ampacity = isc * 1.5625

        return {
            'rated_string_power': rated_string_power, 'nominal_vmp': nominal_vmp,
            'max_voc': max_voc, 'max_vmp': max_vmp, 'min_vmp': min_vmp,
            'nec_ampacity_factor': nec_ampacity_factor,
            'nec_sizing_ampacity': nec_sizing_ampacity,
            'nec_min_table_ampacity': nec_min_table_ampacity,
        }

    @staticmethod
    def calculate_voltage_drop(length, conductor, material, imp, vmp):
        """Calculate voltage drop for a DC cable run (2-way)"""
        ohms = CONDUCTOR_OHMS_CACHE.get((material, conductor), 0)
        v_drop = 2 * length * imp * ohms / 1000
        pct_drop = (v_drop / vmp) * 100 if vmp > 0 else 0
        return ohms, v_drop, pct_drop

    @staticmethod
    def get_temp_correction_factor(ambient_temp_c):
        """Get NEC temperature correction factor for 90C rated conductors"""
        for max_temp, factor in NEC_TEMP_CORRECTION_90C:
            if ambient_temp_c <= max_temp:
                return factor
        return 0.41

    @staticmethod
    def get_conduit_fill_factor(num_current_carrying):
        """Get NEC conduit fill adjustment factor"""
        for max_conductors, factor in NEC_CONDUIT_FILL_FACTORS:
            if num_current_carrying <= max_conductors:
                return factor
        return 0.35

    @staticmethod
    def get_ground_conductor(ocpd_amps, material="CU"):
        """Get equipment grounding conductor size per NEC Table 250.122"""
        table = NEC_GROUND_CONDUCTOR_AL if material == "AL" else NEC_GROUND_CONDUCTOR_CU
        gc_size = table[-1][1]
        for rating, size in table:
            if ocpd_amps <= rating:
                gc_size = size
                break
        return gc_size

    @staticmethod
    def get_nec_ampacity(conductor, material, rating="90C"):
        """Get NEC ampacity for a conductor"""
        mat = "COPPER" if material == "CU" else "ALUMINUM"
        if conductor in CONDUCTOR_REFERENCE_DATA[mat]:
            return CONDUCTOR_REFERENCE_DATA[mat][conductor].get(rating, 0)
        return 0

    @staticmethod
    def calc_max_strings_per_combiner(string_isc, conductor, material,
                                      nec_factor=1.25, ambient_temp_c=30.0,
                                      wires_per_set=2, num_sets=1,
                                      temp_rating="90C"):
        """Calculate max strings a combiner can hold given conductor ampacity.

        Formula: max_strings = floor(derated_ampacity x num_sets / (Isc x NEC_factor))

        Args:
            string_isc: String short-circuit current (A)
            conductor: Conductor size (e.g., "500 kcmil")
            material: "CU" or "AL"
            nec_factor: NEC 690.8(A) multiplier (typical 1.25 x 1.25 = 1.5625)
            ambient_temp_c: Ambient temp for derating
            wires_per_set: # current-carrying wires per set (for conduit fill)
            num_sets: # parallel conductor sets
            temp_rating: "60C", "75C", or "90C"

        Returns:
            dict with: max_strings, derated_ampacity, nec_ampacity, derate_total
        """
        if string_isc <= 0:
            return {'max_strings': 0, 'derated_ampacity': 0,
                    'nec_ampacity': 0, 'derate_total': 0}

        nec_ampacity = CalculationEngine.get_nec_ampacity(
            conductor, material, temp_rating)
        derate_temp = CalculationEngine.get_temp_correction_factor(
            ambient_temp_c)
        derate_cond = CalculationEngine.get_conduit_fill_factor(
            wires_per_set * num_sets)
        derate_total = derate_temp * derate_cond
        derated_ampacity = nec_ampacity * derate_total * num_sets

        max_strings = int(derated_ampacity / (string_isc * nec_factor)) \
            if string_isc * nec_factor > 0 else 0

        return {
            'max_strings': max_strings,
            'derated_ampacity': derated_ampacity,
            'nec_ampacity': nec_ampacity,
            'derate_total': derate_total,
        }

    @staticmethod
    def auto_size_conductor(required_amps, material, derate_total,
                            min_table_ampacity=0.0, term_rating="75C", term_current=0.0):
        """Find minimum conductor size meeting all NEC requirements."""
        mat = "COPPER" if material == "CU" else "ALUMINUM"
        for size in CONDUCTOR_SIZES_ORDERED:
            if size in CONDUCTOR_REFERENCE_DATA[mat]:
                data = CONDUCTOR_REFERENCE_DATA[mat][size]
                ampacity_90c = data["90C"]
                derated = ampacity_90c * derate_total
                if derated < required_amps:
                    continue
                if ampacity_90c < min_table_ampacity:
                    continue
                if term_current > 0 and term_rating in data:
                    if data[term_rating] < term_current:
                        continue
                return size
        return "1000 kcmil"

    @staticmethod
    def calculate_dc_comb_vdrop(ohms_per_kft, length_ft, num_sets, num_strings,
                                imp, string_vmp, string_avg_vdrop=0):
        """Calculate DC combiner-to-inverter voltage drop.

        vdrop_pct is cumulative: the string-segment average drop plus the
        combiner-feeder drop, expressed as a fraction of string Vmp. Pass
        string_avg_vdrop=0 for the feeder-only fraction.
        """
        if num_sets <= 0 or string_vmp <= 0:
            return 0, 0
        cb_vdrop = (2 * ohms_per_kft * length_ft) / \
            (num_sets * 1000) * (num_strings * imp)
        vdrop_pct = (string_avg_vdrop + cb_vdrop) / string_vmp
        return cb_vdrop, vdrop_pct
