"""
main.py
-------
Ponto de entrada do Simulador SCADA.

Uso:
  python main.py [--host BROKER] [--port PORT] [--interval SECONDS]

Ou via variáveis de ambiente:
  MQTT_HOST, MQTT_PORT, TELEMETRY_INTERVAL

O simulador:
  1. Carrega a topologia de rede de config/network.json
  2. Conecta ao broker MQTT
  3. Publica telemetria de todos os pontos de medição continuamente
  4. Injeta faltas aleatórias periodicamente
  5. Aguarda comandos de controle de chaves
  6. Encerra graciosamente com Ctrl+C (SIGTERM/SIGINT)
"""

import os
import sys
import signal
import logging
import argparse
import time
from pathlib import Path

# Garante que o diretório src esteja no path
sys.path.insert(0, str(Path(__file__).parent))

from network import NetworkModel
from telemetry import TelemetryGenerator
from fault_engine import FaultEngine
from mqtt_handler import MQTTHandler

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("simulator.main")


# ---------------------------------------------------------------------------
# Argparse / Env
# ---------------------------------------------------------------------------
def parse_args():
    parser = argparse.ArgumentParser(description="Simulador SCADA de Rede Elétrica de Distribuição")
    parser.add_argument("--host",     default=os.getenv("MQTT_HOST", "localhost"),
                        help="Host do broker MQTT (padrão: localhost)")
    parser.add_argument("--port",     type=int, default=int(os.getenv("MQTT_PORT", "1883")),
                        help="Porta do broker MQTT (padrão: 1883)")
    parser.add_argument("--interval", type=float, default=float(os.getenv("TELEMETRY_INTERVAL", "1.0")),
                        help="Intervalo de publicação de telemetria em segundos (padrão: 1.0)")
    parser.add_argument("--no-faults", action="store_true",
                        help="Desabilita injeção automática de faltas")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
BANNER = r"""
╔═══════════════════════════════════════════════════════════╗
║        SIMULADOR SCADA — Rede Elétrica de Distribuição    ║
║                                                           ║
║  1 Subestação  |  3 Alimentadores  |  10 Medidores        ║
║  5 Chaves seccionadoras  |  Motor de faltas ativo         ║
╚═══════════════════════════════════════════════════════════╝
"""

TOPICS_INFO = """
📡 TÓPICOS MQTT PUBLICADOS:
   scada/telemetry/{MED-XX}        → Leituras de telemetria (1 s)
   scada/switch/{CH-XX}/state      → Estado das chaves
   scada/alarm/fault               → Eventos de falta
   scada/alarm/measurement         → Alarmes de grandezas
   scada/network/snapshot          → Snapshot da rede
   scada/simulator/status          → Heartbeat

📥 TÓPICOS MQTT SUBSCRITOS (comandos):
   scada/commands/switch/{CH-XX}   → Operar chave
     Payload: {"command": "open"|"close", "operator": "usuario"}
   scada/commands/fault/inject     → Injetar falta manualmente
     Payload: {"feeder_id": "AL-01", "fault_type": "short_circuit", "segment": 2, "duration": 30}
   scada/commands/fault/clear      → Remover falta
     Payload: {"feeder_id": "AL-01"}
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    args = parse_args()
    print(BANNER)
    print(TOPICS_INFO)

    logger.info("Iniciando simulador — broker=%s:%d  intervalo=%.1fs",
                args.host, args.port, args.interval)

    # 1. Modelo de rede
    network = NetworkModel()

    # 2. Gerador de telemetria
    telemetry_gen = TelemetryGenerator(network)

    # 3. MQTT Handler
    mqtt_handler = MQTTHandler(
        network=network,
        telemetry_gen=telemetry_gen,
        broker_host=args.host,
        broker_port=args.port,
        telemetry_interval=args.interval,
    )

    # 4. Motor de faltas
    fault_engine = None
    if not args.no_faults:
        fault_engine = FaultEngine(
            network=network,
            on_fault_event=mqtt_handler.on_fault_event,
            on_fault_cleared=mqtt_handler.on_fault_cleared,
        )
        mqtt_handler.fault_engine = fault_engine

    # 5. Sinal de encerramento gracioso
    _running = {"value": True}

    def _shutdown(signum, frame):
        logger.info("Sinal de encerramento recebido — finalizando…")
        _running["value"] = False

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # 6. Inicia componentes
    try:
        mqtt_handler.connect()
        time.sleep(2)  # Aguarda conexão estabilizar

        mqtt_handler.start_publishing()

        if fault_engine:
            fault_engine.start()

        logger.info("Simulador rodando. Pressione Ctrl+C para encerrar.")

        while _running["value"]:
            time.sleep(1)

    finally:
        logger.info("Encerrando componentes…")
        if fault_engine:
            fault_engine.stop()
        mqtt_handler.stop()
        logger.info("Simulador encerrado.")


if __name__ == "__main__":
    main()
