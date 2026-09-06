import type { components } from "./schema";

export type WorldState = components["schemas"]["WorldState"];
export type Entity = components["schemas"]["Entity"];
export type Action = components["schemas"]["Action"];
export type WorldEvent = components["schemas"]["Event"];
export type ActionResult = components["schemas"]["ActionResult"];

export type Observation = Readonly<Omit<WorldState, "entities">> & {
  readonly entities: readonly {
    readonly id: string;
    readonly position: Readonly<Entity["position"]>;
  }[];
};
