/**
 * Jev's option keys in plain words, for the observer's "Doing now" line.
 *
 * The keys are the ones `agents/src/agents/jev_agent/options.py` builds
 * (`step_towards:tree_12`, `move_north`, `deposit:chest_1:wood`, ...). Anything
 * unrecognised falls back to the key with its separators softened, so a new
 * option never shows up as an empty line.
 */

/** `oak_tree_12` -> `oak tree 12`, so ids read as words. */
function soften(value: string): string {
  return value.replace(/_/g, ' ');
}

const DIRECTION_ACTIONS: Record<string, string> = {
  move_north: 'stepping north',
  move_south: 'stepping south',
  move_east: 'stepping east',
  move_west: 'stepping west',
  move_northeast: 'stepping north-east',
  move_northwest: 'stepping north-west',
  move_southeast: 'stepping south-east',
  move_southwest: 'stepping south-west',
};

const SIMPLE_ACTIONS: Record<string, string> = {
  wait: 'waiting',
  wake: 'waking up',
  'sleep:ground': 'sleeping on the ground',
};

/** `walking toward the river`, or the bare phrase when there is no target. */
function withTarget(phrase: string, target: string, bare?: string): string {
  if (target) return `${phrase} ${target}`;
  return bare ?? phrase;
}

/** A readable phrase for one Jev option key. */
export function actionInWords(option: string): string {
  const key = option.trim();
  if (!key) return '-';
  if (SIMPLE_ACTIONS[key]) return SIMPLE_ACTIONS[key];
  if (DIRECTION_ACTIONS[key]) return DIRECTION_ACTIONS[key];

  const [verb, ...rest] = key.split(':');
  const target = soften(rest.join(' '));

  switch (verb) {
    case 'step_towards':
      return rest[0] === 'shout'
        ? `walking toward ${soften(rest.slice(1).join(' '))}, who shouted`
        : withTarget('walking toward', target, 'walking on');
    case 'attack':
      return withTarget('attacking', target);
    case 'collect':
      return withTarget('collecting from', target, 'collecting');
    case 'extract':
      return withTarget('working', target);
    case 'craft':
      return withTarget('crafting', target, 'crafting');
    case 'eat':
      return `eating ${target || 'something'}`;
    case 'equip':
      return withTarget('taking up the', target, 'equipping a tool');
    case 'pickup':
      return withTarget('picking up', target, 'picking something up');
    case 'deposit':
      return rest.length > 1
        ? `putting ${soften(rest[1])} into ${soften(rest[0])}`
        : withTarget('putting something into', target, 'storing something');
    case 'withdraw':
      return rest.length > 1
        ? `taking ${soften(rest[1])} from ${soften(rest[0])}`
        : withTarget('taking something from', target, 'taking something out');
    case 'place':
      return withTarget('placing a', soften(rest[0] ?? ''), 'building something');
    case 'rest':
      return withTarget('resting on', target, 'resting');
    case 'sleep':
      return withTarget('going to sleep on', target, 'going to sleep');
    case 'say':
      return 'speaking';
    case 'shout':
      return 'shouting';
    case 'invite':
      return 'offering to talk';
    case 'talk_to':
      return `joining ${target} to talk`;
    case 'dismantle':
      return withTarget('dismantling', target, 'dismantling');
    default:
      return soften(key.replace(/:/g, ' '));
  }
}
