import Phaser from 'phaser';

export class PreloadScene extends Phaser.Scene {
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

    // Load tilesets as spritesheets (16x16 tiles)
    this.load.spritesheet('floor', 'assets/tiles/Floor.png', {
      frameWidth: 16,
      frameHeight: 16,
    });

    this.load.spritesheet('wall', 'assets/tiles/Wall.png', {
      frameWidth: 16,
      frameHeight: 16,
    });

    this.load.spritesheet('player0', 'assets/tiles/Player0.png', {
      frameWidth: 16,
      frameHeight: 16,
    });

    this.load.spritesheet('player1', 'assets/tiles/Player1.png', {
      frameWidth: 16,
      frameHeight: 16,
    });

    // Note: Bushes are rendered using Graphics objects, no sprite needed
  }

  create(): void {
    // Create player idle animation (toggle between frame 0 of player0 and player1)
    this.anims.create({
      key: 'player-idle',
      frames: [
        { key: 'player0', frame: 0 },
        { key: 'player1', frame: 0 },
      ],
      frameRate: 2,
      repeat: -1,
    });

    this.scene.start('GameScene');
  }
}
