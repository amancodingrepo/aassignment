import { useEffect, useRef, useState } from 'react'
import {
  cancelAction,
  confirmAction,
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
  TierLegend
} from './components/Panels.jsx'

// Drawn from the eval set so a reviewer needs no instructions.
const SUGGESTIONS = {
  'customer-ACCT-001': [
    'Can we cancel ORD-1001 without a fee?',
    'Can we cancel ORD-1002?',
    'Last time you told us we would owe a 250 fee after 30 minutes — is that right?',
    'TKT-504 — did the pickup actually happen?'
  ],
  'customer-ACCT-002': [
    'Can we cancel ORD-2001 without a fee?',
    'ORD-2002 has not been picked up. What do we owe them?',
    'A pickup is 3 hours late and the carrier is at fault. Do we get a credit?',
    'Why does our 4,200-row upload fail? Support said Growth caps at 3,000.',
    'What are ORD-1001 and Northstar SLA terms?'
  ],
  'customer-ACCT-003': [
    'Can we cancel ORD-3001 without a fee?',
    'A pickup is 3 hours late, carrier fault. Do we get a credit?'
  ],
  'customer-ACCT-004': ['Can we cancel ORD-4001?', 'What is our P1 response target?'],
  internal: [
    'Escalate TKT-505',
    'What are Northstar SLA terms, and how do they differ from the default?',
    'Which open tickets are breaching right now?',
    'What did we used to promise Enterprise customers for P1?',
    'ORD-2002 has not been picked up. What do we owe them, and should this be escalated?'
  ]
}

export default function App() {
  const [personas, setPersonas] = useState([])
  const [session, setSession] = useState(null)
  const [tab, setTab] = useState('chat')
  const [turns, setTurns] = useState([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const bottom = useRef(null)

  useEffect(() => {
    getPersonas().then((data) => {
      setPersonas(data.personas)
      getSession()
        .then((existing) => setSession(existing.session))
        .catch(() => pick(data.personas[0].id))
    })
  }, [])

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth' })
  }, [turns, busy])

  async function pick(personaId) {
    const result = await setPersona(personaId)
    setSession(result.session)
    // A role switch starts a new conversation. Carrying history across a scope
    // change would mean one account's answers sitting in another's transcript.
    setTurns([])
    setError(null)
    setTab('chat')
  }

  const personaKey = session
    ? session.role === 'internal'
      ? 'internal'
      : `customer-${session.account_id}`
    : null

  async function send(text) {
    const question = (text ?? input).trim()
    if (!question || busy) return
    setInput('')
    setError(null)
    setBusy(true)

    const history = turns.flatMap((turn) => [
      { role: 'user', content: turn.question },
      { role: 'assistant', content: turn.answer?.prose || '' }
    ]).filter((message) => message.content)

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

  if (!session) return <div className="booting">Loading ParcelPilot…</div>

  return (
    <div className="app">
      <header>
        <div className="brand">
          <strong>ParcelPilot</strong> support agent
          <span className={`role-pill ${session.role}`}>{session.role}</span>
        </div>
        <PersonaPicker personas={personas} current={currentPersonaId(session)} onPick={pick} />
      </header>

      <nav className="tabs">
        <button className={tab === 'chat' ? 'active' : ''} onClick={() => setTab('chat')}>
          Chat
        </button>
        {session.role === 'internal' && (
          <button className={tab === 'signals' ? 'active' : ''} onClick={() => setTab('signals')}>
            Signals
          </button>
        )}
        <button className={tab === 'actions' ? 'active' : ''} onClick={() => setTab('actions')}>
          Actions
        </button>
        {session.role === 'internal' && (
          <button className={tab === 'audit' ? 'active' : ''} onClick={() => setTab('audit')}>
            Audit
          </button>
        )}
        {session.role === 'internal' && (
          <button
            className="reset"
            onClick={() => resetDemo().then(() => setRefreshKey((key) => key + 1))}
          >
            Reset demo data
          </button>
        )}
      </nav>

      {error && <div className="error-bar">{error}</div>}

      {tab === 'chat' && (
        <main className="chat">
          {turns.length === 0 && (
            <div className="empty-state">
              <p>
                Ask about orders, cancellations, credits, SLAs or known issues. Answers cite
                the clause they came from, and show which source won when sources disagreed.
              </p>
              <TierLegend />
              <div className="suggestions">
                {(SUGGESTIONS[personaKey] || []).map((suggestion) => (
                  <button key={suggestion} onClick={() => send(suggestion)}>
                    {suggestion}
                  </button>
                ))}
              </div>
            </div>
          )}

          {turns.map((turn, index) => (
            <div key={index} className="turn">
              <div className="bubble user">{turn.question}</div>
              <ToolTrace calls={turn.calls} />

              {turn.answer ? (
                <div className="bubble agent">
                  <ConflictBanner conflicts={turn.answer.conflicts} />
                  <StaleGuidanceBanner items={turn.answer.stale_guidance} />
                  <EscalationBanner escalation={turn.answer.escalation} />

                  <div className="prose">{turn.answer.prose}</div>

                  {turn.answer.sources.length > 0 && (
                    <div className="sources">
                      <div className="sources-title">Sources</div>
                      {turn.answer.sources.map((source, position) => (
                        <SourceCard key={position} source={source} />
                      ))}
                    </div>
                  )}

                  <div className={`confidence ${turn.answer.confidence}`}>
                    confidence: {turn.answer.confidence}
                  </div>
                </div>
              ) : (
                turn.streaming && <div className="bubble agent muted">{turn.streaming}</div>
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
            </div>
          ))}

          {busy && <div className="bubble agent muted">Working…</div>}
          <div ref={bottom} />

          <form
            className="composer"
            onSubmit={(event) => {
              event.preventDefault()
              send()
            }}
          >
            <input
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder={
                session.role === 'internal'
                  ? 'Investigate across accounts…'
                  : 'Ask about your orders, credits or SLAs…'
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
        <SignalsPanel
          onSeedChat={(query) => {
            setTab('chat')
            send(query)
          }}
        />
      )}
      {tab === 'actions' && <ActionsPanel refreshKey={refreshKey} />}
      {tab === 'audit' && <AuditPanel refreshKey={refreshKey} />}
    </div>
  )
}

function currentPersonaId(session) {
  if (session.role === 'customer') return `customer-${session.account_id}`
  return session.user_name.startsWith('Maya') ? 'internal-ops' : 'internal-support'
}
