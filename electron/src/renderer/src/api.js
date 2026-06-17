/**
 * API client for the Python backend.
 *
 * In Electron, the port is obtained via IPC from the main process.
 * In dev browser mode, falls back to localhost:8000.
 */

let _baseUrl = null

async function getBaseUrl() {
  if (_baseUrl) return _baseUrl
  if (window.electronAPI) {
    const port = await window.electronAPI.getApiPort()
    _baseUrl = `http://127.0.0.1:${port}`
  } else {
    _baseUrl = 'http://127.0.0.1:8000'
  }
  return _baseUrl
}

/**
 * Call a service action (sync — waits for full result).
 */
export async function dispatch(action, payload = {}) {
  const base = await getBaseUrl()
  const res = await fetch(`${base}/api/${action}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }))
    throw new Error(err.error || `Action ${action} failed`)
  }
  return res.json()
}

/**
 * Call a service action with SSE streaming (progress + final result).
 *
 * @param {string}   action
 * @param {object}   payload
 * @param {function} onProgress  - called with each progress event
 * @returns {Promise<object>}    - resolves with the final result
 */
export async function dispatchStream(action, payload = {}, onProgress = null) {
  const base = await getBaseUrl()
  const res = await fetch(`${base}/api/${action}/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let finalResult = null

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    const lines = buffer.split('\n')
    buffer = lines.pop() || ''

    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      const event = JSON.parse(line.slice(6))
      if (event.type === 'progress' && onProgress) {
        onProgress(event.data)
      } else if (event.type === 'done') {
        finalResult = event.data
      } else if (event.type === 'error') {
        throw new Error(event.data)
      }
    }
  }

  return finalResult
}

/**
 * Health check.
 */
export async function healthCheck() {
  const base = await getBaseUrl()
  const res = await fetch(`${base}/api/health`)
  return res.ok
}
