import Phaser from 'phaser';
import { WebSocketClient, WorldState } from '../network';
import type {
  ConnectionState,
  InterpolatedEntity,
  TrackedObject,
  UtteranceEvent,
  WorldClock,
} from '../network';
import { GROUND_LAYER_TYPES, NIGHT_START_FRACTION } from '../generated/rules';
import type { SpriteIndex } from '../sprites';
import { getSpriteFrame } from '../sprites';
import { ChunkManager, ViewportTracker } from '../terrain';
import { Hud, INSPECTABLE_TYPES, ObjectPanel, OverlayUI, ReplayBar } from '../ui';
import type { DeepLinkParams, DeepLinkState } from '../DeepLink';
import { REPLAY_WS_URL, buildQuery, buildUrl, parseDeepLink, resolveWsUrl } from '../DeepLink';
import { CONVERSATION_TYPE, parseConversationParticipants } from '../conversation';

const TILE_SIZE = 16;
const SCALE = 3; // Scale up for visibility (16 * 3 = 48px per tile)
const MIN_ZOOM = 0.5;
const MAX_ZOOM = 3;
const ZOOM_STEP = 0.1;

const PLAYER_TYPE = 'player';
const WOLF_TYPE = 'wolf';
const WOLF_SPRITE = 'wolf';

/** Sprite keys available for actors, used by name and by the hash fallback. */
const ACTOR_SPRITES = [
  'actor-1',
  'actor-2',
  'actor-3',
  'actor-4',
  'actor-5',
  'actor-6',
  'actor-7',
  'actor-8',
  'actor-9',
  'actor-10',
  'actor-11',
  'actor-12',
];

// Actor sprite assignments for entities
const ENTITY_SPRITE_MAP: Record<string, string> = {
  alice: 'actor-1',
  bob: 'actor-5',
  // hamlet's three named settlers, on the sprites of the ones they replaced.
  sloopa: 'actor-1',
  meduski: 'actor-2',
  pooka: 'actor-3',
  // The twelve settlers.
  ada: 'actor-1',
  bram: 'actor-2',
  cleo: 'actor-3',
  dov: 'actor-4',
  esme: 'actor-5',
  finn: 'actor-6',
  greta: 'actor-7',
  hale: 'actor-8',
  iris: 'actor-9',
  jory: 'actor-10',
  kai: 'actor-11',
  lena: 'actor-12',
};

/**
 * Object type to sprite key mapping.
 * Maps backend ObjectType values to sprite keys from TSX files.
 */
const OBJECT_SPRITE_MAP: Record<string, string> = {
  tree: 'oak-tree',
  rock_small: 'rock-small',
  rock_medium: 'rock-medium',
  rock_large: 'rock-large',
  boulder: 'boulder',
  chest: 'chest-closed',
  message_board: 'message-board',
  // Signs (docs/08_building.md, "Signs").
  sign: 'sign',
  item_pile: 'item-pile',
  // Natural building materials (docs/08_building.md).
  reeds: 'reeds',
  clay_deposit: 'clay-deposit',
  // Ore veins (docs/10_metal_and_sleep.md, section 2).
  copper_vein: 'copper-vein',
  iron_vein: 'iron-vein',
  // Ground layer.
  road: 'road',
  wood_floor: 'wood-floor',
  stone_floor: 'stone-floor',
  // Structure layer.
  wood_wall: 'wood-wall',
  stone_wall: 'stone-wall',
  door: 'door',
  bed: 'bed',
  chair: 'chair',
  table: 'table',
  workshop_table: 'workshop-table',
  // Crafting stations (docs/10_metal_and_sleep.md, section 1).
  furnace: 'furnace',
  anvil: 'anvil',
};

/*
 * GROUND_LAYER_TYPES (`../generated/rules`): a tile may hold one ground-layer
 * object plus one structure-layer object, so they draw underneath everything
 * else (docs/08_building.md, "Layers and placement").
 */

/** Draw depths: ground objects, then structures, then entities. */
const GROUND_OBJECT_DEPTH = 4;
const STRUCTURE_OBJECT_DEPTH = 5;

/** Draw depths for the conversation marker and its lines to participants. */
const CONVERSATION_LINE_DEPTH = 6;
const CONVERSATION_MARKER_DEPTH = 7;

/** Radius (px) of the conversation marker drawn on its anchor tile. */
const CONVERSATION_MARKER_RADIUS = 10;
const CONVERSATION_LINE_COLOR = 0x9aa0c0;
const CONVERSATION_SPEAKER_COLOR = 0xffe066;

/** Utterance channels rendered as speech bubbles (`local`, `shout`, `conversation`). */
const SPEECH_BUBBLE_CHANNELS = new Set(['local', 'shout', 'conversation']);

// Bush sprites are special - they have state-dependent sprites
const BUSH_SPRITE_FULL = 'berry-bush-full';
const BUSH_SPRITE_EMPTY = 'berry-bush-empty';

const BAR_WIDTH = TILE_SIZE * SCALE - 8;
const HEALTH_BAR_HEIGHT = 4;
const FOOD_BAR_HEIGHT = 2;
const SPEECH_BUBBLE_MS = 3000;
/** How long each step of the thinking-bubble dot animation lasts. */
const THOUGHT_DOT_MS = 500;
const DAMAGE_FLASH_MS = 350;

/**
 * Day/night tint (docs/10_metal_and_sleep.md, sections 3 and 4). The overlay is
 * a screen-space rectangle above the world but below the HUD text (depth 100)
 * and below the HTML overlay entirely, so only the map darkens. Everything is
 * derived from the latest tick's clock, never accumulated, so a replay seek
 * lands on exactly the right shade.
 */
const NIGHT_OVERLAY_DEPTH = 50;
const NIGHT_TINT_COLOR = 0x0a1436;
const NIGHT_TINT_ALPHA = 0.55;
/** Dusk ramps over the last tenth of the daytime. */
const DUSK_FRACTION = 0.1;
/** Dawn ramps over the first tenth of the whole day. */
const DAWN_FRACTION = 0.1;

/** Marker drawn above a sleeping entity, red once it has collapsed. */
const SLEEP_MARKER_COLOR = '#cfe3ff';
const COLLAPSE_MARKER_COLOR = '#ff6b6b';

/** How often the address bar is rewritten (about 4 Hz). */
const URL_SYNC_MS = 250;

/** Deterministic fallback sprite for entity ids we have no mapping for. */
function hashToActorSprite(entityId: string): string {
  let hash = 0;
  for (let i = 0; i < entityId.length; i++) {
    hash = (hash * 31 + entityId.charCodeAt(i)) >>> 0;
  }
  return ACTOR_SPRITES[hash % ACTOR_SPRITES.length];
}

/**
 * Tint strength for a moment in the day: 0 in broad daylight, full at night,
 * with a dusk ramp at the end of the daytime and a dawn ramp at the start of
 * the day. Pure function of the clock, so seeking is exact.
 */
export function nightTintAlpha(clock: WorldClock): number {
  const dayLength = clock.day_length;
  if (dayLength <= 0) return clock.night ? NIGHT_TINT_ALPHA : 0;

  const t = Math.max(0, Math.min(dayLength, clock.tick_of_day));
  const dayEnd = dayLength * NIGHT_START_FRACTION;
  if (t >= dayEnd) return NIGHT_TINT_ALPHA;

  const dawnEnd = dayLength * DAWN_FRACTION;
  if (t < dawnEnd) {
    return NIGHT_TINT_ALPHA * (1 - t / dawnEnd);
  }

  const duskStart = dayEnd * (1 - DUSK_FRACTION);
  if (t >= duskStart) {
    return NIGHT_TINT_ALPHA * ((t - duskStart) / (dayEnd - duskStart));
  }
  return 0;
}

/** `day 2 · 143/300 · night`, for the overlay clock readout. */
export function formatClock(clock: WorldClock): string {
  return `day ${clock.day} · ${clock.tick_of_day}/${clock.day_length} · ${
    clock.night ? 'night' : 'day'
  }`;
}

function spriteKeyForEntity(entity: InterpolatedEntity): string {
  if (entity.entityType === WOLF_TYPE) return WOLF_SPRITE;
  return ENTITY_SPRITE_MAP[entity.entityId] ?? hashToActorSprite(entity.entityId);
}

export class GameScene extends Phaser.Scene {
  private cursors?: Phaser.Types.Input.Keyboard.CursorKeys;
  private wasdKeys?: {
    W: Phaser.Input.Keyboard.Key;
    A: Phaser.Input.Keyboard.Key;
    S: Phaser.Input.Keyboard.Key;
    D: Phaser.Input.Keyboard.Key;
  };
  private spriteIndex?: SpriteIndex;

  // Network state
  private wsClient?: WebSocketClient;
  /**
   * Live mode only: a second socket to the replay server, used for nothing but
   * `get_agent_detail`, so the panel can show the journal and the planner turn
   * of the run being watched (docs/07_replay.md). Absent in replay mode, where
   * `wsClient` already talks to the replay server.
   */
  private detailClient?: WebSocketClient;
  private worldState: WorldState = new WorldState();
  private entitySprites: Map<string, Phaser.GameObjects.Sprite> = new Map();
  private objectSprites: Map<string, Phaser.GameObjects.Sprite> = new Map();
  private speechBubbles: Map<string, Phaser.GameObjects.Text> = new Map();
  /** Small "..." bubbles over agents whose planner is thinking. */
  private thoughtBubbles: Map<string, Phaser.GameObjects.Text> = new Map();
  private damageFlashUntil: Map<string, number> = new Map();
  /** "z" markers above sleeping entities, keyed by entity id. */
  private sleepMarkers: Map<string, Phaser.GameObjects.Text> = new Map();
  /** Screen-space day/night tint over the map. */
  private nightOverlay?: Phaser.GameObjects.Rectangle;
  private statusBars?: Phaser.GameObjects.Graphics;
  /** Marker on each conversation's anchor tile, keyed by object id. */
  private conversationMarkers: Map<string, Phaser.GameObjects.Graphics> = new Map();
  /** One shared graphics object for the anchor-to-participant lines, redrawn every frame. */
  private conversationLines?: Phaser.GameObjects.Graphics;

  // Chunk-based terrain
  private chunkManager?: ChunkManager;
  private viewportTracker?: ViewportTracker;
  private worldInitialized: boolean = false;

  // Camera controls
  private cameraFollowing: boolean = true;
  private followTarget?: Phaser.GameObjects.Sprite;

  // HTML overlay
  private hud?: Hud;
  private overlay?: OverlayUI;
  private objectPanel?: ObjectPanel;
  private replayBar?: ReplayBar;

  // Deep links and replay
  private deepLink: DeepLinkParams = parseDeepLink('');
  private deepLinkApplied: boolean = false;
  private pendingObjectId: string = '';
  private lastUrlQuery: string = '';
  private lastUrlSyncMs: number = 0;
  private connectionLabel: string = 'Connecting...';
  private connectionColor: string = '#ffff00';
  private keyHandler?: (event: KeyboardEvent) => void;

  constructor() {
    super({ key: 'GameScene' });
  }

  create(): void {
    this.deepLink = parseDeepLink(window.location.search);
    this.pendingObjectId = this.deepLink.object;
    // Get sprite index from registry
    this.spriteIndex = this.registry.get('spriteIndex') as SpriteIndex;

    if (!this.spriteIndex) {
      console.error('Sprite index not found in registry');
    }

    // Setup keyboard controls
    this.setupKeyboardControls();

    // Setup scroll wheel zoom
    this.setupScrollZoom();

    // Help line, connection status and camera position now live in the HTML
    // overlay (src/ui/Hud.ts): a scroll-factor-0 Phaser text is still scaled
    // by camera zoom, so it shrank/grew and drifted at zoom != 1.
    this.hud = new Hud();
    this.hud.setHelp(
      'Arrows/WASD: pan | Scroll: zoom | F: follow | P: panel | 0: settlement | 1-5: jump'
    );
    this.hud.setConnection(this.connectionLabel, this.connectionColor);

    // Graphics layer for health/food bars and the selection ring
    this.statusBars = this.add.graphics();
    this.statusBars.setDepth(15);

    // Day/night tint: screen-space, above the world, below the HUD text
    this.nightOverlay = this.add
      .rectangle(0, 0, 10, 10, NIGHT_TINT_COLOR, 0)
      .setOrigin(0.5)
      .setScrollFactor(0)
      .setDepth(NIGHT_OVERLAY_DEPTH);

    // Graphics layer for conversation anchor-to-participant lines
    this.conversationLines = this.add.graphics();
    this.conversationLines.setDepth(CONVERSATION_LINE_DEPTH);

    // Setup camera dev tools
    this.setupCameraControls();

    // HTML overlay (entity picker, agent panel, object panel, replay bar)
    this.setupOverlay();

    // Replay transport keys (space, , . [ ])
    this.setupReplayKeys();

    // Setup network
    this.setupNetwork();
  }

  private setupOverlay(): void {
    this.overlay = new OverlayUI(this.worldState, {
      onSelectEntity: (entityId) => this.selectEntity(entityId),
      onRequestAgentDetail: (entityId, tickId) => {
        if (this.detailClient) {
          // Live mode: the replay server serves the run this world is writing.
          this.detailClient.getAgentDetail(entityId, tickId, this.worldState.getRunId());
        } else {
          this.wsClient?.getAgentDetail(entityId, tickId);
        }
      },
    }, this.spriteIndex);
    this.overlay.setFollowing(this.cameraFollowing);
    if (this.deepLink.panel === false) {
      this.overlay.setPanelVisible(false);
    }

    this.objectPanel = new ObjectPanel(this.worldState, {
      onClose: () => this.selectObject(''),
    });

    this.replayBar = new ReplayBar(this.worldState, {
      onSeek: (tickId) => this.wsClient?.seek(tickId),
      onStep: (delta) => this.wsClient?.step(delta),
      onPlay: (speed) => this.wsClient?.play(speed),
      onPause: () => this.wsClient?.pause(),
      onSelectEntity: (entityId) => this.selectEntity(entityId),
      onCopyLink: () => this.copyDeepLink(),
      onTypingChange: (typing) => {
        if (this.input.keyboard) this.input.keyboard.enabled = !typing;
      },
    });
    if (this.deepLink.speed !== null) {
      this.replayBar.setSpeed(this.deepLink.speed);
    }

    this.refreshOverlays();
  }

  private refreshOverlays(): void {
    this.overlay?.refresh();
    this.objectPanel?.refresh();
    this.replayBar?.refresh();
  }

  /** Select a world object (pass '' to clear) and show its inspector. */
  private selectObject(objectId: string): void {
    this.worldState.setSelectedObject(objectId);
    this.refreshOverlays();
  }

  /** True while a form field has focus, so game keys should stay quiet. */
  private isTyping(): boolean {
    const active = document.activeElement;
    if (!active) return false;
    const tag = active.tagName;
    return (
      tag === 'INPUT' ||
      tag === 'TEXTAREA' ||
      tag === 'SELECT' ||
      (active as HTMLElement).isContentEditable === true
    );
  }

  /** Replay transport keys: space play/pause, ,/. step ±1, [/] step ±10. */
  private setupReplayKeys(): void {
    this.keyHandler = (event: KeyboardEvent) => {
      if (this.isTyping()) return;
      if (!this.worldState.isReplay()) return;

      switch (event.key) {
        case ' ':
          event.preventDefault();
          this.replayBar?.toggle();
          break;
        case ',':
          this.wsClient?.step(-1);
          break;
        case '.':
          this.wsClient?.step(1);
          break;
        case '[':
          this.wsClient?.step(-10);
          break;
        case ']':
          this.wsClient?.step(10);
          break;
        default:
          return;
      }
    };
    window.addEventListener('keydown', this.keyHandler);
  }

  /**
   * Copy a deep link to the current moment. In replay mode that is the current
   * URL; in live mode it is a replay link for the run being watched.
   */
  private copyDeepLink(): void {
    const state = this.deepLinkState();
    if (!this.worldState.isReplay()) {
      const runId = this.worldState.getRunId();
      if (!runId) {
        this.replayBar?.flashCopy('no run id');
        return;
      }
      state.run = runId;
      state.tick = this.worldState.getCurrentTick();
      // The replay server listens on its own port, so drop any live override.
      state.ws = '';
    }

    const url = buildUrl(state);
    navigator.clipboard.writeText(url).then(
      () => this.replayBar?.flashCopy('copied!'),
      (error: unknown) => {
        console.error('Clipboard write failed', error);
        this.replayBar?.flashCopy('copy failed');
      }
    );
  }

  /** The viewer state the address bar mirrors. */
  private deepLinkState(): DeepLinkState {
    const cam = this.cameras.main;
    const replay = this.worldState.isReplay();
    const status = this.worldState.getReplayStatus();
    const tileX = Math.floor((cam.scrollX + cam.width / 2) / (TILE_SIZE * SCALE));
    const tileY = Math.floor((cam.scrollY + cam.height / 2) / (TILE_SIZE * SCALE));

    return {
      run: replay ? this.worldState.getRunId() : '',
      ws: this.deepLink.ws,
      tick: replay ? (status?.tick_id ?? this.worldState.getCurrentTick()) : null,
      entity: this.worldState.getSelectedEntityId(),
      object: this.worldState.getSelectedObjectId(),
      x: this.cameraFollowing ? null : tileX,
      y: this.cameraFollowing ? null : tileY,
      zoom: cam.zoom,
      play: replay ? (status?.playing ?? false) : false,
      speed: replay ? (this.replayBar?.getSpeed() ?? 1) : null,
      panel: this.overlay?.isPanelVisible() ?? true,
    };
  }

  /** Keep the address bar a valid deep link, at about 4 Hz and only on change. */
  private syncUrl(): void {
    const now = performance.now();
    if (now - this.lastUrlSyncMs < URL_SYNC_MS) return;
    this.lastUrlSyncMs = now;

    const query = buildQuery(this.deepLinkState());
    if (query === this.lastUrlQuery) return;
    this.lastUrlQuery = query;
    window.history.replaceState(null, '', `${window.location.pathname}${query}`);
  }

  /**
   * Apply the deep link once the first snapshot has arrived: camera, zoom,
   * selection, then (replay only) the seek and playback.
   */
  private applyDeepLink(): void {
    if (this.deepLinkApplied) return;
    this.deepLinkApplied = true;

    const link = this.deepLink;

    if (link.zoom !== null) {
      this.cameras.main.zoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, link.zoom));
    }

    if (link.entity) {
      // The sprite may not exist yet; createEntitySprite re-attaches the
      // camera when it appears.
      this.worldState.setSelectedEntity(link.entity);
      this.toggleCameraFollow(true);
    }

    if (link.x !== null && link.y !== null) {
      this.gotoTile(link.x, link.y);
    }

    if (this.worldState.isReplay()) {
      this.wsClient?.getRunIndex();
      if (link.tick !== null) {
        this.wsClient?.seek(link.tick);
      }
      if (link.play) {
        this.wsClient?.play(this.replayBar?.getSpeed() ?? 1);
      }
    }

    this.refreshOverlays();
  }

  /**
   * Select an entity: updates the panel, makes it the follow target and
   * starts camera follow.
   */
  private selectEntity(entityId: string): void {
    this.worldState.setSelectedEntity(entityId);

    const sprite = entityId ? this.entitySprites.get(entityId) : undefined;
    if (sprite) {
      this.followTarget = sprite;
      this.toggleCameraFollow(true);
    }
    this.refreshOverlays();
  }

  private setupNetwork(): void {
    // Handle entity changes
    this.worldState.onEntityChange((action, entity) => {
      if (action === 'added') {
        this.createEntitySprite(entity);
      } else {
        this.removeEntitySprite(entity.entityId);
      }
    });

    // Flash entities red when they lose health
    this.worldState.onEntityStats((entity, healthDelta) => {
      if (healthDelta < 0) {
        this.damageFlashUntil.set(entity.entityId, this.time.now + DAMAGE_FLASH_MS);
        this.entitySprites.get(entity.entityId)?.setTint(0xff4444);
      }
    });

    // Speech bubbles for local utterances
    this.worldState.onUtterance((utterance) => this.showSpeechBubble(utterance));

    // Keep the HTML overlays in sync
    this.worldState.onStateUpdate(() => this.refreshOverlays());

    // Handle object changes
    this.worldState.onObjectChange((action, obj) => {
      if (obj.objectType === CONVERSATION_TYPE) {
        // Conversations have no sprite: a marker plus lines to participants,
        // drawn with Phaser graphics (see createConversationMarker). `updated`
        // needs no extra work here: the lines are redrawn every frame from
        // the latest object state in `update()`.
        if (action === 'added') {
          this.createConversationMarker(obj);
          if (this.pendingObjectId && obj.objectId === this.pendingObjectId) {
            this.pendingObjectId = '';
            this.selectObject(obj.objectId);
          }
        } else if (action === 'removed') {
          this.removeConversationMarker(obj.objectId);
        }
        return;
      }

      if (action === 'added') {
        this.createObjectSprite(obj);
        if (this.pendingObjectId && obj.objectId === this.pendingObjectId) {
          this.pendingObjectId = '';
          this.selectObject(obj.objectId);
        }
      } else if (action === 'removed') {
        this.removeObjectSprite(obj.objectId);
      } else if (action === 'updated') {
        this.updateObjectSprite(obj);
      }
    });

    // Handle chunk changes
    this.worldState.onChunkChange((action, chunkX, chunkY, terrain, version, changes) => {
      if (!this.chunkManager) return;

      if (action === 'loaded' && terrain && version !== undefined) {
        this.chunkManager.loadChunk(chunkX, chunkY, terrain, version);
      } else if (action === 'unloaded') {
        this.chunkManager.unloadChunk(chunkX, chunkY);
      } else if (action === 'terrain_updated' && changes) {
        for (const change of changes) {
          this.chunkManager.updateTile(chunkX, chunkY, change.x, change.y, change.floor_type);
        }
      }
    });

    // Create WebSocket client. The URL comes from the deep link: the replay
    // server's default port when a run is named, the live one otherwise.
    this.wsClient = new WebSocketClient(
      (message) => {
        this.worldState.handleMessage(message);

        // Initialize world after first snapshot. Later snapshots (a replay
        // seek sends one) must not recentre the camera or rebuild the chunks.
        if (!this.worldInitialized && this.worldState.isInitialized()) {
          this.initializeWorld();
          this.applyDeepLink();
        }
      },
      { url: resolveWsUrl(this.deepLink) }
    );

    // Handle connection state changes
    this.wsClient.onStateChange((state) => {
      this.updateConnectionStatus(state);
      if (state === 'connected' && this.deepLink.run) {
        this.wsClient?.openRun(this.deepLink.run);
      }
    });

    // Connect
    this.wsClient.connect();

    // Live mode: agent detail comes from the replay server reading this run's
    // traces. It is a best-effort extra - when no replay server is running,
    // every request is simply dropped.
    if (!this.deepLink.run) {
      this.detailClient = new WebSocketClient(
        (message) => this.worldState.handleMessage(message),
        { url: REPLAY_WS_URL }
      );
      this.detailClient.connect();
    }
  }

  private initializeWorld(): void {
    const worldSize = this.worldState.getWorldSize();
    const chunkSize = this.worldState.getChunkSize();

    console.log(`Initializing world: ${worldSize.width}x${worldSize.height}, chunk_size=${chunkSize}`);

    // Initialize chunk manager with sprite index for terrain mappings
    this.chunkManager = new ChunkManager(this, chunkSize, this.spriteIndex);

    // Setup camera bounds
    const worldWidth = worldSize.width * TILE_SIZE * SCALE;
    const worldHeight = worldSize.height * TILE_SIZE * SCALE;
    this.cameras.main.setBounds(0, 0, worldWidth, worldHeight);

    // Center on the settlement when the server knows one, else on the world
    const settlement = this.worldState.getSettlement();
    if (settlement) {
      this.cameras.main.centerOn(
        settlement.x * TILE_SIZE * SCALE,
        settlement.y * TILE_SIZE * SCALE
      );
    } else {
      this.cameras.main.centerOn(worldWidth / 2, worldHeight / 2);
    }

    // Initialize viewport tracker for chunk subscriptions
    this.viewportTracker = new ViewportTracker(this, chunkSize, (chunks) => {
      this.wsClient?.subscribeChunks(chunks);
    });

    // Request initial chunks for current viewport
    this.viewportTracker.forceUpdate();

    this.worldInitialized = true;
  }

  private updateConnectionStatus(state: ConnectionState): void {
    switch (state) {
      case 'connected':
        this.connectionLabel = 'Connected';
        this.connectionColor = '#00ff00';
        break;
      case 'connecting':
        this.connectionLabel = 'Connecting...';
        this.connectionColor = '#ffff00';
        break;
      case 'reconnecting':
        this.connectionLabel = 'Reconnecting...';
        this.connectionColor = '#ff8800';
        break;
      case 'disconnected':
        this.connectionLabel = 'Disconnected';
        this.connectionColor = '#ff0000';
        break;
    }
    this.refreshConnectionText();
  }

  /** Connection state plus the replay badge and the current tick. */
  private refreshConnectionText(): void {
    const badge = this.worldState.isReplay() ? ' [replay]' : '';
    const tick = this.worldState.isInitialized()
      ? ` t${this.worldState.getCurrentTick()}`
      : '';
    this.hud?.setConnection(`${this.connectionLabel}${badge}${tick}`, this.connectionColor);
  }

  private createEntitySprite(entity: InterpolatedEntity): void {
    const posX = entity.currentX * TILE_SIZE * SCALE + (TILE_SIZE * SCALE) / 2;
    const posY = entity.currentY * TILE_SIZE * SCALE + (TILE_SIZE * SCALE) / 2;

    const spriteKey = spriteKeyForEntity(entity);
    const spriteData = this.spriteIndex ? getSpriteFrame(this.spriteIndex, spriteKey) : null;

    if (!spriteData) {
      console.warn(`No sprite found for key ${spriteKey}, using fallback`);
      return;
    }

    const sprite = this.add.sprite(posX, posY, spriteData.textureKey, spriteData.frame);
    sprite.setScale(SCALE);
    sprite.setDepth(10); // Above tiles
    sprite.setVisible(entity.alive);

    // Play idle animation if available
    const animKey = `${spriteKey}-idle`;
    if (this.anims.exists(animKey)) {
      sprite.play(animKey);
    }

    // Clicking a sprite selects that entity
    sprite.setInteractive({ useHandCursor: true });
    sprite.on('pointerdown', () => this.selectEntity(entity.entityId));

    this.entitySprites.set(entity.entityId, sprite);
    console.log(
      `Created sprite for entity ${entity.entityId} (${spriteKey}) at (${entity.currentX}, ${entity.currentY})`
    );

    // If this is the first entity, set it as follow target
    if (!this.followTarget) {
      this.followTarget = sprite;
      if (this.cameraFollowing) {
        this.cameras.main.startFollow(sprite, true, 0.1, 0.1);
      }
    }

    // Re-attach the camera if this sprite belongs to the selected entity
    if (this.worldState.getSelectedEntityId() === entity.entityId) {
      this.followTarget = sprite;
      if (this.cameraFollowing) {
        this.cameras.main.startFollow(sprite, true, 0.1, 0.1);
      }
    }
  }

  private removeEntitySprite(entityId: string): void {
    const sprite = this.entitySprites.get(entityId);
    if (sprite) {
      if (this.followTarget === sprite) {
        this.cameras.main.stopFollow();
        this.followTarget = undefined;
      }
      sprite.destroy();
      this.entitySprites.delete(entityId);
      console.log(`Removed sprite for entity ${entityId}`);
    }
    this.speechBubbles.get(entityId)?.destroy();
    this.speechBubbles.delete(entityId);
    this.thoughtBubbles.get(entityId)?.destroy();
    this.thoughtBubbles.delete(entityId);
    this.sleepMarkers.get(entityId)?.destroy();
    this.sleepMarkers.delete(entityId);
    this.damageFlashUntil.delete(entityId);
  }

  /**
   * Position a world-space text label so it stays a constant on-screen size
   * and a constant screen-pixel offset from the entity regardless of camera
   * zoom (`MIN_ZOOM`-`MAX_ZOOM`). `worldX`/`worldY` are the anchor point in
   * world pixels (typically the entity's head); `screenOffsetX`/`screenOffsetY`
   * are the desired offset from that anchor in on-screen pixels.
   */
  private placeLabel(
    label: Phaser.GameObjects.Text,
    worldX: number,
    worldY: number,
    screenOffsetX: number,
    screenOffsetY: number,
  ): void {
    const zoom = this.cameras.main.zoom > 0 ? this.cameras.main.zoom : 1;
    label.setScale(1 / zoom);
    label.x = worldX + screenOffsetX / zoom;
    label.y = worldY + screenOffsetY / zoom;
  }

  /** Show a spoken (`local`, `shout` or `conversation`) utterance above the speaker for a few seconds. */
  private showSpeechBubble(utterance: UtteranceEvent): void {
    if (!SPEECH_BUBBLE_CHANNELS.has(utterance.channel)) return;

    const existing = this.speechBubbles.get(utterance.speaker_id);
    if (existing) {
      existing.destroy();
    }

    const text = this.add.text(0, 0, utterance.text, {
      fontFamily: 'monospace',
      fontSize: '14px',
      color: '#ffffff',
      backgroundColor: '#000000cc',
      padding: { x: 4, y: 2 },
      wordWrap: { width: 180 },
      align: 'center',
      resolution: 2,
    });
    text.setOrigin(0.5, 1);
    text.setDepth(30);

    this.speechBubbles.set(utterance.speaker_id, text);
    this.time.delayedCall(SPEECH_BUBBLE_MS, () => {
      if (this.speechBubbles.get(utterance.speaker_id) === text) {
        this.speechBubbles.delete(utterance.speaker_id);
      }
      text.destroy();
    });
  }

  /**
   * Show an animated "..." bubble while the agent's planner is thinking
   * (agent_status mode `planning`), and hide it otherwise.
   */
  private updateThoughtBubble(entityId: string, x: number, y: number, allowed: boolean): void {
    const thinking =
      allowed && this.worldState.getAgentStatus(entityId)?.mode === 'planning';
    let bubble = this.thoughtBubbles.get(entityId);
    if (!thinking) {
      bubble?.setVisible(false);
      return;
    }
    if (!bubble) {
      bubble = this.add.text(0, 0, '', {
        fontFamily: 'monospace',
        fontSize: '14px',
        fontStyle: 'bold',
        color: '#333333',
        backgroundColor: '#ffffffdd',
        padding: { x: 4, y: 0 },
        resolution: 2,
      });
      bubble.setOrigin(0, 1);
      bubble.setDepth(29);
      this.thoughtBubbles.set(entityId, bubble);
    }
    // One to three dots, stepping twice a second; fixed width so it does not jitter.
    const dots = 1 + (Math.floor(this.time.now / THOUGHT_DOT_MS) % 3);
    bubble.setText('.'.repeat(dots).padEnd(3, ' '));
    bubble.setVisible(true);
    this.placeLabel(bubble, x, y - (TILE_SIZE * SCALE) / 2, 12, -2);
  }

  private createObjectSprite(obj: TrackedObject): void {
    const posX = obj.position.x * TILE_SIZE * SCALE + (TILE_SIZE * SCALE) / 2;
    const posY = obj.position.y * TILE_SIZE * SCALE + (TILE_SIZE * SCALE) / 2;

    const spriteKey = this.objectSpriteKey(obj);
    if (!spriteKey) {
      console.warn(`No sprite mapping for object type: ${obj.objectType}`);
      return;
    }

    const spriteData = this.spriteIndex ? getSpriteFrame(this.spriteIndex, spriteKey) : null;

    if (!spriteData) {
      console.warn(`No sprite found for key: ${spriteKey} (object type: ${obj.objectType})`);
      return;
    }

    const existing = this.objectSprites.get(obj.objectId);
    if (existing) {
      existing.destroy();
    }

    const sprite = this.add.sprite(posX, posY, spriteData.textureKey, spriteData.frame);
    sprite.setScale(SCALE);
    sprite.setDepth(
      GROUND_LAYER_TYPES.has(obj.objectType) ? GROUND_OBJECT_DEPTH : STRUCTURE_OBJECT_DEPTH
    );

    const animKey = `${spriteKey}-idle`;
    if (this.anims.exists(animKey)) {
      sprite.play(animKey);
    }

    // Chests, piles, boards and bushes open the object inspector when clicked.
    if (INSPECTABLE_TYPES.has(obj.objectType)) {
      sprite.setInteractive({ useHandCursor: true });
      sprite.on('pointerdown', () => this.selectObject(obj.objectId));
    }

    this.objectSprites.set(obj.objectId, sprite);
  }

  /**
   * Draw the marker for a conversation object on its anchor tile. There is no
   * sprite for this in the tileset, so it is drawn with Phaser graphics: a
   * small speech-bubble shape, clickable to open the object inspector.
   */
  private createConversationMarker(obj: TrackedObject): void {
    this.conversationMarkers.get(obj.objectId)?.destroy();

    const posX = obj.position.x * TILE_SIZE * SCALE + (TILE_SIZE * SCALE) / 2;
    const posY = obj.position.y * TILE_SIZE * SCALE + (TILE_SIZE * SCALE) / 2;

    const marker = this.add.graphics();
    marker.setDepth(CONVERSATION_MARKER_DEPTH);
    marker.fillStyle(0xffe066, 0.9);
    marker.lineStyle(2, 0x333333, 1);
    marker.fillCircle(0, 0, CONVERSATION_MARKER_RADIUS);
    marker.strokeCircle(0, 0, CONVERSATION_MARKER_RADIUS);
    marker.fillTriangle(
      -4,
      CONVERSATION_MARKER_RADIUS - 2,
      4,
      CONVERSATION_MARKER_RADIUS - 2,
      0,
      CONVERSATION_MARKER_RADIUS + 6
    );
    marker.setPosition(posX, posY);
    marker.setInteractive(
      new Phaser.Geom.Circle(0, 0, CONVERSATION_MARKER_RADIUS),
      Phaser.Geom.Circle.Contains
    );
    marker.on('pointerdown', () => this.selectObject(obj.objectId));

    this.conversationMarkers.set(obj.objectId, marker);
  }

  private removeConversationMarker(objectId: string): void {
    this.conversationMarkers.get(objectId)?.destroy();
    this.conversationMarkers.delete(objectId);
  }

  /**
   * Redraw the lines from every live conversation's anchor to each current
   * participant's rendered position, highlighting the current speaker's line.
   * Runs every frame since participants move; state (participants, speaker)
   * comes straight from the latest tracked object.
   */
  private updateConversationLines(): void {
    const g = this.conversationLines;
    if (!g) return;
    g.clear();

    for (const obj of this.worldState.getObjects()) {
      if (obj.objectType !== CONVERSATION_TYPE) continue;

      const anchorX = obj.position.x * TILE_SIZE * SCALE + (TILE_SIZE * SCALE) / 2;
      const anchorY = obj.position.y * TILE_SIZE * SCALE + (TILE_SIZE * SCALE) / 2;
      const speaker = obj.state.speaker ?? '';

      for (const participantId of parseConversationParticipants(obj.state.participants)) {
        const entity = this.worldState.getEntity(participantId);
        if (!entity || !entity.alive) continue;

        const ex = entity.currentX * TILE_SIZE * SCALE + (TILE_SIZE * SCALE) / 2;
        const ey = entity.currentY * TILE_SIZE * SCALE + (TILE_SIZE * SCALE) / 2;
        const isSpeaker = participantId !== '' && participantId === speaker;

        g.lineStyle(
          isSpeaker ? 3 : 1,
          isSpeaker ? CONVERSATION_SPEAKER_COLOR : CONVERSATION_LINE_COLOR,
          isSpeaker ? 0.95 : 0.5
        );
        g.beginPath();
        g.moveTo(anchorX, anchorY);
        g.lineTo(ex, ey);
        g.strokePath();
      }
    }
  }

  private objectSpriteKey(obj: TrackedObject): string | null {
    if (obj.objectType === 'bush') {
      // Bushes have state-dependent sprites
      return obj.state.berry_count === '1' ? BUSH_SPRITE_FULL : BUSH_SPRITE_EMPTY;
    }
    return OBJECT_SPRITE_MAP[obj.objectType] ?? null;
  }

  private removeObjectSprite(objectId: string): void {
    const sprite = this.objectSprites.get(objectId);
    if (sprite) {
      sprite.destroy();
      this.objectSprites.delete(objectId);
    }
  }

  private updateObjectSprite(obj: TrackedObject): void {
    const sprite = this.objectSprites.get(obj.objectId);
    if (!sprite || !this.spriteIndex) {
      // The object may have been added by tick_completed before we saw it.
      this.createObjectSprite(obj);
      return;
    }

    const spriteKey = this.objectSpriteKey(obj);
    if (!spriteKey) return;

    const spriteData = getSpriteFrame(this.spriteIndex, spriteKey);
    if (spriteData) {
      sprite.setTexture(spriteData.textureKey, spriteData.frame);
    }
  }

  private setupKeyboardControls(): void {
    if (!this.input.keyboard) return;

    // Arrow keys
    this.cursors = this.input.keyboard.createCursorKeys();

    // WASD keys
    this.wasdKeys = {
      W: this.input.keyboard.addKey(Phaser.Input.Keyboard.KeyCodes.W),
      A: this.input.keyboard.addKey(Phaser.Input.Keyboard.KeyCodes.A),
      S: this.input.keyboard.addKey(Phaser.Input.Keyboard.KeyCodes.S),
      D: this.input.keyboard.addKey(Phaser.Input.Keyboard.KeyCodes.D),
    };
  }

  private setupCameraControls(): void {
    if (!this.input.keyboard) return;

    // F key to toggle camera follow
    this.input.keyboard.on('keydown-F', () => {
      this.toggleCameraFollow();
    });

    // P key toggles the agent panel
    this.input.keyboard.on('keydown-P', () => {
      this.overlay?.togglePanel();
    });

    // Number keys for quick navigation
    // 0: settlement, 1: Top-left, 2: Top-right, 3: Bottom-left, 4: Bottom-right, 5: Center
    this.input.keyboard.on('keydown-ZERO', () => this.jumpToSettlement());
    this.input.keyboard.on('keydown-ONE', () => this.jumpToCorner('top-left'));
    this.input.keyboard.on('keydown-TWO', () => this.jumpToCorner('top-right'));
    this.input.keyboard.on('keydown-THREE', () => this.jumpToCorner('bottom-left'));
    this.input.keyboard.on('keydown-FOUR', () => this.jumpToCorner('bottom-right'));
    this.input.keyboard.on('keydown-FIVE', () => this.jumpToCorner('center'));

    // Expose camera controls to window for console access
    (window as unknown as Record<string, unknown>).cam = {
      goto: (tileX: number, tileY: number) => this.gotoTile(tileX, tileY),
      follow: () => this.toggleCameraFollow(true),
      free: () => this.toggleCameraFollow(false),
      select: (entityId: string) => this.selectEntity(entityId),
      settlement: () => this.jumpToSettlement(),
      zoom: (level: number) => {
        this.cameras.main.zoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, level));
      },
      // Read access to the interpolated entities, for console checks and scripted tests.
      entities: () => this.worldState.getEntities(),
      tick: () => this.worldState.getCurrentTick(),
      pos: () => {
        const cam = this.cameras.main;
        const tileX = Math.floor(cam.scrollX / (TILE_SIZE * SCALE));
        const tileY = Math.floor(cam.scrollY / (TILE_SIZE * SCALE));
        return { x: cam.scrollX, y: cam.scrollY, tileX, tileY };
      },
    };
    console.log('Camera controls available: cam.goto(x,y), cam.follow(), cam.free(), cam.zoom(n), cam.pos(), cam.entities(), cam.tick()');
  }

  private toggleCameraFollow(follow?: boolean): void {
    if (follow !== undefined) {
      this.cameraFollowing = follow;
    } else {
      this.cameraFollowing = !this.cameraFollowing;
    }

    if (this.cameraFollowing && this.followTarget) {
      this.cameras.main.startFollow(this.followTarget, true, 0.1, 0.1);
    } else {
      this.cameras.main.stopFollow();
    }
    this.overlay?.setFollowing(this.cameraFollowing);
  }

  private jumpToSettlement(): void {
    const settlement = this.worldState.getSettlement();
    if (!settlement) {
      console.warn('No settlement position known for this world');
      return;
    }
    this.gotoTile(settlement.x, settlement.y);
  }

  private jumpToCorner(corner: 'top-left' | 'top-right' | 'bottom-left' | 'bottom-right' | 'center'): void {
    if (!this.worldInitialized) return;

    const worldSize = this.worldState.getWorldSize();
    const margin = 100; // Tiles from edge

    let tileX: number, tileY: number;
    switch (corner) {
      case 'top-left':
        tileX = margin;
        tileY = margin;
        break;
      case 'top-right':
        tileX = worldSize.width - margin;
        tileY = margin;
        break;
      case 'bottom-left':
        tileX = margin;
        tileY = worldSize.height - margin;
        break;
      case 'bottom-right':
        tileX = worldSize.width - margin;
        tileY = worldSize.height - margin;
        break;
      case 'center':
        tileX = worldSize.width / 2;
        tileY = worldSize.height / 2;
        break;
    }

    this.gotoTile(tileX, tileY);
  }

  private gotoTile(tileX: number, tileY: number): void {
    // Stop following when jumping
    this.toggleCameraFollow(false);

    const worldX = tileX * TILE_SIZE * SCALE;
    const worldY = tileY * TILE_SIZE * SCALE;
    this.cameras.main.centerOn(worldX, worldY);
    console.log(`Camera: jumped to tile (${tileX}, ${tileY})`);
  }

  private setupScrollZoom(): void {
    this.input.on(
      'wheel',
      (
        _pointer: Phaser.Input.Pointer,
        _gameObjects: Phaser.GameObjects.GameObject[],
        _deltaX: number,
        deltaY: number
      ) => {
        const camera = this.cameras.main;

        if (deltaY > 0) {
          // Zoom out
          camera.zoom = Math.max(MIN_ZOOM, camera.zoom - ZOOM_STEP);
        } else if (deltaY < 0) {
          // Zoom in
          camera.zoom = Math.min(MAX_ZOOM, camera.zoom + ZOOM_STEP);
        }
      }
    );
  }

  update(_time: number, delta: number): void {
    // Update world state interpolation
    this.worldState.update(delta);

    const selectedId = this.worldState.getSelectedEntityId();
    this.statusBars?.clear();

    // Update entity sprite positions from interpolated state
    for (const entity of this.worldState.getEntities()) {
      const sprite = this.entitySprites.get(entity.entityId);
      if (!sprite) continue;

      const x = entity.currentX * TILE_SIZE * SCALE + (TILE_SIZE * SCALE) / 2;
      const y = entity.currentY * TILE_SIZE * SCALE + (TILE_SIZE * SCALE) / 2;
      sprite.x = x;
      sprite.y = y;
      sprite.setVisible(entity.alive);

      // Clear the damage flash once it has run its course
      const flashUntil = this.damageFlashUntil.get(entity.entityId);
      if (flashUntil !== undefined && this.time.now >= flashUntil) {
        this.damageFlashUntil.delete(entity.entityId);
        sprite.clearTint();
      }

      const bubble = this.speechBubbles.get(entity.entityId);
      if (bubble) {
        this.placeLabel(bubble, x, y - (TILE_SIZE * SCALE) / 2, 0, -14);
      }
      // Speech wins the space above the head; the thought bubble yields to it.
      this.updateThoughtBubble(entity.entityId, x, y, entity.alive && !bubble);
      this.updateSleepMarker(entity, x, y);

      if (entity.alive) {
        this.drawEntityBars(entity, x, y, entity.entityId === selectedId);
      }
    }

    // Redraw conversation anchor-to-participant lines
    this.updateConversationLines();

    // Day/night tint from the latest tick's clock
    this.updateNightOverlay();

    // Update viewport tracker (requests new chunks when camera moves)
    this.viewportTracker?.update();

    // Status text and the address bar
    this.refreshConnectionText();
    this.syncUrl();

    // Update position display
    {
      const cam = this.cameras.main;
      const centerX = cam.scrollX + cam.width / 2;
      const centerY = cam.scrollY + cam.height / 2;
      const tileX = Math.floor(centerX / (TILE_SIZE * SCALE));
      const tileY = Math.floor(centerY / (TILE_SIZE * SCALE));
      const followStatus = this.cameraFollowing ? ' [Following]' : ' [Free]';
      this.hud?.setPosition(
        `Tile: (${tileX}, ${tileY}) Zoom: ${cam.zoom.toFixed(1)}x${followStatus}`
      );
    }

    // Camera panning
    const camSpeed = 5;

    // Arrow key panning
    if (this.cursors) {
      if (this.cursors.left.isDown) {
        this.cameras.main.scrollX -= camSpeed;
      }
      if (this.cursors.right.isDown) {
        this.cameras.main.scrollX += camSpeed;
      }
      if (this.cursors.up.isDown) {
        this.cameras.main.scrollY -= camSpeed;
      }
      if (this.cursors.down.isDown) {
        this.cameras.main.scrollY += camSpeed;
      }
    }

    // WASD panning
    if (this.wasdKeys) {
      if (this.wasdKeys.A.isDown) {
        this.cameras.main.scrollX -= camSpeed;
      }
      if (this.wasdKeys.D.isDown) {
        this.cameras.main.scrollX += camSpeed;
      }
      if (this.wasdKeys.W.isDown) {
        this.cameras.main.scrollY -= camSpeed;
      }
      if (this.wasdKeys.S.isDown) {
        this.cameras.main.scrollY += camSpeed;
      }
    }
  }

  /**
   * Resize and re-tint the day/night overlay. The rectangle has no scroll
   * factor, so it is positioned in screen space and enlarged by the camera
   * zoom (which scales screen-space objects about the camera centre).
   */
  private updateNightOverlay(): void {
    const overlay = this.nightOverlay;
    if (!overlay) return;

    const clock = this.worldState.getClock();
    if (!clock) {
      overlay.setAlpha(0);
      return;
    }

    const cam = this.cameras.main;
    const zoom = cam.zoom > 0 ? cam.zoom : 1;
    overlay.setPosition(cam.width / 2, cam.height / 2);
    overlay.setSize((cam.width / zoom) * 1.1, (cam.height / zoom) * 1.1);
    overlay.setAlpha(nightTintAlpha(clock));
  }

  /**
   * Show a small "z" above a sleeping entity: red once it has collapsed from
   * exhaustion (asleep at full fatigue, docs/10_metal_and_sleep.md).
   */
  private updateSleepMarker(entity: InterpolatedEntity, x: number, y: number): void {
    const asleep = entity.asleep && entity.alive;
    let marker = this.sleepMarkers.get(entity.entityId);

    if (!asleep) {
      marker?.setVisible(false);
      return;
    }

    if (!marker) {
      marker = this.add.text(0, 0, 'z', {
        fontFamily: 'monospace',
        fontSize: '14px',
        fontStyle: 'bold',
        color: SLEEP_MARKER_COLOR,
        backgroundColor: '#00000099',
        padding: { x: 3, y: 0 },
        resolution: 2,
      });
      marker.setOrigin(0.5, 1);
      marker.setDepth(28);
      this.sleepMarkers.set(entity.entityId, marker);
    }

    const collapsed = entity.maxFatigue > 0 && entity.fatigue >= entity.maxFatigue;
    marker.setColor(collapsed ? COLLAPSE_MARKER_COLOR : SLEEP_MARKER_COLOR);
    marker.setVisible(true);
    this.placeLabel(marker, x, y - (TILE_SIZE * SCALE) / 2, -16, -2);
  }

  /**
   * Draw the health bar (and, for players, the thin food bar) above an
   * entity, plus a selection outline for the currently selected one.
   */
  private drawEntityBars(
    entity: InterpolatedEntity,
    x: number,
    y: number,
    selected: boolean
  ): void {
    const g = this.statusBars;
    if (!g) return;

    const left = x - BAR_WIDTH / 2;
    const top = y - (TILE_SIZE * SCALE) / 2 - HEALTH_BAR_HEIGHT - 3;

    const healthRatio =
      entity.maxHealth > 0 ? Math.max(0, Math.min(1, entity.health / entity.maxHealth)) : 0;

    g.fillStyle(0x7a1f1f, 1);
    g.fillRect(left, top, BAR_WIDTH, HEALTH_BAR_HEIGHT);
    g.fillStyle(0x3fbf5f, 1);
    g.fillRect(left, top, BAR_WIDTH * healthRatio, HEALTH_BAR_HEIGHT);

    if (entity.entityType === PLAYER_TYPE) {
      const foodRatio =
        entity.maxFood > 0 ? Math.max(0, Math.min(1, entity.food / entity.maxFood)) : 0;
      const foodTop = top + HEALTH_BAR_HEIGHT + 1;
      g.fillStyle(0x3a2a14, 1);
      g.fillRect(left, foodTop, BAR_WIDTH, FOOD_BAR_HEIGHT);
      g.fillStyle(0xe0913a, 1);
      g.fillRect(left, foodTop, BAR_WIDTH * foodRatio, FOOD_BAR_HEIGHT);
    }

    if (selected) {
      const size = TILE_SIZE * SCALE;
      g.lineStyle(2, 0xffe066, 1);
      g.strokeRect(x - size / 2, y - size / 2, size, size);
    }
  }

  shutdown(): void {
    if (this.keyHandler) {
      window.removeEventListener('keydown', this.keyHandler);
      this.keyHandler = undefined;
    }
    // Cleanup network on scene shutdown
    if (this.wsClient) {
      this.wsClient.disconnect();
    }
    if (this.detailClient) {
      this.detailClient.disconnect();
      this.detailClient = undefined;
    }
    // Cleanup terrain chunks
    if (this.chunkManager) {
      this.chunkManager.clear();
    }
    // Cleanup conversation markers (the shared lines graphics is destroyed with the scene)
    for (const marker of this.conversationMarkers.values()) {
      marker.destroy();
    }
    this.conversationMarkers.clear();
    for (const marker of this.sleepMarkers.values()) {
      marker.destroy();
    }
    this.sleepMarkers.clear();
  }
}
