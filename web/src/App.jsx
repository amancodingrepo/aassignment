import { useEffect, useRef, useState } from 'react'
import {
  cancelAction,
  confirmAction,
  getContext,
  getPersonas,
  getSession,
  resetDemo,
  setPersona,
  streamChat
} from './api.js'
import {
  ConfirmCard,
  ConflictBanner,
  EscalationBanner,
  SourceCard,
  StaleGuidanceBanner,
  ToolTrace
} from './components/Cards.jsx'
import {
  ActionsPanel,
  AuditPanel,
  PersonaPicker,
  SignalsPanel,
  TierLegend,
  UserBadge
} from './components/Panels.jsx'

/* ─── Per-persona suggested queries ─────────────────────────────────────── */

const SUGGESTIONS = {
  'customer-ACCT-001': [
    'Can Northstar cancel ORD-1001 without a cancellation fee? Explain why.',
    'A pickup is three hours late because of carrier fault. Should I get a service credit?',
    'Can we cancel ORD-1002? Status says picked up.',
    'Last time you told us we would owe a 250 fee after 30 minutes — is that right?'
  ],
  'customer-ACCT-002': [
    'Can we cancel ORD-2001 without a fee?',
    'A pickup is three hours late because of carrier fault. Should I get a service credit?',
    'ORD-2002 still has not been picked up. Do we get a service credit?',
    'Why does our 4,200-row upload fail? Support said Growth caps at 3,000.'
  ],
  'customer-ACCT-003': [
    'Can we cancel ORD-3001 without a fee?',
    'A pickup is three hours late because of carrier fault. Should I get a service credit?',
    'How do I change our billing contact?'
  ],
  'customer-ACCT-004': [
    'Can we cancel ORD-4001?',
    'What is our P1 response target?',
    'We think a production API key was posted publicly. What happens now?'
  ],
  internal: [
    'Escalate TKT-505',
    'Which open tickets are breaching SLA right now?',
    'ORD-2002 has not been picked up. What do we owe LumenWorks, and should this be escalated?',
    'TKT-501 has been open 30 minutes. Has Northstar’s 15-minute P1 already breached?',
    'What did we used to promise Enterprise customers for P1?',
    'TKT-450 told Northstar they would owe a 250 cancellation fee. Was that guidance correct?'
  ]
}

const TOOL_LABEL = {
  search_documents: 'Reading policy',
  lookup_orders: 'Looking up shipment',
  lookup_tickets: 'Looking up ticket',
  lookup_account: 'Opening account',
  compute: 'Running the calculator',
  propose_action: 'Preparing a work order',
  list_signals: 'Scanning the queue'
}

/* ─── Status helpers ─────────────────────────────────────────────────────── */

const STATUS_CLASS = {
  'In Transit': 'status-transit',
  'Out for Delivery': 'status-transit',
  Delivered: 'status-delivered',
  Cancelled: 'status-cancelled',
  Pending: 'status-pending',
  'Awaiting Pickup': 'status-pending'
}

const SEV_CLASS = { P1: 'sev sev-p1', P2: 'sev sev-p2', P3: 'sev sev-p3' }

/* ─── App root ───────────────────────────────────────────────────────────── */

export default function App() {
  const [personas, setPersonas] = useState([])
  const [session, setSession] = useState(null)
  const [tab, setTab] = useState('chat')
  const [turns, setTurns] = useState([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const [snapshotNow, setSnapshotNow] = useState(null)
  const [chatReady, setChatReady] = useState(true)
  const [navOpen, setNavOpen] = useState(false)
  const [context, setContext] = useState(null)
  const bottom = useRef(null)
  const inputRef = useRef(null)

  useEffect(() => {
    getPersonas().then((data) => {
      setPersonas(data.personas)
      setSnapshotNow(data.snapshot_now)
      setChatReady(data.chat_ready !== false)
      getSession()
        .then((existing) => {
          setSession(existing.session)
          loadContext()
        })
        .catch(() => pick(data.personas[0].id))
    })
  }, [])

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth' })
  }, [turns, busy])

  function loadContext() {
    getContext()
      .then(setContext)
      .catch(() => setContext(null))
  }

  async function pick(personaId) {
    const result = await setPersona(personaId)
    setSession(result.session)
    setTurns([])
    setError(null)
    setTab('chat')
    setNavOpen(false)
    setContext(null)
    // Small delay so the cookie is set before context fetch
    setTimeout(loadContext, 50)
  }

  const personaKey = session
    ? session.role === 'internal'
      ? 'internal'
      : `customer-${session.account_id}`
    : null

  const suggestions = SUGGESTIONS[personaKey] || []

  async function send(text) {
    const question = (text ?? input).trim()
    if (!question || busy) return
    setInput('')
    setError(null)
    setBusy(true)
    setTab('chat')
    setNavOpen(false)

    const history = turns
      .flatMap((turn) => [
        { role: 'user', content: turn.question },
        { role: 'assistant', content: turn.answer?.prose || '' }
      ])
      .filter((message) => message.content)

    const turn = { question, calls: [], answer: null, streaming: '', proposals: [] }
    setTurns((previous) => [...previous, turn])
    const index = turns.length

    const update = (mutate) =>
      setTurns((previous) => {
        const next = [...previous]
        next[index] = mutate({ ...next[index] })
        return next
      })

    try {
      await streamChat([...history, { role: 'user', content: question }], (event) => {
        if (event.type === 'tool_start') {
          update((current) => ({
            ...current,
            calls: [...current.calls, { id: event.id, tool: event.tool, args: event.args }]
          }))
        } else if (event.type === 'tool_result') {
          update((current) => ({
            ...current,
            calls: current.calls.map((call) =>
              call.id === event.id
                ? {
                    ...call,
                    summary: event.summary,
                    result: event.result,
                    denied: Boolean(event.result?.denied)
                  }
                : call
            )
          }))
        } else if (event.type === 'confirm_required') {
          update((current) => ({ ...current, proposals: [...current.proposals, event] }))
        } else if (event.type === 'token') {
          update((current) => ({ ...current, streaming: event.text }))
        } else if (event.type === 'answer') {
          update((current) => ({ ...current, answer: event.answer }))
        } else if (event.type === 'error') {
          setError(event.error)
        }
      })
    } catch (problem) {
      setError(problem.message)
    } finally {
      setBusy(false)
      setRefreshKey((key) => key + 1)
      inputRef.current?.focus()
    }
  }

  async function onConfirm(token) {
    try {
      const result = await confirmAction(token)
      markProposal(token, 'executed', result.action_id)
      setRefreshKey((key) => key + 1)
    } catch (problem) {
      setError(problem.message)
    }
  }

  async function onCancel(token) {
    await cancelAction(token)
    markProposal(token, 'cancelled')
  }

  function markProposal(token, state, actionId) {
    setTurns((previous) =>
      previous.map((turn) => ({
        ...turn,
        proposals: turn.proposals.map((proposal) =>
          proposal.token === token ? { ...proposal, state, action_id: actionId } : proposal
        )
      }))
    )
  }

  if (!session) {
    return (
      <div className="boot">
        <div className="mark" aria-hidden="true">PP</div>
        <p>Opening the desk…</p>
      </div>
    )
  }

  const isInternal = session.role === 'internal'

  /* ── Navigation items differ by role ─── */
  const nav = [
    { id: 'chat', label: isInternal ? 'Investigation' : 'Support Chat', hint: isInternal ? 'Ask across accounts & policy' : 'Ask about orders & credits' },
    ...(isInternal
      ? [{ id: 'signals', label: 'Issue Queue', hint: 'Proactive detections' }]
      : []),
    { id: 'actions', label: 'Work Orders', hint: 'Confirmed writes' },
    ...(isInternal
      ? [{ id: 'audit', label: 'Gate Log', hint: 'Every scoped call' }]
      : [])
  ]

  const titles = {
    chat: isInternal ? 'Operations Console' : 'Support Desk',
    signals: 'Issue Queue',
    actions: 'Work Orders',
    audit: 'Access Gate'
  }

  return (
    <div className={`shell ${navOpen ? 'nav-open' : ''} ${isInternal ? 'mode-internal' : 'mode-customer'}`}>

      {/* ── Left rail ── */}
      <aside className="rail" aria-label="Workspace">
        <div className="rail-brand">
          <div className="mark" aria-hidden="true">PP</div>
          <div>
            <div className="wordmark">ParcelPilot</div>
            <div className="wordmark-sub">{isInternal ? 'Ops Console' : 'Support Desk'}</div>
          </div>
        </div>

        {/* Role context block */}
        <div className={`rail-context-block ${isInternal ? 'context-internal' : 'context-customer'}`}>
          <div className="rail-context-label">
            {isInternal ? '🔐 Internal Access' : '🏢 Customer Portal'}
          </div>
          <div className="rail-context-desc">
            {isInternal
              ? 'Full read across all accounts. Signals, audit, actions.'
              : `Scoped to ${session.account_id} only. No cross-account access.`}
          </div>
        </div>

        <nav className="rail-nav">
          {nav.map((item) => (
            <button
              key={item.id}
              className={tab === item.id ? 'nav-item active' : 'nav-item'}
              aria-current={tab === item.id ? 'page' : undefined}
              onClick={() => {
                setTab(item.id)
                setNavOpen(false)
              }}
            >
              <span className="nav-label">{item.label}</span>
              <span className="nav-hint">{item.hint}</span>
            </button>
          ))}
        </nav>

        <div className="rail-foot">
          <div className="stamp">
            <span className="stamp-k">Snapshot</span>
            <span className="stamp-v">{formatSnapshot(snapshotNow)}</span>
          </div>
          <UserBadge session={session} />
        </div>
      </aside>

      {/* ── Main stage ── */}
      <div className="stage">
        <header className="topbar">
          <button className="menu" aria-label="Open navigation" onClick={() => setNavOpen((open) => !open)}>
            Menu
          </button>
          <div className="topbar-copy">
            <h1>{titles[tab]}</h1>
            <p>
              {session.user_name}
              {session.account_id ? ` · ${session.account_id}` : ''}
            </p>
          </div>
          <PersonaPicker personas={personas} current={currentPersonaId(session)} onPick={pick} />
          <div className={`context-badge ${session.role}`}>
            <span className="context-badge-icon">{isInternal ? '🔐' : '🏢'}</span>
            <span className="context-badge-label">{isInternal ? 'Internal' : 'Customer'}</span>
          </div>
        </header>

        {!chatReady && (
          <div className="banner warn" role="status">
            Chat needs a Gemini key in <code>.env</code>. The queue, gate log and calculators still run.
          </div>
        )}
        {error && (
          <div className="banner danger" role="alert">
            {error}
          </div>
        )}

        {/* ── Tab: Chat ── */}
        {tab === 'chat' && (
          <main className="desk">
            <div className="thread" aria-live="polite">
              {turns.length === 0 && (
                isInternal
                  ? <InternalWelcome context={context} suggestions={suggestions} onSend={send} />
                  : <CustomerWelcome context={context} suggestions={suggestions} onSend={send} session={session} />
              )}

              {turns.map((turn, index) => (
                <article key={index} className="turn">
                  <div className="msg you">
                    <span className="who">You</span>
                    <p>{turn.question}</p>
                  </div>

                  <ToolTrace calls={turn.calls} />

                  {turn.answer ? (
                    <div className="msg agent">
                      <span className="who">ParcelPilot</span>
                      <ConflictBanner conflicts={turn.answer.conflicts} />
                      <StaleGuidanceBanner items={turn.answer.stale_guidance} />
                      <EscalationBanner escalation={turn.answer.escalation} />
                      <div className="prose">{turn.answer.prose}</div>
                      {turn.answer.sources.length > 0 && (
                        <div className="docket">
                          <div className="docket-title">Cited</div>
                          {turn.answer.sources.map((source, position) => (
                            <SourceCard key={position} source={source} />
                          ))}
                        </div>
                      )}
                      <div className={`confidence ${turn.answer.confidence}`}>
                        {turn.answer.confidence === 'escalate'
                          ? 'Needs a human'
                          : turn.answer.confidence === 'high'
                            ? 'High confidence'
                            : 'Medium confidence'}
                      </div>
                    </div>
                  ) : (
                    (busy && index === turns.length - 1) && (
                      <div className="msg agent pending">
                        <span className="who">ParcelPilot</span>
                        <p className="status-line">
                          {latestToolLabel(turn.calls) || 'Working the request'}
                        </p>
                      </div>
                    )
                  )}

                  {turn.proposals.map((proposal) => (
                    <ConfirmCard
                      key={proposal.token}
                      proposal={proposal}
                      state={proposal.state}
                      onConfirm={onConfirm}
                      onCancel={onCancel}
                    />
                  ))}
                </article>
              ))}
              <div ref={bottom} />
            </div>

            <form
              className="composer"
              onSubmit={(event) => {
                event.preventDefault()
                send()
              }}
            >
              <label className="sr-only" htmlFor="ask">Ask a support question</label>
              <textarea
                id="ask"
                ref={inputRef}
                rows={1}
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault()
                    send()
                  }
                }}
                placeholder={
                  isInternal
                    ? 'Investigate across accounts, tickets, or policy…'
                    : 'Ask about an order, credit, or SLA…'
                }
                disabled={busy}
              />
              <button className="primary" disabled={busy || !input.trim()}>
                Send
              </button>
            </form>
          </main>
        )}

        {tab === 'signals' && (
          <SignalsPanel onSeedChat={(query) => { setTab('chat'); send(query) }} />
        )}
        {tab === 'actions' && <ActionsPanel refreshKey={refreshKey} />}
        {tab === 'audit' && <AuditPanel refreshKey={refreshKey} />}

        {isInternal && tab !== 'chat' && (
          <div className="stage-foot">
            <button
              className="ghost"
              onClick={() => resetDemo().then(() => setRefreshKey((key) => key + 1))}
            >
              Clear written demo data
            </button>
          </div>
        )}
      </div>

      {navOpen && (
        <button className="scrim" aria-label="Close navigation" onClick={() => setNavOpen(false)} />
      )}
    </div>
  )
}

/* ─── Customer welcome screen ────────────────────────────────────────────── */

function CustomerWelcome({ context, suggestions, onSend, session }) {
  return (
    <section className="welcome welcome-customer">
      <div className="welcome-header">
        <p className="kicker">Customer Support · {session.account_id}</p>
        <h2>How can we help you today?</h2>
        <p className="lede">
          Ask about your shipments, service credits, cancellations, or contract terms.
          Answers are cited from the documents that apply to your account.
        </p>
      </div>

      {/* Account + orders snapshot */}
      {context && (
        <div className="context-cards">
          <div className="ctx-panel ctx-account">
            <div className="ctx-panel-label">Your account</div>
            <div className="ctx-account-name">{context.account?.account_name}</div>
            <div className="ctx-account-meta">
              <span className="ctx-plan-badge">{context.account?.plan}</span>
              {context.account?.account_manager && (
                <span className="ctx-mgr">AM: {context.account.account_manager}</span>
              )}
            </div>
          </div>

          {context.open_tickets?.length > 0 && (
            <div className="ctx-panel ctx-tickets">
              <div className="ctx-panel-label">Open tickets</div>
              {context.open_tickets.map((t) => (
                <div key={t.ticket_id} className="ctx-ticket-row">
                  <span className={SEV_CLASS[t.severity] || 'sev'}>{t.severity}</span>
                  <span className="ctx-ticket-id mono">{t.ticket_id}</span>
                  <span className="ctx-ticket-subj">{t.subject}</span>
                </div>
              ))}
            </div>
          )}

          {context.recent_orders?.length > 0 && (
            <div className="ctx-panel ctx-orders">
              <div className="ctx-panel-label">Recent shipments</div>
              {context.recent_orders.map((o) => (
                <div key={o.order_id} className="ctx-order-row">
                  <span className="ctx-order-id mono">{o.order_id}</span>
                  <span className={`ctx-order-status ${STATUS_CLASS[o.status] || ''}`}>{o.status}</span>
                  <span className="ctx-order-route">{o.origin_city} → {o.destination_city}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <TierLegend />

      <p className="kicker prompt-heading">Ask about your shipments and entitlements</p>
      <div className="prompt-grid">
        {suggestions.slice(0, 2).map((s) => (
          <button key={s} className="prompt-card" onClick={() => onSend(s)}>{s}</button>
        ))}
      </div>
      {suggestions.length > 2 && (
        <div className="chips">
          {suggestions.slice(2).map((s) => (
            <button key={s} className="chip" onClick={() => onSend(s)}>{s}</button>
          ))}
        </div>
      )}
    </section>
  )
}

/* ─── Internal welcome screen ────────────────────────────────────────────── */

function InternalWelcome({ context, suggestions, onSend }) {
  const stats = context?.stats

  return (
    <section className="welcome welcome-internal">
      <div className="welcome-header">
        <p className="kicker kicker-internal">Operations Console · Internal Access</p>
        <h2>What should we look at first?</h2>
        <p className="lede">
          Investigate across all accounts, tickets, and policy. Signals are pre-ranked by severity.
          Actions require confirmation before writing.
        </p>
      </div>

      {/* Live stats dashboard */}
      {stats && (
        <div className="ops-stats">
          <div className={`ops-stat ${stats.p1_tickets > 0 ? 'ops-stat-danger' : ''}`}>
            <span className="ops-stat-val">{stats.p1_tickets}</span>
            <span className="ops-stat-label">Open P1 tickets</span>
          </div>
          <div className="ops-stat">
            <span className="ops-stat-val">{stats.open_tickets}</span>
            <span className="ops-stat-label">Total open tickets</span>
          </div>
          <div className="ops-stat">
            <span className="ops-stat-val">{stats.open_orders}</span>
            <span className="ops-stat-label">Active shipments</span>
          </div>
          <div className={`ops-stat ${stats.p1_signal_count > 0 ? 'ops-stat-warn' : ''}`}>
            <span className="ops-stat-val">{stats.signal_count}</span>
            <span className="ops-stat-label">Signals detected</span>
          </div>
          <div className="ops-stat">
            <span className="ops-stat-val">{stats.total_accounts}</span>
            <span className="ops-stat-label">Accounts</span>
          </div>
        </div>
      )}

      {/* Access capabilities */}
      <div className="ops-capabilities">
        <div className="ops-cap ops-cap-allowed">
          <span className="ops-cap-icon">✓</span>
          <div>
            <strong>Full read access</strong>
            <p>All accounts, orders, tickets, audit log</p>
          </div>
        </div>
        <div className="ops-cap ops-cap-allowed">
          <span className="ops-cap-icon">✓</span>
          <div>
            <strong>Proactive signals</strong>
            <p>SLA breaches, complaint clusters, carrier issues</p>
          </div>
        </div>
        <div className="ops-cap ops-cap-allowed">
          <span className="ops-cap-icon">✓</span>
          <div>
            <strong>Write actions</strong>
            <p>Escalate tickets, create work orders (with confirmation)</p>
          </div>
        </div>
      </div>

      <TierLegend />

      <p className="kicker prompt-heading">Investigate, compare accounts, or file a work order</p>
      <div className="prompt-grid">
        {suggestions.slice(0, 2).map((s) => (
          <button key={s} className="prompt-card prompt-card-internal" onClick={() => onSend(s)}>{s}</button>
        ))}
      </div>
      {suggestions.length > 2 && (
        <div className="chips">
          {suggestions.slice(2).map((s) => (
            <button key={s} className="chip" onClick={() => onSend(s)}>{s}</button>
          ))}
        </div>
      )}
    </section>
  )
}

/* ─── Helpers ────────────────────────────────────────────────────────────── */

function latestToolLabel(calls) {
  if (!calls || calls.length === 0) return ''
  const last = calls[calls.length - 1]
  return TOOL_LABEL[last.tool] || last.tool
}

function currentPersonaId(session) {
  if (session.persona_id) return session.persona_id
  if (session.role === 'customer') return `customer-${session.account_id}`
  return session.user_name.startsWith('Maya') ? 'internal-ops' : 'internal-support'
}

function formatSnapshot(iso) {
  if (!iso) return '—'
  try {
    const stamp = new Date(iso)
    return stamp.toLocaleString('en-IN', {
      timeZone: 'Asia/Kolkata',
      day: '2-digit',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false
    })
  } catch {
    return iso
  }
}
