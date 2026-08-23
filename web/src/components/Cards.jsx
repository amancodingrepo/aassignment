import { useState } from 'react'

const TIER_CLASS = {
  1: 'tier tier-1',
  2: 'tier tier-2',
  3: 'tier tier-3',
  4: 'tier tier-4',
  5: 'tier tier-5',
  6: 'tier tier-6'
}

export function TierBadge({ tier, label }) {
  if (!tier) return null
  return (
    <span className={TIER_CLASS[tier] || 'tier'} title={label || `tier ${tier}`}>
      T{tier}
    </span>
  )
}

export function SourceCard({ source }) {
  return (
    <div className="source-card">
      <TierBadge tier={source.tier} label={source.tier_label} />
      <div className="source-body">
        <div className="source-doc">
          {source.doc} {source.clause || ''}
        </div>
        <div className="source-meta">
          {source.section_title}
          {source.scope && source.scope !== 'global' ? ` · ${source.scope}` : ''}
          {source.status && source.status !== 'current' ? ` · ${source.status}` : ''}
        </div>
      </div>
    </div>
  )
}

export function ConflictBanner({ conflicts }) {
  if (!conflicts || conflicts.length === 0) return null
  return (
    <div className="notice conflict">
      <div className="notice-title">A higher-authority source won</div>
      {conflicts.map((conflict, index) => (
        <div key={index} className="notice-row">
          <div>
            <strong>{conflict.winner}</strong>
            <span className="loser"> {conflict.loser}</span>
          </div>
          <div className="notice-why">{conflict.why}</div>
        </div>
      ))}
    </div>
  )
}

export function StaleGuidanceBanner({ items }) {
  if (!items || items.length === 0) return null
  return (
    <div className="notice stale">
      <div className="notice-title">Past guidance — context only</div>
      {items.map((item, index) => (
        <div key={index} className="notice-row">
          <div>
            <strong>{item.ticket_id}</strong> said: “{item.said}”
          </div>
          <div className="notice-why">{item.why}</div>
        </div>
      ))}
    </div>
  )
}

export function EscalationBanner({ escalation }) {
  if (!escalation) return null
  return (
    <div className="notice escalate">
      <div className="notice-title">Needs a human</div>
      <ul>
        {escalation.reasons.map((reason, index) => (
          <li key={index}>{reason}</li>
        ))}
      </ul>
    </div>
  )
}

export function ToolTrace({ calls }) {
  const [open, setOpen] = useState(true)
  if (!calls || calls.length === 0) return null

  return (
    <div className="trace">
      <button className="trace-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
        {calls.length} step{calls.length === 1 ? '' : 's'}
      </button>
      {open &&
        calls.map((call) => <ToolCallRow key={call.id} call={call} />)}
    </div>
  )
}

function ToolCallRow({ call }) {
  const [expanded, setExpanded] = useState(false)
  const args = Object.entries(call.args || {})
    .filter(([, value]) => value !== undefined && value !== null && value !== '')
    .map(([key, value]) => `${key}=${JSON.stringify(value)}`)
    .join('  ')

  return (
    <div className={`trace-row ${call.denied ? 'denied' : ''} ${call.summary ? 'done' : 'live'}`}>
      <button className="trace-head" onClick={() => setExpanded(!expanded)}>
        <span className="trace-name">{call.tool}</span>
        <span className="trace-args">{args}</span>
        <span className="trace-summary">{call.summary || 'running'}</span>
      </button>
      {expanded && <pre className="trace-detail">{JSON.stringify(call.result, null, 2)}</pre>}
    </div>
  )
}

export function ConfirmCard({ proposal, onConfirm, onCancel, state }) {
  const preview = proposal.preview || {}
  const rows = Object.entries(preview).filter(
    ([key, value]) => key !== 'kind' && value !== null && value !== undefined && value !== ''
  )

  return (
    <div className={`work-order ${state || ''}`}>
      <div className="work-order-head">
        <span className="kicker">Work order · not written yet</span>
        <strong>{prettyKind(proposal.kind)}</strong>
      </div>
      <table className="spec">
        <tbody>
          {rows.map(([key, value]) => (
            <tr key={key}>
              <th>{key.replace(/_/g, ' ')}</th>
              <td>{Array.isArray(value) ? value.join('; ') : String(value)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {(proposal.warnings || []).length > 0 && (
        <ul className="work-warnings">
          {proposal.warnings.map((warning, index) => (
            <li key={index}>{warning}</li>
          ))}
        </ul>
      )}
      {state === 'executed' && (
        <div className="work-done ok">Filed as {proposal.action_id}</div>
      )}
      {state === 'cancelled' && <div className="work-done">Cancelled. Nothing was written.</div>}
      {!state && (
        <div className="work-actions">
          <button className="primary" onClick={() => onConfirm(proposal.token)}>
            Confirm and write
          </button>
          <button className="ghost" onClick={() => onCancel(proposal.token)}>
            Cancel
          </button>
        </div>
      )}
    </div>
  )
}

function prettyKind(kind) {
  if (kind === 'escalation') return 'Escalation'
  if (kind === 'ticket_update') return 'Ticket update'
  if (kind === 'follow_up_task') return 'Follow-up task'
  return kind || 'Action'
}
