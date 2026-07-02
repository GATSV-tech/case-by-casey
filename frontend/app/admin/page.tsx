"use client";

// Ops console — dark mission control. Live reasoning theater for both agents,
// CRM directory, escalation queue (dispatch Morgan), refunds ledger.

import { useEffect, useRef, useState } from "react";
import {
  dispatchMorgan,
  getAdminState,
  getCustomers,
  subscribeAdminStream,
  type AdminState,
  type AgentEvent,
  type CrmCustomer,
} from "@/lib/api";

const AGENT_STYLES: Record<string, { badge: string; text: string; label: string }> = {
  casey: { badge: "bg-teal-500/15 text-teal-300 border-teal-500/30", text: "text-teal-200", label: "CASEY · T1" },
  morgan: { badge: "bg-violet-500/15 text-violet-300 border-violet-500/30", text: "text-violet-200", label: "MORGAN · T2" },
};

function ts(e: AgentEvent): string {
  return e.ts ? new Date(e.ts).toLocaleTimeString([], { hour12: false }) : "";
}

function EventRow({ e }: { e: AgentEvent }) {
  const a = AGENT_STYLES[e.agent ?? "casey"] ?? AGENT_STYLES.casey;
  const base = "px-3 py-2 border-b border-white/5 text-[13px] leading-relaxed";

  if (e.type === "user_message")
    return (
      <div className={base}>
        <Meta e={e} tag="CUSTOMER" tagClass="bg-white/10 text-zinc-300 border-white/10" />
        <div className="text-zinc-100 mt-1">💬 {e.text}</div>
      </div>
    );
  if (e.type === "agent_text")
    return (
      <div className={base}>
        <Meta e={e} tag={a.label} tagClass={a.badge} />
        <div className={`${a.text} mt-1 whitespace-pre-wrap`}>{e.text}</div>
      </div>
    );
  if (e.type === "agent_thinking")
    return (
      <div className={`${base} bg-white/[0.015]`}>
        <Meta e={e} tag={a.label} tagClass={a.badge} extra="THINKING" />
        <div className="mt-1 text-zinc-500 italic text-[12px] whitespace-pre-wrap">
          🧠 {e.text}
        </div>
      </div>
    );
  if (e.type === "tool_call")
    return (
      <div className={`${base} bg-white/[0.02]`}>
        <Meta e={e} tag={a.label} tagClass={a.badge} extra="TOOL CALL" />
        <div className="font-mono text-[12px] text-amber-200/90 mt-1">
          ⚙ {e.tool}
          <span className="text-zinc-500">({JSON.stringify(e.input)})</span>
        </div>
      </div>
    );
  if (e.type === "tool_result") {
    const r = (e.result ?? {}) as Record<string, unknown>;
    const rules = Array.isArray(r.rule_evaluations)
      ? (r.rule_evaluations as Array<{ rule: string }>).map((x) => x.rule).join(" ")
      : null;
    const headline =
      (r.recommended_decision &&
        `→ ${r.recommended_decision} $${r.recommended_amount_usd} ${rules ? `[${rules}]` : ""}`) ||
      (r.executed !== undefined && `executed: ${String(r.executed)} ${r.refund_id ?? ""}`) ||
      (r.resolved !== undefined && `resolved: ${String(r.resolved)} ${r.decision ?? ""}`) ||
      (r.found !== undefined && `found: ${String(r.found)}`) ||
      (r.created !== undefined && `ticket: ${r.ticket_id}`) ||
      "result";
    return (
      <div className={`${base} bg-white/[0.02]`}>
        <Meta e={e} tag={a.label} tagClass={a.badge} extra={`RESULT · ${e.tool}`} />
        <div className="font-mono text-[12px] text-emerald-200/80 mt-1">{String(headline)}</div>
        <details className="mt-1">
          <summary className="cursor-pointer text-[11px] text-zinc-500 hover:text-zinc-300">
            raw json
          </summary>
          <pre className="mt-1 max-h-48 overflow-auto rounded bg-black/40 p-2 text-[11px] text-zinc-400">
            {JSON.stringify(r, null, 2)}
          </pre>
        </details>
      </div>
    );
  }
  if (e.type === "guardrail")
    return (
      <div className={`${base} border-l-2 border-l-red-500 bg-red-500/10`}>
        <Meta e={e} tag="GUARDRAIL" tagClass="bg-red-500/20 text-red-300 border-red-500/40" />
        <div className="text-red-300 font-mono text-[12px] mt-1">
          🛑 {e.guardrail} — {e.detail}
        </div>
      </div>
    );
  if (e.type === "retry")
    return (
      <div className={`${base} border-l-2 border-l-amber-500 bg-amber-500/5`}>
        <Meta e={e} tag="RETRY" tagClass="bg-amber-500/20 text-amber-300 border-amber-500/40" />
        <div className="text-amber-300 font-mono text-[12px] mt-1">↻ {e.detail}</div>
      </div>
    );
  if (e.type === "error")
    return (
      <div className={`${base} border-l-2 border-l-red-500`}>
        <Meta e={e} tag="ERROR" tagClass="bg-red-500/20 text-red-300 border-red-500/40" />
        <div className="text-red-300 font-mono text-[12px] mt-1">✖ {e.detail}</div>
      </div>
    );
  if (e.type === "review_started")
    return (
      <div className={`${base} bg-violet-500/10`}>
        <Meta e={e} tag="MORGAN · T2" tagClass={AGENT_STYLES.morgan.badge} />
        <div className="text-violet-300 mt-1">🕴 picked up {e.ticket_id}</div>
      </div>
    );
  if (e.type === "turn_end")
    return (
      <div className="px-3 py-1.5 border-b border-white/5 text-[11px] text-zinc-600 font-mono">
        ─── turn complete{e.iterations ? ` · ${e.iterations} loop iteration${e.iterations > 1 ? "s" : ""}` : ""} ───
      </div>
    );
  return null;
}

function Meta({
  e,
  tag,
  tagClass,
  extra,
}: {
  e: AgentEvent;
  tag: string;
  tagClass: string;
  extra?: string;
}) {
  return (
    <div className="flex items-center gap-2 text-[10px] font-mono text-zinc-600">
      <span className={`rounded border px-1.5 py-0.5 tracking-wider ${tagClass}`}>{tag}</span>
      {extra && <span className="text-zinc-500 tracking-wider">{extra}</span>}
      <span>{ts(e)}</span>
      {e.session_id && <span className="text-zinc-700">{e.session_id}</span>}
    </div>
  );
}

export default function OpsConsole() {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [state, setState] = useState<AdminState | null>(null);
  const [customers, setCustomers] = useState<CrmCustomer[]>([]);
  const [reviewing, setReviewing] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const feedRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const cleanup = subscribeAdminStream((e) => {
      if (e.type === "admin_connected") {
        setConnected(true);
        setEvents([]); // server replays history on (re)connect — start clean
        return;
      }
      setEvents((prev) => [...prev.slice(-400), e]);
    });
    getCustomers().then(setCustomers).catch(() => {});
    const poll = setInterval(() => getAdminState().then(setState).catch(() => {}), 2500);
    getAdminState().then(setState).catch(() => {});
    return () => {
      cleanup();
      clearInterval(poll);
    };
  }, []);

  useEffect(() => {
    feedRef.current?.scrollTo({ top: feedRef.current.scrollHeight, behavior: "smooth" });
  }, [events]);

  async function review(ticketId: string) {
    setReviewing(ticketId);
    try {
      await dispatchMorgan(ticketId, () => {});
    } finally {
      setReviewing(null);
      getAdminState().then(setState).catch(() => {});
    }
  }

  const openTickets = state?.escalations.filter((t) => t.status === "open") ?? [];
  const resolvedTickets = state?.escalations.filter((t) => t.status !== "open") ?? [];

  return (
    <div className="min-h-screen bg-[#0a0c10] text-zinc-200 font-sans">
      {/* header */}
      <header className="border-b border-white/10 bg-[#0a0c10]/95 sticky top-0 z-10">
        <div className="px-5 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="font-mono font-bold tracking-widest text-sm">
              CASEY <span className="text-zinc-500">OPS</span>
            </span>
            <span className="text-[10px] font-mono text-zinc-500 border border-white/10 rounded px-1.5 py-0.5">
              MISSION CONTROL
            </span>
          </div>
          <div className="flex items-center gap-4 text-[11px] font-mono text-zinc-500">
            <span className="flex items-center gap-1.5">
              <span
                className={`h-1.5 w-1.5 rounded-full ${connected ? "bg-emerald-400" : "bg-red-500"}`}
              />
              {connected ? "stream live" : "stream down"}
            </span>
            <span>refunds {state?.refunds.length ?? 0}</span>
            <span>tickets {state?.escalations.length ?? 0}</span>
            <a href="/" className="underline underline-offset-4 hover:text-zinc-200">
              ← customer chat
            </a>
          </div>
        </div>
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-0 h-[calc(100vh-49px)]">
        {/* reasoning theater */}
        <section className="lg:col-span-2 border-r border-white/10 flex flex-col min-h-0">
          <div className="px-4 py-2 border-b border-white/10 flex items-center justify-between">
            <span className="text-[11px] font-mono tracking-widest text-zinc-500">
              LIVE REASONING — ALL AGENTS
            </span>
            <span className="flex gap-3 text-[10px] font-mono">
              <span className="text-teal-300">■ casey (tier 1)</span>
              <span className="text-violet-300">■ morgan (tier 2)</span>
              <span className="text-red-400">■ guardrail</span>
            </span>
          </div>
          <div ref={feedRef} className="flex-1 overflow-y-auto min-h-0">
            {events.length === 0 ? (
              <div className="h-full grid place-items-center text-zinc-600 font-mono text-xs">
                waiting for agent activity… start a conversation in the customer chat
              </div>
            ) : (
              events.map((e, i) => <EventRow key={i} e={e} />)
            )}
          </div>
        </section>

        {/* right rail */}
        <section className="overflow-y-auto min-h-0 divide-y divide-white/10">
          {/* escalation queue */}
          <div className="p-4">
            <h3 className="text-[11px] font-mono tracking-widest text-zinc-500 mb-3">
              ESCALATION QUEUE
            </h3>
            {openTickets.length === 0 && (
              <div className="text-xs text-zinc-600 font-mono">no open tickets</div>
            )}
            {openTickets.map((t) => (
              <div
                key={t.ticket_id}
                className="mb-2 rounded border border-amber-500/30 bg-amber-500/5 p-3"
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono text-xs text-amber-300">{t.ticket_id}</span>
                  <button
                    onClick={() => review(t.ticket_id)}
                    disabled={reviewing !== null}
                    className="rounded bg-violet-600 hover:bg-violet-500 disabled:opacity-40 px-2.5 py-1 text-[11px] font-medium"
                  >
                    {reviewing === t.ticket_id ? "Morgan reviewing…" : "Dispatch Morgan →"}
                  </button>
                </div>
                <div className="mt-1.5 text-[11px] text-zinc-400">
                  {t.customer_id} · {t.order_id}
                </div>
                <div className="mt-1 text-[11px] text-zinc-500 line-clamp-2">{t.reason}</div>
              </div>
            ))}
            {resolvedTickets.length > 0 && (
              <div className="mt-3 space-y-1">
                {resolvedTickets.map((t) => (
                  <div
                    key={t.ticket_id}
                    className="flex items-center justify-between text-[11px] font-mono text-zinc-500"
                  >
                    <span>{t.ticket_id}</span>
                    <span
                      className={
                        t.resolution === "approved_manager" ? "text-emerald-400" : "text-red-400"
                      }
                    >
                      {t.resolution}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* refunds ledger */}
          <div className="p-4">
            <h3 className="text-[11px] font-mono tracking-widest text-zinc-500 mb-3">
              REFUNDS LEDGER
            </h3>
            {(state?.refunds.length ?? 0) === 0 ? (
              <div className="text-xs text-zinc-600 font-mono">no refunds executed</div>
            ) : (
              <div className="space-y-2">
                {state!.refunds.map((r, i) => (
                  <div key={i} className="rounded border border-white/10 bg-white/[0.02] p-2.5">
                    <div className="flex items-center justify-between font-mono text-[11px]">
                      <span className="text-zinc-300">{String(r.refund_id)}</span>
                      <span className="text-emerald-300">
                        ${Number(r.amount_usd).toFixed(2)}
                      </span>
                    </div>
                    <div className="mt-1 text-[11px] text-zinc-500 font-mono">
                      {String(r.decision)} · {String(r.customer_id)} ·{" "}
                      {Array.isArray(r.rule_ids) ? (r.rule_ids as string[]).join(",") : ""}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* CRM directory */}
          <div className="p-4">
            <h3 className="text-[11px] font-mono tracking-widest text-zinc-500 mb-3">
              CRM DIRECTORY · {customers.length}
            </h3>
            <div className="space-y-1">
              {customers.map((c) => (
                <details key={c.customer_id} className="group">
                  <summary className="flex cursor-pointer items-center justify-between rounded px-2 py-1.5 hover:bg-white/5 text-[12px]">
                    <span className="text-zinc-300">{c.name}</span>
                    <span className="flex items-center gap-1.5 font-mono text-[10px]">
                      {c.flags.map((f) => (
                        <span
                          key={f}
                          className="rounded border border-red-500/40 bg-red-500/10 text-red-300 px-1 py-0.5"
                        >
                          {f}
                        </span>
                      ))}
                      <span className="text-zinc-600">{c.loyalty_tier}</span>
                    </span>
                  </summary>
                  <div className="px-2 pb-2 text-[11px] font-mono text-zinc-500">
                    {c.customer_id} · {c.email}
                    <br />
                    orders: {c.orders.join(", ")}
                  </div>
                </details>
              ))}
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
