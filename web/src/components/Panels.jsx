import { useEffect, useState } from 'react'
import { getActions, getAudit, getSignals } from '../api.js'
import { SourceCard, TierBadge } from './Cards.jsx'

const SEVERITY_CLASS = { P1: 'sev sev-p1', P2: 'sev sev-p2', P3: 'sev sev-p3' }

/* ─── Persona picker ──────────────────────────────────────────────────────── */

const ROLE_ICONS = {
  customer: '🏢',
  internal: '🔐'
}

const PLAN_COLOR = {
  Enterprise: '#6b3fa0',
  Growth: '#0e6b63',
  Standard: '#3d6f86'
}

export function PersonaPicker({ personas, current, onPick }) {
  const [open, setOpen] = useState(false)
  const currentPersona = personas.find((p) => p.id === current)

  const customers = personas.filter((p) => p.role === 'customer')
  const internals = personas.filter((p) => p.role === 'internal')

  function pick(id) {
    onPick(id)
    setOpen(false)
  }

  return (
    <>
      <button className="persona-trigger" onClick={() => setOpen(true)} aria-label="Switch persona">
        <span className="persona-trigger-icon">
          {currentPersona?.role === 'internal' ? '🔐' : '🏢'}
        </span>
        <span className="persona-trigger-name">
          {currentPersona?.account_name || currentPersona?.user_name || 'Select persona'}
        </span>
        <span className="persona-trigger-caret">▾</span>
      </button>

      {open && (
        <>
          <div className="persona-backdrop" onClick={() => setOpen(false)} />
          <div className="persona-modal" role="dialog" aria-label="Choose a workspace">
            <div className="persona-modal-head">
              <h2>Choose workspace</h2>
              <p>Auth is mocked — picking a name sets a signed cookie. No password required.</p>
              <button className="persona-close" onClick={() => setOpen(false)} aria-label="Close">✕</button>
            </div>

            <div className="persona-section-label">
              <span className="persona-section-icon">🏢</span> Customer accounts
            </div>
            <div className="persona-grid">
              {customers.map((p) => (
                <button
                  key={p.id}
                  className={`persona-card customer ${p.id === current ? 'selected' : ''}`}
                  onClick={() => pick(p.id)}
                >
                  <div className="persona-card-top">
                    <span className="persona-avatar customer-avatar">
                      {(p.account_name || 'C')[0]}
                    </span>
                    {p.id === current && <span className="persona-active-dot" title="Active" />}
                  </div>
                  <div className="persona-card-name">{p.account_name}</div>
                  <div className="persona-card-id">{p.account_id}</div>
                  {p.plan && (
                    <span
                      className="persona-plan-badge"
                      style={{ '--plan-color': PLAN_COLOR[p.plan] || '#5d6673' }}
                    >
                      {p.plan}
                    </span>
                  )}
                </button>
              ))}
            </div>

            <div className="persona-section-label" style={{ marginTop: '20px' }}>
              <span className="persona-section-icon">🔐</span> ParcelPilot internal
            </div>
            <div className="persona-grid">
              {internals.map((p) => (
                <button
                  key={p.id}
                  className={`persona-card internal ${p.id === current ? 'selected' : ''}`}
                  onClick={() => pick(p.id)}
                >
                  <div className="persona-card-top">
                    <span className="persona-avatar internal-avatar">
                      {(p.user_name || 'I')[0]}
                    </span>
                    {p.id === current && <span className="persona-active-dot" title="Active" />}
                  </div>
                  <div className="persona-card-name">{p.user_name}</div>
                  <div className="persona-card-id">
                    {p.internal_permissions?.includes('read_all') ? 'Full access · all accounts' : 'Restricted'}
                  </div>
                  <span className="persona-internal-badge">Internal</span>
                </button>
              ))}
            </div>

            <div className="persona-modal-note">
              Access control is enforced in the data layer — not by model instructions.
            </div>
          </div>
        </>
      )}
    </>
  )
}

/* ─── User badge in the rail ─────────────────────────────────────────────── */

export function UserBadge({ session }) {
  if (!session) return null
  const isInternal = session.role === 'internal'
  const initials = (session.user_name || '?')
    .split(/[\s(]/)[0]
    .slice(0, 2)
    .toUpperCase()

  return (
    <div className={`user-badge ${isInternal ? 'user-badge-internal' : 'user-badge-customer'}`}>
      <div className="user-badge-avatar">
        {initials}
      </div>
      <div className="user-badge-info">
        <div className="user-badge-name">{session.user_name}</div>
        {session.account_id && (
          <div className="user-badge-account">{session.account_id}</div>
        )}
      </div>
      <span className={`role-pill-lg ${isInternal ? 'internal' : 'customer'}`}>
        {isInternal ? 'Internal' : 'Customer'}
      </span>
    </div>
  )
}

/* ─── Signals panel ───────────────────────────────────────────────────────── */

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

  if (loading) {
    return (
      <div className="panel">
        <div className="skeleton" />
        <div className="skeleton" />
      </div>
    )
  }
  if (error) return <div className="panel-error">{error}</div>

  return (
    <div className="panel">
      <div className="panel-head">
        <div>
          <h2>What deserves attention</h2>
          <p className="panel-note">
            Ranked by severity × accounts × breach. The arithmetic is on the card.
          </p>
        </div>
        <button className="ghost" onClick={load}>
          Recompute
        </button>
      </div>

      <div className="signal-list">
        {signals.map((signal) => (
          <article key={signal.id} className="signal-card">
            <header className="signal-head">
              <span className={SEVERITY_CLASS[signal.severity] || 'sev'}>{signal.severity}</span>
              <h3>{signal.title}</h3>
              <span className="signal-score" title={JSON.stringify(signal.rank_terms)}>
                {signal.rank_score}
              </span>
            </header>
            <p className="signal-detail">{signal.detail}</p>
            <p className="signal-rank">
              {signal.rank_terms.severity} sev × {signal.rank_terms.account_impact} accounts ×{' '}
              {signal.rank_terms.breach_magnitude} magnitude
            </p>
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
            <footer className="signal-foot">
              <span className="recommended">{signal.recommended_action}</span>
              <button className="primary" onClick={() => onSeedChat(signal.seed_query)}>
                Ask about this
              </button>
            </footer>
          </article>
        ))}
      </div>
    </div>
  )
}

/* ─── Actions panel ───────────────────────────────────────────────────────── */

export function ActionsPanel({ refreshKey }) {
  const [actions, setActions] = useState([])

  useEffect(() => {
    getActions()
      .then((data) => setActions(data.actions))
      .catch(() => setActions([]))
  }, [refreshKey])

  if (actions.length === 0) {
    return (
      <div className="panel empty">
        <h2>No work orders yet</h2>
        <p>Nothing is written until you confirm a preview in the inbox.</p>
      </div>
    )
  }

  return (
    <div className="panel">
      <div className="panel-head">
        <h2>Filed work orders</h2>
      </div>
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>Kind</th>
              <th>Ticket</th>
              <th>Account</th>
              <th>By</th>
              <th>At</th>
            </tr>
          </thead>
          <tbody>
            {actions.map((action) => (
              <tr key={action.action_id}>
                <td className="mono">{action.action_id}</td>
                <td>{action.kind}</td>
                <td className="mono">{action.ticket_id}</td>
                <td className="mono">{action.account_id}</td>
                <td>{action.created_by}</td>
                <td className="mono">{action.created_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/* ─── Audit panel ─────────────────────────────────────────────────────────── */

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
      <div className="panel-head">
        <div>
          <h2>Gate log</h2>
          <p className="panel-note">
            Every tool call as the model sent it — including denials and overwritten account IDs.
          </p>
        </div>
      </div>
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Role</th>
              <th>Account</th>
              <th>Tool</th>
              <th>Args</th>
              <th>Allowed</th>
              <th>Note</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry) => (
              <tr key={entry.id} className={entry.allowed ? '' : 'denied-row'}>
                <td>{entry.session_role}</td>
                <td className="mono">{entry.session_account || '—'}</td>
                <td className="mono">{entry.tool}</td>
                <td className="args-cell">{entry.args_json}</td>
                <td>{entry.allowed ? 'yes' : 'no'}</td>
                <td>{entry.denial_reason || ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/* ─── Tier legend ─────────────────────────────────────────────────────────── */

export function TierLegend() {
  const tiers = [
    [1, 'Signed agreement'],
    [2, 'SOP'],
    [3, 'Support policy'],
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
