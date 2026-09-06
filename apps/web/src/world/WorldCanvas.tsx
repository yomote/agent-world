import Phaser from "phaser";
import { useEffect, useRef } from "react";
import type { WorldState } from "../api/types";

const CELL = 64;
const MARGIN = 38;

class WorldScene extends Phaser.Scene {
  private snapshot: WorldState | null = null;
  private graphics?: Phaser.GameObjects.Graphics;
  private labels: Phaser.GameObjects.Text[] = [];

  constructor() {
    super("world");
  }

  create() {
    this.graphics = this.add.graphics();
    this.drawWorld();
  }

  setWorld(world: WorldState) {
    this.snapshot = world;
    this.drawWorld();
  }

  private drawWorld() {
    const world = this.snapshot;
    const graphics = this.graphics;
    if (!world || !graphics) return;
    graphics.clear();
    this.labels.forEach((label) => label.destroy());
    this.labels = [];
    this.scale.resize(world.width * CELL + MARGIN * 2, world.height * CELL + MARGIN * 2);
    const label = (x: number, y: number, text: string, color = "#90a5be") => {
      const item = this.add.text(x, y, text, { fontFamily: "monospace", fontSize: "15px", color });
      item.setOrigin(0.5);
      this.labels.push(item);
    };
    graphics.lineStyle(1, 0x2a3d56);
    for (let x = 0; x <= world.width; x++) {
      graphics.lineBetween(
        MARGIN + x * CELL,
        MARGIN,
        MARGIN + x * CELL,
        MARGIN + world.height * CELL,
      );
      if (x < world.width) label(MARGIN + x * CELL + CELL / 2, MARGIN / 2, String(x));
    }
    for (let y = 0; y <= world.height; y++) {
      graphics.lineBetween(
        MARGIN,
        MARGIN + y * CELL,
        MARGIN + world.width * CELL,
        MARGIN + y * CELL,
      );
      if (y < world.height) label(MARGIN / 2, MARGIN + y * CELL + CELL / 2, String(y));
    }
    for (const entity of world.entities) {
      const x = MARGIN + (entity.position.x + 0.5) * CELL;
      const y = MARGIN + (entity.position.y + 0.5) * CELL;
      graphics.fillStyle(0x58dfc2, 0.1);
      graphics.fillRect(x - CELL / 2 + 1, y - CELL / 2 + 1, CELL - 2, CELL - 2);
      graphics.fillStyle(0x58dfc2);
      graphics.fillCircle(x, y, 20);
      label(x, y, entity.id, "#102333");
    }
  }
}

export function WorldCanvas({ world }: { world: WorldState }) {
  const host = useRef<HTMLDivElement>(null);
  const scene = useRef<WorldScene | null>(null);
  useEffect(() => {
    const current = new WorldScene();
    scene.current = current;
    const game = new Phaser.Game({
      type: Phaser.CANVAS,
      parent: host.current!,
      backgroundColor: "#101e30",
      width: 588,
      height: 460,
      scene: current,
      scale: { mode: Phaser.Scale.FIT, autoCenter: Phaser.Scale.CENTER_BOTH },
      banner: false,
      audio: { noAudio: true },
    });
    return () => {
      scene.current = null;
      game.destroy(true);
    };
  }, []);
  useEffect(() => {
    scene.current?.setWorld(world);
  }, [world]);
  return (
    <div
      className="world-canvas"
      ref={host}
      role="img"
      aria-label={`World ${world.width}×${world.height}。${world.entities.map((entity) => `${entity.id}の位置(${entity.position.x}, ${entity.position.y})`).join("、")}`}
    />
  );
}
