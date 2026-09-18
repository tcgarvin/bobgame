/**
 * Shared parsing for `conversation` world objects (docs/09_conversation_and_reflex.md,
 * section 2.1). Used by GameScene (marker + lines), OverlayUI (agent panel
 * transcript) and ObjectPanel (inspector). The world side is being built
 * concurrently, so every field is read defensively: a missing or malformed
 * value degrades to an empty result rather than throwing.
 */

/** `object_type` for a conversation object. */
export const CONVERSATION_TYPE = 'conversation';

/** Utterance channel used for `speak` actions inside a conversation. */
export const CONVERSATION_CHANNEL = 'conversation';

export interface ConversationTranscriptLine {
  tick: number;
  speaker: string;
  text: string;
}

/** `participants`: a JSON list of entity ids in join order, opener first. */
export function parseConversationParticipants(raw: string | undefined): string[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((value): value is string => typeof value === 'string');
  } catch (error) {
    console.error('conversation participants is not valid JSON', error, raw);
    return [];
  }
}

/** `transcript`: the last 12 `{tick, speaker, text}` lines, opening line first. */
export function parseConversationTranscript(raw: string | undefined): ConversationTranscriptLine[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    const lines: ConversationTranscriptLine[] = [];
    for (const entry of parsed) {
      if (entry && typeof entry === 'object') {
        const { tick, speaker, text } = entry as Record<string, unknown>;
        lines.push({
          tick: typeof tick === 'number' ? tick : 0,
          speaker: typeof speaker === 'string' ? speaker : '',
          text: typeof text === 'string' ? text : '',
        });
      }
    }
    return lines;
  } catch (error) {
    console.error('conversation transcript is not valid JSON', error, raw);
    return [];
  }
}
