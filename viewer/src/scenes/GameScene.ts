import Phaser from 'phaser';

const TILE_SIZE = 16;
const SCALE = 3;  // Scale up for visibility (16 * 3 = 48px per tile)

export class GameScene extends Phaser.Scene {
  private player?: Phaser.GameObjects.Sprite;
  private cursors?: Phaser.Types.Input.Keyboard.CursorKeys;

  constructor() {
    super({ key: 'GameScene' });
  }

  create(): void {
    // Create a simple test room (10x10)
    this.createTestRoom();

    // Create player
    this.createPlayer();

    // Setup camera controls
    this.setupCamera();

    // Add keyboard controls
    if (this.input.keyboard) {
      this.cursors = this.input.keyboard.createCursorKeys();
    }

    // Display instructions
    this.add.text(10, 10, 'Arrow keys to move camera\nPlayer animates automatically', {
      fontFamily: 'monospace',
      fontSize: '14px',
      color: '#ffffff',
    });
  }

  private createTestRoom(): void {
    const roomWidth = 10;
    const roomHeight = 10;

    // Floor tile indices in the Floor.png spritesheet
    // Stone floor is in the first few rows
    const stoneFloorTiles = [0, 1, 2];  // Variety of stone floor tiles

    // Wall tile index - using a simple filled tile
    // In Wall.png, there are various wall configurations
    const wallTileIndex = 0;

    for (let y = 0; y < roomHeight; y++) {
      for (let x = 0; x < roomWidth; x++) {
        const posX = x * TILE_SIZE * SCALE + TILE_SIZE * SCALE / 2;
        const posY = y * TILE_SIZE * SCALE + TILE_SIZE * SCALE / 2;

        // Border = walls, interior = floor
        const isWall = x === 0 || x === roomWidth - 1 || y === 0 || y === roomHeight - 1;

        if (isWall) {
          const wall = this.add.sprite(posX, posY, 'wall', wallTileIndex);
          wall.setScale(SCALE);
        } else {
          // Random floor tile for variety
          const floorTile = stoneFloorTiles[Math.floor(Math.random() * stoneFloorTiles.length)];
          const floor = this.add.sprite(posX, posY, 'floor', floorTile);
          floor.setScale(SCALE);
        }
      }
    }
  }

  private createPlayer(): void {
    // Place player in center of room
    const centerX = 5 * TILE_SIZE * SCALE + TILE_SIZE * SCALE / 2;
    const centerY = 5 * TILE_SIZE * SCALE + TILE_SIZE * SCALE / 2;

    this.player = this.add.sprite(centerX, centerY, 'player0', 0);
    this.player.setScale(SCALE);
    this.player.play('player-idle');
  }

  private setupCamera(): void {
    // Set camera bounds
    const worldWidth = 10 * TILE_SIZE * SCALE;
    const worldHeight = 10 * TILE_SIZE * SCALE;

    this.cameras.main.setBounds(0, 0, worldWidth, worldHeight);

    if (this.player) {
      this.cameras.main.startFollow(this.player, true, 0.1, 0.1);
    }
  }

  update(): void {
    if (!this.cursors) return;

    const camSpeed = 5;

    // Camera panning with arrow keys (for testing)
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
}
