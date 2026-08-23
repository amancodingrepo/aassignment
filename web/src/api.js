// Thin API client. The session cookie is httpOnly and set by the server, so
// every call is credentialed and the browser never holds an account id it could
// be persuaded to change.

async function request(path, options = {}) {
  const response = await fetch(path, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    ...options
  })
  if (!response.ok) {
    let detail = response.statusText
    try {
      detail = (await response.json()).detail || detail
    } catch {
      // A non-JSON error body is still an error; keep the status text.
    }
    throw new Error(detail)
  }
  return response.json()
}

export const getPersonas = () => request('/api/personas')
export const getSession = () => request('/api/session')
export const setPersona = (personaId) =>
  request('/api/session', { method: 'POST', body: JSON.stringify({ persona_id: personaId }) })
export const getSignals = () => request('/api/signals')
export const getActions = () => request('/api/actions')
export const getAudit = () => request('/api/audit')
export const confirmAction = (token) =>
  request('/api/confirm', { method: 'POST', body: JSON.stringify({ token }) })
export const cancelAction = (token) =>
  request('/api/cancel', { method: 'POST', body: JSON.stringify({ token }) })
export const resetDemo = () => request('/api/reset', { method: 'POST' })
export const getContext = () => request('/api/context')


// The chat endpoint streams server-sent events over POST, which EventSource
// cannot do, so the stream is read off the response body directly.
export async function streamChat(messages, onEvent) {
  const response = await fetch('/api/chat', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages })
  })
  if (!response.ok || !response.body) {
    throw new Error(`chat failed: ${response.status}`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let boundary = buffer.indexOf('\n\n')
    while (boundary !== -1) {
      const frame = buffer.slice(0, boundary).trim()
      buffer = buffer.slice(boundary + 2)
      if (frame.startsWith('data:')) {
        try {
          onEvent(JSON.parse(frame.slice(5).trim()))
        } catch {
          // A malformed frame is dropped rather than killing the stream.
        }
      }
      boundary = buffer.indexOf('\n\n')
    }
  }
}
