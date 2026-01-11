import Phaser from 'phaser';
import type { SpriteIndex } from '../sprites';
import { spritesheetToKey, getUniqueSpritesheets, getAnimationFrames } from '../sprites';

const TILE_SIZE = 16;

export class PreloadScene extends Phaser.Scene {
  private spriteIndex?: SpriteIndex;

  constructor() {
    super({ key: 'PreloadScene' });
  }

  preload(): void {
    // Show loading progress
    const width = this.cameras.main.width;
    const height = this.cameras.main.height;

    const progressBar = this.add.graphics();
    const progressBox = this.add.graphics();
    progressBox.fillStyle(0x222222, 0.8);
    progressBox.fillRect(width / 2 - 160, height / 2 - 25, 320, 50);

    const loadingText = this.add.text(width / 2, height / 2 - 50, 'Loading...', {
      fontFamily: 'monospace',
      fontSize: '20px',
      color: '#ffffff',
    });
    loadingText.setOrigin(0.5, 0.5);

    this.load.on('progress', (value: number) => {
      progressBar.clear();
      progressBar.fillStyle(0x4a9eff, 1);
      progressBar.fillRect(width / 2 - 150, height / 2 - 15, 300 * value, 30);
    });

    this.load.on('complete', () => {
      progressBar.destroy();
      progressBox.destroy();
      loadingText.destroy();
    });

    // Load the sprite index JSON
    this.load.json('sprite-index', 'assets/sprite-index.json');
  }

  create(): void {
    // Get the sprite index
    this.spriteIndex = this.cache.json.get('sprite-index') as SpriteIndex;

    if (!this.spriteIndex) {
      console.error('Failed to load sprite index');
      this.scene.start('GameScene');
      return;
    }

    // Store sprite index in registry for other scenes to access
    this.registry.set('spriteIndex', this.spriteIndex);

    // Get all unique spritesheets and load them
    const spritesheets = getUniqueSpritesheets(this.spriteIndex);
    console.log('Loading spritesheets:', spritesheets);

    // Queue all spritesheets for loading
    for (const sheet of spritesheets) {
      const key = spritesheetToKey(sheet);
      this.load.spritesheet(key, `assets/dawnlike/${sheet}`, {
        frameWidth: TILE_SIZE,
        frameHeight: TILE_SIZE,
      });
    }

    // Load spritesheets and then create animations
    this.load.once('complete', () => {
      this.createAnimations();
      this.scene.start('GameScene');
    });

    this.load.start();
  }

  private createAnimations(): void {
    if (!this.spriteIndex) return;

    // Create animations for all sprites with animation frames
    let animCount = 0;
    for (const spriteKey of Object.keys(this.spriteIndex)) {
      const frames = getAnimationFrames(this.spriteIndex, spriteKey);
      if (frames && frames.length > 0) {
        const animKey = `${spriteKey}-idle`;
        this.anims.create({
          key: animKey,
          frames: frames,
          frameRate: 2,
          repeat: -1,
        });
        animCount++;
      }
    }

    console.log('Created animations:', animCount);
  }
}
