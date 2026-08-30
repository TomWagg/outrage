/**
 * Human-readable titles and descriptions for tower and raven cards.
 *
 * Used by the notifications module to render the body of card-acquired and
 * card-triggered modals. Server card payloads carry only ``id`` / ``name`` /
 * ``effect_key`` / ``params`` — descriptive text lives entirely on the client
 * so the server data files can stay terse.
 */

export interface CardCopy {
  title: string;
  description: string;
}

// ---- Tower cards (keyed by card ``name`` from data/tower_cards.json) ------

export const TOWER_CARDS: Record<string, CardCopy> = {
  File: {
    title: "File",
    description: "Burglary tool, value 2. Play during a jewel-theft attempt to lower the threshold.",
  },
  Dynamite: {
    title: "Dynamite",
    description: "Burglary tool, value 5. Play during a jewel-theft attempt to lower the threshold.",
  },
  Crowbar: {
    title: "Crowbar",
    description: "Burglary tool, value 4. Play during a jewel-theft attempt to lower the threshold.",
  },
  "A Key to the Jewel Cases": {
    title: "A Key to the Jewel Cases",
    description: "Burglary tool, value 9. The most powerful tool — almost guarantees a successful jewel theft.",
  },

  "Brace of Pistols": {
    title: "Brace of Pistols",
    description: "Weapon, value 15. Top-tier combat card.",
  },
  Crossbow: {
    title: "Crossbow",
    description: "Weapon, value 10. Strong combat card.",
  },
  "Suit of Armour": {
    title: "Suit of Armour",
    description: "Weapon, value 8. Defender only — cannot be used to attack.",
  },
  Mace: {
    title: "Mace",
    description: "Weapon, value 2. Useful filler in combat.",
  },
  Sword: {
    title: "Sword",
    description: "Weapon, value 5. Solid combat card.",
  },
  Dagger: {
    title: "Dagger",
    description: "Weapon, value 3. Modest combat card.",
  },

  Ladder: {
    title: "Ladder",
    description: "Traversal. Escape Beauchamp Tower without waiting out your sentence.",
  },
  Rope: {
    title: "Rope",
    description: "Traversal. Escape Beauchamp Tower without waiting out your sentence.",
  },

  "Tower Pass": {
    title: "Tower Pass",
    description: "Auto-accreditation at Queen's House, OR play to take an extra turn.",
  },
  Sanctuary: {
    title: "Sanctuary",
    description: "Teleport to Chapel Royal. Defenders may also play this in combat to cancel the attack.",
  },
  Disguise: {
    title: "Disguise",
    description: "Escape prison (not torture or rack), OR slip past a Yeoman Warder.",
  },
  "Royal Pardon": {
    title: "Royal Pardon",
    description: "Free yourself from Prison or Torture. Does NOT get you out of the Rack.",
  },
  "Rack Pardon": {
    title: "Rack Pardon",
    description: "The only card that frees you from the Rack.",
  },
  Confession: {
    title: "Confession",
    description: "While in Bowyer Tower torture, swap places with another player and frame them — they take over your remaining torture sentence.",
  },

  Firecrackers: {
    title: "Firecrackers",
    description: "Play while inside the White Tower. Every player still in the White Tower at the end of their next turn is sent to the Rack.",
  },
  Lasso: {
    title: "Lasso",
    description: "Pull a player to your space from up to 5 spaces away.",
  },
  "Binary Disruption": {
    title: "Binary Disruption",
    description:
      "Play after you roll, before you move. Deal the two dice out one each " +
      "between you and another player — a 5 and a 3 sends one of you 5 and " +
      "the other 3. Unlike a natural 7, you cannot cut the total anywhere else.",
  },
  "Mass Accretor": {
    title: "Mass Accretor",
    description: "Play on defence before reveal. Steal one random weapon from the attacker's committed pile, then resolve combat.",
  },
};

// ---- Raven cards (keyed by ``effect_key`` + optional param suffix) --------

const LOCATION_LABELS: Record<string, string> = {
  broad_arrow_tower: "Broad Arrow Tower",
  bell_tower: "Bell Tower",
  flint_tower: "Flint Tower",
  royal_armouries: "Royal Armouries",
  brick_tower: "Brick Tower",
  martin_tower: "Martin Tower",
  museum: "Museum",
  constable_tower: "Constable Tower",
  lanthorn_tower: "Lanthorn Tower",
  devereux_tower: "Devereux Tower",
  wakefield_tower: "Wakefield Tower",
  salt_tower: "Salt Tower",
  beauchamp_tower: "Beauchamp Tower",
  player_choice: "any tower of the drawer's choice",
};

/** Human name for a Summons destination, for prompts outside the card modal. */
export function summonsLocationLabel(location: string | undefined): string {
  if (!location) return "a location";
  return LOCATION_LABELS[location] ?? location.replace(/_/g, " ");
}

const JEWEL_LABELS: Record<string, string> = {
  sword: "the Sword",
  sceptre: "the Sceptre",
  orb: "the Orb",
  crown_prince_of_wales: "the Prince of Wales' Crown",
  crown_st_edward: "St Edward's Crown",
};

const POST_LABELS: Record<string, string> = {
  scaffold: "the Scaffold post",
  lanthorn: "the Lanthorn post",
  waterloo: "the Waterloo post",
  chapel: "the Chapel post",
  chooser: "a post of the drawer's choice",
};

/**
 * Resolve copy for a raven card given its effect_key and params.
 * Some cards vary by params (location, jewel, post); we substitute names in.
 */
export function ravenCardCopy(
  effectKey: string,
  params: Record<string, unknown> | null | undefined,
): CardCopy {
  const p = params ?? {};
  switch (effectKey) {
    case "go_to_location": {
      const loc = String(p.location ?? "");
      const label = LOCATION_LABELS[loc] ?? loc.replace(/_/g, " ") ?? "a location";
      return {
        title: "Summons",
        description: `Go to ${label}. You may opt out and forfeit a turn instead.`,
      };
    }
    case "go_to_jewel_view": {
      const j = String(p.jewel ?? "");
      const label = JEWEL_LABELS[j] ?? j.replace(/_/g, " ");
      return {
        title: "Jewel Glimpse",
        description: `Go to ${label}. You may immediately attempt to steal it.`,
      };
    }
    case "call_warder_to_post": {
      const post = String(p.post ?? "");
      const label = POST_LABELS[post] ?? post;
      return {
        title: "Warder Called",
        description: `A Yeoman Warder is deployed from the Barracks to ${label}.`,
      };
    }
    case "return_warder_to_barracks":
      return {
        title: "Warder Recalled",
        description: "Drawer picks any Yeoman Warder on a post and sends them back to the Barracks.",
      };
    case "pecked_by_ravens":
      return {
        title: "Pecked by Ravens",
        description: "Off to hospital with you peasant — miss your next turn.",
      };
    case "rest_on_bench":
      return {
        title: "Rest on a Bench",
        description: "Move to any bench of your choice and miss your next turn.",
      };
    case "photo_with_warder":
      return {
        title: "Photo with a Warder",
        description: "Move to any square adjacent to an occupied warder post.",
      };
    case "stopped_and_searched":
      return {
        title: "Stopped & Searched",
        description: "If you hold a jewel: play a Disguise, OR forfeit all jewels and weapons and go to the Bloody Tower.",
      };
    case "clerk_tea_exception":
      return {
        title: "Clerk's Tea Exception",
        description: "All movable players are sent to Queen's House. The drawer takes another turn.",
      };
    case "ghost":
      return {
        title: "Ghost",
        description: "Spooked by the Ghost of Anne Boleyn! Go to the Chapel Royal and miss your next turn.",
      };
    case "queens_birthday": {
      const today = new Date();
      const isBirthday = today.getMonth() === 3 && today.getDate() === 21;
      return {
        title: "Queen's Birthday",
        description: isBirthday
          ? "Every player draws TWO tower cards (today is 21 April!)."
          : "Every player draws a tower card.",
      };
    }
    case "lost":
      return {
        title: "Lost",
        description: "You wandered the wrong way — go to Queen's House to ask for directions.",
      };
    case "chief_yeoman_passes":
      return {
        title: "Chief Yeoman Passes",
        description: "The Chief Yeoman passes you by — draw a tower card.",
      };
    case "bowyer_questioning":
      return {
        title: "Bowyer Questioning",
        description: "You're hauled in for questioning — go to the Bowyer Tower (torture).",
      };
    case "shop_for_film":
      return {
        title: "Shop for Film",
        description: "Oh no your camera is out of film! Time for a quick errand — go to the Shop.",
      };
    case "governors_tea":
      return {
        title: "Governor's Tea",
        description: "Tea with the Governor — go to Queen's House and miss your next turn.",
      };
    case "beauchamp_imprisonment":
      return {
        title: "Beauchamp Imprisonment",
        description: "Locked up — go to the Beauchamp Tower (imprisoned).",
      };
    case "rack_of_torment":
      return {
        title: "The Rack of Torment",
        description: "Straight to the Rack with you peasant!",
      };
    case "metallicity":
      return {
        title: "Metallicity",
        description: "Every jewel still in the White Tower is flung to a random outer location and lies loose — pick it up by landing on its space.",
      };
    default:
      return {
        title: effectKey.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
        description: "A raven card has been triggered.",
      };
  }
}

/**
 * Resolve a tower card description from a Card payload (uses ``name`` field).
 */
/** "Brace of Pistols" / "brace_of_pistols" / "Brace Of Pistols" → same key. */
function slugify(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
}

// Built once, so a name that has been round-tripped through a card id (which
// lower-cases and underscores it) still matches. Without this, "Brace of
// Pistols" comes back as "Brace Of Pistols" and misses the exact-match lookup,
// leaving the card with no description at all.
const TOWER_CARDS_BY_SLUG: Record<string, CardCopy> = Object.fromEntries(
  Object.entries(TOWER_CARDS).map(([name, copy]) => [slugify(name), copy]),
);

// ---- Confinement banner ---------------------------------------------------

const CONFINEMENT_CAUSE_LEAD: Record<string, string> = {
  landed:        "The door was open. It is not open now.",
  three_doubles: "Three doubles in a row. The guards took an interest.",
  rack_sender:   "A tripwire, an alarm bell, and a great many boots on stone.",
  firecrackers:  "The White Tower is no place to linger once the powder goes up.",
  raven:         "The ravens saw. The ravens always see.",
  searched:      "Stopped, searched, and found wanting.",
  combat:        "You lost the fight, and the fight was the easy part.",
  framed:        "Somebody signed a confession. It had your name on it.",
};

/**
 * Copy for the red confinement banner. Deliberately theatrical: being locked up
 * is the worst thing that happens to a player who isn't losing a fight.
 */
export function confinementCopy(
  status: string,
  opts: { turns?: number; cause?: string; you: boolean; username: string },
): CardCopy {
  const who = opts.you ? "You are" : `${opts.username} is`;
  const sentence = opts.turns
    ? ` ${opts.turns} turn${opts.turns === 1 ? "" : "s"} to serve.`
    : "";
  const lead = CONFINEMENT_CAUSE_LEAD[opts.cause ?? ""] ?? "";
  const prefix = lead ? `${lead} ` : "";

  if (status === "RACKED") {
    return {
      title: "The Rack",
      description:
        `${prefix}${who} strapped to the rack beneath the White Tower. ` +
        `No dice will help — the wheel turns at its own pace and stops when ` +
        `it is finished.${sentence}`,
    };
  }
  if (status === "TORTURED") {
    return {
      title: "Taken for Questioning",
      description:
        `${prefix}${who} held in the Bowyer Tower, where the questions are ` +
        `patient and the answers are written down. Roll a double to walk out ` +
        `— or find somebody else to blame.${sentence}`,
    };
  }
  return {
    title: "Imprisoned",
    description:
      `${prefix}${who} locked in a cell with a very good view of the ` +
      `courtyard and no way down to it. Roll a double to get out.${sentence}`,
  };
}

export function towerCardCopy(name: string): CardCopy {
  return TOWER_CARDS[name]
    ?? TOWER_CARDS_BY_SLUG[slugify(name)]
    ?? {
      title: name || "Tower Card",
      description: "A tower card has been acquired.",
    };
}
