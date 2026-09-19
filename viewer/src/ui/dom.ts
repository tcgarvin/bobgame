/**
 * Small DOM builders shared by the overlay panels. They only produce elements;
 * the styling lives with the rest of the panel CSS in index.html.
 */

export function mutedDiv(text: string): HTMLElement {
  const div = document.createElement('div');
  div.className = 'muted';
  div.textContent = text;
  return div;
}

export function mutedItem(text: string): HTMLElement {
  const li = document.createElement('li');
  li.className = 'muted';
  li.textContent = text;
  return li;
}

/** An uppercase section label. */
export function label(text: string): HTMLElement {
  const div = document.createElement('div');
  div.className = 'label';
  div.textContent = text;
  return div;
}

/** A wrapping paragraph of prose. */
export function textDiv(text: string, className = 'text'): HTMLElement {
  const div = document.createElement('div');
  div.className = className;
  div.textContent = text;
  return div;
}

export function pre(text: string): HTMLElement {
  const element = document.createElement('pre');
  element.className = 'json';
  element.textContent = text;
  return element;
}

export function details(summaryText: string, children: HTMLElement[]): HTMLElement {
  const element = document.createElement('details');
  element.className = 'sub';
  const summary = document.createElement('summary');
  summary.textContent = summaryText;
  element.append(summary, ...children);
  return element;
}

/** A label/value row; the value is right-aligned. */
export function kvRow(labelText: string, value: string): HTMLElement {
  const row = document.createElement('div');
  row.className = 'kv';
  const left = document.createElement('span');
  left.textContent = labelText;
  const right = document.createElement('span');
  right.textContent = value;
  row.append(left, right);
  return row;
}

/** A single small muted line, used for the demoted brief details. */
export function noteLine(text: string): HTMLElement {
  const div = document.createElement('div');
  div.className = 'note-line';
  div.textContent = text;
  return div;
}

export function barRow(
  labelText: string,
  value: number,
  max: number,
  color: string,
  valueText?: string
): HTMLElement {
  const row = document.createElement('div');
  row.className = 'bar-row';

  const labelEl = document.createElement('span');
  labelEl.className = 'bar-label';
  labelEl.textContent = labelText;
  labelEl.title = labelText;

  const track = document.createElement('div');
  track.className = 'bar-track';
  const fill = document.createElement('div');
  fill.className = 'bar-fill';
  const ratio = max > 0 ? Math.max(0, Math.min(1, value / max)) : 0;
  fill.style.width = `${(ratio * 100).toFixed(1)}%`;
  fill.style.background = color;
  track.appendChild(fill);

  const valueEl = document.createElement('span');
  valueEl.className = 'bar-value';
  valueEl.textContent = valueText ?? `${Math.round(value)}/${Math.round(max)}`;

  row.append(labelEl, track, valueEl);
  return row;
}
