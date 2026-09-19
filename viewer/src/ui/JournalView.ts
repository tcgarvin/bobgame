/**
 * The agent panel's Journal section (docs/12_sleep_journal.md).
 *
 * The journal is the settler's own writing, so it is shown as prose rather than
 * as data: the plan it wrote for itself as it fell asleep first, then the story
 * it tells about the run, then the day's notes as they accumulate. The three
 * reference sections (Me, Others, Learnings) are folded away by default.
 */

import type { AgentJournal } from '../network';
import { details, label, mutedDiv, textDiv } from './dom';

/** Section written last thing at night: the plan for the coming day. */
const PLAN_SECTION = 'Tomorrow';
/** The running story of the run so far. */
const STORY_SECTION = 'Story so far';
/** The scratch section that grows through the day. */
const NOTES_SECTION = "Today's notes";
/** Folded away: reference material rather than narrative. */
const FOLDED_SECTIONS = ['Me', 'Others', 'Learnings'];

/** `written at t950 (fell asleep)` - where this journal text came from. */
export function journalSourceLine(journal: AgentJournal): string {
  const at = journal.tick === null ? '' : `t${journal.tick}`;
  if (journal.source === 'journal_rewrite') {
    return at ? `written at ${at} (fell asleep)` : 'written when it fell asleep';
  }
  if (journal.source === 'turn_start') {
    return at ? `as of ${at}` : 'as of this turn';
  }
  return 'final journal (old run)';
}

function section(journal: AgentJournal, name: string): string {
  return (journal.sections[name] ?? '').trim();
}

/** The `Today's notes` scratch text as a list, one item per written line. */
function notesList(text: string): HTMLElement {
  const list = document.createElement('ul');
  for (const rawLine of text.split('\n')) {
    const line = rawLine.trim().replace(/^[-*]\s*/, '');
    if (!line) continue;
    const item = document.createElement('li');
    item.textContent = line;
    list.appendChild(item);
  }
  return list;
}

/**
 * The Journal section's body: an empty journal (or none recorded yet) is one
 * muted line, so the section never disappears while a run is young.
 */
export function journalParts(journal: AgentJournal | null | undefined): HTMLElement[] {
  if (!journal) return [mutedDiv('no journal yet')];

  const parts: HTMLElement[] = [mutedDiv(journalSourceLine(journal))];

  const plan = section(journal, PLAN_SECTION);
  if (plan) {
    parts.push(label("Today's plan"), textDiv(plan));
  }

  const story = section(journal, STORY_SECTION);
  if (story) {
    parts.push(label(STORY_SECTION), textDiv(story));
  }

  const notes = section(journal, NOTES_SECTION);
  if (notes) {
    parts.push(label(NOTES_SECTION), notesList(notes));
  }

  const folded: HTMLElement[] = [];
  for (const name of FOLDED_SECTIONS) {
    const body = section(journal, name);
    if (!body) continue;
    folded.push(label(name), textDiv(body));
  }
  if (folded.length > 0) {
    parts.push(details('Me, others, learnings', folded));
  }

  if (parts.length === 1) {
    parts.push(mutedDiv('journal is empty'));
  }
  return parts;
}

/** The whole journal as plain text, for the raw view in the Details block. */
export function journalRawText(journal: AgentJournal | null | undefined): string {
  if (!journal) return '';
  return Object.entries(journal.sections)
    .map(([name, body]) => `## ${name}\n${body}`)
    .join('\n\n');
}
