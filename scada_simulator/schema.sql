-- Enable TimescaleDB if available, otherwise standard PG works fine
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- 1. High-Frequency Telemetry Ledger
CREATE TABLE IF NOT EXISTS metrics_history (
    timestamp TIMESTAMPTZ NOT NULL,
    measurement_id VARCHAR(50) NOT NULL,
    feeder_id VARCHAR(50) NOT NULL,
    voltage_v DOUBLE PRECISION,
    current_a DOUBLE PRECISION,
    active_power_kw DOUBLE PRECISION,
    reactive_power_kvar DOUBLE PRECISION,
    power_factor DOUBLE PRECISION,
    frequency_hz DOUBLE PRECISION,
    energized BOOLEAN,
    fault_current BOOLEAN
);
-- Convert to hypertable for massive performance boost if using Timescale
SELECT create_hypertable('metrics_history', 'timestamp', if_not_exists => TRUE);

-- 2. System State Cache (Current State)
CREATE TABLE IF NOT EXISTS switch_states (
    switch_id VARCHAR(50) PRIMARY KEY,
    state VARCHAR(20) NOT NULL,
    last_updated TIMESTAMPTZ NOT NULL
);

-- 3. Alarm Logging Registry
CREATE TABLE IF NOT EXISTS alarm_registry (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    source_id VARCHAR(50) NOT NULL, -- Device ID causing the alarm
    severity VARCHAR(20) NOT NULL,  -- CRITICAL, WARNING, INFO
    alarm_type VARCHAR(50) NOT NULL, -- overvoltage, short_circuit, etc.
    description TEXT NOT NULL,
    acknowledged BOOLEAN DEFAULT FALSE,
    cleared BOOLEAN DEFAULT FALSE
);

-- 4. Supervisor Command Audit Log (Security Criterion)
CREATE TABLE IF NOT EXISTS command_audit_log (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    switch_id VARCHAR(50) NOT NULL,
    command VARCHAR(20) NOT NULL,
    operator VARCHAR(100) NOT NULL,
    success BOOLEAN DEFAULT TRUE
);
