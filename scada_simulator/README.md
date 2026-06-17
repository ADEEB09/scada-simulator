# 🔌 Simulador SCADA — Rede Elétrica de Distribuição

Simulador de telemetria em tempo real de uma rede de distribuição elétrica de médio porte.
Desenvolvido para o **Desafio Técnico de Desenvolvedor Full Stack SCADA/ADMS**.

---

## 🏗️ Arquitetura do Simulador

```
┌─────────────────────────────────────────────────────────────────┐
│                        SIMULADOR SCADA                          │
│                                                                 │
│  ┌─────────────┐    ┌──────────────┐    ┌──────────────────┐   │
│  │NetworkModel │    │ TelemetryGen │    │  FaultEngine     │   │
│  │             │───►│              │    │                  │   │
│  │ - Topologia │    │ - Curva diária│   │ - Faltas aleat.  │   │
│  │ - Chaves    │    │ - Física elét.│   │ - Injeção manual │   │
│  │ - Faltas    │    │ - Ruído/Noise │   │ - Auto-remoção   │   │
│  └─────────────┘    └──────────────┘    └──────────────────┘   │
│           │                │                      │            │
│           └────────────────┴──────────────────────┘            │
│                                    │                           │
│                          ┌─────────▼────────┐                  │
│                          │   MQTTHandler     │                  │
│                          │                  │                  │
│                          │  Pub: telemetria │                  │
│                          │  Pub: alarmes    │                  │
│                          │  Sub: comandos   │                  │
│                          └─────────┬────────┘                  │
└────────────────────────────────────│────────────────────────────┘
                                     │ MQTT
                          ┌──────────▼────────┐
                          │ Broker Mosquitto   │
                          │  porta 1883 (TCP)  │
                          │  porta 9001 (WS)   │
                          └──────────┬────────┘
                                     │
                          ┌──────────▼────────┐
                          │  Aplicação do      │
                          │  CANDIDATO         │
                          └───────────────────┘
```

---

## 🗺️ Topologia da Rede

```
                    ┌─────────────────┐
                    │  SE-01          │
                    │  Subestação     │  ◄── MED-01 (medição geral)
                    │  13,8 kV        │
                    └────┬──────┬──┬──┘
                         │      │  │
              ┌──────────┘      │  └──────────────┐
              │                 │                 │
           [CH-01]           [CH-03]           [CH-04]
              │                 │                 │
         ─────┴─────       ─────┴─────       ─────┴─────
        │  AL-01    │     │  AL-02    │     │  AL-03    │
        │ Alim. Norte│     │ Alim. Sul │     │ Alim. Leste│
         ─────┬─────       ─────┬─────       ─────┬─────
              │                 │                 │
         MED-02 (início)   MED-05 (início)   MED-08 (início)
              │                 │                 │
           [CH-02]             ...              ...
              │
         MED-03 (meio)     MED-06 (meio)    MED-09 (meio)
              │                 │                 │
         MED-04 (fim)      MED-07 (fim)     MED-10 (fim)
              │                 │
              └────[CH-05]──────┘
                 (chave de interligação — normalmente ABERTA)
```

### Medidores (10 pontos)
| ID     | Descrição             | Alimentador |
|--------|-----------------------|-------------|
| MED-01 | Saída geral SE-01     | —           |
| MED-02 | Entrada AL-01         | AL-01       |
| MED-03 | Meio AL-01            | AL-01       |
| MED-04 | Fim AL-01             | AL-01       |
| MED-05 | Entrada AL-02         | AL-02       |
| MED-06 | Meio AL-02            | AL-02       |
| MED-07 | Fim AL-02             | AL-02       |
| MED-08 | Entrada AL-03         | AL-03       |
| MED-09 | Meio AL-03            | AL-03       |
| MED-10 | Fim AL-03             | AL-03       |

### Chaves (5 unidades)
| ID    | Tipo            | Estado inicial | Alimentador    |
|-------|-----------------|----------------|----------------|
| CH-01 | Seccionadora    | Fechada        | AL-01          |
| CH-02 | Seccionadora    | Fechada        | AL-01          |
| CH-03 | Seccionadora    | Fechada        | AL-02          |
| CH-04 | Seccionadora    | Fechada        | AL-03          |
| CH-05 | Interligação    | **Aberta**     | AL-01 / AL-02  |

---

## 📡 Protocolo de Comunicação (MQTT)

### Tópicos Publicados pelo Simulador

#### `scada/telemetry/{measurement_id}` — QoS 0 (1 vez por segundo)
```json
{
  "timestamp": 1710000000.123,
  "measurement_id": "MED-03",
  "feeder_id": "AL-01",
  "energized": true,
  "voltage_v": 7845.32,
  "current_a": 48.271,
  "active_power_kw": 285.4,
  "reactive_power_kvar": 138.7,
  "apparent_power_kva": 317.8,
  "power_factor": 0.8981,
  "frequency_hz": 59.998,
  "overvoltage": false,
  "undervoltage": false,
  "overcurrent": false,
  "fault_current": false
}
```

#### `scada/switch/{switch_id}/state` — QoS 1, retain=true
```json
{
  "switch_id": "CH-01",
  "name": "Chave Entrada AL-01",
  "state": "closed",
  "type": "sectionalizer",
  "feeder": "AL-01",
  "timestamp": 1710000000.0
}
```

#### `scada/alarm/fault` — QoS 1
```json
{
  "event": "fault_injected",
  "feeder_id": "AL-02",
  "fault_type": "short_circuit",
  "segment": 2,
  "timestamp": 1710000050.0,
  "severity": "CRITICAL",
  "description": "Curto-circuito detectado no meio do alimentador AL-02"
}
```

#### `scada/alarm/measurement` — QoS 1
```json
{
  "timestamp": 1710000051.0,
  "measurement_id": "MED-06",
  "feeder_id": "AL-02",
  "severity": "CRITICAL",
  "name": "Corrente de falta detectada",
  "description": "Corrente de falta em MED-06: 412.3 A",
  "value": 412.3
}
```

#### `scada/network/snapshot` — QoS 1, retain=true
```json
{
  "switches": {
    "CH-01": "closed",
    "CH-02": "closed",
    "CH-03": "closed",
    "CH-04": "closed",
    "CH-05": "open"
  },
  "energized_feeders": ["AL-01", "AL-02", "AL-03"],
  "active_faults": {},
  "timestamp": 1710000000.0
}
```

#### `scada/simulator/status` — QoS 0, retain=true
```json
{"status": "online", "timestamp": 1710000000.0, "version": "1.0.0"}
```

---

### Tópicos Subscritos pelo Simulador (Comandos)

#### `scada/commands/switch/{switch_id}` — Operar Chave
```json
{
  "command": "open",
  "operator": "joao.silva",
  "timestamp": 1710000100.0
}
```

**Resposta** publicada em `scada/commands/switch/{switch_id}/response`:
```json
{
  "success": true,
  "switch_id": "CH-01",
  "old_state": "closed",
  "new_state": "open",
  "operator": "joao.silva",
  "command_timestamp": 1710000100.0,
  "response_timestamp": 1710000100.05
}
```

#### `scada/commands/fault/inject` — Injetar Falta Manualmente
```json
{
  "feeder_id": "AL-01",
  "fault_type": "short_circuit",
  "segment": 1,
  "duration": 45
}
```
> `fault_type`: `"short_circuit"` | `"high_impedance"` | `"open_circuit"`
> `segment`: `1` (início) | `2` (meio) | `3` (fim)

#### `scada/commands/fault/clear` — Remover Falta
```json
{"feeder_id": "AL-01"}
```

---

## 🚀 Como Executar

### Pré-requisitos
- Docker e Docker Compose instalados

### 1. Subir o simulador completo
```bash
docker-compose up --build
```

### 2. Monitorar mensagens MQTT no terminal
```bash
docker-compose --profile monitor up
```

### 3. Subscrever manualmente (cliente externo)
```bash
# Instalar cliente MQTT
pip install paho-mqtt

# Subscrever todos os tópicos
mosquitto_sub -h localhost -t "scada/#" -v
```

### 4. Enviar comando de chave manualmente
```bash
mosquitto_pub -h localhost \
  -t "scada/commands/switch/CH-01" \
  -m '{"command": "open", "operator": "teste"}' \
  -q 1
```

### 5. Injetar falta manualmente
```bash
mosquitto_pub -h localhost \
  -t "scada/commands/fault/inject" \
  -m '{"feeder_id": "AL-02", "fault_type": "short_circuit", "segment": 2, "duration": 30}' \
  -q 1
```

### 6. Rodar sem Docker (desenvolvimento local)
```bash
pip install -r requirements.txt

# Broker local necessário
mosquitto -c mosquitto/mosquitto.conf &

# Simulador
cd src
python main.py --host localhost --port 1883 --interval 1.0
```

### Opções de linha de comando
```
python main.py [--host HOST] [--port PORT] [--interval SECONDS] [--no-faults]

  --host       Host do broker MQTT          (padrão: localhost)
  --port       Porta do broker MQTT         (padrão: 1883)
  --interval   Intervalo de telemetria (s)  (padrão: 1.0)
  --no-faults  Desabilita faltas automáticas
```

---

## ⚡ Física Elétrica Simplificada

| Grandeza            | Valor nominal              | Observação                          |
|---------------------|----------------------------|-------------------------------------|
| Tensão (fase-fase)  | 13.800 V                   | Rede MT típica brasileira            |
| Tensão (fase-neutro)| ~7.967 V                   | Vnominal / √3                       |
| Frequência          | 60 Hz                      | Padrão brasileiro                   |
| Fator de potência   | 0,92 (típico)              | Variação ±0,05                      |
| Sobretensão alarm.  | > 105% Vn (>8.365 V)       | Configurável em network.json        |
| Subtensão alarm.    | < 92% Vn (<7.330 V)        | Configurável em network.json        |
| Curva de carga      | Diária (00h–23h)           | Pico às 18h, vale às 3h             |

### Efeito das faltas
| Tipo               | Corrente          | Tensão                      |
|--------------------|-------------------|-----------------------------|
| Curto-circuito     | 8× a nominal       | Colpasa no segmento afetado |
| Alta impedância    | 1,35× a nominal    | Leve depressão              |
| Circuito aberto    | Zero (downstream) | Zero (downstream)           |

---

## 📁 Estrutura de Arquivos

```
scada_simulator/
├── docker-compose.yml          # Orquestração dos serviços
├── Dockerfile                  # Imagem do simulador
├── requirements.txt            # Dependências Python
├── mosquitto/
│   └── mosquitto.conf          # Configuração do broker MQTT
├── config/
│   └── network.json            # Topologia da rede elétrica
└── src/
    ├── main.py                 # Ponto de entrada
    ├── network.py              # Modelo de rede (estado, chaves, faltas)
    ├── telemetry.py            # Gerador de telemetria realista
    ├── fault_engine.py         # Motor de faltas automáticas
    └── mqtt_handler.py         # Comunicação MQTT (pub/sub)
```

---

## ❓ Perguntas Frequentes

**P: Posso alterar a topologia da rede?**
> Sim. Edite `config/network.json` para adicionar medidores, chaves ou alimentadores. O simulador carrega a configuração na inicialização.

**P: Como ajustar a frequência de faltas?**
> Edite as constantes `CHECK_INTERVAL_MIN/MAX` e `FAULT_PROBABILITY` em `src/fault_engine.py`.

**P: O simulador suporta múltiplos clientes conectados?**
> Sim. O broker Mosquitto aceita múltiplas conexões simultâneas. Você pode conectar sua aplicação e um cliente de debug ao mesmo tempo.

**P: Qual porta usar para uma HMI web com WebSocket?**
> Porta `9001` — o Mosquitto está configurado com listener WebSocket nessa porta. Use bibliotecas como `mqtt.js` no frontend.

---

## 📜 Licença

Distribuído para uso exclusivo no processo seletivo. Proibida distribuição sem autorização.
