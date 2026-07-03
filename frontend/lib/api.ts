// Casey frontend — API layer.
// The backend (FastAPI, :8100) exposes SSE streams; these helpers parse them.

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8100";

export type AgentEvent = {
  type: string;
  agent?: "casey" | "morgan";
  session_id?: string;
  ts?: number;
  text?: string;
  tool?: string;
  input?: Record<string, unknown>;
  result?: Record<string, unknown>;
  guardrail?: string;
  detail?: string;
  ticket_id?: string;
  iterations?: number;
};

/** Parse an SSE byte stream from a fetch Response, invoking onEvent per event. */
async function parseSSE(
  res: Response,
  onEvent: (e: AgentEvent) => void,
): Promise<void> {
  const reader = res.body?.getReader();
  if (!reader) throw new Error("No response body");
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() ?? "";
    for (const chunk of chunks) {
      for (const line of chunk.split("\n")) {
        if (line.startsWith("data: ")) {
          try {
            onEvent(JSON.parse(line.slice(6)));
          } catch {
            /* keepalive / partial — ignore */
          }
        }
      }
    }
  }
}

/** Send one customer message; stream back the agent turn's events. */
export async function streamChat(
  sessionId: string,
  message: string,
  onEvent: (e: AgentEvent) => void,
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, message }),
  });
  if (!res.ok) throw new Error(`chat failed: ${res.status}`);
  await parseSSE(res, onEvent);
}

/** Dispatch Morgan (manager agent) on a ticket; stream its review events. */
export async function dispatchMorgan(
  ticketId: string,
  onEvent: (e: AgentEvent) => void,
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/api/admin/escalations/${ticketId}/review`,
    { method: "POST" },
  );
  if (!res.ok) throw new Error(`review failed: ${res.status}`);
  await parseSSE(res, onEvent);
}

/** Subscribe to the global admin event feed. Returns a cleanup function. */
export function subscribeAdminStream(
  onEvent: (e: AgentEvent) => void,
): () => void {
  const es = new EventSource(`${API_BASE}/api/admin/stream`);
  es.onmessage = (msg) => {
    try {
      onEvent(JSON.parse(msg.data));
    } catch {
      /* ignore */
    }
  };
  return () => es.close();
}

export type AdminState = {
  refunds: Array<Record<string, unknown>>;
  escalations: Array<{
    ticket_id: string;
    customer_id: string;
    order_id: string;
    reason: string;
    status: string;
    resolution: string | null;
    timestamp: string;
  }>;
  sessions: Record<string, number>;
};

export async function getAdminState(): Promise<AdminState> {
  const res = await fetch(`${API_BASE}/api/admin/state`);
  return res.json();
}

export type CrmCustomer = {
  customer_id: string;
  name: string;
  email: string;
  loyalty_tier: string;
  flags: string[];
  orders: string[];
};

export async function getCustomers(): Promise<CrmCustomer[]> {
  const res = await fetch(`${API_BASE}/api/customers`);
  const data = await res.json();
  return data.customers;
}

/** Fetch spoken audio for text (ElevenLabs, server-side). null on any failure
 *  so the caller can fall back to the browser's built-in voice. */
export async function fetchTTS(text: string): Promise<Blob | null> {
  try {
    const res = await fetch(`${API_BASE}/api/tts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!res.ok) return null;
    const blob = await res.blob();
    return blob.type.startsWith("audio") ? blob : null;
  } catch {
    return null;
  }
}
