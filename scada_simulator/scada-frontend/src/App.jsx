import React, { useState, useEffect } from 'react';
import { Activity, ShieldAlert, Radio, Power } from 'lucide-react';

export default function App() {
  const [telemetry, setTelemetry] = useState([]);
  const [systemLogs, setSystemLogs] = useState([]);
  const [backendAlive, setBackendAlive] = useState(false);

  const fetchSnapshot = async () => {
    try {
      const res = await fetch('http://127.0.0.1:8000/api/telemetry/snapshot');
      if (!res.ok) throw new Error("HTTP state error");
      const data = await res.json();
      setTelemetry(data);
      setBackendAlive(true);
    } catch (err) {
      setBackendAlive(false);
    }
  };

  useEffect(() => {
    fetchSnapshot();
    const interval = setInterval(fetchSnapshot, 1000);
    return () => clearInterval(interval);
  }, []);

  const dispatchSwitchCommand = async (switchId, action) => {
    const timestamp = new Date().toLocaleTimeString();
    try {
      const response = await fetch('http://127.0.0.1:8000/api/switch/control', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          switch_id: switchId,
          command: action,
          operator: "Kushagra_HMI"
        })
      });
      const result = await response.json();
      
      if (result.status === "success") {
        setSystemLogs(prev => [`[${timestamp}] SUCCESS: ${switchId} forced ${action.toUpperCase()}`, ...prev]);
      } else {
        setSystemLogs(prev => [`[${timestamp}] FAILED: ${result.message}`, ...prev]);
      }
    } catch (err) {
      setSystemLogs(prev => [`[${timestamp}] NETWORK ERROR: Control path broken`, ...prev]);
    }
  };

  return (
    <div style={{ minHeight: '100vh', backgroundColor: '#020617', color: '#f8fafc', padding: '24px', fontFamily: 'sans-serif', textAlign: 'left' }}>
      
      {/* Header Panel */}
      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid #1e293b', paddingBottom: '16px', marginBottom: '24px' }}>
        <div>
          <h1 style={{ fontSize: '24px', fontWeight: 'bold', color: '#22d3ee', margin: 0 }}>⚡ SHARIKA MINI-SCADA MANAGEMENT CONTROL</h1>
          <p style={{ fontSize: '12px', color: '#94a3b8', margin: '4px 0 0 0' }}>Real-Time Operational Distribution HMI Console</p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', backgroundColor: '#0f172a', padding: '8px 16px', borderRadius: '4px', border: '1px solid #1e293b' }}>
          <Radio style={{ width: '16px', height: '16px', color: backendAlive ? '#34d399' : '#f87171' }} />
          <span style={{ fontSize: '12px', fontWeight: 'bold', color: backendAlive ? '#34d399' : '#f87171' }}>
            {backendAlive ? 'BACKEND LINK ENGINE ACTIVE' : 'NO BACKEND DATA DETECTED'}
          </span>
        </div>
      </header>

      {/* Grid Layout Layout Workspace */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '24px' }}>
        
        {/* Single Line Diagram Component */}
        <div style={{ gridColumn: 'span 2', backgroundColor: '#0f172a', border: '1px solid #1e293b', borderRadius: '12px', padding: '20px' }}>
          <h2 style={{ fontSize: '14px', fontWeight: '600', color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.05em', margin: '0 0 16px 0', display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Activity style={{ width: '16px', height: '16px', color: '#22d3ee' }} /> System Infrastructure Topology
          </h2>
          
          <div style={{ backgroundColor: '#020617', border: '1px solid #1e293b', borderRadius: '8px', padding: '24px', minHeight: '300px' }}>
            
            {/* Substation Widget */}
            <div style={{ display: 'flex', justifyContent: 'center', marginBottom: '32px' }}>
              <div style={{ backgroundColor: 'rgba(6, 78, 116, 0.2)', border: '1px solid #06b6d4', borderRadius: '4px', padding: '12px 24px', textAlign: 'center' }}>
                <p style={{ fontSize: '12px', color: '#22d3ee', fontWeight: 'bold', margin: 0 }}>SE-01 SUBSTATION FEEDER MAIN</p>
                <p style={{ fontSize: '16px', fontFamily: 'monospace', color: '#f1f5f9', margin: '4px 0 0 0' }}>
                  {telemetry.find(m => m.meter === 'MED-01')?.v?.toFixed(1) || '0.0'} V
                </p>
              </div>
            </div>

            {/* Downstream Circuit Feeders Row */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '16px' }}>
              {['AL-01', 'AL-02', 'AL-03'].map((feederId, idx) => {
                const feederMeters = telemetry.filter(m => m.feeder === feederId);
                const isEnergized = feederMeters.some(m => m.alive) || telemetry.find(m => m.meter === 'MED-01')?.v > 0;
                
                return (
                  <div key={feederId} style={{ padding: '16px', borderRadius: '6px', backgroundColor: 'rgba(15, 23, 42, 0.5)', border: `1px solid ${isEnergized ? '#065f46' : '#991b1b'}` }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                      <span style={{ fontSize: '14px', fontWeight: 'bold', color: isEnergized ? '#34d399' : '#f87171' }}>{feederId}</span>
                      <div style={{ display: 'flex', gap: '6px' }}>
                        <button 
                          onClick={() => dispatchSwitchCommand(`CH-0${idx+1}`, 'open')}
                          style={{ padding: '4px 8px', borderRadius: '4px', backgroundColor: '#7f1d1d', color: '#fca5a5', border: '1px solid #991b1b', cursor: 'pointer', fontSize: '10px', fontWeight: 'bold' }}>
                          TRIP
                        </button>
                        <button 
                          onClick={() => dispatchSwitchCommand(`CH-0${idx+1}`, 'close')}
                          style={{ padding: '4px 8px', borderRadius: '4px', backgroundColor: '#064e3b', color: '#a7f3d0', border: '1px solid #065f46', cursor: 'pointer', fontSize: '10px', fontWeight: 'bold' }}>
                          CLOSE
                        </button>
                      </div>
                    </div>

                    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                      {feederMeters.length === 0 ? (
                        <span style={{ fontSize: '10px', color: '#475569', italic: 'true' }}>Awaiting initial scan...</span>
                      ) : (
                        feederMeters.map(m => (
                          <div key={m.meter} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11px', fontFamily: 'monospace', borderBottom: '1px solid #1e293b', paddingBottom: '4px' }}>
                            <span style={{ color: '#94a3b8' }}>{m.meter}:</span>
                            <span style={{ color: m.fault ? '#f87171' : '#e2e8f0', fontWeight: m.fault ? 'bold' : 'normal' }}>
                              {m.v?.toFixed(0)}V | {m.a?.toFixed(1)}A
                            </span>
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                );
              })}
            </div>

          </div>
        </div>

        {/* Real-time Alarm Logs Display */}
        <div style={{ backgroundColor: '#0f172a', border: '1px solid #1e293b', borderRadius: '12px', padding: '20px', display: 'flex', flexDirection: 'column', height: '400px' }}>
          <h2 style={{ fontSize: '14px', fontWeight: '600', color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.05em', margin: '0 0 12px 0', display: 'flex', alignItems: 'center', gap: '8px' }}>
            <ShieldAlert style={{ width: '16px', height: '16px', color: '#f59e0b' }} /> Operational Security Audit Logs
          </h2>
          <div style={{ flex: 1, backgroundColor: '#020617', borderRadius: '8px', padding: '12px', fontFamily: 'monospace', fontSize: '11px', color: '#e2e8f0', overflowY: 'auto', border: '1px solid #1e293b' }}>
            {systemLogs.length === 0 ? (
              <p style={{ color: '#475569', textAlign: 'center', paddingTop: '140px', margin: 0 }}>HMI tracking lines quiet. Ready for operation...</p>
            ) : (
              systemLogs.map((log, i) => (
                <div key={i} style={{ borderLeft: '2px solid #06b6d4', paddingLeft: '8px', marginBottom: '6px', color: '#cbd5e1' }}>
                  {log}
                </div>
              ))
            )}
          </div>
        </div>

      </div>
    </div>
  );
}
