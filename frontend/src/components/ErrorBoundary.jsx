import { Component } from 'react'

/**
 * Catches render-phase exceptions so a single bad component shows a readable
 * error instead of tearing down the whole tree (which renders as a black
 * screen). Operators get the message + a reload button rather than a dead page.
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    // Surface to the console for anyone with devtools open / log capture.
    console.error('Unhandled UI error:', error, info?.componentStack)
  }

  handleReload = () => {
    this.setState({ error: null })
    window.location.reload()
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children

    return (
      <div className="h-svh flex items-center justify-center p-6">
        <div className="max-w-lg w-full border border-red-900 bg-zinc-950 rounded p-4">
          <h2 className="text-sm font-medium text-red-400 mb-2">
            Something broke in the UI
          </h2>
          <p className="text-xs text-zinc-400 mb-3">
            The interface hit an unexpected error and stopped rendering. This is
            a bug — the details below help track it down.
          </p>
          <pre className="mono text-xs text-zinc-300 bg-zinc-900 border border-zinc-800 rounded p-2 overflow-auto max-h-48 whitespace-pre-wrap">
            {error?.message || String(error)}
          </pre>
          <button
            onClick={this.handleReload}
            className="mono text-xs mt-3 px-2 py-0.5 border border-zinc-700 rounded text-zinc-300 hover:text-zinc-100 hover:border-zinc-500"
          >
            ↻ reload
          </button>
        </div>
      </div>
    )
  }
}
