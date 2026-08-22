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

// The source card is the trust surface: document, clause and tier are visible
// without opening anything, because a citation you have to click for is a
// citation nobody checks.
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

// Non-empty conflicts are the whole point of the retrieval design, so they get
// a banner rather than a footnote.
export function ConflictBanner({ conflicts }) {
  if (!conflicts || conflicts.length === 0) return null
  return (
    <div className="conflict-banner">
      <div className="conflict-title">Sources disagreed — here is which one won</div>
      {conflicts.map((conflict, index) => (
        <div key={index} className="conflict-row">
          <div>
            <strong>{conflict.winner}</strong> over <span className="loser">{conflict.loser}</span>
          </div>
          <div className="conflict-why">{conflict.why}</div>
        </div>
      ))}
    </div>
  )
}

export function StaleGuidanceBanner({ items }) {
  if (!items || items.length === 0) return null
  return (
    <div className="stale-banner">
      <div className="conflict-title">Past guidance retrieved — treat as context only</div>
      {items.map((item, index) => (
        <div key={index} className="conflict-row">
          <div>
            <strong>{item.ticket_id}</strong> said: “{item.said}”
          </div>
          <div className="conflict-why">{item.why}</div>
        </div>
      ))}
    </div>
  )
}

export function EscalationBanner({ escalation }) {
  if (!escalation) return null
  return (
    <div className="escalation-banner">
      <div className="conflict-title">Escalation required</div>
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
    <div className="tool-trace">
      <button className="trace-toggle" onClick={() => setOpen(!open)}>
        {open ? '▾' : '▸'} {calls.length} tool call{calls.length === 1 ? '' : 's'}
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
    .join(', ')

  return (
    <div className={`trace-row ${call.denied ? 'denied' : ''}`}>
      <button className="trace-head" onClick={() => setExpanded(!expanded)}>
        <span className="trace-name">{call.tool}</span>
        <span className="trace-args">({args})</span>
        <span className="trace-summary">{call.summary || '…'}</span>
      </button>
      {expanded && (
        <pre className="trace-detail">{JSON.stringify(call.result, null, 2)}</pre>
      )}
    </div>
  )
}

// Every field that will be written, shown before anything is written. Editing
// is not offered inline: an edited proposal invalidates its token by design, so
// the honest affordance is cancel-and-ask-again.
export function ConfirmCard({ proposal, onConfirm, onCancel, state }) {
  const preview = proposal.preview || {}
  const rows = Object.entries(preview).filter(
    ([key, value]) => key !== 'kind' && value !== null && value !== undefined && value !== ''
  )

  return (
    <div className={`confirm-card ${state || ''}`}>
      <div className="confirm-head">
        Confirm required · <strong>{proposal.kind}</strong>
      </div>
      <div className="confirm-note">
        Nothing has been written yet. This is exactly what will be written.
      </div>

      <table className="confirm-table">
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
        <ul className="confirm-warnings">
          {proposal.warnings.map((warning, index) => (
            <li key={index}>{warning}</li>
          ))}
        </ul>
      )}

      {state === 'executed' && <div className="confirm-done">Written. {proposal.action_id}</div>}
      {state === 'cancelled' && <div className="confirm-done">Cancelled. Nothing was written.</div>}

      {!state && (
        <div className="confirm-buttons">
          <button className="primary" onClick={() => onConfirm(proposal.token)}>
            Confirm
          </button>
          <button onClick={() => onCancel(proposal.token)}>Cancel</button>
        </div>
      )}
    </div>
  )
}
