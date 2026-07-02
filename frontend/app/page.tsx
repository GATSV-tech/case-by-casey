"use client";

// Customer-facing chat — warm, calm, human. The complexity lives in /admin.

import { useEffect, useRef, useState } from "react";
import { streamChat, type AgentEvent } from "@/lib/api";

type Bubble = { role: "user" | "casey" | "system"; text: string };

export default function CustomerChat() {
  const [sessionId] = useState(() =>
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? `chat-${crypto.randomUUID().slice(0, 8)}`
      : `chat-${Math.random().toString(36).slice(2, 10)}`,
  );
  const [bubbles, setBubbles] = useState<Bubble[]>([
    {
      role: "casey",
      text: "Hi, I'm Casey 👋 — I can help with refunds on your Casey Commerce orders. What's going on?",
    },
  ]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [bubbles, busy]);

  async function send() {
    const message = input.trim();
    if (!message || busy) return;
    setInput("");
    setBusy(true);
    setBubbles((b) => [...b, { role: "user", text: message }]);
    try {
      await streamChat(sessionId, message, (e: AgentEvent) => {
        if (e.type === "agent_text" && e.agent === "casey" && e.text) {
          setBubbles((b) => [...b, { role: "casey", text: e.text! }]);
        }
      });
    } catch {
      setBubbles((b) => [
        ...b,
        {
          role: "system",
          text: "Connection hiccup — is the backend running on :8100?",
        },
      ]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen w-full bg-[#faf6ee] text-[#1d1a13] flex flex-col">
      {/* header */}
      <header className="border-b border-[#e8e0cf] bg-[#faf6ee]/90 backdrop-blur sticky top-0 z-10">
        <div className="max-w-2xl mx-auto px-5 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="h-9 w-9 rounded-full bg-[#1d1a13] text-[#faf6ee] grid place-items-center font-bold">
              C
            </div>
            <div>
              <div className="font-semibold leading-tight">Casey</div>
              <div className="text-xs text-[#8a8272] flex items-center gap-1.5">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 inline-block" />
                AI support agent · Casey Commerce Co.
              </div>
            </div>
          </div>
          <a
            href="/admin"
            className="text-xs text-[#8a8272] hover:text-[#1d1a13] underline underline-offset-4"
          >
            ops console →
          </a>
        </div>
      </header>

      {/* messages */}
      <main className="flex-1 overflow-y-auto">
        <div className="max-w-2xl mx-auto px-5 py-8 space-y-4 w-full">
          {bubbles.map((b, i) =>
            b.role === "system" ? (
              <div key={i} className="text-center text-xs text-[#b0a892]">
                {b.text}
              </div>
            ) : (
              <div
                key={i}
                className={`flex ${b.role === "user" ? "justify-end" : "justify-start"}`}
              >
                <div
                  className={
                    b.role === "user"
                      ? "max-w-[80%] rounded-2xl rounded-br-md bg-[#1d1a13] text-[#faf6ee] px-4 py-3 text-[15px] leading-relaxed"
                      : "max-w-[80%] rounded-2xl rounded-bl-md bg-white border border-[#ece4d2] shadow-[0_1px_3px_rgba(29,26,19,0.06)] px-4 py-3 text-[15px] leading-relaxed whitespace-pre-wrap"
                  }
                >
                  {b.text}
                </div>
              </div>
            ),
          )}
          {busy && (
            <div className="flex justify-start">
              <div className="rounded-2xl rounded-bl-md bg-white border border-[#ece4d2] px-4 py-3">
                <span className="inline-flex gap-1">
                  <span className="h-1.5 w-1.5 rounded-full bg-[#c9bfa6] animate-bounce [animation-delay:0ms]" />
                  <span className="h-1.5 w-1.5 rounded-full bg-[#c9bfa6] animate-bounce [animation-delay:120ms]" />
                  <span className="h-1.5 w-1.5 rounded-full bg-[#c9bfa6] animate-bounce [animation-delay:240ms]" />
                </span>
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>
      </main>

      {/* input */}
      <footer className="border-t border-[#e8e0cf] bg-[#faf6ee]">
        <div className="max-w-2xl mx-auto px-5 py-4 w-full">
          <div className="flex gap-2">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && send()}
              placeholder="Describe your refund request…"
              disabled={busy}
              className="flex-1 rounded-full border border-[#e0d7c2] bg-white px-5 py-3 text-[15px] outline-none focus:border-[#1d1a13] disabled:opacity-60"
            />
            <button
              onClick={send}
              disabled={busy || !input.trim()}
              className="rounded-full bg-[#1d1a13] text-[#faf6ee] px-6 py-3 text-sm font-medium disabled:opacity-40 hover:opacity-90 transition"
            >
              Send
            </button>
          </div>
          <div className="mt-2 text-[11px] text-[#b0a892] text-center">
            Demo CRM — try: maria.alvarez@example.com · gary.lindqvist@example.com
            · whitney.cho@example.com
          </div>
        </div>
      </footer>
    </div>
  );
}
