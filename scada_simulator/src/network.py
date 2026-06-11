"""
network.py
----------
Modelo da rede elétrica de distribuição.
Gerencia a topologia, o estado das chaves e a energização dos segmentos.
"""

import json
import logging
import threading
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class Switch:
    id: str
    name: str
    feeder: Optional[str]
    type: str           # 'sectionalizer' | 'tie'
    state: str          # 'open' | 'closed'
    locked: bool = False

    @property
    def is_closed(self) -> bool:
        return self.state == "closed"


@dataclass
class Measurement:
    id: str
    name: str
    feeder: Optional[str]
    position: str       # 'substation' | 'start' | 'middle' | 'end'
    segment: int        # 0=substation, 1=início, 2=meio, 3=fim


@dataclass
class Feeder:
    id: str
    name: str
    entry_switch: str
    measurements: list
    base_load_kw: float


@dataclass
class FaultEvent:
    feeder_id: Optional[str]
    fault_type: str     # 'short_circuit' | 'open_circuit' | 'high_impedance'
    segment: int        # segmento afetado (1, 2 ou 3)
    active: bool = True


class NetworkModel:
    """
    Mantém o estado completo da rede em memória:
    - topologia (alimentadores, medidores, chaves)
    - estado de cada chave (aberta/fechada)
    - faltas ativas
    - lock para thread-safety
    """

    def __init__(self, config_path: str = None):
        if config_path is None:
            config_path = Path(__file__).parent.parent / "config" / "network.json"

        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        self._lock = threading.RLock()

        # --- Alimentadores ---
        self.feeders: Dict[str, Feeder] = {}
        for fd in cfg["feeders"]:
            self.feeders[fd["id"]] = Feeder(
                id=fd["id"],
                name=fd["name"],
                entry_switch=fd["entry_switch"],
                measurements=fd["measurements"],
                base_load_kw=fd["base_load_kw"],
            )

        # --- Medidores ---
        self.measurements: Dict[str, Measurement] = {}
        for m in cfg["measurements"]:
            self.measurements[m["id"]] = Measurement(
                id=m["id"],
                name=m["name"],
                feeder=m.get("feeder"),
                position=m["position"],
                segment=m["segment"],
            )

        # --- Chaves ---
        self.switches: Dict[str, Switch] = {}
        for sw in cfg["switches"]:
            self.switches[sw["id"]] = Switch(
                id=sw["id"],
                name=sw["name"],
                feeder=sw.get("feeder"),
                type=sw["type"],
                state=sw["initial_state"],
            )

        # --- Configurações gerais ---
        self.nominal_voltage_v: float = cfg["substation"]["nominal_voltage_kv"] * 1000
        self.thresholds: dict = cfg["thresholds"]
        self.substation_measurement_id: str = cfg["substation"]["measurement_id"]

        # --- Faltas ativas ---
        self.active_faults: Dict[str, FaultEvent] = {}

        logger.info("NetworkModel inicializado — %d alimentadores, %d medidores, %d chaves",
                    len(self.feeders), len(self.measurements), len(self.switches))

    # ------------------------------------------------------------------
    # Chaves
    # ------------------------------------------------------------------

    def get_switch_state(self, switch_id: str) -> Optional[str]:
        with self._lock:
            sw = self.switches.get(switch_id)
            return sw.state if sw else None

    def operate_switch(self, switch_id: str, command: str) -> dict:
        """
        Executa um comando em uma chave.
        command: 'open' | 'close'
        Retorna dicionário com resultado da operação.
        """
        with self._lock:
            sw = self.switches.get(switch_id)
            if sw is None:
                return {"success": False, "reason": f"Chave {switch_id} não encontrada"}

            if sw.locked:
                return {"success": False, "reason": f"Chave {switch_id} está bloqueada para operação"}

            if command not in ("open", "close"):
                return {"success": False, "reason": f"Comando inválido: {command}"}

            new_state = command + "d"  # 'open' -> 'opened'... mas usaremos 'open'/'closed'
            new_state = "open" if command == "open" else "closed"

            if sw.state == new_state:
                return {"success": False, "reason": f"Chave já está {new_state}"}

            old_state = sw.state
            sw.state = new_state
            logger.info("Chave %s: %s → %s", switch_id, old_state, new_state)
            return {"success": True, "switch_id": switch_id, "old_state": old_state, "new_state": new_state}

    # ------------------------------------------------------------------
    # Energização
    # ------------------------------------------------------------------

    def is_feeder_energized(self, feeder_id: str) -> bool:
        """Alimentador está energizado se sua chave de entrada estiver fechada."""
        with self._lock:
            feeder = self.feeders.get(feeder_id)
            if feeder is None:
                return False
            entry_sw = self.switches.get(feeder.entry_switch)
            return entry_sw is not None and entry_sw.is_closed

    def is_measurement_energized(self, measurement_id: str) -> bool:
        """
        Verifica se um ponto de medição está energizado, considerando:
        - o estado das chaves do alimentador
        - faltas ativas que podem isolar o segmento
        """
        with self._lock:
            med = self.measurements.get(measurement_id)
            if med is None:
                return False

            # Medidor da subestação: sempre energizado
            if med.feeder is None:
                return True

            # Verifica energização do alimentador
            if not self.is_feeder_energized(med.feeder):
                return False

            # Verifica se há falta que isola este segmento
            fault = self.active_faults.get(med.feeder)
            if fault and fault.active:
                # Segmentos a partir da falta ficam sem energia
                if med.segment >= fault.segment:
                    # Para curto-circuito e alta impedância, o relay isola
                    if fault.fault_type in ("short_circuit", "high_impedance"):
                        return False

            return True

    # ------------------------------------------------------------------
    # Faltas
    # ------------------------------------------------------------------

    def inject_fault(self, fault: FaultEvent):
        with self._lock:
            key = fault.feeder_id or "system"
            self.active_faults[key] = fault
            logger.warning("FALTA INJETADA: tipo=%s feeder=%s segmento=%d",
                           fault.fault_type, fault.feeder_id, fault.segment)

    def clear_fault(self, feeder_id: Optional[str]):
        with self._lock:
            key = feeder_id or "system"
            if key in self.active_faults:
                del self.active_faults[key]
                logger.info("Falta removida: feeder=%s", feeder_id)

    def get_active_fault(self, feeder_id: Optional[str]) -> Optional[FaultEvent]:
        with self._lock:
            return self.active_faults.get(feeder_id or "system")

    def snapshot(self) -> dict:
        """Retorna snapshot completo do estado atual (thread-safe)."""
        with self._lock:
            return {
                "switches": {sid: sw.state for sid, sw in self.switches.items()},
                "energized_feeders": [fid for fid in self.feeders if self.is_feeder_energized(fid)],
                "active_faults": {
                    k: {"type": f.fault_type, "feeder": f.feeder_id, "segment": f.segment}
                    for k, f in self.active_faults.items()
                },
            }
