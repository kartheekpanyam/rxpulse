import { useState, useRef, useEffect } from 'react'
import axios from 'axios'

const API = import.meta.env.VITE_API_BASE_URL

const INITIAL_MESSAGE = {
  role: 'assistant',
  text: "Hi! I'm RxPulse AI. I can answer questions about the medical benefit drug policies that have been uploaded to this system — coverage criteria, prior authorization requirements, step therapy, site-of-care restrictions, and how policies differ across payers. What would you like to know?",
  sources: [],
}

function loadChat() {
  try {
    const saved = localStorage.getItem('rxpulse_chat')
    if (saved) {
      const parsed = JSON.parse(saved)
      if (Array.isArray(parsed) && parsed.length > 0) return parsed
    }
  } catch {}
  return [INITIAL_MESSAGE]
}

export default function ChatInterface() {
  const [messages, setMessages] = useState(loadChat)
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [starterQuestions, setStarterQuestions] = useState([])
  const messagesEndRef = useRef(null)

  // Build starter questions from actual data
  useEffect(() => {
    axios.get(`${API}/drugs/list`)
      .then(res => {
        const drugs = res.data || []
        const starters = []
        // Find a drug with multiple payers
        const multiPayer = drugs.find(d => d.payer_count > 1)
        if (multiPayer) {
          starters.push(`Which payers cover ${multiPayer.drug_name} and what are the key differences?`)
        }
        // Pick first drug for PA question
        if (drugs.length > 0) {
          starters.push(`What prior authorization criteria are required for ${drugs[0].drug_name}?`)
        }
        // Pick another drug for step therapy
        if (drugs.length > 1) {
          starters.push(`Does ${drugs[1].drug_name} require step therapy?`)
        }
        // General comparison
        starters.push('Compare coverage restrictions across all payers in the system')
        setStarterQuestions(starters)
      })
      .catch(() => {
        setStarterQuestions([
          'What drugs are covered in the uploaded policies?',
          'Compare coverage restrictions across payers',
        ])
      })
  }, [])

  useEffect(() => {
    localStorage.setItem('rxpulse_chat', JSON.stringify(messages))
  }, [messages])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  async function ask(question) {
    if (!question.trim()) return
    setInput('')
    setMessages(prev => [...prev, { role: 'user', text: question }])
    setLoading(true)

    try {
      const { data } = await axios.post(`${API}/qa/ask`, { question })
      setMessages(prev => [...prev, {
        role: 'assistant',
        text: data.answer,
        sources: data.sources || [],
        drugs: data.drugs_found || [],
      }])
    } catch {
      setMessages(prev => [...prev, {
        role: 'assistant',
        text: 'Sorry, I could not reach the backend. Make sure the server is running.',
        sources: [],
      }])
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="max-w-3xl mx-auto flex flex-col" style={{ height: 'calc(100vh - 56px)' }}>
      <div className="px-6 py-4 border-b border-[var(--color-border)] flex items-center justify-between">
        <div>
          <h1 className="theme-page-title text-xl font-bold">AI Policy Assistant</h1>
          <p className="theme-subtitle text-sm">Ask about medical benefit drug policies in plain English</p>
        </div>
        {messages.length > 1 && (
          <button
            onClick={() => { setMessages([INITIAL_MESSAGE]); localStorage.removeItem('rxpulse_chat') }}
            className="theme-button-secondary text-xs px-3 py-1.5 rounded-lg transition-colors"
          >Clear Chat</button>
        )}
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[85%] rounded-2xl px-4 py-3 ${
              msg.role === 'user'
                ? 'bg-[var(--color-primary)] text-white'
                : 'theme-card text-[var(--color-text)]'
            }`}>
              {msg.role === 'assistant' && (
                <p className="text-[var(--color-primary)] text-xs font-medium mb-1">RxPulse AI</p>
              )}
              <div className="text-sm whitespace-pre-wrap">{msg.text}</div>
              {msg.sources && msg.sources.length > 0 && (
                <p className="theme-muted text-xs mt-2">
                  Sources: {msg.sources.join(', ')}
                </p>
              )}
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex justify-start">
            <div className="theme-card rounded-2xl px-4 py-3">
              <p className="text-[var(--color-primary)] text-xs font-medium mb-1">RxPulse AI</p>
              <div className="flex gap-1">
                <span className="w-2 h-2 bg-[var(--color-primary-soft)] rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                <span className="w-2 h-2 bg-[var(--color-primary-soft)] rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                <span className="w-2 h-2 bg-[var(--color-primary-soft)] rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
              </div>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {messages.length <= 1 && starterQuestions.length > 0 && (
        <div className="px-6 py-3 border-t border-[var(--color-border)]">
          <p className="theme-muted text-xs mb-2">Try asking:</p>
          <div className="grid grid-cols-2 gap-2">
            {starterQuestions.map(q => (
              <button key={q} onClick={() => ask(q)}
                className="theme-button-secondary text-left text-xs px-3 py-2 rounded-lg transition-colors">
                {q}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="px-6 py-4 border-t border-[var(--color-border)] flex gap-3">
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && ask(input)}
          placeholder="Ask about drug policies, PA criteria, site-of-care..."
          className="theme-input flex-1 rounded-xl px-4 py-2.5 text-sm"
        />
        <button onClick={() => ask(input)} disabled={loading}
          className="theme-button-primary disabled:opacity-50 px-5 py-2.5 rounded-xl text-sm font-medium transition-colors">
          Ask
        </button>
      </div>
    </div>
  )
}
