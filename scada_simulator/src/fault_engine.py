"""
fault_engine.py
---------------
Motor de injeção de faltas elétricas aleatórias.

Tipos de falta simulados:
  - short_circuit   : curto-circuito (corrente muito elevada, colpaso de tensão)
  - high_impedance  : falta de alta impedância (difícil detecção)
  - open_circuit    : circuito aberto (perda de carga downstream)

Comportamento:
  - A cada intervalo aleatório, avalia se injeta uma nova falta
  - Cada falta tem duração aleatória (pode ser permanente até comando de chave)
  - Eventos são publicados via callback para o MQTT handler
  - O motor também emite o "all-clear" quando a falta é removida
"""

import time
import random
import logging
import threading
from typing import Callable, Optional

from network import NetworkModel, FaultEvent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configurações de probabilidade
# ---------------------------------------------------------------------------
class FaultConfig:
    # Intervalo entre verificações de nova falta (segundos)
    CHECK_INTERVAL_MIN = 30
    CHECK_INTERVAL_MAX = 120

    # Probabilidade de injetar falta a cada verificação (0–1)
    FAULT_PROBABILITY = 0.30

    # Duração da falta (segundos) — distribuição uniforme
    FAULT_DURATION_MIN = 15
    FAULT_DURATION_MAX = 90

    # Distribuição dos tipos de falta
    FAULT_TYPES = [
        ("short_circuit",   0.45),
        ("high_impedance",  0.30),
        ("open_circuit",    0.25),
    ]

    # Segmentos que podem ser afetados (1=início, 2=meio, 3=fim)
    FAULT_SEGMENTS = [1, 2, 3]


def _weighted_choice(options):
    """Escolha aleatória ponderada a partir de lista de (valor, peso)."""
    total = sum(w for _, w in options)
    r = random.uniform(0, total)
    acc = 0
    for val, w in options:
        acc += w
        if r <= acc:
            return val
    return options[-1][0]


# ---------------------------------------------------------------------------
# Motor de faltas
# ---------------------------------------------------------------------------
class FaultEngine:
    """
    Roda em thread separada. A cada intervalo aleatório:
      1. Sorteia se vai injetar uma falta
      2. Escolhe alimentador, tipo e segmento
      3. Injeta no NetworkModel
      4. Chama o callback on_fault_event para publicar no MQTT
      5. Após duração aleatória, remove a falta e chama on_fault_cleared
    """

    def __init__(
        self,
        network: NetworkModel,
        on_fault_event: Optional[Callable[[dict], None]] = None,
        on_fault_cleared: Optional[Callable[[dict], None]] = None,
    ):
        self.network = network
        self.on_fault_event = on_fault_event or (lambda e: None)
        self.on_fault_cleared = on_fault_cleared or (lambda e: None)

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._active_timers: dict[str, threading.Timer] = {}

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="FaultEngine", daemon=True)
        self._thread.start()
        logger.info("FaultEngine iniciado")

    def stop(self):
        self._running = False
        for timer in self._active_timers.values():
            timer.cancel()
        logger.info("FaultEngine parado")

    # ------------------------------------------------------------------
    # Loop principal
    # ------------------------------------------------------------------

    def _loop(self):
        while self._running:
            wait = random.uniform(FaultConfig.CHECK_INTERVAL_MIN, FaultConfig.CHECK_INTERVAL_MAX)
            logger.debug("FaultEngine: próxima verificação em %.0f s", wait)
            time.sleep(wait)

            if not self._running:
                break

            if random.random() > FaultConfig.FAULT_PROBABILITY:
                logger.debug("FaultEngine: sem falta neste ciclo")
                continue

            self._inject_random_fault()

    # ------------------------------------------------------------------
    # Injeção e remoção de falta
    # ------------------------------------------------------------------

    def _inject_random_fault(self):
        # Escolhe um alimentador aleatório que esteja energizado e sem falta ativa
        available = [
            fid for fid in self.network.feeders
            if self.network.is_feeder_energized(fid)
            and self.network.get_active_fault(fid) is None
        ]
        if not available:
            logger.debug("FaultEngine: nenhum alimentador disponível para falta")
            return

        feeder_id = random.choice(available)
        fault_type = _weighted_choice(FaultConfig.FAULT_TYPES)
        segment = random.choice(FaultConfig.FAULT_SEGMENTS)
        duration = random.uniform(FaultConfig.FAULT_DURATION_MIN, FaultConfig.FAULT_DURATION_MAX)

        fault = FaultEvent(
            feeder_id=feeder_id,
            fault_type=fault_type,
            segment=segment,
        )
        self.network.inject_fault(fault)

        event_payload = {
            "event": "fault_injected",
            "feeder_id": feeder_id,
            "fault_type": fault_type,
            "segment": segment,
            "timestamp": time.time(),
            "severity": self._severity(fault_type),
            "description": self._describe_fault(fault_type, feeder_id, segment),
        }
        logger.warning("FALTA: %s", event_payload)
        self.on_fault_event(event_payload)

        # Agenda remoção automática
        timer = threading.Timer(duration, self._clear_fault, args=[feeder_id, fault_type, segment])
        self._active_timers[feeder_id] = timer
        timer.start()
        logger.info("Falta em %s será removida em %.0f s", feeder_id, duration)

    def _clear_fault(self, feeder_id: str, fault_type: str, segment: int):
        self.network.clear_fault(feeder_id)
        self._active_timers.pop(feeder_id, None)

        cleared_payload = {
            "event": "fault_cleared",
            "feeder_id": feeder_id,
            "fault_type": fault_type,
            "segment": segment,
            "timestamp": time.time(),
            "description": f"Falta removida em {feeder_id} — segmento {segment}",
        }
        logger.info("FALTA REMOVIDA: %s", cleared_payload)
        self.on_fault_cleared(cleared_payload)

    # ------------------------------------------------------------------
    # API pública para injeção manual (via comando MQTT)
    # ------------------------------------------------------------------

    def inject_manual_fault(self, feeder_id: str, fault_type: str, segment: int, duration: float = 30.0):
        """Permite injetar falta manualmente via mensagem MQTT de controle."""
        if feeder_id not in self.network.feeders:
            logger.warning("inject_manual_fault: alimentador %s inexistente", feeder_id)
            return

        # Cancela falta anterior no mesmo alimentador (se houver)
        existing = self._active_timers.pop(feeder_id, None)
        if existing:
            existing.cancel()
            self.network.clear_fault(feeder_id)

        fault = FaultEvent(feeder_id=feeder_id, fault_type=fault_type, segment=segment)
        self.network.inject_fault(fault)

        payload = {
            "event": "fault_injected",
            "feeder_id": feeder_id,
            "fault_type": fault_type,
            "segment": segment,
            "timestamp": time.time(),
            "severity": self._severity(fault_type),
            "description": self._describe_fault(fault_type, feeder_id, segment),
            "manual": True,
        }
        self.on_fault_event(payload)

        timer = threading.Timer(duration, self._clear_fault, args=[feeder_id, fault_type, segment])
        self._active_timers[feeder_id] = timer
        timer.start()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _severity(fault_type: str) -> str:
        return {
            "short_circuit":   "CRITICAL",
            "high_impedance":  "WARNING",
            "open_circuit":    "MAJOR",
        }.get(fault_type, "WARNING")

    @staticmethod
    def _describe_fault(fault_type: str, feeder_id: str, segment: int) -> str:
        seg_names = {1: "início", 2: "meio", 3: "fim"}
        descriptions = {
            "short_circuit":  "Curto-circuito detectado",
            "high_impedance": "Falta de alta impedância detectada",
            "open_circuit":   "Circuito aberto detectado",
        }
        base = descriptions.get(fault_type, "Falta detectada")
        return f"{base} no {seg_names.get(segment, 'segmento desconhecido')} do alimentador {feeder_id}"
