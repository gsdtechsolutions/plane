/** Copyright (c) 2023-present Plane Software, Inc. and contributors. SPDX-License-Identifier: AGPL-3.0-only */
import { useCallback, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { ArrowUpRight, History, MessageCircleQuestion, RefreshCw, Sparkles } from "lucide-react";
import { Button } from "@makeplane/propel/components/button";
// services
import {
  askError,
  isAIUnconfigured,
  workspaceQAService,
  type AskReference,
  type AskResponse,
} from "@/services/integrations/workspace-qa.service";

const MAX_HISTORY = 5;
const CITATION_PATTERN = /\[([^\][\s]{1,48})\]/g;

type TCitationSegment = { kind: "text"; value: string } | { kind: "citation"; reference: AskReference };

/** Split an answer into plain-text segments and [KEY-n] citation chips. */
function parseCitations(text: string, references: AskReference[]): TCitationSegment[] {
  const byDisplay = new Map(references.map((reference) => [reference.display, reference]));
  const segments: TCitationSegment[] = [];
  let cursor = 0;
  let match: RegExpExecArray | null;
  CITATION_PATTERN.lastIndex = 0;
  while ((match = CITATION_PATTERN.exec(text)) !== null) {
    if (match.index > cursor) segments.push({ kind: "text", value: text.slice(cursor, match.index) });
    const reference = byDisplay.get(match[1]);
    if (reference) segments.push({ kind: "citation", reference });
    else segments.push({ kind: "text", value: match[0] });
    cursor = match.index + match[0].length;
  }
  if (cursor < text.length) segments.push({ kind: "text", value: text.slice(cursor) });
  return segments;
}

const STATE_DOT: Record<string, string> = {
  backlog: "bg-secondary",
  unstarted: "bg-custom-primary-200",
  started: "bg-amber-500",
  completed: "bg-green-500",
  cancelled: "bg-red-500",
};

const stateDot = (group: string | null) => (group ? STATE_DOT[group] ?? "bg-secondary" : "bg-secondary");

function CitationChip({ reference }: { reference: AskReference }) {
  const navigate = useNavigate();
  return (
    <button
      type="button"
      onClick={() => navigate(reference.url)}
      title={`${reference.name} — open work item`}
      className="mx-0.5 inline-flex h-5 translate-y-px items-center rounded-md border border-subtle bg-accent-subtle px-1.5 text-12 font-medium text-accent-primary transition-colors hover:bg-accent-primary/20"
    >
      {reference.display}
    </button>
  );
}

function AnswerBody({ answer, references }: { answer: string; references: AskReference[] }) {
  return (
    <p className="whitespace-pre-wrap text-13 leading-relaxed text-primary">
      {parseCitations(answer, references).map((segment, index) =>
        segment.kind === "text" ? (
          <span key={index}>{segment.value}</span>
        ) : (
          <CitationChip key={index} reference={segment.reference} />
        )
      )}
    </p>
  );
}

function ReferenceRow({ reference }: { reference: AskReference }) {
  const navigate = useNavigate();
  return (
    <li>
      <button
        type="button"
        onClick={() => navigate(reference.url)}
        className="group flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-layer-2"
      >
        <span className={`size-1.5 shrink-0 rounded-full ${stateDot(reference.state_group)}`} aria-hidden />
        <span className="shrink-0 text-13 font-medium text-accent-primary">{reference.display}</span>
        <span className="min-w-0 flex-1 truncate text-13 text-secondary">{reference.name}</span>
        <span className="hidden shrink-0 text-12 text-tertiary sm:inline">{reference.priority ?? ""}</span>
        <ArrowUpRight
          className="size-3.5 shrink-0 text-tertiary transition-colors group-hover:text-primary"
          aria-hidden
        />
      </button>
    </li>
  );
}

export function AskPanel({ workspaceSlug }: { workspaceSlug: string }) {
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [result, setResult] = useState<(AskResponse & { question: string }) | null>(null);
  const [history, setHistory] = useState<string[]>([]);
  const [lastAttempt, setLastAttempt] = useState("");
  const [aiUnconfigured, setAIUnconfigured] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const resize = () => {
    const input = inputRef.current;
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
  };

  const submit = useCallback(
    async (value: string) => {
      const trimmed = value.trim();
      if (asking || trimmed.length === 0) return;
      setAsking(true);
      setLastAttempt(trimmed);
      setAIUnconfigured(false);
      setError(null);
      try {
        const data = await workspaceQAService.ask(workspaceSlug, { question: trimmed });
        setResult({ ...data, question: trimmed });
        setQuestion("");
        requestAnimationFrame(resize);
        setHistory((previous) => [trimmed, ...previous.filter((item) => item !== trimmed)].slice(0, MAX_HISTORY));
      } catch (cause) {
        if (isAIUnconfigured(cause)) setAIUnconfigured(true);
        else setError(askError(cause));
      } finally {
        setAsking(false);
      }
    },
    [asking, workspaceSlug]
  );

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      void submit(question);
    }
  };

  const reAsk = (value: string) => {
    setQuestion(value);
    inputRef.current?.focus();
    void submit(value);
  };

  const hasAnswer = result !== null && result.answer !== null;
  return (
    <div className="relative flex h-full w-full justify-center overflow-y-auto">
      <div className="flex w-full max-w-2xl flex-col gap-8 px-4 pb-16 pt-12">
        {/* heading */}
        <header className="flex flex-col items-center gap-2 text-center">
          <span className="flex size-10 items-center justify-center rounded-xl border border-subtle bg-accent-subtle text-accent-primary">
            <MessageCircleQuestion className="size-5" aria-hidden />
          </span>
          <h1 className="text-24 font-semibold text-primary">Ask anything about your work</h1>
          <p className="text-13 text-tertiary">
            Questions search every issue and comment in this workspace. Answers cite their sources.
          </p>
        </header>

        {/* ask input */}
        <div className="rounded-xl border border-subtle bg-layer-1 p-3 shadow-raised-100 transition-colors focus-within:border-strong">
          <textarea
            ref={inputRef}
            value={question}
            onChange={(event) => {
              setQuestion(event.target.value);
              resize();
            }}
            onKeyDown={handleKeyDown}
            rows={1}
            maxLength={500}
            placeholder="What is the status of login?"
            className="w-full resize-none bg-transparent px-1.5 py-1.5 text-13 text-primary outline-none placeholder:text-placeholder"
          />
          <div className="flex items-center justify-between gap-3 px-1.5 pt-1">
            <span className="text-12 text-tertiary">
              {question.length > 0 ? `${question.length}/500` : "Cmd/Ctrl + Enter to ask"}
            </span>
            <Button
              variant="primary"
              size="sm"
              stretch="auto"
              label="Ask"
              loading={asking}
              disabled={question.trim().length === 0}
              onClick={() => void submit(question)}
            />
          </div>
        </div>

        {/* ai_unconfigured */}
        {aiUnconfigured && (
          <div
            role="status"
            className="flex items-start gap-2.5 rounded-lg border border-subtle bg-layer-2 px-3.5 py-3 text-13 text-secondary"
          >
            <Sparkles className="mt-0.5 size-4 shrink-0 text-tertiary" aria-hidden />
            <p>
              AI is not configured on this instance — set <code className="font-medium">LLM_API_KEY</code> and{" "}
              <code className="font-medium">LLM_MODEL</code> in the admin settings, then ask again.
            </p>
          </div>
        )}

        {/* network error + retry */}
        {error && (
          <div className="flex items-center justify-between gap-3 rounded-lg border border-subtle bg-layer-2 px-3.5 py-3 text-13 text-secondary">
            <p className="min-w-0">{error}</p>
            <Button
              variant="secondary"
              size="sm"
              stretch="auto"
              label="Retry"
              icon={<RefreshCw className="size-3.5" aria-hidden />}
              onClick={() => void submit(lastAttempt)}
            />
          </div>
        )}

        {/* answer */}
        {result && result.answer !== null && (
          <section className="rounded-xl border border-subtle bg-layer-1 shadow-raised-100">
            <div className="border-b border-subtle px-5 py-4">
              <p className="text-12 text-tertiary">Asked: {result.question}</p>
            </div>
            <div className="px-5 py-4">
              <AnswerBody answer={result.answer} references={result.references} />
            </div>
            {result.references.length > 0 && (
              <div className="border-t border-subtle px-3 py-2">
                <p className="px-2 pt-1.5 pb-1 text-12 uppercase tracking-wide text-tertiary">References</p>
                <ul>
                  {result.references.map((reference) => (
                    <ReferenceRow key={reference.id} reference={reference} />
                  ))}
                </ul>
              </div>
            )}
            {result.model && (
              <div className="px-5 pb-3 pt-1">
                <p className="text-12 text-tertiary">Answered by {result.model}</p>
              </div>
            )}
          </section>
        )}

        {/* graceful no-matches message */}
        {result && !hasAnswer && result.message && (
          <div className="rounded-xl border border-dashed border-subtle px-5 py-4 text-center text-13 text-secondary" role="status">
            {result.message}
          </div>
        )}

        {/* first-visit hint */}
        {!result && !asking && !aiUnconfigured && !error && (
          <div className="rounded-xl border border-dashed border-subtle px-5 py-6 text-center text-13 text-tertiary">
            Try questions like <span className="text-secondary">&ldquo;what is blocked right now?&rdquo;</span> or{" "}
            <span className="text-secondary">&ldquo;who is working on onboarding?&rdquo;</span>
          </div>
        )}

        {/* recent questions */}
        {history.length > 0 && (
          <div className="flex flex-col gap-2">
            <p className="flex items-center gap-1.5 text-12 text-tertiary">
              <History className="size-3.5" aria-hidden />
              Recent questions
            </p>
            <div className="flex flex-wrap gap-1.5">
              {history.map((item) => (
                <button
                  key={item}
                  type="button"
                  onClick={() => reAsk(item)}
                  title="Ask again"
                  className="max-w-full truncate rounded-full border border-subtle px-2.5 py-1 text-12 text-secondary transition-colors hover:bg-layer-2 hover:text-primary"
                >
                  {item}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
