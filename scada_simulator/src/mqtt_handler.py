"""
mqtt_handler.py
---------------
Gerencia toda a comunicação MQTT do simulador:

Tópicos publicados (simulador → candidato):
  scada/telemetry/{measurement_id}   → leituras de telemetria (JSON)
  scada/switch/{switch_id}/state     → estado atual de cada chave (JSON)
  scada/alarm/fault                  → eventos de falta (JSON)
  scada/alarm/measurement            → alarmes de grandezas (sobretensão, etc.)
  scada/network/snapshot             → snapshot completo da rede (JSON)
  scada/simulator/status             → heartbeat do simulador

Tópicos subscritos (candidato → simulador):
  scada/commands/switch/{switch_id}  → comando de chave: {"command": "open"|"close", "operator": "..."}
  scada/commands/fault/inject        → injeção manual de falta (para testes)
  scada/commands/fault/clear         → remoção manual de falta

QoS:
  Telemetria: 0 (best-effort, alta frequência)
  Alarmes e comandos: 1 (at-least-once)
  Snapshots e estados: 1
"""

import json
import time
import logging
import threading
from typing import Optional

import paho.mqtt.client as mqtt

from network import NetworkModel
from telemetry import TelemetryGenerator, TelemetryReading

logger = logging.getLogger(__name__)


class MQTTHandler:
    """Conecta ao broker MQTT e gerencia pub/sub do simulador."""

    def __init__(
        self,
        network: NetworkModel,
        telemetry_gen: TelemetryGenerator,
        fault_engine=None,
        broker_host: str = "localhost",
        broker_port: int = 1883,
        telemetry_interval: float = 1.0,
        client_id: str = "scada-simulator",
    ):
        self.network = network
        self.telemetry_gen = telemetry_gen
        self.fault_engine = fault_engine
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.telemetry_interval = telemetry_interval

        self._client = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv5)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

        self._connected = False
        self._running = False
        self._telemetry_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    def connect(self):
        logger.info("Conectando ao broker MQTT %s:%d …", self.broker_host, self.broker_port)
        self._client.connect(self.broker_host, self.broker_port, keepalive=60)
        self._client.loop_start()

    def start_publishing(self):
        self._running = True
        self._telemetry_thread = threading.Thread(
            target=self._telemetry_loop, name="TelemetryPublisher", daemon=True
        )
        self._telemetry_thread.start()
        logger.info("Publicação de telemetria iniciada (intervalo=%.1f s)", self.telemetry_interval)

    def stop(self):
        self._running = False
        self._client.loop_stop()
        self._client.disconnect()
        logger.info("MQTTHandler desconectado")

    # ------------------------------------------------------------------
    # Callbacks MQTT
    # ------------------------------------------------------------------

    def _on_connect(self, client, userdata, flags, rc, props=None):
        if rc == 0:
            self._connected = True
            logger.info("Conectado ao broker MQTT ✓")
            self._subscribe_commands()
            # Publica snapshot inicial
            self._publish_snapshot()
            self._publish_switch_states()
        else:
            logger.error("Falha na conexão MQTT — código %d", rc)

    def _on_disconnect(self, client, userdata, rc, props=None):
        self._connected = False
        logger.warning("Desconectado do broker MQTT (rc=%d)", rc)

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except json.JSONDecodeError:
            logger.warning("Mensagem inválida no tópico %s: %s", topic, msg.payload)
            return

        logger.debug("Mensagem recebida: %s → %s", topic, payload)

        # --- Comandos de chave ---
        if topic.startswith("scada/commands/switch/"):
            switch_id = topic.split("/")[-1]
            self._handle_switch_command(switch_id, payload)

        # --- Injeção manual de falta ---
        elif topic == "scada/commands/fault/inject":
            self._handle_fault_inject(payload)

        # --- Remoção manual de falta ---
        elif topic == "scada/commands/fault/clear":
            self._handle_fault_clear(payload)

    # ------------------------------------------------------------------
    # Subscrição de tópicos de comando
    # ------------------------------------------------------------------

    def _subscribe_commands(self):
        topics = [
            ("scada/commands/switch/+", 1),
            ("scada/commands/fault/inject", 1),
            ("scada/commands/fault/clear", 1),
        ]
        for topic, qos in topics:
            self._client.subscribe(topic, qos)
            logger.info("Subscrito: %s (QoS %d)", topic, qos)

    # ------------------------------------------------------------------
    # Handlers de comando
    # ------------------------------------------------------------------

    def _handle_switch_command(self, switch_id: str, payload: dict):
        command   = payload.get("command", "")
        operator  = payload.get("operator", "unknown")
        timestamp = payload.get("timestamp", time.time())

        logger.info("Comando de chave: %s → %s (operador: %s)", switch_id, command, operator)

        result = self.network.operate_switch(switch_id, command)
        result["operator"] = operator
        result["command_timestamp"] = timestamp
        result["response_timestamp"] = time.time()

        # Publica resposta da operação
        self._publish(
            topic=f"scada/commands/switch/{switch_id}/response",
            payload=result,
            qos=1,
        )

        # Se bem-sucedido, publica novo estado da chave
        if result["success"]:
            self._publish_switch_state(switch_id)
            self._publish_snapshot()

            # Registra log de auditoria
            self._publish(
                topic="scada/audit/switch_operation",
                payload={
                    "switch_id": switch_id,
                    "old_state": result["old_state"],
                    "new_state": result["new_state"],
                    "operator": operator,
                    "timestamp": result["response_timestamp"],
                },
                qos=1,
                retain=False,
            )

    def _handle_fault_inject(self, payload: dict):
        if self.fault_engine is None:
            return
        feeder_id  = payload.get("feeder_id")
        fault_type = payload.get("fault_type", "short_circuit")
        segment    = int(payload.get("segment", 1))
        duration   = float(payload.get("duration", 30))

        if not feeder_id:
            logger.warning("fault/inject sem feeder_id")
            return

        logger.info("Injeção manual: feeder=%s tipo=%s seg=%d dur=%.0fs",
                    feeder_id, fault_type, segment, duration)
        self.fault_engine.inject_manual_fault(feeder_id, fault_type, segment, duration)

    def _handle_fault_clear(self, payload: dict):
        feeder_id = payload.get("feeder_id")
        self.network.clear_fault(feeder_id)
        self._publish(
            topic="scada/alarm/fault",
            payload={
                "event": "fault_cleared",
                "feeder_id": feeder_id,
                "timestamp": time.time(),
                "manual": True,
            },
            qos=1,
        )

    # ------------------------------------------------------------------
    # Loop de telemetria
    # ------------------------------------------------------------------

    def _telemetry_loop(self):
        cycle = 0
        while self._running:
            start = time.time()

            if self._connected:
                try:
                    readings = self.telemetry_gen.generate_all()
                    for reading in readings:
                        self._publish_telemetry(reading)
                        # Alarmes de grandeza
                        self._check_and_publish_measurement_alarm(reading)

                    # A cada 10 ciclos, publica snapshot e estado das chaves
                    if cycle % 10 == 0:
                        self._publish_snapshot()
                        self._publish_switch_states()

                    # Heartbeat a cada 30 ciclos
                    if cycle % 30 == 0:
                        self._publish_heartbeat()

                except Exception as exc:
                    logger.exception("Erro no loop de telemetria: %s", exc)

            cycle += 1
            elapsed = time.time() - start
            sleep_time = max(0.0, self.telemetry_interval - elapsed)
            time.sleep(sleep_time)

    # ------------------------------------------------------------------
    # Publicadores específicos
    # ------------------------------------------------------------------

    def _publish_telemetry(self, reading: TelemetryReading):
        self._publish(
            topic=f"scada/telemetry/{reading.measurement_id}",
            payload=reading.to_dict(),
            qos=0,
        )

    def _check_and_publish_measurement_alarm(self, reading: TelemetryReading):
        alarms = []
        if reading.fault_current:
            alarms.append(("CRITICAL", "Corrente de falta detectada",
                           f"Corrente de falta em {reading.measurement_id}: {reading.current_a:.1f} A"))
        if reading.overcurrent:
            alarms.append(("MAJOR", "Sobrecorrente",
                           f"Sobrecorrente em {reading.measurement_id}: {reading.current_a:.1f} A"))
        if reading.overvoltage:
            alarms.append(("WARNING", "Sobretensão",
                           f"Sobretensão em {reading.measurement_id}: {reading.voltage_v:.1f} V"))
        if reading.undervoltage and reading.energized:
            alarms.append(("WARNING", "Subtensão",
                           f"Subtensão em {reading.measurement_id}: {reading.voltage_v:.1f} V"))

        for severity, name, description in alarms:
            self._publish(
                topic="scada/alarm/measurement",
                payload={
                    "timestamp": reading.timestamp,
                    "measurement_id": reading.measurement_id,
                    "feeder_id": reading.feeder_id,
                    "severity": severity,
                    "name": name,
                    "description": description,
                    "value": reading.voltage_v if "tensão" in name.lower() else reading.current_a,
                },
                qos=1,
            )

    def _publish_switch_states(self):
        for switch_id in self.network.switches:
            self._publish_switch_state(switch_id)

    def _publish_switch_state(self, switch_id: str):
        sw = self.network.switches.get(switch_id)
        if sw:
            self._publish(
                topic=f"scada/switch/{switch_id}/state",
                payload={
                    "switch_id": sw.id,
                    "name": sw.name,
                    "state": sw.state,
                    "type": sw.type,
                    "feeder": sw.feeder,
                    "timestamp": time.time(),
                },
                qos=1,
                retain=True,
            )

    def _publish_snapshot(self):
        snap = self.network.snapshot()
        snap["timestamp"] = time.time()
        self._publish(topic="scada/network/snapshot", payload=snap, qos=1, retain=True)

    def _publish_heartbeat(self):
        self._publish(
            topic="scada/simulator/status",
            payload={"status": "online", "timestamp": time.time(), "version": "1.0.0"},
            qos=0,
            retain=True,
        )

    # Callbacks registrados pelo FaultEngine
    def on_fault_event(self, event: dict):
        self._publish(topic="scada/alarm/fault", payload=event, qos=1)

    def on_fault_cleared(self, event: dict):
        self._publish(topic="scada/alarm/fault", payload=event, qos=1)

    # ------------------------------------------------------------------
    # Helper de publicação
    # ------------------------------------------------------------------

    def _publish(self, topic: str, payload: dict, qos: int = 0, retain: bool = False):
        if not self._connected:
            return
        try:
            msg = json.dumps(payload, ensure_ascii=False, default=str)
            self._client.publish(topic, msg, qos=qos, retain=retain)
        except Exception as exc:
            logger.error("Erro ao publicar em %s: %s", topic, exc)
