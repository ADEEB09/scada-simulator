"""
telemetry.py
------------
Gera dados de telemetria elétrica realistas para cada ponto de medição.

Física simplificada:
  - Tensão nominal: 13.800 V (fase-fase)  →  7.967 V (fase-terra) ≈ 7.967 Vfn
  - Curva de carga diária em p.u. (0.0 – 1.0)
  - Queda de tensão proporcional à corrente (impedância simplificada)
  - Ruído gaussiano para simular instrumentação real
  - Corrente e potência calculadas a partir da carga do segmento
"""

import math
import time
import random
import logging
from dataclasses import dataclass
from typing import Dict, List

from network import NetworkModel, FaultEvent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes de sistema
# ---------------------------------------------------------------------------
SQRT3          = math.sqrt(3)
NOMINAL_VLL    = 13_800.0          # V fase-fase
NOMINAL_VLN    = NOMINAL_VLL / SQRT3   # V fase-neutro  ≈ 7967 V
BASE_FREQ      = 60.0              # Hz
NOMINAL_PF     = 0.92              # fator de potência típico

# Impedância de sequência positiva por segmento (ohm) — simplificada
Z_SEGMENT = 0.8 + 0.4j             # R + jX por segmento de alimentador


# ---------------------------------------------------------------------------
# Curva de carga diária (p.u.)  —  resolução horária, interpolada
# ---------------------------------------------------------------------------
DAILY_LOAD_CURVE = {
    0: 0.38, 1: 0.35, 2: 0.33, 3: 0.32, 4: 0.33, 5: 0.38,
    6: 0.50, 7: 0.65, 8: 0.78, 9: 0.85, 10: 0.88, 11: 0.90,
    12: 0.92, 13: 0.89, 14: 0.87, 15: 0.88, 16: 0.90, 17: 0.95,
    18: 1.00, 19: 0.98, 20: 0.93, 21: 0.85, 22: 0.70, 23: 0.52,
}


def _load_factor() -> float:
    """Retorna o fator de carga atual interpolado da curva diária."""
    now = time.localtime()
    h = now.tm_hour
    m = now.tm_min
    p0 = DAILY_LOAD_CURVE[h]
    p1 = DAILY_LOAD_CURVE[(h + 1) % 24]
    factor = p0 + (p1 - p0) * (m / 60.0)
    # Adiciona variação aleatória de ±3 % para simular flutuação de carga
    factor += random.gauss(0, 0.015)
    return max(0.1, min(1.2, factor))


def _noise(value: float, pct: float = 0.005) -> float:
    """Adiciona ruído gaussiano de ±pct ao valor."""
    return value * (1 + random.gauss(0, pct))


# ---------------------------------------------------------------------------
# Dataclass de leitura
# ---------------------------------------------------------------------------
@dataclass
class TelemetryReading:
    timestamp: float          # Unix timestamp (UTC)
    measurement_id: str
    feeder_id: str | None
    energized: bool

    voltage_v: float          # Tensão fase-neutro (V)
    current_a: float          # Corrente (A)
    active_power_kw: float    # Potência ativa (kW)
    reactive_power_kvar: float # Potência reativa (kVAr)
    apparent_power_kva: float  # Potência aparente (kVA)
    power_factor: float        # Fator de potência
    frequency_hz: float        # Frequência (Hz)

    # flags de alarme
    overvoltage: bool = False
    undervoltage: bool = False
    overcurrent: bool = False
    fault_current: bool = False

    def to_dict(self) -> dict:
        return self.__dict__


# ---------------------------------------------------------------------------
# Gerador principal
# ---------------------------------------------------------------------------
class TelemetryGenerator:
    """
    Gera leituras realistas para todos os pontos de medição,
    levando em conta o estado das chaves e faltas ativas.
    """

    def __init__(self, network: NetworkModel):
        self.network = network
        # Carga base de cada alimentador (kW) — carregada do modelo
        self._base_loads: Dict[str, float] = {
            fid: f.base_load_kw for fid, f in network.feeders.items()
        }

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def generate_all(self) -> List[TelemetryReading]:
        lf = _load_factor()
        readings = []
        total_p = 0.0
        total_q = 0.0

        for mid, med in self.network.measurements.items():
            if med.feeder is None:
                # Medidor da subestação — calculado depois
                continue
            r = self._generate_for_segment(mid, med.feeder, med.segment, lf)
            readings.append(r)
            if r.energized:
                total_p += r.active_power_kw
                total_q += r.reactive_power_kvar

        # Medidor da subestação agrega tudo
        se_reading = self._generate_substation(total_p, total_q, lf)
        readings.insert(0, se_reading)

        return readings

    # ------------------------------------------------------------------
    # Geração por segmento de alimentador
    # ------------------------------------------------------------------

    def _generate_for_segment(
        self, measurement_id: str, feeder_id: str, segment: int, load_factor: float
    ) -> TelemetryReading:

        energized = self.network.is_measurement_energized(measurement_id)
        fault = self.network.get_active_fault(feeder_id)
        ts = time.time()

        if not energized:
            return self._zero_reading(measurement_id, feeder_id, ts)

        base_load = self._base_loads.get(feeder_id, 500.0)

        # Carga do segmento diminui conforme avança (início > meio > fim)
        seg_factors = {1: 0.60, 2: 0.28, 3: 0.12}
        seg_p_kw = base_load * seg_factors.get(segment, 0.20) * load_factor

        # Falta ativa modifica os valores
        seg_p_kw, fault_flag, fault_current_flag = self._apply_fault(
            seg_p_kw, fault, segment
        )

        # Potência reativa (fator de potência com variação)
        pf = NOMINAL_PF + random.gauss(0, 0.01)
        pf = max(0.80, min(0.99, pf))
        seg_q_kvar = seg_p_kw * math.tan(math.acos(pf))
        seg_s_kva  = math.sqrt(seg_p_kw**2 + seg_q_kvar**2)

        # Corrente (trifásico)
        current_a = (seg_s_kva * 1000) / (SQRT3 * NOMINAL_VLL)

        # Queda de tensão acumulada por segmento
        v_drop = abs(complex(Z_SEGMENT.real, Z_SEGMENT.imag) * current_a) * segment
        voltage_vln = NOMINAL_VLN - v_drop
        voltage_vln = _noise(max(voltage_vln, NOMINAL_VLN * 0.80))
        current_a   = _noise(current_a)

        # Recalcula potências com a tensão real
        seg_p_kw   = _noise(seg_p_kw)
        seg_q_kvar = _noise(seg_q_kvar)
        seg_s_kva  = math.sqrt(seg_p_kw**2 + seg_q_kvar**2)
        pf_real    = seg_p_kw / seg_s_kva if seg_s_kva > 0 else 0.0

        th = self.network.thresholds
        ov = voltage_vln > NOMINAL_VLN * th["overvoltage_pu"]
        uv = voltage_vln < NOMINAL_VLN * th["undervoltage_pu"]
        oc = current_a > (seg_s_kva * 1000 / (SQRT3 * NOMINAL_VLL)) * th["overcurrent_pu"] * 1.5

        return TelemetryReading(
            timestamp=ts,
            measurement_id=measurement_id,
            feeder_id=feeder_id,
            energized=True,
            voltage_v=round(voltage_vln, 2),
            current_a=round(current_a, 3),
            active_power_kw=round(seg_p_kw, 3),
            reactive_power_kvar=round(seg_q_kvar, 3),
            apparent_power_kva=round(seg_s_kva, 3),
            power_factor=round(pf_real, 4),
            frequency_hz=round(_noise(BASE_FREQ, 0.001), 3),
            overvoltage=ov,
            undervoltage=uv,
            overcurrent=oc or fault_flag,
            fault_current=fault_current_flag,
        )

    def _generate_substation(self, total_p: float, total_q: float, lf: float) -> TelemetryReading:
        ts = time.time()
        total_s = math.sqrt(total_p**2 + total_q**2)
        current_a = (total_s * 1000) / (SQRT3 * NOMINAL_VLL) if total_s > 0 else 0.0
        pf = total_p / total_s if total_s > 0 else 1.0

        return TelemetryReading(
            timestamp=ts,
            measurement_id=self.network.substation_measurement_id,
            feeder_id=None,
            energized=True,
            voltage_v=round(_noise(NOMINAL_VLN, 0.003), 2),
            current_a=round(_noise(current_a), 3),
            active_power_kw=round(_noise(total_p), 3),
            reactive_power_kvar=round(_noise(total_q), 3),
            apparent_power_kva=round(_noise(total_s), 3),
            power_factor=round(pf, 4),
            frequency_hz=round(_noise(BASE_FREQ, 0.001), 3),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_fault(base_p: float, fault: "FaultEvent | None", segment: int):
        """Modifica a potência de acordo com o tipo de falta."""
        if fault is None or not fault.active:
            return base_p, False, False

        # Falta está no mesmo segmento ou antes (downstream)
        if fault.segment > segment:
            return base_p, False, False

        if fault.fault_type == "short_circuit":
            # Corrente muito alta, tensão colapsa → lógica de medição satura
            fault_p = base_p * 8.0
            return fault_p, True, True

        if fault.fault_type == "high_impedance":
            # Corrente levemente elevada, difícil de detectar
            fault_p = base_p * 1.35
            return fault_p, True, False

        if fault.fault_type == "open_circuit":
            # Sem corrente downstream
            return 0.0, False, False

        return base_p, False, False

    @staticmethod
    def _zero_reading(measurement_id: str, feeder_id: str | None, ts: float) -> TelemetryReading:
        return TelemetryReading(
            timestamp=ts,
            measurement_id=measurement_id,
            feeder_id=feeder_id,
            energized=False,
            voltage_v=0.0,
            current_a=0.0,
            active_power_kw=0.0,
            reactive_power_kvar=0.0,
            apparent_power_kva=0.0,
            power_factor=0.0,
            frequency_hz=0.0,
        )
