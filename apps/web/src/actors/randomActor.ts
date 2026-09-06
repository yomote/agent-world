import type { Action, Observation } from "../api/types";

export interface Actor {
  propose(observation: Observation): Action | null;
}

export const directions = [
  { dx: 0, dy: -1 },
  { dx: 1, dy: 0 },
  { dx: 0, dy: 1 },
  { dx: -1, dy: 0 },
] as const;

// Actorは提案だけを返す。境界判定・状態変更・通信は担当しない。
export function createRandomActor(random: () => number = Math.random): Actor {
  return {
    propose(observation) {
      if (!observation.entities.some((entity) => entity.id === "A")) return null;
      const direction = directions[Math.floor(random() * directions.length)];
      return { action_id: crypto.randomUUID(), actor_id: "A", type: "move", ...direction };
    },
  };
}
