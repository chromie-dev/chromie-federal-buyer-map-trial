"use client"

import Link from "next/link"
import { useCallback, useEffect, useState } from "react"
import { ArrowRightLeft, ExternalLink, LoaderCircle, Sparkles } from "lucide-react"
import {
  BTN_OUTLINE,
  BTN_PRIMARY,
  CHIP,
  CHIP_TONES,
  INPUT_CLASS,
  SELECT_CLASS,
} from "@/components/ui/codex-surface/surface-theme"
import { INTERACTION_KINDS, STANCES } from "@/lib/gov/people/person-history.mjs"

const GROUP_LABEL = "text-[12.5px] text-cdx-ink-3"
const ROW = "rounded-[10px] border border-cdx-line bg-cdx-sunken p-3"
const STANCE_LABELS = { target: "Target", engaged: "Engaged", champion: "Champion", detractor: "Detractor", unknown: "Unknown" }
const EVENT_LABELS = {
  transfer: "Moved to", appointment: "Took up", departure: "Left", announcement: "Named to",
  promotion: "Promoted to", acting: "Acting as",
}
const SIGNAL_LABELS = {
  new_in_post: "New in the post", solicitation_out: "Solicitation out", talks_to_industry: "Talks to industry",
}

const STANDING_CHIPS = {
  confirmed_current: ["Confirmed current", CHIP_TONES.blue], last_observed: ["Last observed", CHIP_TONES.neutral],
  ended: ["Ended", CHIP_TONES.neutral],
}
const CERTAINTY_CHIPS = {
  confirmed: ["Confirmed", CHIP_TONES.green], reported: ["Reported", CHIP_TONES.amber], inferred: ["Inferred", CHIP_TONES.neutral],
}

function Chip({ value, chips }) {
  const [label, tone] = chips[value] || [value, CHIP_TONES.neutral]
  return <span className={`${CHIP} ${tone}`}>{label}</span>
}

function Fact({ label, children }) {
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
      <dt className="font-medium">{label}:</dt>
      <dd className="flex flex-wrap items-center gap-2">{children}</dd>
    </div>
  )
}

function SourceLink({ href, children }) {
  if (!href) return <span className="text-[12px] text-cdx-ink-4">{children}</span>
  return (
    <a href={href} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[12px] text-cdx-ink-3 hover:text-cdx-blue">
      {children}
      <ExternalLink className="h-3 w-3" />
    </a>
  )
}

function OfficeLink({ organization }) {
  if (!organization) return null
  return (
    <Link href={`/contractors/offices/${organization.id}`} className="text-cdx-ink-2 hover:text-cdx-blue">
      {organization.name}
    </Link>
  )
}

function Relationship({ contactId, relationship, onChange }) {
  const [stance, setStance] = useState(relationship?.stance || "")
  const [notes, setNotes] = useState(relationship?.notes || "")
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState("")

  useEffect(() => {
    setStance(relationship?.stance || "")
    setNotes(relationship?.notes || "")
  }, [relationship])

  const save = async (method, body) => {
    setSaving(true)
    setError("")
    const response = await fetch(`/api/gov-people/contact:${contactId}`, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    }).catch(() => null)
    const json = await response?.json().catch(() => ({}))
    setSaving(false)
    if (!response?.ok) return setError(json?.error || "Could not save.")
    onChange()
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <select value={stance} onChange={(event) => setStance(event.target.value)} aria-label="Stance" className={`${SELECT_CLASS} mt-0 w-44`}>
          <option value="">Not tracked</option>
          {STANCES.map((value) => <option key={value} value={value}>{STANCE_LABELS[value]}</option>)}
        </select>
        <button type="button" disabled={saving || !stance} onClick={() => save("PUT", { stance, notes })} className={`${BTN_PRIMARY} h-9 shrink-0 px-3.5`}>
          {relationship ? "Save" : "Track person"}
        </button>
        {relationship ? (
          <button type="button" disabled={saving} onClick={() => save("DELETE")} className={`${BTN_OUTLINE} h-9 shrink-0 px-3.5`}>Stop tracking</button>
        ) : null}
      </div>
      <textarea
        value={notes}
        onChange={(event) => setNotes(event.target.value)}
        placeholder="Notes for your team"
        aria-label="Relationship notes"
        rows={2}
        className={`${INPUT_CLASS} mt-0 w-full`}
      />
      {error ? <p className="text-[12px] text-cdx-red">{error}</p> : null}
    </div>
  )
}

function Interactions({ contactId, interactions, onChange }) {
  const [kind, setKind] = useState("meeting")
  const [happenedOn, setHappenedOn] = useState(() => new Date().toISOString().slice(0, 10))
  const [summary, setSummary] = useState("")
  const [error, setError] = useState("")

  const add = async (event) => {
    event.preventDefault()
    setError("")
    const response = await fetch(`/api/gov-people/contact:${contactId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind, happened_on: happenedOn, summary }),
    }).catch(() => null)
    const json = await response?.json().catch(() => ({}))
    if (!response?.ok) return setError(json?.error || "Could not log the interaction.")
    setSummary("")
    onChange()
  }

  return (
    <div className="space-y-2">
      <form onSubmit={add} className="grid gap-2 sm:grid-cols-[120px_150px_minmax(0,1fr)_auto]">
        <select value={kind} onChange={(event) => setKind(event.target.value)} aria-label="Interaction kind" className={`${SELECT_CLASS} mt-0`}>
          {INTERACTION_KINDS.map((value) => <option key={value} value={value}>{value[0].toUpperCase() + value.slice(1)}</option>)}
        </select>
        <input type="date" value={happenedOn} onChange={(event) => setHappenedOn(event.target.value)} aria-label="Date" className={`${INPUT_CLASS} mt-0`} />
        <input value={summary} onChange={(event) => setSummary(event.target.value)} placeholder="What happened" aria-label="Summary" className={`${INPUT_CLASS} mt-0`} />
        <button type="submit" disabled={!summary.trim()} className={`${BTN_OUTLINE} px-3.5`}>Log</button>
      </form>
      {error ? <p className="text-[12px] text-cdx-red">{error}</p> : null}
      {interactions.map((item) => (
        <p key={item.id} className="text-[12.5px] text-cdx-ink-2">
          <span className="tabular-nums text-cdx-ink-4">{item.happened_on}</span> · {item.kind} · {item.summary}
        </p>
      ))}
    </div>
  )
}

function ChampionReading({ reading }) {
  if (!reading) return null
  const role = reading.documented_role
  return (
    <div className={`${ROW} border-cdx-violet-line`}>
      <div className="flex items-center gap-2">
        <Sparkles className="h-3.5 w-3.5 text-cdx-violet" />
        <p className="text-[13px] font-medium text-cdx-ink">Potential champion</p>
        <span className={`${CHIP} ${CHIP_TONES.violet}`}>Reading</span>
      </div>
      <p className="mt-1 text-[12px] text-cdx-ink-3">
        A reading from {reading.signals.length} kinds of dated signal, not a confirmed fact. A title alone establishes
        neither budget authority nor willingness to champion you.
      </p>
      <dl className="mt-2 space-y-1 text-[12.5px] text-cdx-ink-2">
        <Fact label="Documented role">
          <span>{role.title}{role.office ? `, ${role.office}` : ""} · {role.standing_label}</span>
          <SourceLink href={role.source_url}>{role.source_label}</SourceLink>
        </Fact>
        <Fact label="Problem they own">
          {reading.problem_owned ? (
            <>
              <span>{reading.problem_owned.text}</span>
              <SourceLink href={reading.problem_owned.source_url}>{reading.problem_owned.source_label}</SourceLink>
            </>
          ) : <span className="text-cdx-ink-4">Not established</span>}
        </Fact>
        <Fact label="Why your company matters to them">
          {reading.why_it_matters ? <span>{reading.why_it_matters.text}</span>
            : <span className="text-cdx-ink-4">Not recorded; add it to your notes</span>}
        </Fact>
        <Fact label="Evidence they will advocate">
          {reading.advocacy ? <span>{reading.advocacy.text}</span> : <span className="text-cdx-ink-4">None found</span>}
        </Fact>
      </dl>
      <ul className="mt-2 space-y-1">
        {reading.signals.map((signal) => (
          <li key={signal.kind} className="flex flex-wrap items-center gap-2 text-[12.5px] text-cdx-ink-2">
            <span className="font-medium">{SIGNAL_LABELS[signal.kind]}:</span>
            <span>{signal.text}</span>
            {signal.date ? <span className="tabular-nums text-cdx-ink-4">{signal.date}</span> : null}
            <SourceLink href={signal.source_url}>{signal.source_label}</SourceLink>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-[12px] text-cdx-ink-3">Still to confirm: {reading.to_confirm.join("; ")}.</p>
    </div>
  )
}

function RevolvingDoorReading({ reading }) {
  return (
    <div className={`${ROW} border-cdx-violet-line`}>
      <div className="flex items-center gap-2">
        <ArrowRightLeft className="h-3.5 w-3.5 text-cdx-violet" />
        <p className="text-[13px] font-medium text-cdx-ink">Now outside government</p>
        <span className={`${CHIP} ${CHIP_TONES.violet}`}>Reading</span>
      </div>
      <p className="mt-1 text-[12.5px] text-cdx-ink-2">{reading.text}</p>
      <div className="mt-1"><SourceLink href={reading.source_url}>{reading.source_label}</SourceLink></div>
    </div>
  )
}

/** A person's dated posts and moves with sources, and the company's relationship and interactions. */
export default function PersonHistoryPanel({ contactId }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")

  const load = useCallback(async () => {
    if (!contactId) return
    const response = await fetch(`/api/gov-people/contact:${contactId}`).catch(() => null)
    const json = await response?.json().catch(() => ({}))
    if (!response?.ok) setError(json?.error || "Could not load this person's history.")
    else {
      setError("")
      setData(json)
    }
    setLoading(false)
  }, [contactId])

  useEffect(() => {
    setLoading(true)
    setData(null)
    load()
  }, [load])

  if (!contactId) return null
  if (loading) {
    return (
      <p className="mt-8 flex items-center gap-2 text-[13px] text-cdx-ink-3">
        <LoaderCircle className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
        Loading history…
      </p>
    )
  }
  if (error) return <p className="mt-8 text-[13px] text-cdx-red">{error}</p>
  const { timeline, relationship, interactions, champion, revolving_door: revolvingDoor } = data

  return (
    <>
      <div className="mt-8 space-y-3">
        <p className={GROUP_LABEL}>Your relationship</p>
        <Relationship contactId={contactId} relationship={relationship} onChange={load} />
        <Interactions contactId={contactId} interactions={interactions} onChange={load} />
      </div>

      {champion ? <div className="mt-6"><ChampionReading reading={champion} /></div> : null}
      {revolvingDoor ? <div className="mt-6"><RevolvingDoorReading reading={revolvingDoor} /></div> : null}

      <div className="mt-8">
        <p className={GROUP_LABEL}>Posts</p>
        <div className="mt-3 space-y-2">
          {timeline.posts.length ? timeline.posts.map((post) => (
            <div key={post.id} className={ROW}>
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-[13.5px] font-medium text-cdx-ink">{post.title}</p>
                <Chip value={post.standing} chips={STANDING_CHIPS} />
              </div>
              <p className="mt-1 text-[12px] text-cdx-ink-3">
                <OfficeLink organization={post.organization} />
                {post.organization ? " · " : ""}
                {[post.standing_label, post.started ? `in the post since ${post.started.date} (stated)`
                  : post.first_seen ? `first observed ${post.first_seen}` : ""].filter(Boolean).join(" · ")}
              </p>
              <div className="mt-1"><SourceLink href={post.source_url}>{post.source_label}</SourceLink></div>
            </div>
          )) : <p className="text-[13.5px] text-cdx-ink-3">No dated posts recorded yet.</p>}
        </div>
      </div>

      <div className="mt-8">
        <p className={GROUP_LABEL}>Moves</p>
        <div className="mt-3 space-y-2">
          {timeline.moves.length ? timeline.moves.map((move) => (
            <div key={move.id} className={ROW}>
              <div className="flex items-start gap-2">
                <ArrowRightLeft className="mt-0.5 h-3.5 w-3.5 shrink-0 text-cdx-ink-4" />
                <div className="min-w-0">
                  <p className="text-[13.5px] font-medium text-cdx-ink">{EVENT_LABELS[move.event_type] || "Changed post:"} {move.title}</p>
                  {move.previous_title ? <p className="mt-0.5 text-[12px] text-cdx-ink-3">Before: {move.previous_title}</p> : null}
                  {move.now ? <p className="mt-0.5 text-[12px] text-cdx-ink-2">Now: {move.now}</p> : null}
                  <div className="mt-1 flex flex-wrap items-center gap-2">
                    <span className="text-[12px] tabular-nums text-cdx-ink-3">{move.date}</span>
                    <span title={move.certainty_label}><Chip value={move.certainty} chips={CERTAINTY_CHIPS} /></span>
                    {move.status === "review" ? <span className={`${CHIP} ${CHIP_TONES.amber}`}>Needs review</span> : null}
                    <SourceLink href={move.source_url}>{move.source_label}</SourceLink>
                  </div>
                  <p className="mt-1 text-[12px] text-cdx-ink-3">{move.certainty_label}</p>
                  {move.evidence.find((item) => item.quote) ? (
                    <p className="mt-1 text-[12px] italic text-cdx-ink-3">“{move.evidence.find((item) => item.quote).quote}”</p>
                  ) : null}
                </div>
              </div>
            </div>
          )) : <p className="text-[13.5px] text-cdx-ink-3">No moves recorded.</p>}
        </div>
      </div>
    </>
  )
}
