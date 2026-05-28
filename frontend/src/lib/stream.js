/**
 * Stream Server-Sent Events from a POST endpoint.
 *
 * Yields parsed events as { event: string, data: any }.
 * Closes when the server ends the stream or the AbortSignal fires.
 */
export async function* streamSse(url, body, signal) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(body),
    signal,
  })
  if (!res.ok || !res.body) {
    throw new Error(`SSE request failed: ${res.status} ${res.statusText}`)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let sep
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const block = buffer.slice(0, sep)
      buffer = buffer.slice(sep + 2)
      const parsed = parseBlock(block)
      if (parsed) yield parsed
    }
  }

  if (buffer.trim()) {
    const parsed = parseBlock(buffer)
    if (parsed) yield parsed
  }
}

function parseBlock(block) {
  let eventType = 'message'
  let dataRaw = ''
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) {
      eventType = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      dataRaw += line.slice(5).trim()
    }
  }
  if (!dataRaw) return null
  try {
    return { event: eventType, data: JSON.parse(dataRaw) }
  } catch {
    return { event: eventType, data: dataRaw }
  }
}
