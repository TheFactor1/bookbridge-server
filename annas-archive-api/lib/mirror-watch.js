/**
 * Anna's Archive mirror tracking -- on-demand only, no background polling.
 *
 * Anna's Archive rotates across several TLD mirrors (.gd, .gl, .pk, ...) as
 * domains get suspended or seized under legal pressure -- this happened
 * three times in 2026 alone (.org and .se suspended in January, .li deleted
 * from the registry entirely in March). Rather than guessing on a timer
 * (which risks switching away from a mirror that was only ever having a
 * transient blip), this module only checks and switches when explicitly
 * asked -- normally triggered by a real search actually failing for this
 * specific reason, via GET /api/mirror-refresh.
 */

import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";

// Confirmed alive as of 2026-09. .org/.se/.li are dead (suspended or deleted
// from the registry in 2026) -- left out entirely rather than kept as
// candidates that would just waste a check.
const CANDIDATE_TLDS = (process.env.ANNAS_CANDIDATE_TLDS || "gd,gl,pk")
  .split(",")
  .map((s) => s.trim().toLowerCase())
  .filter(Boolean);

const STATE_PATH = process.env.ANNAS_STATE_PATH || "./data/mirror-state.json";
const CHECK_TIMEOUT_MS = 10_000;

const HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) " +
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
};

let activeTld = CANDIDATE_TLDS[0];
let loaded = false;

function loadState() {
  if (loaded) return;
  loaded = true;
  try {
    const data = JSON.parse(readFileSync(STATE_PATH, "utf8"));
    if (data.activeTld && CANDIDATE_TLDS.includes(data.activeTld)) {
      activeTld = data.activeTld;
    }
  } catch {
    // No state file yet, or unreadable -- start from the first candidate.
  }
}

function saveState() {
  try {
    mkdirSync(dirname(STATE_PATH), { recursive: true });
    writeFileSync(
      STATE_PATH,
      JSON.stringify({ activeTld, updatedAt: new Date().toISOString() }, null, 2)
    );
  } catch (err) {
    console.error(`[mirror-watch] failed to persist state: ${err.message}`);
  }
}

export function getActiveTld() {
  loadState();
  return activeTld;
}

export function getCandidateTlds() {
  return CANDIDATE_TLDS.slice();
}

export function isCandidateTld(tld) {
  return CANDIDATE_TLDS.includes(String(tld || "").toLowerCase());
}

/**
 * A seized/suspended domain either fails to resolve at all (deleted from the
 * registry, e.g. .li in March 2026) or resolves to something that plainly
 * isn't Anna's Archive (a registrar parking page, a legal seizure notice).
 * DDoS-Guard's own challenge page counts as "alive" here -- it's served by
 * Anna's Archive's real infrastructure, just gating anonymous requests, which
 * is a completely different situation from the domain being gone. Exported
 * so the search path can reuse the exact same content check on its own
 * already-empty-results case, which a plain "did it respond" check can't
 * catch (that's exactly what .li did -- responded fine, wasn't Anna's
 * Archive).
 */
export async function isMirrorAlive(tld) {
  const url = `https://annas-archive.${tld}/`;
  try {
    const resp = await fetch(url, {
      headers: HEADERS,
      redirect: "manual",
      signal: AbortSignal.timeout(CHECK_TIMEOUT_MS),
    });
    if (resp.status === 403 && /ddos-guard/i.test(resp.headers.get("server") || "")) {
      return true;
    }
    if (resp.status >= 300 && resp.status < 400) {
      const loc = resp.headers.get("location") || "";
      return loc === "" || loc.includes(`annas-archive.${tld}`) || loc.startsWith("/");
    }
    if (!resp.ok) return false;
    const text = await resp.text();
    return /anna.?s archive/i.test(text);
  } catch {
    return false;
  }
}

/**
 * Test the current mirror; if it's actually dead, walk the other candidates
 * until one responds and switch to it. Called on-demand (GET
 * /api/mirror-refresh), never on a timer -- see the module comment for why.
 */
export async function refreshMirror() {
  loadState();
  const previousTld = activeTld;

  if (await isMirrorAlive(activeTld)) {
    return { previousTld, activeTld, switched: false, allDead: false };
  }

  for (const candidate of CANDIDATE_TLDS) {
    if (candidate === activeTld) continue;
    if (await isMirrorAlive(candidate)) {
      activeTld = candidate;
      saveState();
      return { previousTld, activeTld, switched: true, allDead: false };
    }
  }
  return { previousTld, activeTld, switched: false, allDead: true };
}
