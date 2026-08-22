import { useEffect, useState } from 'react'
import { getActions, getAudit, getSignals } from '../api.js'
import { SourceCard, TierBadge } from './Cards.jsx'

const SEVERITY_CLASS = { P1: 'sev sev-p1', P2: 'sev sev-p2', P3: 'sev sev-p3' }

export function SignalsPanel({ onSeedChat }) {
  const [signals, setSignals] = useState([])
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  const load = () => {
    setLoading(true)
    getSignals()
      .then((data) => {
        setSignals(data.signals)
        setError(null)
      })
      .catch((problem) => setError(problem.message))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  if (loading) return <div className="panel-empty">Computing signals…</div>
  if (error) return <div className="panel-error">{error}</div>

  return (
    <div className="panel">
      <div className="panel-head">
        <h2>Proactive queue</h2>
        <button onClick={load}>Recompute</button>
      </div>
      <p className="panel-note">
        Deterministic rules over the snapshot. Ranked by severity × account impact ×
        breach magnitude — the terms are printed on every card.
      </p>

      {signals.map((signal) => (
        <div key={signal.id} className="signal-card">
          <div className="signal-head">
            <span className={SEVERITY_CLASS[signal.severity] || 'sev'}>{signal.severity}</span>
            <span className="signal-title">{signal.title}</span>
            <span className="signal-score" title={JSON.stringify(signal.rank_terms)}>
              {signal.rank_score}
            </span>
          </div>
          <div className="signal-detail">{signal.detail}</div>

          <div className="signal-rank">
            rank = severity {signal.rank_terms.severity} × accounts{' '}
            {signal.rank_terms.account_impact} × magnitude {signal.rank_terms.breach_magnitude}
          </div>

          <table className="evidence-table">
            <tbody>
              {signal.evidence.map((row, index) => (
                <tr key={index}>
                  {Object.entries(row).map(([key, value]) => (
                    <td key={key}>
                      <span className="evidence-key">{key.replace(/_/g, ' ')}</span>
                      {String(value)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>

          {signal.sources.length > 0 && (
            <div className="signal-sources">
              {signal.sources.map((source, index) => (
                <SourceCard key={index} source={source} />
              ))}
            </div>
          )}

          <div className="signal-foot">
            <span className="recommended">{signal.recommended_action}</span>
            <button className="primary" onClick={() => onSeedChat(signal.seed_query)}>
              Ask the agent about this
            </button>
          </div>
        </div>
      ))}
    </div>
  )
}

export function ActionsPanel({ refreshKey }) {
  const [actions, setActions] = useState([])

  useEffect(() => {
    getActions()
      .then((data) => setActions(data.actions))
      .catch(() => setActions([]))
  }, [refreshKey])

  if (actions.length === 0) {
    return <div className="panel-empty">No actions written yet.</div>
  }

  return (
    <div className="panel">
      <h2>Actions written</h2>
      <table className="data-table">
        <thead>
          <tr>
            <th>id</th>
            <th>kind</th>
            <th>ticket</th>
            <th>account</th>
            <th>by</th>
            <th>at</th>
          </tr>
        </thead>
        <tbody>
          {actions.map((action) => (
            <tr key={action.action_id}>
              <td>{action.action_id}</td>
              <td>{action.kind}</td>
              <td>{action.ticket_id}</td>
              <td>{action.account_id}</td>
              <td>{action.created_by}</td>
              <td>{action.created_at}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// "Access control is enforced in the data layer" is a claim. This table is the
// claim made checkable, including the denials.
export function AuditPanel({ refreshKey }) {
  const [entries, setEntries] = useState([])
  const [error, setError] = useState(null)

  useEffect(() => {
    getAudit()
      .then((data) => setEntries(data.entries))
      .catch((problem) => setError(problem.message))
  }, [refreshKey])

  if (error) return <div className="panel-error">{error}</div>

  return (
    <div className="panel">
      <h2>Gated call log</h2>
      <p className="panel-note">
        Every tool call, allowed or denied, with the arguments as the model sent them.
      </p>
      <table className="data-table">
        <thead>
          <tr>
            <th>role</th>
            <th>account</th>
            <th>tool</th>
            <th>args</th>
            <th>allowed</th>
            <th>note</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => (
            <tr key={entry.id} className={entry.allowed ? '' : 'denied-row'}>
              <td>{entry.session_role}</td>
              <td>{entry.session_account || '—'}</td>
              <td>{entry.tool}</td>
              <td className="args-cell">{entry.args_json}</td>
              <td>{entry.allowed ? 'yes' : 'no'}</td>
              <td>{entry.denial_reason || ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function PersonaPicker({ personas, current, onPick }) {
  return (
    <div className="persona-picker">
      <label htmlFor="persona">Signed in as</label>
      <select
        id="persona"
        value={current || ''}
        onChange={(event) => onPick(event.target.value)}
      >
        <optgroup label="Customer">
          {personas
            .filter((persona) => persona.role === 'customer')
            .map((persona) => (
              <option key={persona.id} value={persona.id}>
                {persona.account_name} · {persona.plan}
              </option>
            ))}
        </optgroup>
        <optgroup label="ParcelPilot internal">
          {personas
            .filter((persona) => persona.role === 'internal')
            .map((persona) => (
              <option key={persona.id} value={persona.id}>
                {persona.user_name}
              </option>
            ))}
        </optgroup>
      </select>
    </div>
  )
}

export function TierLegend() {
  const tiers = [
    [1, 'Signed agreement'],
    [2, 'SOP v4'],
    [3, 'Support Policy v3'],
    [4, 'Product guide'],
    [5, 'Deprecated'],
    [6, 'Past tickets']
  ]
  return (
    <div className="tier-legend">
      {tiers.map(([tier, label]) => (
        <span key={tier}>
          <TierBadge tier={tier} /> {label}
        </span>
      ))}
    </div>
  )
}
