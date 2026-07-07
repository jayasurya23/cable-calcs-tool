"""
AC Calculation Engine for 3-Phase and 1-Phase Circuit Sizing.

Implements NEC-compliant conductor sizing, voltage drop calculations,
and OCPD selection for AC circuits (inverter-to-switchboard, auxiliary loads).
"""
import math

from nec_tables import (
    CONDUCTOR_REFERENCE_DATA, CONDUCTOR_SIZES_ORDERED,
    CONDUCTOR_SIZE_INDEX, CONDUCTOR_OHMS_CACHE, CIRCULAR_MILS,
    NEC_TEMP_CORRECTION_90C, NEC_CONDUIT_FILL_FACTORS,
    NEC_GROUND_CONDUCTOR_CU, NEC_GROUND_CONDUCTOR_AL,
    NEC_STANDARD_OCPD_RATINGS,
)


class ACCalculationEngine:
    """Calculation logic for AC (3-phase and 1-phase) circuit sizing."""

    # ─── Voltage Drop Formulas ──────────────────────────────────────────

    @staticmethod
    def calculate_vdrop_3phase(length_ft, current_a, ohms_per_kft, voltage,
                               num_sets=1):
        """3-phase voltage drop per IEEE/NEC.
        VD = sqrt(3) * L * I * R / (num_sets * 1000)
        Returns (vdrop_volts, vdrop_pct)
        """
        if voltage <= 0 or num_sets <= 0:
            return 0, 0
        vdrop = math.sqrt(3) * length_ft * current_a * ohms_per_kft / (num_sets * 1000)
        pct = (vdrop / voltage) * 100
        return vdrop, pct

    @staticmethod
    def calculate_vdrop_1phase(length_ft, current_a, ohms_per_kft, voltage,
                               num_sets=1):
        """1-phase voltage drop.
        VD = 2 * L * I * R / (num_sets * 1000)
        Returns (vdrop_volts, vdrop_pct)
        """
        if voltage <= 0 or num_sets <= 0:
            return 0, 0
        vdrop = 2 * length_ft * current_a * ohms_per_kft / (num_sets * 1000)
        pct = (vdrop / voltage) * 100
        return vdrop, pct

    # ─── Current Calculations ───────────────────────────────────────────

    @staticmethod
    def fla_from_kva_3phase(kva, voltage):
        """Full Load Amperage from kVA for 3-phase.
        FLA = kVA * 1000 / (sqrt(3) * V)
        """
        if voltage <= 0:
            return 0
        return (kva * 1000) / (math.sqrt(3) * voltage)

    @staticmethod
    def fla_from_kw_3phase(kw, voltage, power_factor=1.0):
        """Full Load Amperage from kW for 3-phase.
        FLA = kW * 1000 / (sqrt(3) * V * PF)
        """
        if voltage <= 0 or power_factor <= 0:
            return 0
        return (kw * 1000) / (math.sqrt(3) * voltage * power_factor)

    @staticmethod
    def fla_from_kva_1phase(kva, voltage):
        """Full Load Amperage from kVA for 1-phase.
        FLA = kVA * 1000 / V
        """
        if voltage <= 0:
            return 0
        return (kva * 1000) / voltage

    @staticmethod
    def fla_from_kw_1phase(kw, voltage, power_factor=1.0):
        """Full Load Amperage from kW for 1-phase.
        FLA = kW * 1000 / (V * PF)
        """
        if voltage <= 0 or power_factor <= 0:
            return 0
        return (kw * 1000) / (voltage * power_factor)

    # ─── NEC Lookups (shared with DC engine, re-exposed for convenience) ─

    @staticmethod
    def get_temp_correction_factor(ambient_temp_c):
        """NEC 310.15(B)(1) temperature correction for 90C conductors."""
        for max_temp, factor in NEC_TEMP_CORRECTION_90C:
            if ambient_temp_c <= max_temp:
                return factor
        return 0.41

    @staticmethod
    def get_conduit_fill_factor(num_current_carrying):
        """NEC 310.15(C)(1) conduit fill adjustment."""
        for max_conductors, factor in NEC_CONDUIT_FILL_FACTORS:
            if num_current_carrying <= max_conductors:
                return factor
        return 0.35

    @staticmethod
    def get_ground_conductor(ocpd_amps, material="CU"):
        """NEC Table 250.122 equipment grounding conductor."""
        table = NEC_GROUND_CONDUCTOR_AL if material == "AL" else NEC_GROUND_CONDUCTOR_CU
        gc_size = table[-1][1]
        for rating, size in table:
            if ocpd_amps <= rating:
                gc_size = size
                break
        return gc_size

    @staticmethod
    def get_nec_ampacity(conductor, material, rating="90C"):
        """Get NEC ampacity for a conductor at given temperature rating."""
        mat = "COPPER" if material == "CU" else "ALUMINUM"
        if conductor in CONDUCTOR_REFERENCE_DATA[mat]:
            return CONDUCTOR_REFERENCE_DATA[mat][conductor].get(rating, 0)
        return 0

    @staticmethod
    def get_ohms_per_kft(conductor, material):
        """Get conductor resistance in Ohms/kFt."""
        mat = "COPPER" if material == "CU" else "ALUMINUM"
        return CONDUCTOR_OHMS_CACHE.get((mat, conductor), 0)

    # ─── OCPD Sizing ───────────────────────────────────────────────────

    @staticmethod
    def select_ocpd(continuous_current, noncontinuous_current=0):
        """Select standard OCPD rating per NEC 240.6.
        For continuous loads: OCPD >= 1.25 * continuous + noncontinuous
        """
        required = continuous_current * 1.25 + noncontinuous_current
        for rating in NEC_STANDARD_OCPD_RATINGS:
            if rating >= required:
                return rating
        return NEC_STANDARD_OCPD_RATINGS[-1]

    @staticmethod
    def next_standard_ocpd(amps):
        """Find next standard OCPD rating >= given amps."""
        for rating in NEC_STANDARD_OCPD_RATINGS:
            if rating >= amps:
                return rating
        return NEC_STANDARD_OCPD_RATINGS[-1]

    # ─── Conductor Auto-Sizing ──────────────────────────────────────────

    @staticmethod
    def auto_size_conductor(required_amps, material, derate_total,
                            min_table_ampacity=0.0, term_rating="75C",
                            term_current=0.0):
        """Find minimum conductor meeting all NEC requirements for AC circuits.
        Same logic as DC but also checks NEC 110.14(C)(1) 60C limit for <100A equipment.
        """
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

    # ─── Full AC Circuit Calculation ────────────────────────────────────

    @staticmethod
    def calculate_ac_circuit(circuit_params):
        """Calculate all derived values for an AC circuit row.

        Args:
            circuit_params: dict with keys:
                phase: "3" or "1"
                voltage: circuit voltage (V)
                power_kw: power in kW (or kVA if power_factor=1)
                power_factor: power factor (default 1.0)
                fla_override: if set, use this instead of calculating FLA
                continuous: True if continuous load (1.25x multiplier)
                conductor: conductor size string
                material: "CU" or "AL"
                num_sets: number of parallel conductor sets
                num_wires: wires per set (for conduit fill)
                length_ft: one-way cable length in feet
                ambient_temp_c: ambient temperature
                term_from_c: terminal temp rating at source (60, 75, or 90)
                term_to_c: terminal temp rating at destination
                ocpd_override: if set, use this OCPD instead of auto

        Returns:
            dict with all calculated values
        """
        phase = circuit_params.get('phase', '3')
        voltage = circuit_params.get('voltage', 480)
        power_kw = circuit_params.get('power_kw', 0)
        pf = circuit_params.get('power_factor', 1.0)
        continuous = circuit_params.get('continuous', True)
        conductor = circuit_params.get('conductor', '500 kcmil')
        material = circuit_params.get('material', 'AL')
        num_sets = max(1, circuit_params.get('num_sets', 1))
        num_wires = max(2, circuit_params.get('num_wires', 3))
        length_ft = circuit_params.get('length_ft', 0)
        ambient_c = circuit_params.get('ambient_temp_c', 30)
        term_from = circuit_params.get('term_from_c', 75)
        term_to = circuit_params.get('term_to_c', 75)

        eng = ACCalculationEngine

        # FLA calculation
        fla_override = circuit_params.get('fla_override', None)
        if fla_override is not None and fla_override > 0:
            fla = fla_override
        elif phase == '3':
            fla = eng.fla_from_kw_3phase(power_kw, voltage, pf)
        else:
            fla = eng.fla_from_kw_1phase(power_kw, voltage, pf)

        # NEC sizing current (1.25x for continuous loads)
        sizing_current = fla * 1.25 if continuous else fla

        # OCPD
        ocpd_override = circuit_params.get('ocpd_override', None)
        if ocpd_override is not None and ocpd_override > 0:
            ocpd = ocpd_override
        else:
            ocpd = eng.select_ocpd(fla) if continuous else eng.next_standard_ocpd(fla)

        # NEC ampacities
        mat = "COPPER" if material == "CU" else "ALUMINUM"
        nec_60 = eng.get_nec_ampacity(conductor, material, "60C")
        nec_75 = eng.get_nec_ampacity(conductor, material, "75C")
        nec_90 = eng.get_nec_ampacity(conductor, material, "90C")

        # Derating
        derate_temp = eng.get_temp_correction_factor(ambient_c)
        derate_cond = eng.get_conduit_fill_factor(num_wires * num_sets)
        derate_total = derate_temp * derate_cond
        final_ampacity = nec_90 * derate_total

        # Number of sets needed
        if final_ampacity > 0:
            sets_needed = math.ceil(sizing_current / final_ampacity)
        else:
            sets_needed = 1
        current_per_set = sizing_current / num_sets if num_sets > 0 else sizing_current

        # Terminal temperature check
        term_temp = f"{min(term_from, term_to)}C"
        if term_temp not in ("60C", "75C", "90C"):
            term_temp = "75C"

        # NEC 110.14(C)(1): for equipment rated <= 100A, use 60C column
        # unless the equipment is specifically listed for 75C
        effective_term = term_temp
        if ocpd <= 100 and term_temp == "75C":
            effective_term = "60C"

        # Auto-size conductor
        auto_sized = eng.auto_size_conductor(
            current_per_set, material, derate_total,
            min_table_ampacity=current_per_set * 1.25 if continuous else 0,
            term_rating=effective_term,
            term_current=current_per_set)

        # Use larger of user-selected and auto-sized
        final_size = conductor
        if (conductor in CONDUCTOR_SIZE_INDEX and auto_sized in CONDUCTOR_SIZE_INDEX):
            if CONDUCTOR_SIZE_INDEX[auto_sized] > CONDUCTOR_SIZE_INDEX[conductor]:
                final_size = auto_sized

        # Ground conductor
        gnd_conductor = eng.get_ground_conductor(ocpd, material)

        # Adjusted ground conductor if final size differs
        adj_gnd = gnd_conductor
        if final_size in CIRCULAR_MILS and conductor in CIRCULAR_MILS:
            ratio = CIRCULAR_MILS.get(final_size, 1) / CIRCULAR_MILS.get(conductor, 1)
            if ratio < 1 and gnd_conductor in CIRCULAR_MILS:
                adj_gnd_mils = CIRCULAR_MILS[gnd_conductor] * ratio
                for sz in CONDUCTOR_SIZES_ORDERED:
                    if sz in CIRCULAR_MILS and CIRCULAR_MILS[sz] >= adj_gnd_mils:
                        adj_gnd = sz
                        break

        # Voltage drop
        ohms = eng.get_ohms_per_kft(conductor, material)
        if phase == '3':
            vdrop, vdrop_pct = eng.calculate_vdrop_3phase(
                length_ft, fla, ohms, voltage, num_sets)
        else:
            vdrop, vdrop_pct = eng.calculate_vdrop_1phase(
                length_ft, fla, ohms, voltage, num_sets)

        return {
            'fla': fla,
            'sizing_current': sizing_current,
            'ocpd': ocpd,
            'nec_60': nec_60,
            'nec_75': nec_75,
            'nec_90': nec_90,
            'derate_temp': derate_temp,
            'derate_cond': derate_cond,
            'derate_total': derate_total,
            'final_ampacity': final_ampacity,
            'sets_needed': sets_needed,
            'current_per_set': current_per_set,
            'auto_sized': auto_sized,
            'final_size': final_size,
            'gnd_conductor': gnd_conductor,
            'adj_gnd': adj_gnd,
            'ohms_per_kft': ohms,
            'vdrop': vdrop,
            'vdrop_pct': vdrop_pct,
        }
