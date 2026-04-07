import { useState, useRef, useEffect, useCallback } from 'react'
import { getDrugsList, askQuestion } from '../../api'

const INITIAL_MESSAGE = {
  role: 'assistant',
  text: "Hi! I'm RxPulse AI. I can answer questions about the medical benefit drug policies that have been uploaded to this system — coverage criteria, prior authorization requirements, step therapy, site-of-care restrictions, and how policies differ across payers. What would you like to know?",
  sources: [],
}

/* ── Voice helpers (free browser APIs) ── */
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition
const synth = window.speechSynthesis

function speak(text) {
  if (!synth) return
  synth.cancel()
  const cleaned = text.replace(/\*\*(.*?)\*\*/g, '$1').replace(/#{1,6}\s+/g, '').replace(/[*_`~]/g, '')
  const utterance = new SpeechSynthesisUtterance(cleaned)
  utterance.rate = 1.05
  utterance.pitch = 1
  utterance.lang = 'en-US'
  synth.speak(utterance)
}

function useVoiceInput(onResult) {
  const [listening, setListening] = useState(false)
  const recogRef = useRef(null)

  const toggle = useCallback(() => {
    if (!SpeechRecognition) return
    if (listening) {
      recogRef.current?.stop()
      setListening(false)
      return
    }
    const recog = new SpeechRecognition()
    recog.lang = 'en-US'
    recog.interimResults = false
    recog.maxAlternatives = 1
    recog.onresult = (e) => {
      const transcript = e.results[0][0].transcript
      onResult(transcript)
    }
    recog.onend = () => setListening(false)
    recog.onerror = () => setListening(false)
    recogRef.current = recog
    recog.start()
    setListening(true)
  }, [listening, onResult])

  return { listening, toggle, supported: !!SpeechRecognition }
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
  const [speakingIdx, setSpeakingIdx] = useState(null)
  const messagesEndRef = useRef(null)

  const voice = useVoiceInput(useCallback((transcript) => setInput(prev => prev + transcript), []))

  // Build starter questions from actual data
  useEffect(() => {
    getDrugsList()
      .then(data => {
        const drugs = data || []
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
      const data = await askQuestion(question)
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
                <div className="flex items-center justify-between mb-1">
                  <p className="text-[var(--color-primary)] text-xs font-medium">RxPulse AI</p>
                  {synth && i > 0 && (
                    <button
                      onClick={() => {
                        if (speakingIdx === i) { synth.cancel(); setSpeakingIdx(null) }
                        else { speak(msg.text); setSpeakingIdx(i) }
                      }}
                      className="text-[var(--color-primary-soft)] hover:text-[var(--color-primary)] transition-colors ml-2"
                      title={speakingIdx === i ? 'Stop speaking' : 'Read aloud'}
                    >
                      {speakingIdx === i ? (
                        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/></svg>
                      ) : (
                        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/></svg>
                      )}
                    </button>
                  )}
                </div>
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

      <div className="px-6 py-4 border-t border-[var(--color-border)] flex gap-2">
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && ask(input)}
          placeholder={voice.listening ? 'Listening...' : 'Ask about drug policies, PA criteria, site-of-care...'}
          className={`theme-input flex-1 rounded-xl px-4 py-2.5 text-sm ${voice.listening ? 'ring-2 ring-red-400' : ''}`}
        />
        {voice.supported && (
          <button onClick={voice.toggle}
            className={`px-3 py-2.5 rounded-xl text-sm transition-colors ${
              voice.listening
                ? 'bg-red-500 text-white animate-pulse'
                : 'theme-button-secondary'
            }`}
            title={voice.listening ? 'Stop listening' : 'Voice input'}
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
              <path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/>
            </svg>
          </button>
        )}
        <button onClick={() => ask(input)} disabled={loading}
          className="theme-button-primary disabled:opacity-50 px-5 py-2.5 rounded-xl text-sm font-medium transition-colors">
          Ask
        </button>
      </div>
    </div>
  )
}
