/**
 * Fixed-position HUD block (help line, connection status, camera position),
 * anchored top-left. Previously these were Phaser `add.text` objects with
 * `setScrollFactor(0)`, but scroll-factor-0 objects are still scaled by the
 * camera zoom (0.5-3), so they shrank/grew and drifted off their pinned
 * position as the user zoomed. Plain DOM, like the rest of the overlay
 * (`OverlayUI`), sits outside the Phaser canvas and is immune to camera zoom.
 */

function requireElement(id: string): HTMLElement {
  const element = document.getElementById(id);
  if (!element) {
    throw new Error(`Hud element #${id} is missing from index.html`);
  }
  return element;
}

export class Hud {
  private helpEl: HTMLElement;
  private connectionEl: HTMLElement;
  private positionEl: HTMLElement;

  constructor() {
    this.helpEl = requireElement('hud-help');
    this.connectionEl = requireElement('hud-connection');
    this.positionEl = requireElement('hud-position');
  }

  setHelp(text: string): void {
    this.helpEl.textContent = text;
  }

  /** `color` is a CSS color string, e.g. '#00ff00'. */
  setConnection(text: string, color: string): void {
    this.connectionEl.textContent = text;
    this.connectionEl.style.color = color;
  }

  setPosition(text: string): void {
    this.positionEl.textContent = text;
  }
}
