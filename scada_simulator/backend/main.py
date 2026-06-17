import json
from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import paho.mqtt.client as mqtt
import psycopg2

# Initialize the central FastAPI instance
app = FastAPI(title="Sharika Mini-SCADA Core Engine")

# Enable CORS for frontend dashboard connectivity
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Core Connection Configurations
# Change host=127.0.0.1 to host=scada-db
DB_DSN = "dbname=scada_network user=scada_user password=scada_password host=scada-db port=5432"
MQTT_BROKER = "mosquitto"
MQTT_PORT = 1883

def get_db_connection():
    return psycopg2.connect(DB_DSN)

class SwitchCommand(BaseModel):
    switch_id: str
    command: str  # "open" or "close"
    operator: str

# --- Real-Time Processing & Alarm Generation (Layer 3) ---
def evaluate_realtime_anomalies(cursor, data):
    """Parses raw stream metrics to flag safety violations in the grid."""
    m_id = data.get("measurement_id")
    v = data.get("voltage_v", 0)
    ts = datetime.fromtimestamp(data.get("timestamp", datetime.now().timestamp()), tz=timezone.utc)

    # 13.8kV Nominal Phase-to-Neutral is ~7967V. Overvoltage > 105% (8365V), Undervoltage < 92% (7330V)
    if v > 8365.0:
        cursor.execute(
            """INSERT INTO alarm_registry (timestamp, source_id, severity, alarm_type, description)
               VALUES (%s, %s, 'CRITICAL', 'OVERVOLTAGE', %s) ON CONFLICT DO NOTHING""",
            (ts, m_id, f"High voltage limit exceeded: {v} V")
        )
    elif v < 7330.0 and data.get("energized", True):
        cursor.execute(
            """INSERT INTO alarm_registry (timestamp, source_id, severity, alarm_type, description)
               VALUES (%s, %s, 'WARNING', 'UNDERVOLTAGE', %s) ON CONFLICT DO NOTHING""",
            (ts, m_id, f"Low voltage sag registered: {v} V")
        )

# --- MQTT Communication Ingestion Driver (Layer 1) ---
def on_message(client, userdata, msg):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        topic = msg.topic
        payload = json.loads(msg.payload.decode('utf-8'))
        
        # Route 1: Telemetry Packets
        if "telemetry" in topic:
            ts = datetime.fromtimestamp(payload["timestamp"], tz=timezone.utc)
            feeder_id = payload.get("feeder_id") or "SUBSTATION"  # Safe Null fallback for MED-01
            
            cursor.execute(
                """INSERT INTO metrics_history (timestamp, measurement_id, feeder_id, voltage_v, current_a, active_power_kw, reactive_power_kvar, power_factor, frequency_hz, energized, fault_current)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (ts, payload["measurement_id"], feeder_id, payload["voltage_v"], payload["current_a"], payload["active_power_kw"], payload["reactive_power_kvar"], payload["power_factor"], payload["frequency_hz"], payload["energized"], payload["fault_current"])
            )
            evaluate_realtime_anomalies(cursor, payload)
            
        # Route 2: Breaker Status Changes
        elif "switch" in topic and "state" in topic:
            ts = datetime.fromtimestamp(payload["timestamp"], tz=timezone.utc)
            cursor.execute(
                """INSERT INTO switch_states (switch_id, state, last_updated)
                   VALUES (%s, %s, %s) ON CONFLICT (switch_id) DO UPDATE SET state = EXCLUDED.state, last_updated = EXCLUDED.last_updated""",
                (payload["switch_id"], payload["state"], ts)
            )
            
        # Route 3: Native Simulator Alarms
        elif "alarm" in topic:
            ts = datetime.fromtimestamp(payload["timestamp"], tz=timezone.utc)
            cursor.execute(
                """INSERT INTO alarm_registry (timestamp, source_id, severity, alarm_type, description)
                   VALUES (%s, %s, %s, %s, %s)""",
                (ts, payload.get("measurement_id", payload.get("feeder_id", "GRID")), payload["severity"], "NATIVE_FAULT", payload["description"])
            )

        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Database Ingestion Malfunction: {e}")
    finally:
        cursor.close()
        conn.close()

@app.on_event("startup")
def startup_mqtt():
    mqtt_client = mqtt.Client()
    mqtt_client.on_message = on_message
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
    mqtt_client.subscribe("scada/#")
    mqtt_client.loop_start()

# --- REST Service API (Layer 5 Endpoints) ---
@app.get("/api/telemetry/snapshot")
def get_snapshot():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT ON (measurement_id) measurement_id, feeder_id, voltage_v, current_a, active_power_kw, energized, fault_current 
        FROM metrics_history ORDER BY measurement_id, timestamp DESC;
    """)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return [{"meter": r[0], "feeder": r[1], "v": r[2], "a": r[3], "kw": r[4], "alive": r[5], "fault": r[6]} for r in rows]

@app.post("/api/switch/control")
def control_switch(cmd: SwitchCommand):
    try:
        # Establish isolated client to fire command to broker
        client = mqtt.Client()
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        
        topic = f"scada/commands/switch/{cmd.switch_id}"
        payload = {
            "command": cmd.command,
            "operator": cmd.operator,
            "timestamp": datetime.now(timezone.utc).timestamp()
        }
        
        # Publish direct trip command to the broker
        client.publish(topic, json.dumps(payload), qos=1)
        client.disconnect()

        # Write execution event directly to immutable security log table
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO command_audit_log (timestamp, switch_id, command, operator, success)
               VALUES (NOW(), %s, %s, %s, TRUE)""",
            (cmd.switch_id, cmd.command, cmd.operator)
        )
        conn.commit()
        cursor.close()
        conn.close()
        
        return {"status": "success", "message": f"Dispatched {cmd.command} to {cmd.switch_id}"}
    except Exception as e:
        print(f"Control Loop Failure: {e}")
        return {"status": "error", "message": str(e)}
